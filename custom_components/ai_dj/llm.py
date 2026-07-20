"""Pluggable LLM client (Anthropic / OpenAI) for the AI DJ."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    PROVIDER_ANTHROPIC,
    PROVIDER_GEMINI,
    PROVIDER_OPENAI,
    PROVIDER_OPENAI_COMPATIBLE,
)

_LOGGER = logging.getLogger(__name__)

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=90)

SYSTEM_PROMPT = """You are an AI DJ running a live, continuous listening session in a
smart home. You pick real, existing recordings that can be found in a music
streaming library.

Your personality is given in "dj_personality" - stay fully in character in
every "dj_comment". Let it colour your word choice and your picks.

You receive a JSON object describing the session:
- "dj_personality": the persona you must embody
- "brief": what the listener asked for when the session started
- "wishes": later requests, newest last (a specific song wish or a mood change);
  the newest wish outranks the original brief when they conflict
- "liked": tracks the listener explicitly liked - steer towards similar
  artists, eras, energy and genres, but do not simply repeat those artists
- "recently_played": tracks already played this session - NEVER pick any of
  these again, and avoid a different version of the same song
- "upcoming": tracks already queued - never duplicate these either
- "your_recent_comments": things YOU already said earlier this session
- "tracks_played_so_far": how many tracks have already played
- "count": the target number of tracks to add to the queue
- "candidates": how many ranked candidates to return (more than "count",
  because some may not resolve in the library - order by preference)
- "respond_to_this_wish_now": present only when the listener just made a new
  request. Decide whether it is:
  (a) a SPECIFIC SONG/ARTIST REQUEST ("play some Prince", "queue Superstition
      next") - it does not change the overall direction of the set, or
  (b) a MOOD/VIBE/GENRE SHIFT ("switch to something mellow", "let's get more
      upbeat", "less pop, more jazz") - it changes where the set is headed.
  Set "mood_shift" to true ONLY for case (b). For case (a), set it to false
  and return ONLY the requested track(s) in "tracks" (do not pad with extra,
  unrelated picks) - up to "count" is a ceiling, not a target, when
  "mood_shift" is false. For case (b), return up to "count" tracks that
  commit fully to the new direction, since they will replace everything
  currently queued.
- "needs_initial_plan": present and true only on the very first round of a
  brand-new session - see "Planning the arc" below.
- "plan": the session's current arc (once one exists) - an ordered list of
  phases, each {"label", "description", "target_track_count"}.
- "current_phase_index": which phase of "plan" is current right now
  (0-based) - shape your picks to fit that phase's mood and energy.

Planning the arc:
- A DJ set has a shape, not just a playlist. Include a "plan" in your
  response ONLY when "needs_initial_plan" is true, or when you set
  "mood_shift" to true - otherwise omit "plan" entirely (an existing plan,
  if any, carries on unchanged).
- A plan is 3-6 phases: [{"label": "short name", "description": "one
  sentence on the mood/energy/genre direction", "target_track_count": N},
  ...]. "target_track_count" is a rough length for that phase, not a hard
  rule.
- Choose the arc's SHAPE to fit "brief" - don't force a generic party arc
  onto everything:
  * a party/dance brief usually wants the classic warm-up -> build -> peak
    -> cool-down shape
  * a dinner-party or background-listening brief usually wants sustained,
    steady warmth with little or no peak
  * a workout brief usually wants high energy sustained for most of the
    session, easing off only near the very end
  * a focus/study brief usually wants a flat, unobtrusive mood throughout
  Use judgement for anything else - the arc should serve the listener's
  actual activity, not a template.
- You do not have BPM or musical-key data for tracks. Reason about energy
  and transitions from genre, era, tempo feel and your general music
  knowledge - not precise harmonic mixing.
- Avoid clustering the same artist too tightly, within a phase or across
  adjacent phases.
- When "mood_shift" is true, the new "plan" replaces the old one and
  describes the arc for the REST of the session starting now - this is a
  fresh direction the listener chose, not a continuation of the old plan.

Continuity is essential:
- This is ONE ongoing set. If "tracks_played_so_far" is above zero, the
  session is already rolling - do NOT talk as if you are just starting
  ("let's kick things off", "to open", etc.). Reference the flow so far,
  build on the mood, and don't repeat points you made in
  "your_recent_comments".
- Treat "liked", "recently_played" and "wishes" as your running memory of
  how this session has gone, and keep the journey coherent.

Rules:
- Only well-known, verifiable studio recordings. Never invent songs.
- Prefer the original artist and the plain studio version (no live/remix).
- Sequence like a DJ: coherent flow, gradual energy shifts, occasional
  pleasant surprises that still fit the room.
- "dj_comment" is one short, in-character sentence about where you're taking
  the music next. No emoji spam, no track-by-track list.

Respond with ONLY a JSON object, no markdown fences, in this exact shape:
{"dj_comment": "...", "mood_shift": false, "plan": [{"label": "...", "description": "...", "target_track_count": 4}, ...], "tracks": [{"artist": "...", "title": "..."}, ...]}
Omit "mood_shift" (or leave it false) when there is no "respond_to_this_wish_now" to judge.
Omit "plan" unless "needs_initial_plan" is true or you are setting "mood_shift" to true."""


class LLMError(HomeAssistantError):
    """Raised when the LLM request fails."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


# HTTP statuses worth retrying: rate limit, overloaded, transient server error.
RETRYABLE_STATUS = {429, 500, 502, 503, 529}
MAX_ATTEMPTS = 3


@dataclass
class TrackSuggestion:
    """A track suggested by the LLM."""

    artist: str
    title: str

    @property
    def label(self) -> str:
        return f"{self.artist} – {self.title}"


@dataclass
class Phase:
    """One stretch of the session's planned arc."""

    label: str
    description: str
    target_track_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "description": self.description,
            "target_track_count": self.target_track_count,
        }


@dataclass
class DJPick:
    """Result of one LLM selection round."""

    comment: str
    tracks: list[TrackSuggestion] = field(default_factory=list)
    # True only when the LLM judged the wish being resolved as a mood/vibe
    # shift rather than a specific song request; meaningless otherwise.
    mood_shift: bool = False
    # Present only when the LLM was asked to (re)plan the session's arc.
    plan: list[Phase] | None = None


class LLMClient:
    """Thin async client for the configured LLM provider."""

    def __init__(
        self,
        hass: HomeAssistant,
        provider: str,
        api_key: str,
        model: str,
        base_url: str | None = None,
    ) -> None:
        self._session = async_get_clientsession(hass)
        self._provider = provider
        self._api_key = api_key
        self._model = model
        # For OpenAI-compatible third parties; falls back to the OpenAI URL.
        self._base_url = (base_url or "").rstrip("/") or None

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
        return DJPick(
            comment=str(data.get("dj_comment", "")).strip(),
            tracks=tracks,
            mood_shift=bool(data.get("mood_shift", False)),
            plan=_parse_plan(data.get("plan")),
        )

    async def async_validate(self) -> None:
        """Cheap round-trip to verify credentials and model name."""
        await self._complete("Reply with the single word: ok", "ping")

    async def _complete(self, system: str, user: str) -> str:
        """Dispatch to the provider, retrying transient failures with backoff."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return await self._dispatch(system, user)
            except LLMError as err:
                if err.retryable and attempt < MAX_ATTEMPTS:
                    delay = 1.5 * attempt
                    _LOGGER.warning(
                        "AI DJ LLM call failed (%s), retry %d/%d in %.1fs",
                        err,
                        attempt,
                        MAX_ATTEMPTS - 1,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise
        raise LLMError("LLM request failed after retries")  # pragma: no cover

    async def _dispatch(self, system: str, user: str) -> str:
        try:
            if self._provider == PROVIDER_ANTHROPIC:
                return await self._complete_anthropic(system, user)
            if self._provider == PROVIDER_GEMINI:
                return await self._complete_gemini(system, user)
            if self._provider in (PROVIDER_OPENAI, PROVIDER_OPENAI_COMPATIBLE):
                return await self._complete_openai(system, user)
        except aiohttp.ClientError as err:
            raise LLMError(
                f"Cannot reach {self._provider} API: {err}", retryable=True
            ) from err
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
                "max_tokens": 3072,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
        )
        body = await resp.json()
        if resp.status != 200:
            raise LLMError(
                _api_error("Anthropic", resp.status, body),
                retryable=resp.status in RETRYABLE_STATUS,
            )
        try:
            return body["content"][0]["text"]
        except (KeyError, IndexError, TypeError) as err:
            raise LLMError(f"Unexpected Anthropic response: {body}") from err

    async def _complete_openai(self, system: str, user: str) -> str:
        url = f"{self._base_url}/chat/completions" if self._base_url else OPENAI_URL
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # Generous ceiling: several "OpenAI-compatible" backends (DeepSeek,
            # some Groq/OpenRouter models, local reasoning models via Ollama)
            # default to a hidden reasoning pass that eats the output budget
            # before the visible answer, same failure class as the Gemini
            # thinking-model truncation.
            "max_tokens": 8192,
        }
        if self._base_url and "deepseek.com" in self._base_url:
            # DeepSeek's reasoning ("thinking") mode is on by default even
            # for deepseek-chat; this task needs a plain JSON answer, not
            # visible chain-of-thought, so turn it off at the source.
            payload["thinking"] = {"type": "disabled"}
        resp = await self._session.post(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json=payload,
        )
        body = await resp.json()
        if resp.status != 200:
            raise LLMError(
                _api_error("OpenAI-compatible", resp.status, body),
                retryable=resp.status in RETRYABLE_STATUS,
            )
        try:
            choice = body["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as err:
            raise LLMError(f"Unexpected OpenAI response: {body}") from err
        if not content or choice.get("finish_reason") == "length":
            raise LLMError(
                f"Model '{self._model}' hit its output-token limit before "
                "finishing (likely spent it on hidden reasoning). Try a "
                "non-reasoning model or a model with 'chat'/'instruct' in "
                "its name rather than 'reasoner'/'thinking'/'r1'.",
                retryable=True,
            )
        return content

    async def _complete_gemini(self, system: str, user: str) -> str:
        url = f"{GEMINI_URL}/{self._model}:generateContent"
        resp = await self._session.post(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={"x-goog-api-key": self._api_key},
            json={
                "system_instruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {
                    # Generous budget: "thinking" Gemini models (2.5/3) spend
                    # output tokens on hidden reasoning before the JSON, so a
                    # small cap truncates the answer mid-object.
                    "maxOutputTokens": 8192,
                    "responseMimeType": "application/json",
                },
            },
        )
        body = await resp.json()
        if resp.status != 200:
            raise LLMError(
                _api_error("Gemini", resp.status, body),
                retryable=resp.status in RETRYABLE_STATUS,
            )
        try:
            candidate = body["candidates"][0]
        except (KeyError, IndexError, TypeError) as err:
            raise LLMError(f"Unexpected Gemini response: {body}") from err
        parts = candidate.get("content", {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts)
        if not text or candidate.get("finishReason") == "MAX_TOKENS":
            raise LLMError(
                "Gemini hit its output-token limit before finishing (model "
                f"'{self._model}' spent the budget on reasoning). Try a "
                "non-preview model such as gemini-2.5-flash.",
                retryable=True,
            )
        return text


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


def _parse_plan(raw: Any) -> list[Phase] | None:
    """Parse a "plan" array defensively; malformed entries are dropped."""
    if not isinstance(raw, list) or not raw:
        return None
    phases: list[Phase] = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("label"):
            continue
        try:
            count = int(item.get("target_track_count", 3))
        except (TypeError, ValueError):
            count = 3
        phases.append(
            Phase(
                label=str(item["label"]).strip(),
                description=str(item.get("description", "")).strip(),
                target_track_count=max(count, 1),
            )
        )
    return phases or None
