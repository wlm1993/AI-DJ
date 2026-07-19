"""The rolling-queue DJ session engine."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event

from .const import EXTRA_CANDIDATES, SIGNAL_SESSION_UPDATE
from .llm import DJPick, LLMClient, LLMError, TrackSuggestion

_LOGGER = logging.getLogger(__name__)

MA_DOMAIN = "music_assistant"
HISTORY_CONTEXT_SIZE = 40
# Words in an album/version name that usually mean "not the studio cut".
BAD_VERSION_WORDS = ("live", "remix", "karaoke", "tribute", "cover", "instrumental", "acoustic version", "re-record")


@dataclass
class ResolvedTrack:
    """A suggestion resolved to a playable Music Assistant track."""

    artist: str
    title: str
    uri: str

    @property
    def label(self) -> str:
        return f"{self.artist} – {self.title}"

    def as_dict(self) -> dict[str, str]:
        return {"artist": self.artist, "title": self.title}


class DJSession:
    """One live DJ session driving a single Music Assistant player."""

    def __init__(
        self,
        hass: HomeAssistant,
        llm: LLMClient,
        player_entity: str,
        prompt: str,
        lookahead: int,
        personality: str,
    ) -> None:
        self.hass = hass
        self.llm = llm
        self.player_entity = player_entity
        self.prompt = prompt
        self.lookahead = lookahead
        self.personality = personality

        self.active = False
        self.dj_comment: str = ""
        self.error: str | None = None
        self.liked: list[dict[str, str]] = []
        self.wishes: list[str] = []
        self.history: list[dict[str, str]] = []
        self.comment_log: list[str] = []
        self.current: ResolvedTrack | None = None
        self.upcoming: list[ResolvedTrack] = []

        self._pick_lock = asyncio.Lock()
        self._unsub_state: Any = None
        self._ma_config_entry_id: str | None = None

    # ---------------------------------------------------------------- lifecycle

    async def async_start(self) -> None:
        """Kick off the session: first LLM round, then play + queue."""
        self._ma_config_entry_id = self._find_ma_config_entry()
        pick = await self._llm_round(count=self.lookahead + 1)
        resolved = await self._resolve(pick.tracks, needed=self.lookahead + 1)
        if not resolved:
            raise HomeAssistantError(
                "None of the DJ's picks could be found in the Music Assistant library"
            )

        self.dj_comment = pick.comment
        if pick.comment:
            self.comment_log.append(pick.comment)
        self.current = resolved[0]
        self.upcoming = resolved[1:]
        self.active = True

        await self._enqueue(resolved[0], mode="replace")
        for track in resolved[1:]:
            await self._enqueue(track, mode="add")

        self._unsub_state = async_track_state_change_event(
            self.hass, [self.player_entity], self._handle_player_event
        )
        self._notify()

    async def async_stop(self) -> None:
        """End the session; the queue keeps playing whatever is left."""
        self.active = False
        if self._unsub_state:
            self._unsub_state()
            self._unsub_state = None
        self._notify()

    # ---------------------------------------------------------------- user input

    async def async_like(self) -> None:
        """Mark the current track as liked and favorite it in Music Assistant."""
        track = self._now_playing() or (self.current.as_dict() if self.current else None)
        if not track:
            return
        if track not in self.liked:
            self.liked.append(track)
        self._notify()
        await self._favorite_current_song()

    async def _favorite_current_song(self) -> None:
        """Press the player's MA 'Favorite current song' button (best-effort).

        Music Assistant exposes one such button per player; favoriting there
        propagates to the underlying provider (e.g. Tidal).
        """
        button = self._favorite_button_entity()
        if button is None:
            _LOGGER.debug("No favorite-song button found for %s", self.player_entity)
            return
        try:
            await self.hass.services.async_call(
                "button", "press", {"entity_id": button}, blocking=True
            )
        except HomeAssistantError as err:
            _LOGGER.warning("AI DJ could not favorite the current song: %s", err)

    def _favorite_button_entity(self) -> str | None:
        """Find the MA favorite-song button on the same device as the player."""
        registry = er.async_get(self.hass)
        entry = registry.async_get(self.player_entity)
        if entry is None or entry.device_id is None:
            return None
        for candidate in er.async_entries_for_device(
            registry, entry.device_id, include_disabled_entities=False
        ):
            if candidate.domain == "button" and candidate.entity_id.endswith(
                "_favorite_current_song"
            ):
                return candidate.entity_id
        return None

    async def async_wish(self, text: str) -> None:
        """Handle a song wish or mood change: queue matching tracks next."""
        self.wishes.append(text)
        self._notify()
        await self._top_up(count=2, mode="next", wish=text)

    async def async_skip(self) -> None:
        """Skip to the next queued track."""
        await self.hass.services.async_call(
            "media_player",
            "media_next_track",
            {"entity_id": self.player_entity},
            blocking=True,
        )

    # ---------------------------------------------------------------- engine

    @callback
    def _handle_player_event(self, event: Event[EventStateChangedData]) -> None:
        new_state = event.data["new_state"]
        if not self.active or new_state is None:
            return
        title = new_state.attributes.get("media_title")
        artist = new_state.attributes.get("media_artist")
        if not title:
            return
        if self.current and title == self.current.title:
            return
        self.hass.async_create_task(self._advance(title, artist or ""))

    async def _advance(self, title: str, artist: str) -> None:
        """A new track started playing on the player."""
        if self.current:
            self.history.append(self.current.as_dict())

        match = next(
            (t for t in self.upcoming if t.title.casefold() == title.casefold()), None
        )
        if match:
            self.upcoming.remove(match)
            self.current = match
        else:
            # Track we didn't queue (radio mode, manual queueing) - track it anyway.
            self.current = ResolvedTrack(artist=artist, title=title, uri="")
        self._notify()

        if len(self.upcoming) < self.lookahead:
            await self._top_up(count=self.lookahead - len(self.upcoming), mode="add")

    async def _top_up(self, count: int, mode: str, wish: str | None = None) -> None:
        """Ask the LLM for more tracks and enqueue them."""
        if count <= 0 or not self.active:
            return
        async with self._pick_lock:
            if not self.active:
                return
            try:
                pick = await self._llm_round(count=count, wish=wish)
                resolved = await self._resolve(pick.tracks, needed=count)
                if not resolved:
                    raise LLMError("no picks could be resolved in the library")
                # "next" inserts right after the current item, so enqueue in
                # reverse to end up with the intended play order.
                order = reversed(resolved) if mode == "next" else resolved
                for track in order:
                    await self._enqueue(track, mode=mode)
                if mode == "next":
                    # Queue-inserted tracks play before everything queued earlier.
                    self.upcoming = resolved + self.upcoming
                else:
                    self.upcoming.extend(resolved)
                self.dj_comment = pick.comment or self.dj_comment
                if pick.comment:
                    self.comment_log.append(pick.comment)
                self.error = None
            except (LLMError, HomeAssistantError) as err:
                _LOGGER.warning("AI DJ top-up failed: %s", err)
                self.error = str(err)
            self._notify()

    async def _llm_round(self, count: int, wish: str | None = None) -> DJPick:
        context: dict[str, Any] = {
            "dj_personality": self.personality,
            "brief": self.prompt,
            "wishes": self.wishes,
            "liked": self.liked,
            "recently_played": (
                self.history[-HISTORY_CONTEXT_SIZE:]
                + ([self.current.as_dict()] if self.current else [])
            ),
            "upcoming": [t.as_dict() for t in self.upcoming],
            "your_recent_comments": self.comment_log[-6:],
            "tracks_played_so_far": len(self.history),
            "count": count,
            "candidates": count + EXTRA_CANDIDATES,
        }
        if wish:
            context["respond_to_this_wish_now"] = wish
        return await self.llm.async_pick_tracks(context)

    # ------------------------------------------------------- Music Assistant I/O

    async def _resolve(
        self, suggestions: list[TrackSuggestion], needed: int
    ) -> list[ResolvedTrack]:
        """Look suggestions up in the library until we have enough playable tracks."""
        resolved: list[ResolvedTrack] = []
        for suggestion in suggestions:
            if len(resolved) >= needed:
                break
            track = await self._search_track(suggestion)
            if track:
                resolved.append(track)
            else:
                _LOGGER.debug("AI DJ could not resolve: %s", suggestion.label)
        return resolved

    async def _search_track(self, suggestion: TrackSuggestion) -> ResolvedTrack | None:
        try:
            response = await self.hass.services.async_call(
                MA_DOMAIN,
                "search",
                {
                    "config_entry_id": self._ma_config_entry_id,
                    "name": suggestion.title,
                    "artist": suggestion.artist,
                    "media_type": ["track"],
                },
                blocking=True,
                return_response=True,
            )
        except HomeAssistantError as err:
            _LOGGER.warning("Music Assistant search failed: %s", err)
            return None

        candidates = (response or {}).get("tracks", [])
        best: tuple[int, dict[str, Any]] | None = None
        for item in candidates:
            score = _score_candidate(item, suggestion)
            if score is None:
                continue
            if best is None or score > best[0]:
                best = (score, item)
        if best is None:
            return None
        item = best[1]
        artists = item.get("artists") or []
        artist_name = artists[0].get("name", suggestion.artist) if artists else suggestion.artist
        return ResolvedTrack(
            artist=artist_name, title=item.get("name", suggestion.title), uri=item["uri"]
        )

    async def _enqueue(self, track: ResolvedTrack, mode: str) -> None:
        """mode: replace | add | next."""
        await self.hass.services.async_call(
            MA_DOMAIN,
            "play_media",
            {
                "entity_id": self.player_entity,
                "media_id": track.uri,
                "media_type": "track",
                "enqueue": mode,
            },
            blocking=True,
        )

    def _find_ma_config_entry(self) -> str:
        all_entries = self.hass.config_entries.async_entries(MA_DOMAIN)
        entries = [
            entry for entry in all_entries if entry.state is ConfigEntryState.LOADED
        ] or all_entries
        if not entries:
            raise HomeAssistantError("The Music Assistant integration is not set up")
        return entries[0].entry_id

    # ---------------------------------------------------------------- state out

    def _now_playing(self) -> dict[str, str] | None:
        state = self.hass.states.get(self.player_entity)
        if state is None:
            return None
        title = state.attributes.get("media_title")
        if not title:
            return None
        return {"artist": state.attributes.get("media_artist", ""), "title": title}

    def _notify(self) -> None:
        async_dispatcher_send(self.hass, SIGNAL_SESSION_UPDATE)

    def snapshot(self) -> dict[str, Any]:
        """Session state for the sensor / card."""
        return {
            "active": self.active,
            "player": self.player_entity,
            "prompt": self.prompt,
            "dj_comment": self.dj_comment,
            "current_track": self.current.as_dict() if self.current else None,
            "upcoming": [t.as_dict() for t in self.upcoming],
            "liked": self.liked,
            "wishes": self.wishes,
            "history": self.history[-20:],
            "error": self.error,
        }


def _score_candidate(item: dict[str, Any], suggestion: TrackSuggestion) -> int | None:
    """Score a search hit; None means reject."""
    name = str(item.get("name", ""))
    if not item.get("uri"):
        return None
    score = 0
    if name.casefold() == suggestion.title.casefold():
        score += 10
    elif suggestion.title.casefold() in name.casefold():
        score += 5
    artists = " ".join(a.get("name", "") for a in item.get("artists") or [])
    if suggestion.artist.casefold() in artists.casefold():
        score += 10
    version = str(item.get("version") or "")
    album = ""
    if isinstance(item.get("album"), dict):
        album = str(item["album"].get("name") or "")
    haystack = f"{version} {album} {name}".casefold()
    if any(word in haystack for word in BAD_VERSION_WORDS):
        score -= 8
    if version:
        score -= 2
    return score
