"""Config flow for Zyxel integration."""
import logging

import voluptuous as vol

from homeassistant import config_entries, core
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

from .api import (
    ZyxelAuthenticationError,
    ZyxelBusyError,
    ZyxelConnectionError,
    close_router,
    create_router,
    fetch_status,
)
from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_HOST,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USERNAME,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
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

    try:
        # Create router instance and test connection
        router = await hass.async_add_executor_job(
            create_router,
            data[CONF_HOST],
            data[CONF_USERNAME],
            data[CONF_PASSWORD]
        )

        try:
            await hass.async_add_executor_job(fetch_status, router)
        finally:
            await hass.async_add_executor_job(close_router, router)
    except (ZyxelAuthenticationError, ZyxelConnectionError):
        raise
    except Exception as ex:
        _LOGGER.error("Unable to connect to Zyxel device: %s", ex)
        raise ZyxelConnectionError from ex

    return {"title": f"Zyxel device: ({data[CONF_HOST]})"}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Zyxel devices."""

    VERSION = 1

    @staticmethod
    @core.callback
    def async_get_options_flow(config_entry):
        """Return the options flow."""
        return OptionsFlowHandler()

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        success = False

        if user_input is not None:
            host = user_input[CONF_HOST]

            # sanitize entry
            if not host.startswith("http://") and not host.startswith("https://"):
                host = f"https://{host}"
                user_input[CONF_HOST] = host

            try:
                info = await validate_input(self.hass, user_input)
                success = True
            except ZyxelAuthenticationError:
                errors["base"] = "invalid_auth"
            except ZyxelBusyError:
                errors["base"] = "router_busy"
            except ZyxelConnectionError as err:
                _LOGGER.error("Connection attempt failed: %s", err)
                errors["base"] = "cannot_connect"

            if not success and "https" not in user_input["host"]:
                _LOGGER.info("User specified http but it failed, trying https...")
                user_input["host"] = user_input["host"].replace("http://", "https://")
                try:
                    info = await validate_input(self.hass, user_input)
                    success = True
                except ZyxelAuthenticationError:
                    errors["base"] = "invalid_auth"
                except ZyxelBusyError:
                    errors["base"] = "router_busy"
                except ZyxelConnectionError:
                    errors["base"] = "cannot_connect"
                except Exception as e:  # pylint: disable=broad-except
                    _LOGGER.exception("Second attempt failed: %s", e)
                    errors["base"] = "unknown"

        if success:
            return self.async_create_entry(title=info["title"], data=user_input)
        else:
            return self.async_show_form(
                step_id="user", data_schema=DATA_SCHEMA, errors=errors
            )


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Let the user change how often the router is polled."""

    async def async_step_init(self, user_input=None):
        """Show and save the polling interval."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                    )
                }
            ),
        )
