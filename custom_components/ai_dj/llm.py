"""Pluggable LLM client (Anthropic / OpenAI) for the AI DJ."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import PROVIDER_ANTHROPIC, PROVIDER_OPENAI

_LOGGER = logging.getLogger(__name__)

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=90)

SYSTEM_PROMPT = """You are an AI DJ running a live listening session in a smart home.
You pick real, existing recordings that can be found in a music streaming library.

You receive a JSON object describing the session:
- "brief": what the listener asked for when the session started
- "wishes": later requests, newest last (a specific song wish or a mood change);
  the newest wish outranks the original brief when they conflict
- "liked": tracks the listener explicitly liked - steer towards similar
  artists, eras, energy and genres, but do not simply repeat those artists
- "recently_played": tracks already played - NEVER pick any of these again,
  and avoid picking a different version of the same song
- "upcoming": tracks already queued - never duplicate these either
- "count": how many tracks you must aim to get on the queue
- "candidates": how many ranked candidates to return (more than "count",
  because some may not resolve in the library - order by preference)

Rules:
- Only well-known, verifiable studio recordings. Never invent songs.
- Prefer the original artist and the plain studio version (no live/remix).
- Sequence like a DJ: coherent flow, gradual energy shifts, occasional
  pleasant surprises that still fit the room.
- "dj_comment" is one short, charming sentence about where you're taking
  the music next. No emoji spam, no track-by-track list.

Respond with ONLY a JSON object, no markdown fences, in this exact shape:
{"dj_comment": "...", "tracks": [{"artist": "...", "title": "..."}, ...]}"""


class LLMError(HomeAssistantError):
    """Raised when the LLM request fails."""


@dataclass
class TrackSuggestion:
    """A track suggested by the LLM."""

    artist: str
    title: str

    @property
    def label(self) -> str:
        return f"{self.artist} – {self.title}"


@dataclass
class DJPick:
    """Result of one LLM selection round."""

    comment: str
    tracks: list[TrackSuggestion] = field(default_factory=list)


class LLMClient:
    """Thin async client for the configured LLM provider."""

    def __init__(
        self, hass: HomeAssistant, provider: str, api_key: str, model: str
    ) -> None:
        self._session = async_get_clientsession(hass)
        self._provider = provider
        self._api_key = api_key
        self._model = model

    async def async_pick_tracks(self, context: dict[str, Any]) -> DJPick:
        """Ask the LLM for the next tracks given the session context."""
        user_message = json.dumps(context, ensure_ascii=False)
        raw = await self._complete(SYSTEM_PROMPT, user_message)
        data = _parse_json(raw)
        tracks = [
            TrackSuggestion(artist=str(t["artist"]), title=str(t["title"]))
            for t in data.get("tracks", [])
            if isinstance(t, dict) and t.get("artist") and t.get("title")
        ]
        if not tracks:
            raise LLMError(f"LLM returned no usable tracks: {raw[:200]}")
        return DJPick(comment=str(data.get("dj_comment", "")).strip(), tracks=tracks)

    async def async_validate(self) -> None:
        """Cheap round-trip to verify credentials and model name."""
        await self._complete("Reply with the single word: ok", "ping")

    async def _complete(self, system: str, user: str) -> str:
        try:
            if self._provider == PROVIDER_ANTHROPIC:
                return await self._complete_anthropic(system, user)
            if self._provider == PROVIDER_OPENAI:
                return await self._complete_openai(system, user)
        except aiohttp.ClientError as err:
            raise LLMError(f"Cannot reach {self._provider} API: {err}") from err
        raise LLMError(f"Unknown provider: {self._provider}")

    async def _complete_anthropic(self, system: str, user: str) -> str:
        resp = await self._session.post(
            ANTHROPIC_URL,
            timeout=REQUEST_TIMEOUT,
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": ANTHROPIC_VERSION,
            },
            json={
                "model": self._model,
                "max_tokens": 1500,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
        )
        body = await resp.json()
        if resp.status != 200:
            raise LLMError(_api_error("Anthropic", resp.status, body))
        try:
            return body["content"][0]["text"]
        except (KeyError, IndexError, TypeError) as err:
            raise LLMError(f"Unexpected Anthropic response: {body}") from err

    async def _complete_openai(self, system: str, user: str) -> str:
        resp = await self._session.post(
            OPENAI_URL,
            timeout=REQUEST_TIMEOUT,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )
        body = await resp.json()
        if resp.status != 200:
            raise LLMError(_api_error("OpenAI", resp.status, body))
        try:
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as err:
            raise LLMError(f"Unexpected OpenAI response: {body}") from err


def _api_error(provider: str, status: int, body: Any) -> str:
    detail = ""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            detail = err.get("message", "")
        elif isinstance(err, str):
            detail = err
    return f"{provider} API error {status}: {detail or body}"


def _parse_json(raw: str) -> dict[str, Any]:
    """Parse a JSON object, tolerating markdown fences and prose padding."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise LLMError(f"LLM response is not JSON: {raw[:200]}")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as err:
        raise LLMError(f"Cannot parse LLM JSON: {err}: {raw[:200]}") from err
    if not isinstance(data, dict):
        raise LLMError(f"LLM JSON is not an object: {raw[:200]}")
    return data
