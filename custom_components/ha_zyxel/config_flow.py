"""Config flow for Zyxel integration."""
import logging
import re

import voluptuous as vol
import requests
from homeassistant import config_entries, core, exceptions
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig, SelectSelectorMode
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.ha_zyxel.api import (
    ZyxelAuthenticationError,
    ZyxelConnectionError,
    create_router,
    fetch_status,
)
from custom_components.ha_zyxel.backend import EX3301T0Client, NWA50AXClient
from .const import (
    CONF_DEVICE_TYPE,
    DEFAULT_DEVICE_TYPE,
    DEFAULT_HOST,
    DEFAULT_NAME,
    DEFAULT_USERNAME,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)
nr7101_logger = logging.getLogger("nr7101.nr7101")
nr7101_logger.setLevel(logging.WARNING)

LEGACY_DEVICE_TYPES = {
    "ax7501-b0",
    "fwa505",
    "fwa510",
    "fwa710-5g-v2",
    "lte3202-m437",
    "lte7490-m904",
    "lte5398-m904",
    "nr5103e",
    "nr5103v2",
    "nr5307",
    "nr7101",
    "nr7102",
    "nr7302",
    "vmg3625-t50b",
    "vmg4005-b50a",
    "vmg8825-t50",
}

DEVICE_CHOICES = [
    {"value": "generic", "label": "Generic Zyxel Device"},
    {"value": "ax7501-b0", "label": "AX7501-B0"},
    {"value": "fwa505", "label": "FWA505"},
    {"value": "fwa510", "label": "FWA510"},
    {"value": "fwa710-5g-v2", "label": "FWA710 5G V2"},
    {"value": "lte3202-m437", "label": "LTE3202-M437"},
    {"value": "lte7490-m904", "label": "LTE7490-M904"},
    {"value": "lte5398-m904", "label": "LTE5398-M904"},
    {"value": "nr5103e", "label": "NR5103E"},
    {"value": "nr5103v2", "label": "NR5103v2"},
    {"value": "nr5307", "label": "NR5307"},
    {"value": "nr7101", "label": "NR7101"},
    {"value": "nr7102", "label": "NR7102"},
    {"value": "nr7302", "label": "NR7302"},
    {"value": "nwa50ax", "label": "NWA50AX AP"},
    {"value": "ex3301_t0", "label": "EX3301-T0 Router"},
    {"value": "vmg3625-t50b", "label": "VMG3625-T50B"},
    {"value": "vmg4005-b50a", "label": "VMG4005-B50A"},
    {"value": "vmg8825-t50", "label": "VMG8825-T50"},
]

SELECT_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DEVICE_TYPE, default=DEFAULT_DEVICE_TYPE): SelectSelector(
            SelectSelectorConfig(options=DEVICE_CHOICES, mode=SelectSelectorMode.LIST)
        )
    }
)

NWA50AX_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_USERNAME, default=DEFAULT_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

EX3301T0_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST, default=""): str,
        vol.Required(CONF_USERNAME, default=DEFAULT_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

LEGACY_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
        vol.Required(CONF_USERNAME, default=DEFAULT_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


def _normalize_host(host: str) -> str:
    if host.startswith("http://"):
        return host.removeprefix("http://")
    if host.startswith("https://"):
        return host.removeprefix("https://")
    return host


def _safe_error_message(err: Exception) -> str:
    message = str(err).strip()
    return message or err.__class__.__name__


def _is_connection_refused(err: Exception) -> bool:
    if isinstance(err, requests.exceptions.ConnectionError):
        cause = err.__cause__ or err.__context__
        if isinstance(cause, ConnectionRefusedError):
            return True
    message = _safe_error_message(err).lower()
    return (
        "connection refused" in message
        or "failed to establish a new connection" in message
        or "max retries exceeded" in message
    )


def _discovery_host(discovery_info) -> str | None:
    for attr in ("ssdp_location", "host", "ip_address", "address", "location"):
        value = getattr(discovery_info, attr, None)
        if isinstance(value, str) and value:
            return _normalize_host(value.split("://", 1)[-1].split("/", 1)[0])
    if isinstance(discovery_info, dict):
        for key in ("ssdp_location", "host", "ip_address", "address", "location"):
            value = discovery_info.get(key)
            if isinstance(value, str) and value:
                return _normalize_host(value.split("://", 1)[-1].split("/", 1)[0])
    return None


def _try_candidates(host: str, device_type: str) -> list[str]:
    if device_type == "nwa50ax":
        return [f"http://{host}", f"https://{host}"]
    if device_type == "ex3301_t0":
        return [f"http://{host}"]
    if host.startswith("http://") or host.startswith("https://"):
        return [host]
    return [f"http://{host}", f"https://{host}"]


def _split_hosts(raw_hosts: str) -> list[str]:
    hosts = [part.strip() for part in re.split(r"[\n,]+", raw_hosts or "") if part.strip()]
    return list(dict.fromkeys(hosts))


async def _validate_connection(hass: core.HomeAssistant, data):
    device_type = data[CONF_DEVICE_TYPE]
    host = _normalize_host(data[CONF_HOST])

    _LOGGER.debug("Validating Zyxel connection to %s as %s (%s)", host, data[CONF_USERNAME], device_type)

    last_error = None
    for candidate in _try_candidates(host, device_type):
        _LOGGER.debug("Trying Zyxel connection candidate %s", candidate)
        try:
            if device_type == "nwa50ax":
                router = NWA50AXClient(candidate, data[CONF_USERNAME], data[CONF_PASSWORD])
                await hass.async_add_executor_job(router.login)
                status = await hass.async_add_executor_job(router.get_status)
                if not status:
                    raise UpdateFailed("zysh-cgi returned an empty status payload")
                device_model = await hass.async_add_executor_job(router.get_device_model, status)
                device_name = await hass.async_add_executor_job(router.get_device_name, status)
                device_name = device_model or device_name
            elif device_type == "ex3301_t0":
                router = EX3301T0Client(candidate, data[CONF_USERNAME], data[CONF_PASSWORD])
                await hass.async_add_executor_job(router.login)
                status = {}
                try:
                    status = await hass.async_add_executor_job(router.get_status)
                except Exception as ex:  # pylint: disable=broad-except
                    _LOGGER.warning(
                        "EX3301-T0 status probes failed during setup for %s: %s; accepting login-only validation",
                        host,
                        _safe_error_message(ex),
                    )
                if status:
                    device_name = await hass.async_add_executor_job(router.get_device_name, status)
                else:
                    device_name = None
            else:
                router = await hass.async_add_executor_job(
                    create_router,
                    candidate,
                    data[CONF_USERNAME],
                    data[CONF_PASSWORD],
                )
                await hass.async_add_executor_job(fetch_status, router)
                device_name = None

            data[CONF_HOST] = host if device_type in {"nwa50ax", "ex3301_t0"} else candidate
            title = device_name or (
                host if device_type in {"nwa50ax", "ex3301_t0"} else DEFAULT_NAME
            )
            return {"title": title}
        except UpdateFailed as ex:
            last_error = ex
            _LOGGER.debug("Candidate %s returned empty data: %s", candidate, _safe_error_message(ex))
        except ZyxelAuthenticationError as ex:
            raise ConfigEntryAuthFailed from ex
        except ZyxelConnectionError as ex:
            last_error = ex
            _LOGGER.debug("Candidate %s failed: %s", candidate, _safe_error_message(ex))
        except Exception as ex:  # pylint: disable=broad-except
            last_error = ex
            error_message = _safe_error_message(ex).lower()
            if "auth" in error_message or "login failed" in error_message:
                raise ConfigEntryAuthFailed from ex
            if _is_connection_refused(ex):
                _LOGGER.debug("Candidate %s refused connection; trying next option", candidate)
                continue
            _LOGGER.debug("Candidate %s failed: %s", candidate, _safe_error_message(ex))

    raise last_error if last_error else Exception("Connection failed")


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            self._device_type = user_input[CONF_DEVICE_TYPE]
            if self._device_type == "nwa50ax":
                return await self.async_step_nwa50ax()
            if self._device_type == "ex3301_t0":
                return await self.async_step_ex3301_t0()
            return await self.async_step_legacy()
        return self.async_show_form(step_id="user", data_schema=SELECT_SCHEMA)

    async def async_step_nwa50ax(self, user_input=None):
        errors = {}
        if user_input is not None:
            hosts = _split_hosts(user_input[CONF_HOST])
            if not hosts:
                errors["base"] = "cannot_connect"
                return self.async_show_form(step_id="nwa50ax", data_schema=NWA50AX_SCHEMA, errors=errors)
            data = {
                CONF_DEVICE_TYPE: "nwa50ax",
                CONF_HOST: hosts[0],
                CONF_USERNAME: user_input[CONF_USERNAME],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            try:
                info = await _validate_connection(self.hass, data)
                self._validated_data = data
                self._validated_info = info
                await self.async_set_unique_id(data[CONF_HOST])
                self._abort_if_unique_id_configured()
                if len(hosts) > 1:
                    for host in hosts[1:]:
                        await self.hass.config_entries.flow.async_init(
                            DOMAIN,
                            context={"source": "import"},
                            data={
                                CONF_DEVICE_TYPE: "nwa50ax",
                                CONF_HOST: host,
                                CONF_USERNAME: user_input[CONF_USERNAME],
                                CONF_PASSWORD: user_input[CONF_PASSWORD],
                            },
                        )
                return self.async_create_entry(title=info["title"], data=data)
            except ConfigEntryAuthFailed:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("NWA50AX validation failed for %s", hosts[0])
                errors["base"] = "cannot_connect"
        return self.async_show_form(step_id="nwa50ax", data_schema=NWA50AX_SCHEMA, errors=errors)

    async def async_step_ex3301_t0(self, user_input=None):
        errors = {}
        if user_input is not None:
            data = {
                CONF_DEVICE_TYPE: "ex3301_t0",
                CONF_HOST: user_input[CONF_HOST],
                CONF_USERNAME: user_input[CONF_USERNAME],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            try:
                info = await _validate_connection(self.hass, data)
                return self.async_create_entry(title=info["title"], data=data)
            except ConfigEntryAuthFailed:
                errors["base"] = "invalid_auth"
            except UpdateFailed:
                _LOGGER.exception("EX3301-T0 validation failed for %s", user_input[CONF_HOST])
                errors["base"] = "cannot_connect"
            except Exception as ex:  # pylint: disable=broad-except
                if _is_connection_refused(ex):
                    _LOGGER.warning("EX3301-T0 connection refused for %s", user_input[CONF_HOST])
                else:
                    _LOGGER.exception("EX3301-T0 validation failed for %s", user_input[CONF_HOST])
                errors["base"] = "cannot_connect"
        return self.async_show_form(step_id="ex3301_t0", data_schema=EX3301T0_SCHEMA, errors=errors)

    async def async_step_legacy(self, user_input=None):
        errors = {}
        if user_input is not None:
            data = {
                CONF_DEVICE_TYPE: getattr(self, "_device_type", "legacy"),
                CONF_HOST: user_input[CONF_HOST],
                CONF_USERNAME: user_input[CONF_USERNAME],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            try:
                info = await _validate_connection(self.hass, data)
                return self.async_create_entry(title=info["title"], data=data)
            except ConfigEntryAuthFailed:
                errors["base"] = "invalid_auth"
            except Exception as ex:  # pylint: disable=broad-except
                if _is_connection_refused(ex):
                    _LOGGER.debug("Legacy connection refused for %s", user_input[CONF_HOST])
                else:
                    _LOGGER.exception("Legacy validation failed for %s", user_input[CONF_HOST])
                errors["base"] = "cannot_connect"
        return self.async_show_form(step_id="legacy", data_schema=LEGACY_SCHEMA, errors=errors)

    async def async_step_import(self, user_input=None):
        if not user_input or user_input.get(CONF_DEVICE_TYPE) != "nwa50ax":
            return self.async_abort(reason="not_supported")
        try:
            await self.async_set_unique_id(user_input[CONF_HOST])
            self._abort_if_unique_id_configured()
            info = await _validate_connection(self.hass, user_input)
            return self.async_create_entry(title=info["title"], data=user_input)
        except ConfigEntryAuthFailed:
            return self.async_abort(reason="invalid_auth")
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("NWA50AX import validation failed for %s", user_input.get(CONF_HOST))
            return self.async_abort(reason="cannot_connect")


class ConnectionError(exceptions.HomeAssistantError):
    """Error to indicate we cannot connect."""