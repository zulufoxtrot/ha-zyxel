"""The Zyxel integration."""
import asyncio
import logging
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

import async_timeout
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from custom_components.ha_zyxel.api import (
    ZyxelAuthenticationError,
    ZyxelConnectionError,
    create_router,
    fetch_status,
)
from custom_components.ha_zyxel.backend import EX3301T0Client, NWA50AXClient
from custom_components.ha_zyxel.const import (
    CONF_DEVICE_TYPE,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# Block excessive nr7101 debug logging
nr7101_logger = logging.getLogger("nr7101.nr7101")
nr7101_logger.setLevel(logging.WARNING)

PLATFORMS = ["sensor", "button"]


def _entry_host(entry: ConfigEntry) -> str:
    host = entry.data.get(CONF_HOST, "")
    if host.startswith(("http://", "https://")):
        host = host.split("://", 1)[1]
    return host.split("/", 1)[0]


def _hostish_title(title: str) -> str:
    return title.strip().lower().replace("(", "").replace(")", "")


def _normalize_device_type(value: str | None) -> str:
    """Normalize device type aliases to canonical internal ids."""
    normalized = str(value or "legacy").strip().lower().replace("-", "_")
    if normalized == "ex3301":
        return "ex3301_t0"
    return normalized


def _leaf_values(data: Mapping | None) -> dict[str, object]:
    if not data:
        return {}
    flat = _flatten_value(data)
    leafs: dict[str, object] = {}
    for path, value in flat.items():
        leaf = path.split(".")[-1]
        if leaf not in leafs:
            leafs[leaf] = value
    return leafs


def _ex3301_wifi_signature(data: Mapping | None) -> tuple[tuple[str, bool, bool, str], ...]:
    """Return a stable signature of EX3301 WiFi radio state for reload detection."""
    if not data:
        return ()
    flat = _flatten_value(data)
    radios: dict[str, dict[str, object]] = {}
    for key, value in flat.items():
        if ".WiFiInfo." not in key:
            continue
        prefix, leaf = key.rsplit(".", 1)
        radios.setdefault(prefix, {})[leaf] = value

    normalized: list[tuple[str, bool, bool, str]] = []
    for prefix, fields in radios.items():
        slot = prefix.split(".")[-1]
        enabled = bool(fields.get("Enable"))
        is_main = bool(fields.get("X_ZYXEL_MainSSID"))
        band = str(fields.get("OperatingFrequencyBand") or "").strip()
        normalized.append((slot, enabled, is_main, band))
    return tuple(sorted(normalized))


def _detected_model_from_data(entry: ConfigEntry, data: Mapping | None) -> str:
    leafs = _leaf_values(data)
    model = (
        leafs.get("ModelName")
        or leafs.get("ProductClass")
        or leafs.get("HardwareVersion")
        or entry.data.get("model")
        or entry.data.get(CONF_DEVICE_TYPE, "")
        or _entry_host(entry)
    )
    return str(model).upper().replace("_", "-")


def _should_update_title_to_model(entry: ConfigEntry, model_title: str) -> bool:
    normalized_title = _hostish_title(entry.title)
    normalized_model = _hostish_title(model_title)
    host = _entry_host(entry).lower()
    device_type = str(entry.data.get(CONF_DEVICE_TYPE, "")).lower()
    defaults = {
        host,
        device_type,
        device_type.upper().lower(),
        f"{host}:80",
        f"{host}:443",
        f"zyxel {host}",
        f"zyxel {device_type}",
        f"zyxel {device_type.upper()}".lower(),
        f"zyxel {host}:80",
        f"zyxel {host}:443",
        "english",
        "zyxel english",
        f"zyxel {normalized_model}",
    }
    if entry.title.startswith("Zyxel "):
        return entry.title.removeprefix("Zyxel ") != model_title
    return normalized_title in defaults and entry.title != model_title


def _flatten_value(value, parent_key: str = "") -> dict:
    items = {}
    if isinstance(value, Mapping):
        for k, v in value.items():
            key = f"{parent_key}.{k}" if parent_key else str(k)
            items.update(_flatten_value(v, key))
    elif isinstance(value, list):
        for idx, v in enumerate(value):
            key = f"{parent_key}.{idx}" if parent_key else str(idx)
            items.update(_flatten_value(v, key))
    else:
        items[parent_key] = value
    return items


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Zyxel integration from a config entry."""

    host = entry.data[CONF_HOST]
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    raw_device_type = entry.data.get(CONF_DEVICE_TYPE, "legacy")
    device_type = _normalize_device_type(raw_device_type)
    if raw_device_type != device_type:
        updated_data = dict(entry.data)
        updated_data[CONF_DEVICE_TYPE] = device_type
        hass.config_entries.async_update_entry(entry, data=updated_data)
    if device_type in {"nwa50ax", "ex3301_t0"} and not host.startswith(("http://", "https://")):
        host = f"http://{host}"

    try:
        _LOGGER.debug("Creating Zyxel client for %s", host)
        router: Any
        if device_type == "nwa50ax":
            router = NWA50AXClient(host, username, password)
            await hass.async_add_executor_job(router.login)
            await hass.async_add_executor_job(router.get_status)
        elif device_type == "ex3301_t0":
            router = EX3301T0Client(host, username, password)
            await hass.async_add_executor_job(router.login)
        else:
            router = await hass.async_add_executor_job(
                create_router, host, username, password
            )
    except (ZyxelAuthenticationError, ZyxelConnectionError) as ex:
        _LOGGER.error("Could not connect to Zyxel device at %s: %s", host, ex)
        raise ConfigEntryNotReady from ex
    except Exception as ex:
        _LOGGER.error("Could not create Zyxel client for %s: %s", host, ex)
        raise ConfigEntryNotReady from ex

    # EX3301 probes each carry a 15s timeout; allow enough wall time for all of them.
    _UPDATE_TIMEOUT = 180 if device_type == "ex3301_t0" else 15

    async def async_update_data():
        """Fetch data from the router."""
        try:
            async with async_timeout.timeout(_UPDATE_TIMEOUT):
                def get_all_data():
                    if device_type in {"nwa50ax", "ex3301_t0"}:
                        data = router.get_status()
                        if not data:
                            raise UpdateFailed("No data received from router")
                        return data
                    data = fetch_status(router)
                    if not data:
                        raise UpdateFailed("No data received from router")
                    return data

                result = await hass.async_add_executor_job(get_all_data)
                if device_type == "ex3301_t0":
                    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
                    if runtime is not None:
                        prev = runtime.get("wifi_signature")
                        new = _ex3301_wifi_signature(result)
                        runtime["wifi_signature"] = new
                        if (
                            prev is not None
                            and prev != new
                            and not runtime.get("wifi_reload_pending")
                        ):
                            runtime["wifi_reload_pending"] = True
                            _LOGGER.info(
                                "EX3301 WiFi state layout changed; reloading entry to refresh entities"
                            )
                            hass.async_create_task(hass.config_entries.async_reload(entry.entry_id))
                return result
        except asyncio.TimeoutError:
            if not device_type in {"nwa50ax", "ex3301_t0"}:
                router.sessionkey = None
            raise UpdateFailed("Router data fetch timed out")
        except UpdateFailed as err:
            # Re-login and retry once if the EX3301 session has expired.
            if device_type == "ex3301_t0" and "session expired" in str(err).lower():
                _LOGGER.info("EX3301-T0 session expired — re-logging in and retrying")
                try:
                    await hass.async_add_executor_job(router.login)
                    return await hass.async_add_executor_job(router.get_status)
                except Exception as relogin_err:
                    raise UpdateFailed(f"EX3301-T0 re-login failed: {relogin_err}") from relogin_err
            if device_type == "nwa50ax" and "login failed" in str(err).lower():
                _LOGGER.info("NWA50AX login failed — re-logging in and retrying")
                try:
                    await hass.async_add_executor_job(router.login)
                    return await hass.async_add_executor_job(router.get_status)
                except Exception as relogin_err:
                    raise UpdateFailed(f"NWA50AX re-login failed: {relogin_err}") from relogin_err
            raise
        except Exception as err:
            if not device_type in {"nwa50ax", "ex3301_t0"}:
                router.sessionkey = None
            _LOGGER.exception("Error communicating with Zyxel device at %s", host)
            raise UpdateFailed(f"Error communicating with router: {err}") from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=async_update_data,
        update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
    )

    await coordinator.async_config_entry_first_refresh()

    # Keep integration title/data aligned with detected model for existing
    # host-based entries (e.g. "Zyxel 172.16.1.254" -> "EX3301-T0").
    detected_model = _detected_model_from_data(entry, coordinator.data)
    detected_title = detected_model
    updated_data = dict(entry.data)
    data_changed = updated_data.get("model") != detected_model
    if data_changed:
        updated_data["model"] = detected_model
    if _should_update_title_to_model(entry, detected_title):
        hass.config_entries.async_update_entry(
            entry,
            title=detected_title,
            data=updated_data,
        )
    elif data_changed:
        hass.config_entries.async_update_entry(entry, data=updated_data)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "router": router,
        "device_type": device_type,
        "wifi_signature": _ex3301_wifi_signature(coordinator.data)
        if device_type == "ex3301_t0"
        else None,
        "wifi_reload_pending": False,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(entry, platform)
                for platform in PLATFORMS
            ]
        )
    )
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok