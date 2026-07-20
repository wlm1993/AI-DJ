"""The rolling-queue DJ session engine."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event

from .announcer import Announcer
from .const import (
    DEFAULT_PERSONALITY,
    EXTRA_CANDIDATES,
    MAX_ANNOUNCE_PITCH,
    MIN_ANNOUNCE_PITCH,
    PERSONALITIES,
    SIGNAL_SESSION_UPDATE,
)
from .llm import DJPick, LLMClient, LLMError, Phase, TrackSuggestion

_LOGGER = logging.getLogger(__name__)

MA_DOMAIN = "music_assistant"
HISTORY_CONTEXT_SIZE = 40
# Words in an album/version name that usually mean "not the studio cut".
BAD_VERSION_WORDS = ("live", "remix", "karaoke", "tribute", "cover", "instrumental", "acoustic version", "re-record")

# The LLM won't set volume above this unless the listener's own words
# clearly ask for it (checked against LOUD_REQUEST_PATTERN below) - a
# code-side backstop on top of the same rule already in the system prompt.
MAX_AI_VOLUME = 75
LOUD_REQUEST_PATTERN = re.compile(
    r"\b(loud(?:er)?|max(?:imum)?(?:\s+volume)?|full\s+volume|crank(?:\s+it)?"
    r"|blast|pump\s+it\s+up|turn\s+it\s+(?:all\s+the\s+way\s+)?up|party\s+volume)\b",
    re.IGNORECASE,
)


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
        tts_entity: str = "",
    ) -> None:
        self.hass = hass
        self.llm = llm
        self.player_entity = player_entity
        self.prompt = prompt
        self.lookahead = lookahead
        # Seeded with a default; the LLM picks the persona that actually
        # fits "prompt" on the first round (see _set_comment/async_start).
        self.personality = DEFAULT_PERSONALITY
        self.announcer = Announcer(hass, player_entity, tts_entity)
        # Voice announcements are off by default - the player interrupts to
        # speak (it can't duck), so the listener opts in via the card toggle.
        self.announce_enabled = False
        # Live per-session pitch trim, added to the current persona's base
        # pitch - the "fun slider" on the card.
        self.announce_pitch = 0
        # Latest comment waiting to be spoken; held until the next track
        # transition so the voice lands between songs, not mid-song.
        self._pending_comment: str | None = None

        self.active = False
        self.dj_comment: str = ""
        self.error: str | None = None
        self.liked: list[dict[str, str]] = []
        self.wishes: list[str] = []
        self.history: list[dict[str, str]] = []
        self.comment_log: list[str] = []
        self.current: ResolvedTrack | None = None
        self.upcoming: list[ResolvedTrack] = []
        self.plan: list[Phase] = []

        self._pick_lock = asyncio.Lock()
        self._advance_lock = asyncio.Lock()
        self._unsub_state: Any = None
        self._ma_config_entry_id: str | None = None
        # len(self.history) at the moment self.plan was (re)set, so phase
        # progress is measured from when this plan started, not from session
        # start - a mood-shift plan restarts the count from "now".
        self._plan_started_at = 0

    # ---------------------------------------------------------------- lifecycle

    async def async_start(self) -> None:
        """Kick off the session: first LLM round, then play + queue."""
        self._ma_config_entry_id = self._find_ma_config_entry()
        pick = await self._llm_round(count=self.lookahead + 1, needs_initial_plan=True)
        resolved = await self._resolve(pick.tracks, needed=self.lookahead + 1)
        if not resolved:
            raise HomeAssistantError(
                "None of the DJ's picks could be found in the Music Assistant library"
            )

        if pick.personality:
            self.personality = pick.personality
        self._set_comment(pick.comment)
        if pick.plan:
            self.plan = pick.plan
            self._plan_started_at = 0
        if pick.volume is not None:
            await self._apply_volume(pick.volume, self.prompt)
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
        # The first track is just starting (playhead ~0), so the opening
        # comment can be spoken now - the interrupt-restart is invisible.
        self._speak_pending()

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

    async def _apply_volume(self, volume: int, source_text: str) -> None:
        """Set player volume from an LLM suggestion.

        Capped at MAX_AI_VOLUME unless source_text (the brief or the
        current wish - the listener's own words) explicitly asks for loud/
        max volume, mirroring the rule already given to the LLM in the
        system prompt.
        """
        target = max(0, min(volume, 100))
        if target > MAX_AI_VOLUME and not LOUD_REQUEST_PATTERN.search(source_text):
            target = MAX_AI_VOLUME
        try:
            await self.hass.services.async_call(
                "media_player",
                "volume_set",
                {"entity_id": self.player_entity, "volume_level": target / 100},
                blocking=True,
            )
        except HomeAssistantError as err:
            _LOGGER.warning("AI DJ could not set volume: %s", err)

    def _current_volume_percent(self) -> int | None:
        """The player's current volume as 0-100, or None if unknown."""
        state = self.hass.states.get(self.player_entity)
        if state is None:
            return None
        level = state.attributes.get("volume_level")
        if not isinstance(level, (int, float)):
            return None
        return round(level * 100)

    async def async_wish(self, text: str) -> None:
        """Handle a song wish or a mood/vibe change."""
        self.wishes.append(text)
        self._notify()
        await self._handle_wish(text)

    async def async_skip(self) -> None:
        """Skip to the next queued track."""
        await self.hass.services.async_call(
            "media_player",
            "media_next_track",
            {"entity_id": self.player_entity},
            blocking=True,
        )

    async def async_set_announce(self, enabled: bool) -> None:
        """Toggle whether dj_comment gets read aloud."""
        self.announce_enabled = enabled
        self._notify()

    async def async_set_announce_pitch(self, pitch: int) -> None:
        """Set the live pitch trim (semitones) applied on top of the persona's base pitch."""
        self.announce_pitch = max(MIN_ANNOUNCE_PITCH, min(pitch, MAX_ANNOUNCE_PITCH))
        self._notify()

    # ---------------------------------------------------------------- engine

    @callback
    def _handle_player_event(self, event: Event[EventStateChangedData]) -> None:
        new_state = event.data["new_state"]
        if not self.active or new_state is None:
            return
        title = new_state.attributes.get("media_title")
        artist = new_state.attributes.get("media_artist")
        content_id = new_state.attributes.get("media_content_id")
        if not title:
            return
        if self.current and title == self.current.title:
            return
        self.hass.async_create_task(self._advance(title, artist or "", content_id))

    async def _advance(
        self, title: str, artist: str, content_id: str | None = None
    ) -> None:
        """A new track started playing on the player.

        Guarded by a lock: fire-and-forget scheduling from
        _handle_player_event means a chatty player (extra state-changed
        events during a track transition) can trigger this twice for the
        same new title before the first call finishes. Without the lock,
        the second call would find self.current already updated, fail to
        match the title in self.upcoming (already consumed by the first
        call), and silently desync the queue bookkeeping from what's
        actually in Music Assistant.

        Matches by URI (media_content_id, which mirrors ResolvedTrack.uri)
        when available - exact and unambiguous - falling back to a
        casefolded title match otherwise. Title-only matching can grab the
        wrong entry when two queued tracks share a display title (e.g. the
        same song resolved as a plain cut and as "(Remastered 2019)").
        """
        async with self._advance_lock:
            if self.current and (
                title.casefold() == self.current.title.casefold()
                or (content_id and content_id == self.current.uri)
            ):
                return  # a duplicate event for the same transition - no-op

            if self.current:
                self.history.append(self.current.as_dict())

            match = None
            if content_id:
                match = next((t for t in self.upcoming if t.uri == content_id), None)
            if match is None:
                match = next(
                    (
                        t
                        for t in self.upcoming
                        if t.title.casefold() == title.casefold()
                    ),
                    None,
                )
            if match:
                self.upcoming.remove(match)
                self.current = match
            else:
                # Track we didn't queue (radio mode, manual queueing) - track
                # it anyway.
                self.current = ResolvedTrack(
                    artist=artist, title=title, uri=content_id or ""
                )
            self._notify()

            # A new song just started - the gap between tracks is where the
            # DJ voice belongs. Speak the comment queued from the last round
            # before topping up (which queues the next one).
            self._speak_pending()

            if len(self.upcoming) < self.lookahead:
                await self._top_up(
                    count=self.lookahead - len(self.upcoming), mode="add"
                )

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
                self._set_comment(pick.comment)
                self.error = None
            except (LLMError, HomeAssistantError) as err:
                _LOGGER.warning("AI DJ top-up failed: %s", err)
                self.error = str(err)
            self._notify()

    async def _handle_wish(self, text: str) -> None:
        """Resolve a wish.

        A mood/vibe shift replaces everything queued after the currently
        playing track (via Music Assistant's REPLACE_NEXT), since the
        listener wants the *direction* to change. A specific song request
        just gets inserted next, leaving the rest of the queue untouched.
        """
        if not self.active:
            return
        async with self._pick_lock:
            if not self.active:
                return
            try:
                pick = await self._llm_round(count=self.lookahead, wish=text)
                needed = self.lookahead if pick.mood_shift else len(pick.tracks)
                resolved = await self._resolve(pick.tracks, needed=needed)
                if not resolved:
                    raise LLMError("no picks could be resolved in the library")

                if pick.mood_shift:
                    if pick.plan:
                        self.plan = pick.plan
                        self._plan_started_at = len(self.history)
                    else:
                        # Direction changed but no fresh arc came back - the
                        # old plan's phases no longer apply, so drop it
                        # rather than show a stale/misleading status.
                        self.plan = []
                    if pick.personality:
                        self.personality = pick.personality
                    await self._enqueue(resolved[0], mode="replace_next")
                    for track in resolved[1:]:
                        await self._enqueue(track, mode="add")
                    self.upcoming = resolved
                else:
                    # "next" inserts right after the current item, so enqueue
                    # in reverse to end up with the intended play order.
                    for track in reversed(resolved):
                        await self._enqueue(track, mode="next")
                    self.upcoming = resolved + self.upcoming

                if pick.volume is not None:
                    await self._apply_volume(pick.volume, text)

                self._set_comment(pick.comment)
                self.error = None
            except (LLMError, HomeAssistantError) as err:
                _LOGGER.warning("AI DJ wish handling failed: %s", err)
                self.error = str(err)
            self._notify()

    async def _llm_round(
        self,
        count: int,
        wish: str | None = None,
        needs_initial_plan: bool = False,
    ) -> DJPick:
        context: dict[str, Any] = {
            "dj_personality": PERSONALITIES[self.personality]["description"],
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
        if self.plan:
            context["plan"] = [p.as_dict() for p in self.plan]
            context["current_phase_index"] = self._current_phase_index()
        if needs_initial_plan:
            context["needs_initial_plan"] = True
        if needs_initial_plan or wish:
            current_volume = self._current_volume_percent()
            if current_volume is not None:
                context["current_volume"] = current_volume
        if wish:
            context["respond_to_this_wish_now"] = wish
        return await self.llm.async_pick_tracks(context)

    def _current_phase_index(self) -> int:
        """Which phase of self.plan the session is in right now (0-based)."""
        if not self.plan:
            return 0
        played_in_plan = max(len(self.history) - self._plan_started_at, 0)
        cumulative = 0
        for index, phase in enumerate(self.plan):
            cumulative += max(phase.target_track_count, 1)
            if played_in_plan < cumulative:
                return index
        return len(self.plan) - 1  # plan exhausted - stay on the final phase

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
        """mode: replace | add | next | replace_next (MA QueueOption values)."""
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

    def _set_comment(self, comment: str) -> None:
        """Store a new dj_comment, log it, and queue it to be spoken.

        A falsy comment means the LLM didn't send a new one this round (e.g.
        a top-up), so the existing self.dj_comment carries on unchanged and
        nothing new gets queued.

        The comment is not spoken here - it's held in self._pending_comment
        and read aloud at the next track transition (see _speak_pending), so
        the voice lands in the gap between songs instead of interrupting one.
        """
        if not comment:
            return
        self.dj_comment = comment
        self.comment_log.append(comment)
        self._pending_comment = comment

    def _speak_pending(self) -> None:
        """Read the queued comment aloud (best-effort), if any.

        Called at track transitions. On a player that can't duck, Music
        Assistant interrupts and restarts the current track to announce;
        timing this to a transition (playhead ~0) makes that restart
        invisible, and the announcer seeks back afterwards to be safe.
        Respects the live announce_enabled toggle, so muting mid-session
        silently drops queued comments rather than banking them.
        """
        comment = self._pending_comment
        self._pending_comment = None
        if comment and self.announce_enabled:
            self.hass.async_create_task(
                self.announcer.async_speak(comment, self.personality, self.announce_pitch)
            )

    def snapshot(self) -> dict[str, Any]:
        """Session state for the sensor / card."""
        return {
            "active": self.active,
            "player": self.player_entity,
            "prompt": self.prompt,
            "dj_comment": self.dj_comment,
            "personality": self.personality,
            "personality_label": PERSONALITIES.get(self.personality, {}).get("label"),
            "announce_enabled": self.announce_enabled,
            "announce_pitch": self.announce_pitch,
            "current_track": self.current.as_dict() if self.current else None,
            "upcoming": [t.as_dict() for t in self.upcoming],
            "liked": self.liked,
            "wishes": self.wishes,
            "history": self.history[-20:],
            "plan": [p.as_dict() for p in self.plan],
            "current_phase_index": self._current_phase_index() if self.plan else None,
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
