"""Session state sensor for AI DJ - the data source for the Lovelace card."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_SESSION_UPDATE
from .dj import DJSession

MA_DOMAIN = "music_assistant"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the AI DJ session sensor."""
    async_add_entities([AIDJSessionSensor(hass, entry)])


class AIDJSessionSensor(SensorEntity):
    """Exposes the DJ session state; entity id is stable: sensor.ai_dj."""

    _attr_has_entity_name = False
    _attr_name = "AI DJ"
    _attr_icon = "mdi:headphones"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self._attr_unique_id = f"{entry.entry_id}_session"
        self.entity_id = "sensor.ai_dj"

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_SESSION_UPDATE, self._handle_update
            )
        )
        self.async_on_remove(
            self.hass.bus.async_listen(
                er.EVENT_ENTITY_REGISTRY_UPDATED, self._handle_registry_update
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @callback
    def _handle_registry_update(self, event: Event) -> None:
        """Re-push state when a media_player is added/removed/renamed.

        available_players is always computed live, but this sensor only
        pushes state on session events - without this, the card's speaker
        list would only refresh after a session action or an HA restart.
        """
        entity_id = event.data.get("entity_id", "")
        old_entity_id = event.data.get("old_entity_id", "")
        if entity_id.startswith("media_player.") or old_entity_id.startswith(
            "media_player."
        ):
            self.async_write_ha_state()

    @property
    def _session(self) -> DJSession | None:
        return self.hass.data.get(DOMAIN, {}).get("session")

    @property
    def native_value(self) -> str:
        session = self._session
        return "active" if session and session.active else "idle"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        session = self._session
        attrs: dict[str, Any] = (
            session.snapshot()
            if session
            else {"active": False, "player": None, "error": None}
        )
        attrs["available_players"] = self._ma_players()
        return attrs

    def _ma_players(self) -> list[str]:
        """All media_player entities provided by Music Assistant."""
        registry = er.async_get(self.hass)
        return sorted(
            entity.entity_id
            for entity in registry.entities.values()
            if entity.platform == MA_DOMAIN and entity.domain == "media_player"
        )
