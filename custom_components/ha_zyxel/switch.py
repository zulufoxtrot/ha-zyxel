"""Switches for Zyxel port-forward rules."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.ha_zyxel.api import set_port_forward_enabled, set_wifi_enabled
from custom_components.ha_zyxel.const import DOMAIN
from custom_components.ha_zyxel.device_info import build_device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create one switch per port-forward rule the router reports."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    rules = (coordinator.data or {}).get("nat_rules") or []
    ssids = (coordinator.data or {}).get("wlan_ssids") or []
    entities: list[SwitchEntity] = [
        PortForwardSwitch(coordinator, data["router"], entry, rule["Index"])
        for rule in rules
        if "Index" in rule
    ]
    default_on = _default_enabled_ssids(ssids)
    entities += [
        WifiSwitch(coordinator, data["router"], entry, ssid["Index"], ssid["Index"] in default_on)
        for ssid in ssids
    ]
    async_add_entities(entities)


def _default_enabled_ssids(ssids: list[dict[str, Any]]) -> set[int]:
    """Indexes enabled by default: main networks and the first guest per band.

    The modem ships several unused guest slots (guest2..guest4); only the first
    guest network per band is created enabled, the rest can be enabled by hand.
    """
    enabled: set[int] = set()
    seen_guest_bands: set[str] = set()
    for ssid in sorted(ssids, key=lambda s: s["Index"]):
        if ssid.get("MainSSID"):
            enabled.add(ssid["Index"])
        elif ssid.get("band") not in seen_guest_bands:
            seen_guest_bands.add(ssid.get("band"))
            enabled.add(ssid["Index"])
    return enabled


class PortForwardSwitch(CoordinatorEntity, SwitchEntity):
    """Enable or disable a single port-forward rule."""

    _attr_icon = "mdi:router-network"
    _attr_has_entity_name = True

    def __init__(self, coordinator, router, entry: ConfigEntry, index: int) -> None:
        super().__init__(coordinator)
        self._router = router
        self._index = index
        self._attr_unique_id = f"{entry.entry_id}_nat_{index}"
        self._attr_device_info = build_device_info(entry, coordinator.data)

    def _rule(self) -> dict[str, Any] | None:
        for rule in (self.coordinator.data or {}).get("nat_rules") or []:
            if rule.get("Index") == self._index:
                return rule
        return None

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and self._rule() is not None

    @property
    def name(self) -> str:
        rule = self._rule() or {}
        return f"Port Forward {rule.get('Description') or self._index}"

    @property
    def is_on(self) -> bool | None:
        rule = self._rule()
        return None if rule is None else bool(rule.get("Enable"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        rule = self._rule() or {}
        return {
            "protocol": rule.get("Protocol"),
            "external_port": _ports(rule, "External"),
            "internal_port": _ports(rule, "Internal"),
            "internal_client": rule.get("InternalClient"),
            "rule_index": self._index,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)

    async def _set(self, enabled: bool) -> None:
        rule = self._rule()
        if rule is None:
            return
        await self.hass.async_add_executor_job(
            set_port_forward_enabled, self._router, dict(rule), enabled
        )
        rule["Enable"] = enabled  # reflect immediately, the next poll confirms it
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()


def _ports(rule: dict[str, Any], side: str) -> str:
    start, end = rule.get(f"{side}PortStart"), rule.get(f"{side}PortEnd")
    return str(start) if start == end else f"{start}-{end}"


class WifiSwitch(CoordinatorEntity, SwitchEntity):
    """Enable or disable one Wi-Fi network (SSID) on one band."""

    _attr_icon = "mdi:wifi"
    _attr_has_entity_name = True

    def __init__(self, coordinator, router, entry: ConfigEntry, index: int, default_on: bool) -> None:
        super().__init__(coordinator)
        self._router = router
        self._index = index
        self._attr_unique_id = f"{entry.entry_id}_wlan_{index}"
        self._attr_device_info = build_device_info(entry, coordinator.data)
        self._attr_entity_registry_enabled_default = default_on

    def _ssid(self) -> dict[str, Any] | None:
        for ssid in (self.coordinator.data or {}).get("wlan_ssids") or []:
            if ssid.get("Index") == self._index:
                return ssid
        return None

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and self._ssid() is not None

    @property
    def name(self) -> str:
        ssid = self._ssid() or {}
        return f"{ssid.get('SSID') or self._index} ({ssid.get('band', '?')})"

    @property
    def is_on(self) -> bool | None:
        """On only when the network is really on air.

        ``wlEnable`` is the per-network flag, but the band's radio
        (``radioenable``) must be on too: the modem turns a radio off when its
        main network is switched off, which silences the guest networks on that
        band even though their own flag stays set.
        """
        ssid = self._ssid()
        if ssid is None:
            return None
        return bool(ssid.get("wlEnable")) and bool(ssid.get("radioenable", True))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ssid = self._ssid() or {}
        return {
            "band": ssid.get("band"),
            "main_network": ssid.get("MainSSID"),
            "network_flag": ssid.get("wlEnable"),
            "radio_enabled": ssid.get("radioenable"),
            "channel": ssid.get("channel"),
            "ssid_index": self._index,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)

    async def _set(self, enabled: bool) -> None:
        ssid = self._ssid()
        if ssid is None:
            return
        if enabled and not ssid.get("MainSSID") and ssid.get("radioenable") is False:
            raise HomeAssistantError(
                f"The {ssid.get('band')} radio is off because its main network is off. "
                "Turn the main network on first."
            )
        await self.hass.async_add_executor_job(
            set_wifi_enabled, self._router, self._index, enabled
        )
        ssid["wlEnable"] = enabled
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
