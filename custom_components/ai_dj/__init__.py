"""AI DJ - an LLM-powered DJ for Music Assistant."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_ENABLED,
    ATTR_PITCH,
    ATTR_PLAYER,
    ATTR_PROMPT,
    ATTR_TEXT,
    CARD_RESOURCE_URL,
    CARD_URL_PATH,
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_LOOKAHEAD,
    CONF_MODEL,
    CONF_PROVIDER,
    CONF_TTS_ENTITY,
    DEFAULT_LOOKAHEAD,
    DEFAULT_TTS_ENTITY,
    DOMAIN,
    MAX_ANNOUNCE_PITCH,
    MIN_ANNOUNCE_PITCH,
    SERVICE_LIKE,
    SERVICE_SET_ANNOUNCE,
    SERVICE_SET_ANNOUNCE_PITCH,
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
SET_ANNOUNCE_SCHEMA = vol.Schema({vol.Required(ATTR_ENABLED): cv.boolean})
SET_ANNOUNCE_PITCH_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_PITCH): vol.All(
            vol.Coerce(int), vol.Range(MIN_ANNOUNCE_PITCH, MAX_ANNOUNCE_PITCH)
        )
    }
)


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
            base_url=entry.data.get(CONF_BASE_URL),
        )
        session = DJSession(
            hass,
            llm,
            player_entity=call.data[ATTR_PLAYER],
            prompt=call.data[ATTR_PROMPT],
            lookahead=entry.options.get(CONF_LOOKAHEAD, DEFAULT_LOOKAHEAD),
            tts_entity=entry.options.get(CONF_TTS_ENTITY, DEFAULT_TTS_ENTITY),
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

    async def handle_set_announce(call: ServiceCall) -> None:
        await _get_session(hass).async_set_announce(call.data[ATTR_ENABLED])

    async def handle_set_announce_pitch(call: ServiceCall) -> None:
        await _get_session(hass).async_set_announce_pitch(call.data[ATTR_PITCH])

    hass.services.async_register(DOMAIN, SERVICE_START, handle_start, START_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_STOP, handle_stop)
    hass.services.async_register(DOMAIN, SERVICE_LIKE, handle_like)
    hass.services.async_register(DOMAIN, SERVICE_WISH, handle_wish, WISH_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SKIP, handle_skip)
    hass.services.async_register(
        DOMAIN, SERVICE_SET_ANNOUNCE, handle_set_announce, SET_ANNOUNCE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_ANNOUNCE_PITCH,
        handle_set_announce_pitch,
        SET_ANNOUNCE_PITCH_SCHEMA,
    )


async def _register_card(hass: HomeAssistant, domain_data: dict) -> None:
    """Serve the bundled Lovelace card and register it as a Lovelace resource."""
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
    await _register_lovelace_resource(hass)
    domain_data["card_registered"] = True


async def _register_lovelace_resource(hass: HomeAssistant) -> None:
    """Add the card to the Lovelace resource registry (storage mode).

    This is how HA reliably loads custom cards — add_extra_js_url does not get
    the element registered before the dashboard renders the card. In YAML-mode
    Lovelace the registry is read-only, so we log instructions instead.
    """
    try:
        lovelace = hass.data.get("lovelace")
        resources = getattr(lovelace, "resources", None)
        if resources is None:
            raise RuntimeError("Lovelace resources are not available")
        if not resources.loaded:
            await resources.async_load()

        if not hasattr(resources, "async_create_item"):
            _LOGGER.warning(
                "Lovelace is in YAML mode; add this resource manually under "
                "your resources: (url: %s, type: module)",
                CARD_RESOURCE_URL,
            )
            return

        for item in resources.async_items():
            if item.get("url", "").split("?")[0] == CARD_URL_PATH:
                if item["url"] != CARD_RESOURCE_URL:
                    await resources.async_update_item(
                        item["id"], {"url": CARD_RESOURCE_URL}
                    )
                return
        await resources.async_create_item(
            {"res_type": "module", "url": CARD_RESOURCE_URL}
        )
    except Exception as err:  # noqa: BLE001 - never block setup on card wiring
        _LOGGER.warning(
            "Could not auto-register the AI DJ card. Add it manually in "
            "Settings > Dashboards > Resources (url: %s, type: JavaScript "
            "Module). Reason: %s",
            CARD_RESOURCE_URL,
            err,
        )
