"""Speaks dj_comment aloud via TTS, ducked over the music by Music Assistant."""

from __future__ import annotations

import json
import logging
from urllib.parse import urlencode

from homeassistant.components import media_source
from homeassistant.components.media_player import async_process_play_media_url
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import PERSONALITIES

_LOGGER = logging.getLogger(__name__)

MA_DOMAIN = "music_assistant"
# How loud the voiceover sits over the ducked music, 1-100.
ANNOUNCE_VOLUME = 60


class Announcer:
    """Turns a DJ comment into speech played over the live queue."""

    def __init__(self, hass: HomeAssistant, player_entity: str, tts_entity: str) -> None:
        self.hass = hass
        self.player_entity = player_entity
        self.tts_entity = tts_entity

    async def async_speak(self, text: str, personality: str) -> None:
        """Fire-and-forget: resolve `text` to audio and play it ducked.

        Best-effort - a TTS/announcement failure should never interrupt the
        DJ session itself, so errors are logged and swallowed.
        """
        if not text or not self.tts_entity:
            return
        try:
            url = await self._async_resolve_tts_url(text, personality)
            await self.hass.services.async_call(
                MA_DOMAIN,
                "play_announcement",
                {
                    "entity_id": self.player_entity,
                    "url": url,
                    "announce_volume": ANNOUNCE_VOLUME,
                },
                blocking=True,
            )
        except HomeAssistantError as err:
            _LOGGER.warning("AI DJ could not speak comment: %s", err)

    async def _async_resolve_tts_url(self, text: str, personality: str) -> str:
        """Render `text` through the configured TTS entity to a playable URL."""
        voice = PERSONALITIES.get(personality, {}).get("voice")
        params = {"message": text}
        if voice:
            params["options"] = json.dumps({"voice": voice})
        media_id = f"media-source://tts/{self.tts_entity}?{urlencode(params)}"
        resolved = await media_source.async_resolve_media(self.hass, media_id, None)
        return async_process_play_media_url(self.hass, resolved.url)
