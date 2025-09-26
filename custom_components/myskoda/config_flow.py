"""Config flow for the MySkoda integration."""

from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol

from aiohttp.client_exceptions import ClientResponseError

from homeassistant.config_entries import (
    ConfigFlow as BaseConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback, HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.schema_config_entry_flow import (
    SchemaCommonFlowHandler,
    SchemaFlowError,
    SchemaFlowFormStep,
    SchemaOptionsFlowHandler,
)
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util.ssl import get_default_context
from myskoda import MySkoda
from myskoda.auth.authorization import (
    AuthorizationError,
    NotAuthorizedError,
    AuthorizationFailedError,
    TermsAndConditionsError,
    MarketingConsentError,
)

from .const import (
    DOMAIN,
    CONF_PASSWORD,
    CONF_POLL_INTERVAL,
    CONF_POLL_INTERVAL_DEFAULT,
    CONF_POLL_INTERVAL_MIN,
    CONF_POLL_INTERVAL_MAX,
    CONF_SPIN,
    CONF_SPIN_REGEX,
    CONF_TRACING,
    CONF_USERNAME,
    CONF_READONLY,
)
from .coordinator import MySkodaConfigEntry

_LOGGER = logging.getLogger(__name__)


async def validate_options_input(
    handler: SchemaCommonFlowHandler, user_input: dict[str, Any]
) -> dict[str, Any]:
    """Validate options are valid."""

    if CONF_POLL_INTERVAL in user_input:
        polling_interval: int = user_input[CONF_POLL_INTERVAL]
        if not CONF_POLL_INTERVAL_MIN <= polling_interval <= CONF_POLL_INTERVAL_MAX:
            raise SchemaFlowError("invalid_polling_interval")

    if CONF_SPIN in user_input:
        s_pin: str = user_input[CONF_SPIN]
        if not re.match(CONF_SPIN_REGEX, s_pin):
            raise SchemaFlowError("invalid_spin_format")

    return user_input


STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)
OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_TRACING, default=False): bool,
        vol.Optional(CONF_READONLY, default=False): bool,
        vol.Optional(
            CONF_POLL_INTERVAL, default=CONF_POLL_INTERVAL_DEFAULT
        ): NumberSelector(
            NumberSelectorConfig(
                min=CONF_POLL_INTERVAL_MIN,
                max=CONF_POLL_INTERVAL_MAX,
                mode=NumberSelectorMode.BOX,
                unit_of_measurement="minutes",
            )
        ),
        vol.Optional(CONF_SPIN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)
OPTIONS_FLOW = {
    "init": SchemaFlowFormStep(
        OPTIONS_SCHEMA,
        validate_user_input=validate_options_input,
    )
}


class ConfigFlow(BaseConfigFlow, domain=DOMAIN):
    """Handle a config flow for MySkoda."""

    VERSION = 2
    MINOR_VERSION = 4

    async def _validate_input(data: dict[str, Any]) -> None:
        """Check that the inputs are valid."""
        hub = MySkoda(
            async_get_clientsession(self.hass), get_default_context(), mqtt_enabled=False
        )

        await hub.connect(data[CONF_USERNAME], data[CONF_PASSWORD])
        await hub.disconnect()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=STEP_USER_DATA_SCHEMA
            )

        errors = {}

        try:
            await self._validate_input(self.hass, user_input)
        except ClientResponseError:
            errors["base"] = "cannot_connect"
        except (
            AuthorizationError,
            AuthorizationFailedError,
            NotAuthorizedError,
        ):
            errors["base"] = "invalid_auth"
        except (TermsAndConditionsError, MarketingConsentError):
            errors["base"] = "relogin_in_app"
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception")
            errors["base"] = "unknown"
        else:
            return self.async_create_entry(title=user_input[CONF_USERNAME], data=user_input)

        # Only called if there was an error.
        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Handle initiation of re-authentication with MySkoda."""
        _LOGGER.debug("Authentication error detected, starting reauth")
        self.reauth_entry = self._get_reauth_entry()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle re-authentication with MySkoda."""
        errors: dict = {}

        if user_input is not None:
            try:
                await self._validate_input(self.hass, user_input)
            except ClientResponseError as err:
                errors["base"] = "cannot_connect"
                raise ConfigEntryNotReady("Error connecting to MySkoda: %s", err)
            except Exception as err:
                errors["base"] = "unknown"
                _LOGGER.error("Failed to log in due to error: %s", str(err))
                return self.async_abort(reason="unknown")

            data = self.reauth_entry.data.copy()
            self.hass.config_entries.async_update_entry(
                self.reauth_entry,
                data={
                    **data,
                    **user_input,
                },
            )
            self.hass.async_create_task(
                self.hass.config_entries.async_reload(self.reauth_entry.entry_id)
            )

            return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USERNAME, default=self.reauth_entry.data[CONF_USERNAME]
                    ): str,
                    vol.Required(
                        CONF_PASSWORD, default=self.reauth_entry.data[CONF_PASSWORD]
                    ): str,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: MySkodaConfigEntry,
    ) -> OptionsFlow:
        """Create the options flow."""
        return SchemaOptionsFlowHandler(config_entry, OPTIONS_FLOW)
