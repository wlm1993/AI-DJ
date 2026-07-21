"""Speaks dj_comment aloud via Chime TTS, ducked over the music by Music Assistant."""

from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .const import DJ_PITCH, DJ_SPEED, DJ_VOICE

_LOGGER = logging.getLogger(__name__)

MA_DOMAIN = "music_assistant"
CHIME_TTS_DOMAIN = "chime_tts"
# How loud the voiceover sits over the ducked music, 1-100.
ANNOUNCE_VOLUME = 60
# Below this many seconds into a track, don't bother seeking back after the
# announcement - the track is close enough to the start already.
MIN_RESUME_SECONDS = 2.0


class Announcer:
    """Turns a DJ comment into speech played over the live queue."""

    def __init__(self, hass: HomeAssistant, player_entity: str, tts_entity: str) -> None:
        self.hass = hass
        self.player_entity = player_entity
        self.tts_entity = tts_entity

    async def async_speak(self, text: str, pitch_trim: int = 0) -> None:
        """Fire-and-forget: render `text` via Chime TTS and play it ducked.

        Best-effort - a TTS/announcement failure should never interrupt the
        DJ session itself, so errors (including Chime TTS not being
        installed/configured yet) are logged and swallowed.
        """
        if not text or not self.tts_entity:
            return
        if not self.hass.services.has_service(CHIME_TTS_DOMAIN, "say_url"):
            _LOGGER.debug("AI DJ: Chime TTS is not installed/configured - skipping announce")
            return
        try:
            url = await self._async_render(text, pitch_trim)
            # Grab the playhead right before the announcement: MA can't duck
            # on this player, so play_announcement interrupts and restarts the
            # current track from 0. We seek back afterwards to hide that.
            resume_position = self._playhead_seconds()
            await self.hass.services.async_call(
                MA_DOMAIN,
                "play_announcement",
                {
                    "entity_id": self.player_entity,
                    "url": url,
                    "announce_volume": ANNOUNCE_VOLUME,
                    # Skip Music Assistant's default "ding" before the voice.
                    "use_pre_announce": False,
                },
                blocking=True,
            )
            if resume_position is not None:
                await self._restore_playhead(resume_position)
        except HomeAssistantError as err:
            _LOGGER.warning("AI DJ could not speak comment: %s", err)

    def _playhead_seconds(self) -> float | None:
        """Live playhead of the player in seconds, or None if not playing.

        media_position is a snapshot taken at media_position_updated_at, so
        for a playing track we add the elapsed wall-clock time to get the
        true current position.
        """
        state = self.hass.states.get(self.player_entity)
        if state is None or state.state != "playing":
            return None
        position = state.attributes.get("media_position")
        if not isinstance(position, (int, float)):
            return None
        updated = state.attributes.get("media_position_updated_at")
        if isinstance(updated, datetime):
            position += max(0.0, (dt_util.utcnow() - updated).total_seconds())
        return float(position)

    async def _restore_playhead(self, position: float) -> None:
        """Seek back to where the music was before the announcement (best-effort)."""
        if position < MIN_RESUME_SECONDS:
            return
        try:
            await self.hass.services.async_call(
                "media_player",
                "media_seek",
                {"entity_id": self.player_entity, "seek_position": position},
                blocking=False,
            )
        except HomeAssistantError as err:
            _LOGGER.debug("AI DJ could not restore playhead: %s", err)

    async def _async_render(self, text: str, pitch_trim: int) -> str:
        """Render `text` through Chime TTS with the DJ's voice/speed/pitch."""
        data: dict = {
            "message": text,
            "tts_platform": self.tts_entity,
            "voice": DJ_VOICE,
            "tts_speed": DJ_SPEED,
            "tts_pitch": DJ_PITCH + pitch_trim,
        }
        result = await self.hass.services.async_call(
            CHIME_TTS_DOMAIN,
            "say_url",
            data,
            blocking=True,
            return_response=True,
        )
        url = (result or {}).get("url")
        if not url:
            raise HomeAssistantError(f"Chime TTS returned no playable url: {result}")
        return url
