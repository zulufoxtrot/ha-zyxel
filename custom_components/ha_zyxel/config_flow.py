"""Config flow for Zyxel integration."""
import logging

import voluptuous as vol

from homeassistant import config_entries, core
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

from .api import (
    ZyxelAuthenticationError,
    ZyxelConnectionError,
    create_router,
    fetch_status,
)
from .const import DEFAULT_HOST, DEFAULT_USERNAME, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Block excessive nr7101 debug logging
nr7101_logger = logging.getLogger("nr7101.nr7101")
nr7101_logger.setLevel(logging.WARNING)

DATA_SCHEMA = vol.Schema(
    {
        vol.Required("device_type", default="router"): vol.In({"router": "Router (existing support)", "nwa50ax": "NWA access point: NWA50AX / NWA50BE (experimental)"}),
        vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
        vol.Required(CONF_USERNAME, default=DEFAULT_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


async def validate_input(hass: core.HomeAssistant, data):
    """Validate that the user input allows us to connect."""

    router = None
    is_ap = data.get("device_type") == "nwa50ax"
    try:
        for _ in range(2):
            if is_ap and data[CONF_HOST].startswith("https://"):
                # Trust on first use: pin the AP's self-signed certificate.
                from .ap_api import fetch_cert_fingerprint
                data["cert_fingerprint"] = await hass.async_add_executor_job(
                    fetch_cert_fingerprint, data[CONF_HOST]
                )
            router = await hass.async_add_executor_job(
                create_router,
                data[CONF_HOST],
                data[CONF_USERNAME],
                data[CONF_PASSWORD],
                data.get("device_type", "router"),
                data.get("cert_fingerprint"),
            )
            try:
                await hass.async_add_executor_job(fetch_status, router)
                break
            except ZyxelConnectionError:
                # Some APs (e.g. NWA50BE) redirect HTTP to HTTPS; retry there once.
                upgraded = getattr(router, "host", data[CONF_HOST])
                if not is_ap or upgraded == data[CONF_HOST]:
                    raise
                await hass.async_add_executor_job(router.close)
                data[CONF_HOST] = upgraded
    except (ZyxelAuthenticationError, ZyxelConnectionError):
        raise
    except Exception as ex:
        _LOGGER.error("Unable to connect to Zyxel device: %s", ex)
        raise ZyxelConnectionError from ex
    finally:
        if data.get("device_type") == "nwa50ax" and router is not None:
            await hass.async_add_executor_job(router.close)

    return {"title": f"Zyxel device: ({data[CONF_HOST]})"}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Zyxel devices."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        success = False

        if user_input is not None:
            host = user_input[CONF_HOST]

            # sanitize entry
            if not host.startswith("http://") and not host.startswith("https://"):
                scheme = "http" if user_input.get("device_type") == "nwa50ax" else "https"
                host = f"{scheme}://{host}"
                user_input[CONF_HOST] = host

            try:
                info = await validate_input(self.hass, user_input)
                success = True
            except ZyxelAuthenticationError:
                errors["base"] = "invalid_auth"
            except ZyxelConnectionError as err:
                _LOGGER.error("Connection attempt failed: %s", err)
                errors["base"] = "cannot_connect"

            if not success and user_input.get("device_type") != "nwa50ax" and "https" not in user_input["host"]:
                _LOGGER.info("User specified http but it failed, trying https...")
                user_input["host"] = user_input["host"].replace("http://", "https://")
                try:
                    info = await validate_input(self.hass, user_input)
                    success = True
                except ZyxelAuthenticationError:
                    errors["base"] = "invalid_auth"
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

