"""AI DJ - an LLM-powered DJ for Music Assistant."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_PLAYER,
    ATTR_PROMPT,
    ATTR_TEXT,
    CARD_URL_PATH,
    CONF_API_KEY,
    CONF_LOOKAHEAD,
    CONF_MODEL,
    CONF_PROVIDER,
    DEFAULT_LOOKAHEAD,
    DOMAIN,
    SERVICE_LIKE,
    SERVICE_SKIP,
    SERVICE_START,
    SERVICE_STOP,
    SERVICE_WISH,
)
from .dj import DJSession
from .llm import LLMClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor"]

START_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_PROMPT): cv.string,
        vol.Required(ATTR_PLAYER): cv.entity_id,
    }
)
WISH_SCHEMA = vol.Schema({vol.Required(ATTR_TEXT): cv.string})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up AI DJ from a config entry."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    domain_data["entry"] = entry
    domain_data.setdefault("session", None)

    await _register_card(hass, domain_data)
    _register_services(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    session: DJSession | None = hass.data[DOMAIN].get("session")
    if session and session.active:
        await session.async_stop()
    hass.data[DOMAIN]["session"] = None
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def _get_session(hass: HomeAssistant, require_active: bool = True) -> DJSession:
    session: DJSession | None = hass.data.get(DOMAIN, {}).get("session")
    if session is None or (require_active and not session.active):
        raise HomeAssistantError("No active AI DJ session")
    return session


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_START):
        return

    async def handle_start(call: ServiceCall) -> None:
        entry: ConfigEntry = hass.data[DOMAIN]["entry"]
        old: DJSession | None = hass.data[DOMAIN].get("session")
        if old and old.active:
            await old.async_stop()

        llm = LLMClient(
            hass,
            provider=entry.data[CONF_PROVIDER],
            api_key=entry.data[CONF_API_KEY],
            model=entry.options.get(CONF_MODEL, entry.data[CONF_MODEL]),
        )
        session = DJSession(
            hass,
            llm,
            player_entity=call.data[ATTR_PLAYER],
            prompt=call.data[ATTR_PROMPT],
            lookahead=entry.options.get(CONF_LOOKAHEAD, DEFAULT_LOOKAHEAD),
        )
        hass.data[DOMAIN]["session"] = session
        await session.async_start()

    async def handle_stop(call: ServiceCall) -> None:
        await _get_session(hass, require_active=False).async_stop()

    async def handle_like(call: ServiceCall) -> None:
        await _get_session(hass).async_like()

    async def handle_wish(call: ServiceCall) -> None:
        await _get_session(hass).async_wish(call.data[ATTR_TEXT])

    async def handle_skip(call: ServiceCall) -> None:
        await _get_session(hass).async_skip()

    hass.services.async_register(DOMAIN, SERVICE_START, handle_start, START_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_STOP, handle_stop)
    hass.services.async_register(DOMAIN, SERVICE_LIKE, handle_like)
    hass.services.async_register(DOMAIN, SERVICE_WISH, handle_wish, WISH_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SKIP, handle_skip)


async def _register_card(hass: HomeAssistant, domain_data: dict) -> None:
    """Serve the bundled Lovelace card and load it on every dashboard."""
    if domain_data.get("card_registered"):
        return
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                CARD_URL_PATH,
                hass.config.path("custom_components", DOMAIN, "www", "ai-dj-card.js"),
                cache_headers=False,
            )
        ]
    )
    add_extra_js_url(hass, CARD_URL_PATH)
    domain_data["card_registered"] = True
