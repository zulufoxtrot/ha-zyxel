"""Config flow for Zyxel integration."""
import logging

import voluptuous as vol
from nr7101 import nr7101

from homeassistant import config_entries, core, exceptions
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

from .const import (
    CONF_CLIENT_DIAGNOSTICS,
    CONF_CONSIDER_HOME,
    CONF_EXPOSE_ALL_ROUTER_SENSORS,
    CONF_SCAN_INTERVAL,
    CONF_TRACK_ALL,
    DEFAULT_CLIENT_DIAGNOSTICS,
    DEFAULT_CONSIDER_HOME,
    DEFAULT_EXPOSE_ALL_ROUTER_SENSORS,
    DEFAULT_HOST,
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TRACK_ALL,
    DEFAULT_USERNAME,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# Block excessive nr7101 debug logging
nr7101_logger = logging.getLogger("nr7101.nr7101")
nr7101_logger.setLevel(logging.WARNING)

DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
        vol.Required(CONF_USERNAME, default=DEFAULT_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


async def validate_input(hass: core.HomeAssistant, data):
    """Validate that the user input allows us to connect."""
    router = None
    try:
        # Create router instance and test connection
        router = await hass.async_add_executor_job(
            nr7101.NR7101,
            data[CONF_HOST],
            data[CONF_USERNAME],
            data[CONF_PASSWORD],
            {"timeout": DEFAULT_REQUEST_TIMEOUT},
        )

        login_success = await hass.async_add_executor_job(router.get_status)
        if not login_success:
            raise ZyxelConnectionError
    except Exception as ex:
        _LOGGER.error("Unable to connect to Zyxel device: %s", ex)
        raise ZyxelConnectionError from ex
    finally:
        if router is not None and router.sessionkey is not None:
            try:
                await hass.async_add_executor_job(router.logout)
            except Exception as ex:  # pylint: disable=broad-except
                _LOGGER.debug("Could not close validation session: %s", ex)

    return {"title": f"Zyxel device: ({data[CONF_HOST]})"}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Zyxel devices."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    @staticmethod
    @core.callback
    def async_get_options_flow(_config_entry):
        """Return the options flow handler."""
        return OptionsFlowHandler()

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        info = None
        success = False

        if user_input is not None:
            host = user_input[CONF_HOST]

            # sanitize entry
            if not host.startswith(("http://", "https://")):
                host = f"https://{host}"
                user_input[CONF_HOST] = host

            try:
                info = await validate_input(self.hass, user_input)
                success = True
            except Exception as e:  # pylint: disable=broad-except
                _LOGGER.exception("First attempt failed: %s", e)
                errors["base"] = "cannot_connect"

            if not success and "https" not in user_input["host"]:
                _LOGGER.info(
                    "User specified http but it failed, trying https..."
                )
                user_input["host"] = user_input["host"].replace(
                    "http://", "https://"
                )
                try:
                    info = await validate_input(self.hass, user_input)
                    success = True
                except ZyxelConnectionError:
                    errors["base"] = "cannot_connect"
                except Exception as e:  # pylint: disable=broad-except
                    _LOGGER.exception("Second attempt failed: %s", e)
                    errors["base"] = "unknown"

        if success and info is not None and user_input is not None:
            return self.async_create_entry(
                title=info["title"], data=user_input
            )
        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle Zyxel integration options."""

    async def async_step_init(self, user_input=None):
        """Manage Zyxel options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=options.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=10, max=3600)),
                vol.Optional(
                    CONF_CONSIDER_HOME,
                    default=options.get(
                        CONF_CONSIDER_HOME, DEFAULT_CONSIDER_HOME
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=86400)),
                vol.Optional(
                    CONF_TRACK_ALL,
                    default=options.get(CONF_TRACK_ALL, DEFAULT_TRACK_ALL),
                ): bool,
                vol.Optional(
                    CONF_CLIENT_DIAGNOSTICS,
                    default=options.get(
                        CONF_CLIENT_DIAGNOSTICS, DEFAULT_CLIENT_DIAGNOSTICS
                    ),
                ): bool,
                vol.Optional(
                    CONF_EXPOSE_ALL_ROUTER_SENSORS,
                    default=options.get(
                        CONF_EXPOSE_ALL_ROUTER_SENSORS,
                        DEFAULT_EXPOSE_ALL_ROUTER_SENSORS,
                    ),
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)


class ZyxelConnectionError(exceptions.HomeAssistantError):
    """Error to indicate we cannot connect."""
