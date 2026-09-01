"""Support for Zyxel device sensors."""
from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.ha_zyxel.const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_UPTIME_LEAF_KEYS = {"UpTime", "DSLUpTime", "ipoeConnectionUpTime", "pppoeConnectionUpTime"}
_WLAN_HINT_TOKENS = ("wlan", "wifi", "wireless", "ssid", "channel", "radio", "band")
_SENSITIVE_HINT_TOKENS = ("password", "passphrase", "psk", "wep", "key")
_LANGUAGE_LIKE_VALUES = {"english", "french", "francais", "german", "deutsch", "spanish", "espanol"}
_EX3301_WIFI_TOP_LEVEL_KEYS: set[str] = set()
_EX3301_WIFIINFO_ALLOWED_LEAFS = {
    "SSID",
    "Channel",
    "OperatingFrequencyBand",
    "OperatingChannelBandwidth",
    "OperatingStandards",
    "X_ZYXEL_Rate",
    "X_ZYXEL_MainSSID",
}

_NWA50AX_DEFAULT_LEAFS = {
    "active",
    "band",
    "builddate",
    "ethernet",
    "firmwareversion",
    "hostname",
    "internet",
    "ipaddress",
    "ipv6dhcp6addressrequest",
    "ipv6enable",
    "ipv6gateway",
    "ipv6metric",
    "ipv6slaac",
    "ipv6staticaddress",
    "macaddress",
    "mode",
    "model",
    "nebulacloudstatus",
    "nebulantpstatus",
    "serialnumber",
    "slot0",
    "slot1",
    "slot2",
    "slotactivate",
    "slotchannelutilization",
    "slotreceivedpktcount",
    "slotslot1outputpower",
    "slotslot2outputpower",
    "slottransmittedpktcount",
    "slottxpower",
    "slotwlanreceivedbyte",
    "slotwlantransmittedbyte",
    "vlanid",
    "vlantag",
    "zyxelcloud",
}


def _normalize_leaf_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.strip().lower())


def _format_uptime_dms(value: Any) -> str | None:
    """Format uptime seconds as <days>d <hours>h <minutes>m <seconds>s."""
    try:
        total_seconds = int(float(value))
    except (TypeError, ValueError):
        return None
    if total_seconds < 0:
        return None
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{days}d {hours}h {minutes}m {seconds}s"


def _looks_like_wlan_path(key: str) -> bool:
    lowered = key.lower()
    return any(token in lowered for token in _WLAN_HINT_TOKENS)


def _is_sensitive_path(key: str) -> bool:
    lowered = key.lower()
    return any(token in lowered for token in _SENSITIVE_HINT_TOKENS)


def _is_ex3301_wifi_sensor_key(key: str) -> bool:
    """Return True for EX3301 WiFi keys we intentionally expose."""
    if key in _EX3301_WIFI_TOP_LEVEL_KEYS:
        return True
    if not key.startswith("DAL?oid=cardpage_status.") or ".WiFiInfo." not in key:
        return False
    leaf = key.split(".")[-1]
    return leaf in _EX3301_WIFIINFO_ALLOWED_LEAFS


def _is_active_wifiinfo_path(flat: dict[str, Any], key: str) -> bool:
    """Return True only for active WiFiInfo interfaces."""
    if ".WiFiInfo." not in key:
        return True
    prefix = key.rsplit(".", 1)[0]
    enabled = flat.get(f"{prefix}.Enable")
    return bool(enabled)


def _normalize_wifi_band(value: Any) -> str:
    band = str(value or "").strip().lower()
    if "2.4" in band:
        return "2.4GHz"
    if "5" in band:
        return "5GHz"
    return ""


def _wifiinfo_prefixes(flat: dict[str, Any]) -> set[str]:
    prefixes: set[str] = set()
    for key in flat:
        if ".WiFiInfo." not in key:
            continue
        prefixes.add(key.rsplit(".", 1)[0])
    return prefixes


def _wifi_profile_band_enabled(flat: dict[str, Any], *, main: bool, band: str) -> bool:
    target_band = band.lower()
    for prefix in _wifiinfo_prefixes(flat):
        if not bool(flat.get(f"{prefix}.Enable")):
            continue
        is_main = bool(flat.get(f"{prefix}.X_ZYXEL_MainSSID"))
        if is_main != main:
            continue
        radio_band = _normalize_wifi_band(flat.get(f"{prefix}.OperatingFrequencyBand")).lower()
        if radio_band == target_band:
            return True
    return False


def _ex3301_sensor_enabled_by_default(key: str) -> bool:
    """Return True only for the EX3301 default-visible sensor allowlist."""
    leaf = key.split(".")[-1]
    if leaf in {"SoftwareVersion", "ModelName", "SerialNumber", "UpTime"}:
        return True
    if leaf == "DHCPStatus" and ".WanLanInfo.0." in key:
        return True
    if leaf == "v4dns":
        return True
    if leaf == "IPAddress" and (".WanLanInfo.2." in key or key.endswith(".Object.0.IPAddress")):
        return True
    if leaf == "pppConnectionStatus" and ".WanLanInfo.2." in key:
        return True
    if leaf == "EthConnectionStatus":
        return True
    if leaf == "v4Gateway" and ".WanLanInfo.2." in key:
        return True
    if leaf == "ipoeConnectionUpTime" and ".WanLanInfo.2." in key:
        return True
    if leaf == "pppoeConnectionUpTime" and ".WanLanInfo.2." in key:
        return True
    return False


def _ex3301_wifi_label_for_key(flat: dict[str, Any], key: str) -> tuple[str, str]:
    """Return (name, icon) for EX3301 WiFi key."""
    leaf = key.split(".")[-1]
    field_name = {
        "SSID": "SSID",
        "Channel": "Channel",
        "OperatingFrequencyBand": "Frequency Band",
        "OperatingChannelBandwidth": "Channel Bandwidth",
        "OperatingStandards": "WiFi Standards",
        "X_ZYXEL_Rate": "Link Rate",
        "X_ZYXEL_MainSSID": "Main SSID",
    }.get(leaf, leaf)
    icon = {
        "SSID": "mdi:wifi-settings",
        "Channel": "mdi:access-point",
        "OperatingFrequencyBand": "mdi:radio-tower",
        "OperatingChannelBandwidth": "mdi:wifi-strength-3",
        "OperatingStandards": "mdi:wifi",
        "X_ZYXEL_Rate": "mdi:speedometer",
        "X_ZYXEL_MainSSID": "mdi:wifi-check",
    }.get(leaf, "mdi:wifi")

    parts = key.split(".")
    try:
        i = parts.index("WiFiInfo")
        slot = parts[i + 1]
    except (ValueError, IndexError):
        slot = "?"
    prefix = key.rsplit(".", 1)[0]
    is_main = bool(flat.get(f"{prefix}.X_ZYXEL_MainSSID"))
    profile = "Private WiFi" if is_main else "Guest WiFi"
    band = str(flat.get(f"{prefix}.OperatingFrequencyBand", "")).strip()
    if band:
        return f"{profile} {band} {field_name} (Radio {slot})", icon
    return f"{profile} {field_name} (Radio {slot})", icon


def _ex3301_section_prefix(key: str) -> str:
    """Return a stable section prefix for EX3301 entity list grouping."""
    leaf = key.split(".")[-1]
    if leaf in {"SoftwareVersion", "HardwareVersion", "ModelName", "SerialNumber"}:
        return "[Core] "
    if leaf in {"DownstreamCurrRate", "UpstreamCurrRate", "DSLUpTime"}:
        return "[DSL] "
    if leaf in {"UpTime", "ipoeConnectionUpTime", "pppoeConnectionUpTime"}:
        return "[Uptime] "
    if leaf in {
        "IPAddress",
        "DefaultGateway",
        "v4Gateway",
        "v4dns",
        "pppConnectionStatus",
        "EthConnectionStatus",
        "DHCPStatus",
        "WanRate_RX",
        "WanRate_TX",
        "WanType",
    }:
        return "[Network] "
    return ""


# Define some known sensor types for proper configuration
KNOWN_SENSORS = {
    "INTF_RSSI": {
        "name": "Cellular RSSI",
        "unit": "dBm",
        "icon": "mdi:signal",
        "device_class": SensorDeviceClass.SIGNAL_STRENGTH,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    "INTF_PhyCell_ID": {
        "name": "Physical Cell ID",
        "unit": None,
        "icon": "mdi:antenna",
        "device_class": None,
        "state_class": None,
    },
    "INTF_RSRP": {
        "name": "Cellular Reference Signal Received Power",
        "unit": "dBm",
        "icon": "mdi:signal",
        "device_class": SensorDeviceClass.SIGNAL_STRENGTH,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    "INTF_RSRQ": {
        "name": "Cellular Reference Signal Received Quality",
        "unit": "dB",
        "icon": "mdi:signal",
        "device_class": SensorDeviceClass.SIGNAL_STRENGTH,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    "INTF_SINR": {
        "name": "Cellular Signal-to-Noise Ratio",
        "unit": "dB",
        "icon": "mdi:signal",
        "device_class": SensorDeviceClass.SIGNAL_STRENGTH,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    "INTF_MCS": {
        "name": "Cellular Modulation and Coding Scheme",
        "unit": "",
        "icon": "mdi:signal",
        "device_class": None,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    "INTF_CQI": {
        "name": "Cellular Channel Quality Indicator",
        "unit": "",
        "icon": "mdi:signal",
        "device_class": None,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    "INTF_RI": {
        "name": "Cellular Rank Indicator",
        "unit": "",
        "icon": "mdi:signal",
        "device_class": None,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    "INTF_PMI": {
        "name": "Cellular Precoding Matrix Indicator",
        "unit": "",
        "icon": "mdi:signal",
        "device_class": None,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    "NSA_PhyCellID": {
        "name": "NSA Physical Cell ID",
        "unit": None,
        "icon": "mdi:antenna",
        "device_class": None,
        "state_class": None,
    },
    "NSA_RSRP": {
        "name": "NSA Reference Signal Received Power",
        "unit": "dBm",
        "icon": "mdi:signal",
        "device_class": SensorDeviceClass.SIGNAL_STRENGTH,
        "state_class": SensorStateClass.MEASUREMENT
    },
    "NSA_RSRQ": {
        "name": "NSA Reference Signal Received Quality",
        "unit": "dB",
        "icon": "mdi:signal",
        "device_class": SensorDeviceClass.SIGNAL_STRENGTH,
        "state_class": SensorStateClass.MEASUREMENT
    },
    "NSA_RSSI": {
        "name": "NSA Reference Signal Strength Indicator",
        "unit": "dBm",
        "icon": "mdi:signal",
        "device_class": SensorDeviceClass.SIGNAL_STRENGTH,
        "state_class": SensorStateClass.MEASUREMENT
    },
    "NSA_SINR": {
        "name": "NSA Signal-to-Noise Ratio",
        "unit": "dB",
        "icon": "mdi:signal",
        "device_class": SensorDeviceClass.SIGNAL_STRENGTH,
        "state_class": SensorStateClass.MEASUREMENT
    },
    "X_ZYXEL_TEMPERATURE_AMBIENT": {
        "name": "Ambient Temperature",
        "unit": "°C",
        "icon": "mdi:thermometer",
        "device_class": SensorDeviceClass.TEMPERATURE,
        "state_class": SensorStateClass.MEASUREMENT
    },
    "X_ZYXEL_TEMPERATURE_SDX": {
        "name": "SDX Temperature",
        "unit": "°C",
        "icon": "mdi:thermometer",
        "device_class": SensorDeviceClass.TEMPERATURE,
        "state_class": SensorStateClass.MEASUREMENT
    },
    "X_ZYXEL_TEMPERATURE_CPU0": {
        "name": "CPU Temperature",
        "unit": "°C",
        "icon": "mdi:thermometer",
        "device_class": SensorDeviceClass.TEMPERATURE,
        "state_class": SensorStateClass.MEASUREMENT
    },
    "BytesSent": {
        "name": "Bytes Sent",
        "unit": "B",
        "icon": "mdi:numeric-10-box",
        "device_class": SensorDeviceClass.DATA_SIZE,
        "state_class": SensorStateClass.TOTAL_INCREASING,
    },
    "BytesReceived": {
        "name": "Bytes Received",
        "unit": "B",
        "icon": "mdi:numeric-10-box",
        "device_class": SensorDeviceClass.DATA_SIZE,
        "state_class": SensorStateClass.TOTAL_INCREASING,
    },
    # ── EX3301-T0: Device info (DAL?oid=cardpage_status.Object.0.DeviceInfo) ──
    "SoftwareVersion": {
        "name": "Firmware Version",
        "unit": None,
        "icon": "mdi:information-outline",
        "device_class": None,
        "state_class": None,
    },
    "ModelName": {
        "name": "Model",
        "unit": None,
        "icon": "mdi:router",
        "device_class": None,
        "state_class": None,
    },
    "SerialNumber": {
        "name": "Serial Number",
        "unit": None,
        "icon": "mdi:barcode",
        "device_class": None,
        "state_class": None,
    },
    "UpTime": {
        "name": "System Uptime",
        "unit": "s",
        "icon": "mdi:timer-outline",
        "device_class": SensorDeviceClass.DURATION,
        "state_class": SensorStateClass.TOTAL_INCREASING,
    },
    # ── EX3301-T0: DSL channel (DslChannelInfo.0) ──
    "DownstreamCurrRate": {
        "name": "DSL Downstream Rate",
        "unit": "kbit/s",
        "icon": "mdi:download-network",
        "device_class": None,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    "UpstreamCurrRate": {
        "name": "DSL Upstream Rate",
        "unit": "kbit/s",
        "icon": "mdi:upload-network",
        "device_class": None,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    "DSLUpTime": {
        "name": "DSL Uptime",
        "unit": "s",
        "icon": "mdi:timer-outline",
        "device_class": SensorDeviceClass.DURATION,
        "state_class": SensorStateClass.TOTAL_INCREASING,
    },
    # ── EX3301-T0: WAN summary (Object.0 level) ──
    "WanRate_RX": {
        "name": "WAN Receive Rate",
        "unit": "kbit/s",
        "icon": "mdi:download",
        "device_class": None,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    "WanRate_TX": {
        "name": "WAN Transmit Rate",
        "unit": "kbit/s",
        "icon": "mdi:upload",
        "device_class": None,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    "WanType": {
        "name": "WAN Type",
        "unit": None,
        "icon": "mdi:wan",
        "device_class": None,
        "state_class": None,
    },
    # ── EX3301-T0: WAN interface (WanLanInfo) ──
    "IPAddress": {
        "name": "IP Address",
        "unit": None,
        "icon": "mdi:ip-network",
        "device_class": None,
        "state_class": None,
    },
    "DefaultGateway": {
        # This field is a boolean: True = this interface is the active default route.
        "name": "Is Default Route",
        "unit": None,
        "icon": "mdi:routes",
        "device_class": None,
        "state_class": None,
    },
    "v4Gateway": {
        "name": "WAN Gateway IP",
        "unit": None,
        "icon": "mdi:router-network",
        "device_class": None,
        "state_class": None,
    },
    "v4dns": {
        "name": "DNS Server",
        "unit": None,
        "icon": "mdi:dns",
        "device_class": None,
        "state_class": None,
    },
    "pppConnectionStatus": {
        "name": "PPP Connection Status",
        "unit": None,
        "icon": "mdi:connection",
        "device_class": None,
        "state_class": None,
    },
    "EthConnectionStatus": {
        "name": "WAN Ethernet Status",
        "unit": None,
        "icon": "mdi:ethernet",
        "device_class": None,
        "state_class": None,
    },
    "ipoeConnectionUpTime": {
        "name": "IPoE Connection Uptime",
        "unit": "s",
        "icon": "mdi:timer-outline",
        "device_class": SensorDeviceClass.DURATION,
        "state_class": SensorStateClass.TOTAL_INCREASING,
    },
    "pppoeConnectionUpTime": {
        "name": "PPPoE Connection Uptime",
        "unit": "s",
        "icon": "mdi:timer-outline",
        "device_class": SensorDeviceClass.DURATION,
        "state_class": SensorStateClass.TOTAL_INCREASING,
    },
    "DHCPStatus": {
        "name": "DHCP Status",
        "unit": None,
        "icon": "mdi:ip",
        "device_class": None,
        "state_class": None,
    },
    # ── EX3301-T0: Temperature (GponStatsInfo) ──
    "Temperature": {
        "name": "Device Temperature",
        "unit": "°C",
        "icon": "mdi:thermometer",
        "device_class": SensorDeviceClass.TEMPERATURE,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    # ── EX3301-T0: WLAN settings (DAL?oid=wlan) ──
    "WiFiSettings_wifikeepsame": {
        "name": "Private WiFi Keep Same",
        "unit": None,
        "icon": "mdi:wifi-sync",
        "device_class": None,
        "state_class": None,
    },
    "WiFiSettings_onessid_24g_switch": {
        "name": "Private WiFi 2.4G Enabled",
        "unit": None,
        "icon": "mdi:wifi",
        "device_class": None,
        "state_class": None,
    },
    "WiFiSettings_onessid_5g_switch": {
        "name": "Private WiFi 5G Enabled",
        "unit": None,
        "icon": "mdi:wifi",
        "device_class": None,
        "state_class": None,
    },
    "WiFiSettings_both_wifiname": {
        "name": "Private WiFi SSID",
        "unit": None,
        "icon": "mdi:wifi-settings",
        "device_class": None,
        "state_class": None,
    },
    "WiFiSettings_24g_randompw": {
        "name": "Private WiFi 2.4G Random Password",
        "unit": None,
        "icon": "mdi:key-variant",
        "device_class": None,
        "state_class": None,
    },
    "WiFiSettings_both_randompw": {
        "name": "Private WiFi Random Password",
        "unit": None,
        "icon": "mdi:key-variant",
        "device_class": None,
        "state_class": None,
    },
    "WiFiSettings_both_hidewifiname": {
        "name": "Private WiFi Hide SSID",
        "unit": None,
        "icon": "mdi:wifi-hidden",
        "device_class": None,
        "state_class": None,
    },
    "Guest_WiFiSettings_both_wifiname": {
        "name": "Guest WiFi SSID",
        "unit": None,
        "icon": "mdi:wifi-settings",
        "device_class": None,
        "state_class": None,
    },
    "Guest_WiFiSettings_both_hidewifiname": {
        "name": "Guest WiFi Hide SSID",
        "unit": None,
        "icon": "mdi:wifi-hidden",
        "device_class": None,
        "state_class": None,
    },
}


def _flatten_dict(d: dict, parent_key: str = "") -> dict:
    """Flatten a nested dictionary with dot notation for keys."""
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}.{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(_flatten_dict(v, new_key).items())
        elif isinstance(v, list):
            for i, item in enumerate(v):
                list_key = f"{new_key}.{i}"
                if isinstance(item, dict):
                    items.extend(_flatten_dict(item, list_key).items())
                else:
                    items.append((list_key, item))
        else:
            items.append((new_key, v))
    return dict(items)


def _is_value_scalar(value: Any) -> bool:
    """Check if a value is a scalar (string, number, bool)."""
    return isinstance(value, (str, int, float, bool)) or value is None


def _looks_like_ip(value: str) -> bool:
    parts = value.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except ValueError:
        return False


def _device_system_name(flat: dict[str, Any]) -> str | None:
    """Return best host-readable system name for any Zyxel model."""
    preferred_tokens = ("system name", "system_name", "systemname", "hostname", "fqdn", "device_name")
    for key, value in flat.items():
        if not isinstance(value, str):
            continue
        candidate = value.strip()
        if not candidate:
            continue
        lowered = candidate.lower()
        if lowered in _LANGUAGE_LIKE_VALUES:
            continue
        if _looks_like_ip(candidate):
            continue
        if lowered.startswith(("http://", "https://")):
            continue
        key_lower = key.lower()
        if any(token in key_lower for token in preferred_tokens):
            return candidate
    return None


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Zyxel sensors."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    device_type = str(hass.data[DOMAIN][entry.entry_id].get("device_type", "legacy")).lower().replace(
        "-",
        "_",
    )

    if not coordinator.data:
        _LOGGER.warning("Zyxel coordinator has no data at sensor setup — no sensors created")
        return

    flat = _flatten_dict(coordinator.data)
    # Log available keys at INFO level to help tune sensor mappings.
    _LOGGER.info(
        "Zyxel (%s) available data keys (%d total): %s",
        device_type,
        len(flat),
        sorted(flat.keys()),
    )
    sensors = []
    if device_type == "ex3301_t0":
        wlan_keys = sorted(
            key for key in flat if _looks_like_wlan_path(key) and not _is_sensitive_path(key)
        )
        _LOGGER.info(
            "Zyxel (%s) WLAN-related data keys (%d total): %s",
            device_type,
            len(wlan_keys),
            wlan_keys,
        )
        sensors.extend(
            [
                EX3301WiFiBandStateSensor(coordinator, entry, "Private", "2.4GHz", True),
                EX3301WiFiBandStateSensor(coordinator, entry, "Private", "5GHz", True),
                EX3301WiFiBandStateSensor(coordinator, entry, "Guest", "2.4GHz", False),
                EX3301WiFiBandStateSensor(coordinator, entry, "Guest", "5GHz", False),
            ]
        )

    if device_type == "nwa50ax":
        all_nwa_leafs = sorted(
            {_normalize_leaf_name(k.split(".")[-1]) for k, v in flat.items() if _is_value_scalar(v) and not k.startswith("zyshdata")}
        )
        enabled_leafs = [l for l in all_nwa_leafs if l in _NWA50AX_DEFAULT_LEAFS]
        disabled_leafs = [l for l in all_nwa_leafs if l not in _NWA50AX_DEFAULT_LEAFS]
        _LOGGER.debug(
            "NWA50AX (%s) leaf summary — total: %d, enabled-by-default: %d %s, disabled-by-default: %d %s",
            entry.data.get("host", entry.entry_id),
            len(all_nwa_leafs),
            len(enabled_leafs),
            enabled_leafs,
            len(disabled_leafs),
            disabled_leafs,
        )

    for key, value in flat.items():
        if not _is_value_scalar(value):
            continue

        if device_type == "nwa50ax" and key.startswith("zyshdata"):
            continue

        # For EX3301, skip API fields that return null — these are firmware stubs
        # (GPON temperature, WAN rate reporting, inactive DSL channels) that will
        # never have useful data and only add clutter to the entity list.
        if device_type == "ex3301_t0" and value is None:
            continue

        leaf = key.split(".")[-1]
        sensor_config = KNOWN_SENSORS.get(leaf)

        if sensor_config:
            sensors.append(ConfiguredZyxelSensor(coordinator, entry, key, sensor_config))
        elif (
            device_type == "ex3301_t0"
            and _is_ex3301_wifi_sensor_key(key)
            and not _is_sensitive_path(key)
            and _is_active_wifiinfo_path(flat, key)
            and str(value).strip().lower() != "unknown"
        ):
            sensors.append(EX3301WiFiSensor(coordinator, entry, key))
        elif device_type != "ex3301_t0":
            # Generic sensors for legacy/NWA50AX — avoid flooding HA for EX3301
            # whose responses contain deeply-nested arrays with hundreds of fields.
            sensors.append(GenericZyxelSensor(coordinator, entry, key))

    # Remove stale entity registry entries that are no longer being created
    # (e.g. sensors suppressed by the null-value filter on this reload).
    current_unique_ids = {s.unique_id for s in sensors}
    ent_reg = er.async_get(hass)
    config_entries = list(er.async_entries_for_config_entry(ent_reg, entry.entry_id))
    for reg_entry in config_entries:
        if reg_entry.unique_id not in current_unique_ids:
            _LOGGER.debug("Removing stale entity %s", reg_entry.entity_id)
            ent_reg.async_remove(reg_entry.entity_id)

    # Migration cleanup: force recreation of entities that were created with the
    # old Zyxel-prefixed naming so the new device/entity names can take effect.
    for reg_entry in config_entries:
        if reg_entry.entity_id.startswith(("sensor.zyxel_", "button.zyxel_")):
            _LOGGER.debug("Removing legacy prefixed entity for rename migration: %s", reg_entry.entity_id)
            ent_reg.async_remove(reg_entry.entity_id)

    # Migration cleanup: purge legacy generic EX3301 entities created by older
    # builds for the same physical device under a different config_entry_id.
    # These have entity IDs like sensor.zyxel_dal_oid_... and are no longer used.
    if device_type == "ex3301_t0":
        current_device_ids = {reg.device_id for reg in config_entries if reg.device_id}
        if current_device_ids:
            for reg_entry in list(ent_reg.entities.values()):
                if reg_entry.platform != DOMAIN:
                    continue
                if reg_entry.config_entry_id == entry.entry_id:
                    continue
                if reg_entry.device_id not in current_device_ids:
                    continue
                if reg_entry.entity_id.startswith("sensor.zyxel_dal_oid_"):
                    _LOGGER.debug(
                        "Removing legacy EX3301 generic entity from old entry: %s",
                        reg_entry.entity_id,
                    )
                    ent_reg.async_remove(reg_entry.entity_id)

    if not sensors:
        _LOGGER.warning(
            "Zyxel (%s): no sensors matched KNOWN_SENSORS — check the data keys log above",
            device_type,
        )
    else:
        _LOGGER.debug("Zyxel sensor setup: creating %d sensors for %s", len(sensors), device_type)
        async_add_entities(sensors)


class AbstractZyxelSensor(CoordinatorEntity, SensorEntity):
    """Base class for Zyxel device sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry: ConfigEntry, key: str):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._key = key
        # Pre-populate immediately in case the coordinator already refreshed
        # before this sensor was registered (sensors are created after first_refresh).
        self._flat_state: dict[str, Any] = (
            _flatten_dict(coordinator.data) if coordinator.data else {}
        )
        safe_key = key.replace("?", "_").replace("=", "_").replace("&", "_").replace(" ", "_")
        self._attr_unique_id = f"{entry.entry_id}_{safe_key}"
        flat = self._flat_state
        # flat has full-path keys; search by leaf name (first match wins).
        leaf_vals: dict[str, Any] = {}
        for k, v in flat.items():
            leaf = k.split(".")[-1]
            if leaf not in leaf_vals:
                leaf_vals[leaf] = v
        model = (
            leaf_vals.get("ModelName")
            or leaf_vals.get("ProductClass")
            or leaf_vals.get("HardwareVersion")
            or entry.data.get("device_type", "").upper().replace("_", "-")
            or entry.data.get("host", "")
        )
        sw_version = leaf_vals.get("SoftwareVersion") or leaf_vals.get("FirmwareVersion")
        host = str(entry.data.get("host", "")).replace("http://", "").replace("https://", "")
        host = host.split("/", 1)[0]
        display_name = _device_system_name(flat) or (f"Zyxel {host}" if host else f"Zyxel {model}")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=display_name,
            manufacturer="Zyxel",
            model=model,
            sw_version=sw_version,
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Refresh flat-state cache then write HA state."""
        if self.coordinator.data:
            self._flat_state = _flatten_dict(self.coordinator.data)
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if not self.coordinator.last_update_success or not self.coordinator.data:
            return False
        return self._key in self._flat_state

    def _get_value_from_path(self) -> Any:
        """Return the value from the cached flat state."""
        return self._flat_state[self._key]


class ConfiguredZyxelSensor(AbstractZyxelSensor):
    """Representation of a configured Zyxel sensor."""

    # Human-readable labels for known array-parent names.
    _PARENT_LABELS: dict[str, str] = {
        "DslChannelInfo": "DSL Ch",
        "WanLanInfo": "WAN",
        "LanInfo": "LAN",
        "LanPortInfo": "LAN Port",
        "dnsv4Server": "DNS",
        "IPv4Address": "",
        "Object": "",
    }

    @classmethod
    def _path_suffix(cls, key: str) -> str:
        """Return a disambiguating suffix for keys that appear at multiple array indices.

        Scans the path right-to-left for the first numeric component whose
        named parent maps to a non-empty label, skipping 'Object' and parents
        mapped to '' (e.g. IPv4Address) so we bubble up to a more meaningful
        ancestor like WanLanInfo.
        """
        parts = key.split(".")
        for i in range(len(parts) - 2, 0, -1):
            if parts[i].isdigit() and not parts[i - 1].isdigit():
                parent = parts[i - 1]
                if parent == "Object":
                    continue
                if parent == "WanLanInfo":
                    if parts[i] == "0":
                        return " (LAN)"
                    return f" (WAN {parts[i]})"
                prefix = cls._PARENT_LABELS.get(parent, parent)
                if prefix == "":
                    continue  # uninformative parent (e.g. IPv4Address) — keep scanning
                label = f"{prefix} {parts[i]}".strip()
                return f" ({label})" if label else ""
        return ""

    def __init__(self, coordinator, entry: ConfigEntry, key: str, config: dict):
        """Initialize the sensor."""
        super().__init__(coordinator, entry, key)
        self._config = config
        self._is_uptime = key.split(".")[-1] in _UPTIME_LEAF_KEYS
        device_type = str(entry.data.get("device_type", "")).lower().replace("-", "_")
        prefix = _ex3301_section_prefix(key) if device_type == "ex3301_t0" else ""
        self._attr_name = f"{prefix}{config['name']}{self._path_suffix(key)}"
        if device_type == "ex3301_t0":
            self._attr_entity_registry_enabled_default = _ex3301_sensor_enabled_by_default(key)
        self._attr_native_unit_of_measurement = None if self._is_uptime else config["unit"]
        self._attr_icon = config["icon"]
        self._attr_device_class = None if self._is_uptime else config["device_class"]
        self._attr_state_class = None if self._is_uptime else config["state_class"]

    @property
    def state(self):
        """Return the state of the sensor, or None for empty strings."""
        try:
            value = self._get_value_from_path()
            if value == "":
                return None
            if self._is_uptime:
                return _format_uptime_dms(value)
            return value
        except (KeyError, AttributeError, TypeError, IndexError, ValueError):
            return None


class GenericZyxelSensor(AbstractZyxelSensor):
    """Representation of a generic Zyxel sensor."""

    def __init__(self, coordinator, entry: ConfigEntry, key: str):
        super().__init__(coordinator, entry, key)
        device_type = str(entry.data.get("device_type", "")).lower().replace("-", "_")
        if device_type == "nwa50ax":
            leaf = _normalize_leaf_name(key.split(".")[-1])
            self._attr_entity_registry_enabled_default = leaf in _NWA50AX_DEFAULT_LEAFS

    @property
    def name(self):
        """Return the name of the sensor."""
        name_parts = self._key.split(".")
        return ".".join(name_parts)

    @property
    def state(self):
        """Return the state of the sensor, or None for empty strings."""
        try:
            value = self._get_value_from_path()
            return value if value != "" else None
        except (KeyError, AttributeError, TypeError, IndexError, ValueError):
            return None

    @property
    def icon(self):
        """Return the icon."""
        return "mdi:router-wireless"


class EX3301WiFiSensor(AbstractZyxelSensor):
    """Friendly-named WiFi telemetry sensor for EX3301."""

    def __init__(self, coordinator, entry: ConfigEntry, key: str):
        super().__init__(coordinator, entry, key)
        name, icon = _ex3301_wifi_label_for_key(self._flat_state, key)
        self._attr_name = f"[WiFi] {name}"
        self._attr_icon = icon
        self._attr_entity_registry_enabled_default = False

    @property
    def state(self):
        """Return the state of the sensor, or None for empty strings."""
        try:
            value = self._get_value_from_path()
            return value if value != "" else None
        except (KeyError, AttributeError, TypeError, IndexError, ValueError):
            return None


class EX3301WiFiBandStateSensor(AbstractZyxelSensor):
    """Derived EX3301 sensor for private/guest WiFi enabled per band."""

    def __init__(self, coordinator, entry: ConfigEntry, profile: str, band: str, main: bool):
        key = f"EX3301.WiFi.{profile}.{band}.Enabled"
        super().__init__(coordinator, entry, key)
        self._profile = profile
        self._band = band
        self._main = main
        self._attr_name = f"[WiFi] {profile} WiFi {band} Enabled"
        self._attr_icon = "mdi:wifi-check"
        self._attr_entity_registry_enabled_default = True

    @property
    def available(self) -> bool:
        return bool(self.coordinator.last_update_success and self.coordinator.data)

    @property
    def state(self):
        try:
            flat = _flatten_dict(self.coordinator.data) if self.coordinator.data else {}
            return _wifi_profile_band_enabled(
                flat,
                main=self._main,
                band=self._band,
            )
        except (TypeError, ValueError, AttributeError):
            return None
