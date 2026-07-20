"""Speaks dj_comment aloud via Chime TTS, ducked over the music by Music Assistant."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import PERSONALITIES

_LOGGER = logging.getLogger(__name__)

MA_DOMAIN = "music_assistant"
CHIME_TTS_DOMAIN = "chime_tts"
# How loud the voiceover sits over the ducked music, 1-100.
ANNOUNCE_VOLUME = 60


class Announcer:
    """Turns a DJ comment into speech played over the live queue."""

    def __init__(self, hass: HomeAssistant, player_entity: str, tts_entity: str) -> None:
        self.hass = hass
        self.player_entity = player_entity
        self.tts_entity = tts_entity

    async def async_speak(self, text: str, personality: str, pitch_trim: int = 0) -> None:
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
            url = await self._async_render(text, personality, pitch_trim)
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
        except HomeAssistantError as err:
            _LOGGER.warning("AI DJ could not speak comment: %s", err)

    async def _async_render(self, text: str, personality: str, pitch_trim: int) -> str:
        """Render `text` through Chime TTS with the persona's voice/speed/pitch."""
        persona = PERSONALITIES.get(personality, {})
        data: dict = {
            "message": text,
            "tts_platform": self.tts_entity,
        }
        if persona.get("voice"):
            data["voice"] = persona["voice"]
        if persona.get("speed"):
            data["tts_speed"] = persona["speed"]
        data["tts_pitch"] = int(persona.get("pitch", 0)) + pitch_trim
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
