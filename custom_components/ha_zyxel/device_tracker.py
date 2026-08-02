"""Device trackers for Zyxel LAN clients."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from homeassistant.components.device_tracker import ScannerEntity, SourceType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.ha_zyxel.const import (
    CONF_CONSIDER_HOME,
    CONF_TRACK_ALL,
    DEFAULT_CONSIDER_HOME,
    DEFAULT_TRACK_ALL,
    DOMAIN,
)
from custom_components.ha_zyxel.helpers import lan_hosts


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create trackers for discovered LAN clients."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    consider_home = entry.options.get(
        CONF_CONSIDER_HOME, DEFAULT_CONSIDER_HOME
    )
    track_all = entry.options.get(CONF_TRACK_ALL, DEFAULT_TRACK_ALL)
    tracked: set[str] = set()

    @callback
    def discover() -> None:
        new_entities = [
            ZyxelDeviceTracker(
                coordinator,
                entry.entry_id,
                mac,
                consider_home,
                track_all,
            )
            for mac in lan_hosts(coordinator)
            if mac not in tracked
        ]
        tracked.update(entity.mac_address for entity in new_entities)
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(discover))
    discover()


class ZyxelDeviceTracker(CoordinatorEntity, ScannerEntity):
    """Represent one LAN client seen by the router."""

    _attr_should_poll = False
    _attr_has_entity_name = False

    def __init__(
        self,
        coordinator,
        entry_id: str,
        mac: str,
        consider_home: int,
        track_all: bool,
    ) -> None:
        """Initialize a LAN client tracker."""
        super().__init__(coordinator)
        self._mac = mac
        self._consider_home = timedelta(seconds=consider_home)
        self._last_seen: datetime | None = None
        self._attr_unique_id = f"{entry_id}_{mac}"
        self._attr_entity_registry_enabled_default = track_all
        self._update_last_seen()

        friendly_name = self._friendly_name()
        self._attr_name = friendly_name
        self._attr_device_info = DeviceInfo(
            connections={(CONNECTION_NETWORK_MAC, mac)},
            default_name=friendly_name,
        )

    @property
    def _host(self) -> dict:
        """Return the current router record for this client."""
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
        """Return the source type."""
        return SourceType.ROUTER

    @property
    def is_connected(self) -> bool:
        """Return whether the client is home within the grace period."""
        if self._host.get("Active"):
            return True
        if self._last_seen is None:
            return False
        elapsed = datetime.now(timezone.utc) - self._last_seen
        return elapsed <= self._consider_home

    @property
    def mac_address(self) -> str:
        """Return the client MAC address."""
        return self._mac

    @property
    def ip_address(self) -> str | None:
        """Return the current IP address."""
        return self._host.get("IPAddress") or None

    @property
    def hostname(self) -> str | None:
        """Return the current hostname."""
        host = self._host
        return host.get("curHostName") or host.get("HostName") or None

    @property
    def extra_state_attributes(self) -> dict:
        """Return useful connection diagnostics."""
        host = self._host
        layer1 = host.get("Layer1Interface") or ""
        is_wifi = "WiFi" in layer1
        ssid_index = (
            layer1.rsplit(".", 1)[-1] if is_wifi and "." in layer1 else None
        )
        network = None
        if is_wifi and "X_ZYXEL_MainSSID" in host:
            network = "main" if host.get("X_ZYXEL_MainSSID") else "guest"
        connection = "wifi" if is_wifi else ("ethernet" if layer1 else None)
        return {
            "ip_address": host.get("IPAddress") or None,
            "connection": connection,
            "band": host.get("SupportedFrequencyBands") or None,
            "network": network,
            "ssid_index": ssid_index,
            "ssid_name": (host.get("WiFiname") or None) if is_wifi else None,
            "access_point": host.get("X_ZYXEL_ConnectedAP") or None,
            "rssi": host.get("X_ZYXEL_RSSI") if is_wifi else None,
            "link_rate_mbps": host.get("X_ZYXEL_PhyRate"),
            "standard": host.get("X_ZYXEL_OperatingStandard") or None,
            "last_seen": (
                self._last_seen.isoformat() if self._last_seen else None
            ),
        }
