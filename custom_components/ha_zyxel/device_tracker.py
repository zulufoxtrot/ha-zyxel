"""Device tracker platform for Zyxel routers (LAN hosts / presence detection)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from homeassistant.components.device_tracker import ScannerEntity, SourceType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers import entity_registry as er

from custom_components.ha_zyxel.const import (
    CONF_CONSIDER_HOME,
    CONF_TRACK_ALL,
    DEFAULT_CONSIDER_HOME,
    DEFAULT_TRACK_ALL,
    DOMAIN,
)
from custom_components.ha_zyxel.helpers import lan_hosts

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create a tracker for every LAN host and pick up new ones as they appear."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    consider_home = entry.options.get(CONF_CONSIDER_HOME, DEFAULT_CONSIDER_HOME)
    track_all = entry.options.get(CONF_TRACK_ALL, DEFAULT_TRACK_ALL)

    # "Track all" on: re-enable trackers we auto-disabled, but leave the ones the
    # user disabled by hand alone.
    if track_all:
        registry = er.async_get(hass)
        for reg in er.async_entries_for_config_entry(registry, entry.entry_id):
            if (
                reg.domain == "device_tracker"
                and reg.disabled_by == er.RegistryEntryDisabler.INTEGRATION
            ):
                registry.async_update_entity(reg.entity_id, disabled_by=None)

    tracked: set[str] = set()

    @callback
    def _discover() -> None:
        new = [
            ZyxelDeviceTracker(coordinator, entry, mac, consider_home, track_all)
            for mac in lan_hosts(coordinator)
            if mac not in tracked
        ]
        for ent in new:
            tracked.add(ent.mac_address)
        if new:
            async_add_entities(new)

    entry.async_on_unload(coordinator.async_add_listener(_discover))
    _discover()


class ZyxelDeviceTracker(CoordinatorEntity, ScannerEntity):
    """A single LAN client seen by the Zyxel router."""

    _attr_should_poll = False
    _attr_has_entity_name = False

    def __init__(
        self,
        coordinator,
        entry: ConfigEntry,
        mac: str,
        consider_home: int,
        track_all: bool = False,
    ) -> None:
        """Initialise the tracker for one MAC address."""
        super().__init__(coordinator)
        self._mac = mac
        self._consider_home = timedelta(seconds=consider_home)
        self._track_all = track_all
        self._last_seen: datetime | None = None
        self._attr_unique_id = mac
        self._update_last_seen()

        friendly = self._friendly_name()
        self._attr_name = friendly
        # MAC connection lets HA merge this onto an existing device of the same MAC.
        self._attr_device_info = DeviceInfo(
            connections={(CONNECTION_NETWORK_MAC, mac)},
            default_name=friendly,
        )

    @property
    def _host(self) -> dict:
        """Current router record for this MAC (empty dict once it disappears)."""
        return lan_hosts(self.coordinator).get(self._mac, {})

    def _friendly_name(self) -> str:
        host = self._host
        return (
            host.get("curHostName")
            or host.get("HostName")
            or host.get("DeviceName")
            or self._mac
        )

    def _update_last_seen(self) -> None:
        if self._host.get("Active"):
            self._last_seen = datetime.now(timezone.utc)

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_last_seen()
        super()._handle_coordinator_update()

    @property
    def source_type(self) -> SourceType:
        return SourceType.ROUTER

    @property
    def entity_registry_enabled_default(self) -> bool:
        # Default (like AsusWRT): only devices HA already knows by MAC are enabled.
        # With the "track all" option on, enable every discovered device.
        if self._track_all:
            return True
        return super().entity_registry_enabled_default

    @property
    def is_connected(self) -> bool:
        """Home if active now, or last seen within the consider-home window."""
        if self._host.get("Active"):
            return True
        if self._last_seen is None:
            return False
        return datetime.now(timezone.utc) - self._last_seen <= self._consider_home

    @property
    def mac_address(self) -> str:
        return self._mac

    @property
    def ip_address(self) -> str | None:
        return self._host.get("IPAddress") or None

    @property
    def hostname(self) -> str | None:
        host = self._host
        return host.get("curHostName") or host.get("HostName") or None

    @property
    def extra_state_attributes(self) -> dict:
        host = self._host
        layer1 = host.get("Layer1Interface") or ""
        is_wifi = "WiFi" in layer1
        ssid_index = layer1.rsplit(".", 1)[-1] if (is_wifi and "." in layer1) else None
        network = None
        if is_wifi and "X_ZYXEL_MainSSID" in host:
            network = "main" if host.get("X_ZYXEL_MainSSID") else "guest"
        return {
            "ip_address": host.get("IPAddress") or None,
            "connection": "wifi" if is_wifi else ("ethernet" if layer1 else None),
            "band": host.get("SupportedFrequencyBands") or None,
            "network": network,
            "ssid_index": ssid_index,
            # WiFiname is the SSID name, not a device name.
            "ssid_name": (host.get("WiFiname") or None) if is_wifi else None,
            "access_point": host.get("X_ZYXEL_ConnectedAP") or None,
            "rssi": host.get("X_ZYXEL_RSSI") if is_wifi else None,
            "link_rate_mbps": host.get("X_ZYXEL_PhyRate"),
            "standard": host.get("X_ZYXEL_OperatingStandard") or None,
            "last_seen": self._last_seen.isoformat() if self._last_seen else None,
        }
