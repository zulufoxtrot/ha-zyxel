"""Services for the Zyxel integration."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .api import (
    ZyxelAuthenticationError,
    ZyxelConnectionError,
    ZyxelSmsError,
    send_sms,
)
from .const import (
    ATTR_CONFIG_ENTRY,
    ATTR_MESSAGE,
    ATTR_NUMBER,
    DOMAIN,
    SERVICE_SEND_SMS,
)

_LOGGER = logging.getLogger(__name__)

SEND_SMS_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY): cv.string,
        vol.Required(ATTR_NUMBER): cv.string,
        vol.Required(ATTR_MESSAGE): cv.string,
    }
)


def _resolve_router(hass: HomeAssistant, call: ServiceCall):
    """Return the router the call targets, defaulting to the only device."""
    entries = hass.data.get(DOMAIN, {})
    if not entries:
        raise ServiceValidationError("No Zyxel device is configured")

    entry_id = call.data.get(ATTR_CONFIG_ENTRY)
    if entry_id is None:
        if len(entries) > 1:
            raise ServiceValidationError(
                "Several Zyxel devices are configured; "
                f"set '{ATTR_CONFIG_ENTRY}' to choose one"
            )
        entry_id = next(iter(entries))

    entry_data = entries.get(entry_id)
    if entry_data is None:
        raise ServiceValidationError(f"Unknown Zyxel config entry: {entry_id}")

    return entry_data["router"]


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration-wide services once."""
    if hass.services.has_service(DOMAIN, SERVICE_SEND_SMS):
        return

    async def async_handle_send_sms(call: ServiceCall) -> None:
        """Send an SMS through the cellular modem."""
        router = _resolve_router(hass, call)
        number = call.data[ATTR_NUMBER]
        message = call.data[ATTR_MESSAGE]

        try:
            await hass.async_add_executor_job(send_sms, router, number, message)
        except ZyxelSmsError as err:
            raise ServiceValidationError(str(err)) from err
        except ZyxelAuthenticationError as err:
            raise HomeAssistantError(
                f"The router rejected the credentials: {err}"
            ) from err
        except ZyxelConnectionError as err:
            raise HomeAssistantError(f"Could not send the SMS: {err}") from err

        _LOGGER.debug("Zyxel SMS sent to %s", number)

    hass.services.async_register(
        DOMAIN, SERVICE_SEND_SMS, async_handle_send_sms, schema=SEND_SMS_SCHEMA
    )


def async_unload_services(hass: HomeAssistant) -> None:
    """Remove the services once the last config entry is gone."""
    hass.services.async_remove(DOMAIN, SERVICE_SEND_SMS)
