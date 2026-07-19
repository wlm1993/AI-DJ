"""Config flow for AI DJ."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_API_KEY,
    CONF_LOOKAHEAD,
    CONF_MODEL,
    CONF_PROVIDER,
    DEFAULT_LOOKAHEAD,
    DEFAULT_MODELS,
    DOMAIN,
    MAX_LOOKAHEAD,
    MIN_LOOKAHEAD,
    PROVIDERS,
)
from .llm import LLMClient, LLMError


class AIDJConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the AI DJ config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._provider: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the LLM provider."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        if user_input is not None:
            self._provider = user_input[CONF_PROVIDER]
            return await self.async_step_credentials()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PROVIDER): SelectSelector(
                        SelectSelectorConfig(
                            options=PROVIDERS,
                            mode=SelectSelectorMode.LIST,
                            translation_key="provider",
                        )
                    )
                }
            ),
        )

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect API key and model, then validate with a live call."""
        assert self._provider is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            client = LLMClient(
                self.hass,
                provider=self._provider,
                api_key=user_input[CONF_API_KEY],
                model=user_input[CONF_MODEL],
            )
            try:
                await client.async_validate()
            except LLMError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title=f"AI DJ ({self._provider})",
                    data={
                        CONF_PROVIDER: self._provider,
                        CONF_API_KEY: user_input[CONF_API_KEY],
                        CONF_MODEL: user_input[CONF_MODEL],
                    },
                )

        return self.async_show_form(
            step_id="credentials",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_KEY): str,
                    vol.Required(
                        CONF_MODEL, default=DEFAULT_MODELS[self._provider]
                    ): str,
                }
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> AIDJOptionsFlow:
        return AIDJOptionsFlow()


class AIDJOptionsFlow(OptionsFlow):
    """Tune model and queue lookahead after setup."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        entry = self.config_entry
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_MODEL,
                        default=entry.options.get(
                            CONF_MODEL, entry.data[CONF_MODEL]
                        ),
                    ): str,
                    vol.Required(
                        CONF_LOOKAHEAD,
                        default=entry.options.get(CONF_LOOKAHEAD, DEFAULT_LOOKAHEAD),
                    ): vol.All(vol.Coerce(int), vol.Range(MIN_LOOKAHEAD, MAX_LOOKAHEAD)),
                }
            ),
        )
