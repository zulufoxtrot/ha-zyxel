"""Support for Zyxel device buttons."""
from __future__ import annotations

import logging
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import entity_registry as er
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
_LANGUAGE_LIKE_VALUES = {"english", "french", "francais", "german", "deutsch", "spanish", "espanol"}


def _looks_like_ip(value: str) -> bool:
    parts = value.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except ValueError:
        return False


def _button_device_model(entry: ConfigEntry, coordinator) -> tuple[str, str | None]:
    """Return (model_name, sw_version) from coordinator data or entry config."""
    data = coordinator.data if coordinator else None
    if data:
        # coordinator data is a nested dict; find leaf values by key suffix.
        def _find(d, key):
            if isinstance(d, dict):
                for k, v in d.items():
                    if k == key and not isinstance(v, (dict, list)):
                        return v
                    found = _find(v, key)
                    if found is not None:
                        return found
            elif isinstance(d, list):
                for item in d:
                    found = _find(item, key)
                    if found is not None:
                        return found
            return None

        model = (
            _find(data, "ModelName")
            or _find(data, "ProductClass")
            or _find(data, "HardwareVersion")
        )
        sw_version = _find(data, "SoftwareVersion") or _find(data, "FirmwareVersion")
    else:
        model = None
        sw_version = None

    if not model:
        model = entry.data.get("device_type", "").upper().replace("_", "-") or entry.data.get("host", "")

    return model, sw_version


def _button_system_name(coordinator) -> str | None:
    data = coordinator.data if coordinator else None
    if not data:
        return None

    def _search(d):
        if isinstance(d, dict):
            for k, v in d.items():
                key_lower = str(k).lower()
                if isinstance(v, str):
                    candidate = v.strip()
                    lowered = candidate.lower()
                    if (
                        candidate
                        and not _looks_like_ip(candidate)
                        and lowered not in _LANGUAGE_LIKE_VALUES
                        and any(t in key_lower for t in ("system name", "system_name", "hostname", "fqdn", "device_name"))
                    ):
                        return candidate
                found = _search(v)
                if found:
                    return found
        elif isinstance(d, list):
            for item in d:
                found = _search(item)
                if found:
                    return found
        return None

    return _search(data)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Zyxel buttons."""
    router = hass.data[DOMAIN][entry.entry_id]["router"]
    coordinator = hass.data[DOMAIN][entry.entry_id].get("coordinator")
    ent_reg = er.async_get(hass)
    for reg_entry in list(er.async_entries_for_config_entry(ent_reg, entry.entry_id)):
        if reg_entry.entity_id.startswith("button.zyxel_"):
            _LOGGER.debug("Removing legacy prefixed button for rename migration: %s", reg_entry.entity_id)
            ent_reg.async_remove(reg_entry.entity_id)
    async_add_entities([ZyxelRebootButton(entry, router, coordinator)])


class ZyxelRebootButton(ButtonEntity):
    """Representation of a Zyxel reboot button."""

    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry, router, coordinator) -> None:
        """Initialize the button."""
        self._router = router
        self._attr_unique_id = f"{entry.entry_id}_reboot"
        model, sw_version = _button_device_model(entry, coordinator)
        host = str(entry.data.get("host", "")).replace("http://", "").replace("https://", "")
        host = host.split("/", 1)[0]
        display_name = _button_system_name(coordinator) or (
            f"Zyxel {host}" if host else f"Zyxel {model}"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=display_name,
            manufacturer="Zyxel",
            model=model,
            sw_version=sw_version,
        )
        self._attr_icon = "mdi:restart"
        self._attr_name = "[Action] Reboot Device"

    async def async_press(self) -> None:
        """Handle the button press."""
        _LOGGER.info("Attempting to reboot Zyxel device")
        try:
            await self.hass.async_add_executor_job(self._router.reboot)
            _LOGGER.info("Zyxel device reboot command sent successfully")
        except Exception as err:
            _LOGGER.error("Failed to send reboot command: %s", err)
