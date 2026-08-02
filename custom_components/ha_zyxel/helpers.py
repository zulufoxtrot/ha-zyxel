"""Shared helpers for the Zyxel integration."""
from __future__ import annotations

import re

from homeassistant.helpers.device_registry import format_mac

CELL_DETAIL_KEYS = (
    "Band",
    "RFCN",
    "PhyCellID",
    "RSRP",
    "RSRQ",
    "RSSI",
    "SINR",
    "DownlinkBandwidth",
    "CA_STATE",
    "NeighbourType",
    "ConnectionMode",
)
DEVICE_METADATA_FIELDS = {
    "model": ("ModelName", "Model"),
    "sw_version": ("SoftwareVersion", "FirmwareVersion", "FWVersion"),
    "hw_version": ("HardwareVersion",),
    "serial_number": ("SerialNumber",),
}
SENSITIVE_IDENTIFIER_FIELDS = {
    "imei": ("IMEI", "INTF_IMEI"),
    "imsi": ("IMSI", "USIM_IMSI"),
    "iccid": ("ICCID", "USIM_ICCID"),
}
SECRET_FIELD_MARKERS = (
    "apikey",
    "authorization",
    "bearer",
    "cookie",
    "credential",
    "password",
    "passwd",
    "pwd",
    "secret",
    "sessionid",
    "sessionkey",
    "token",
)
MAX_STATE_LENGTH = 255


def flattened_scalars(data: dict, parent_key: str = "") -> dict[str, object]:
    """Flatten scalar values from nested router data."""
    result: dict[str, object] = {}
    for key, value in data.items():
        path = f"{parent_key}.{key}" if parent_key else key
        if isinstance(value, dict):
            result.update(flattened_scalars(value, path))
        elif isinstance(value, (str, int, float, bool)) or value is None:
            result[path] = value
    return result


def select_unique_fields(
    data: dict,
    field_names: set[str],
) -> dict[str, tuple[str, object]]:
    """Select one canonical path and value for each requested leaf field."""
    source_priority = {
        "cellular": 0,
        "cardpage": 1,
        "device": 2,
        "device_info": 3,
    }
    flattened = flattened_scalars(data)
    ordered = sorted(
        flattened.items(),
        key=lambda item: (
            item[1] is None or item[1] == "",
            source_priority.get(item[0].split(".", 1)[0], 4),
            len(item[0]),
            item[0],
        ),
    )
    selected: dict[str, tuple[str, object]] = {}
    for path, value in ordered:
        field_name = path.rsplit(".", 1)[-1]
        if field_name in field_names and field_name not in selected:
            selected[field_name] = (path, value)
    return selected


def device_metadata(data: dict) -> dict[str, object]:
    """Return safe device metadata without subscriber or credential fields."""
    field_names = {
        field_name
        for aliases in DEVICE_METADATA_FIELDS.values()
        for field_name in aliases
    }
    selected = select_unique_fields(data, field_names)
    metadata: dict[str, object] = {}
    for metadata_key, aliases in DEVICE_METADATA_FIELDS.items():
        for field_name in aliases:
            if field_name in selected:
                value = selected[field_name][1]
                if value not in (None, ""):
                    metadata[metadata_key] = str(value)
                    break
    return metadata


def sensitive_identifiers(data: dict) -> dict[str, tuple[str, object]]:
    """Return explicitly allowlisted cellular identifiers and their paths."""
    field_names = {
        field_name
        for aliases in SENSITIVE_IDENTIFIER_FIELDS.values()
        for field_name in aliases
    }
    selected = select_unique_fields(data, field_names)
    identifiers: dict[str, tuple[str, object]] = {}
    for identifier, aliases in SENSITIVE_IDENTIFIER_FIELDS.items():
        for field_name in aliases:
            if (
                field_name in selected
                and selected[field_name][1] not in (None, "")
            ):
                identifiers[identifier] = selected[field_name]
                break
    return identifiers


def is_cellular_identifier_field(path: str) -> bool:
    """Return whether a field contains an allowlisted cellular identifier."""
    field_name = path.rsplit(".", 1)[-1]
    normalized = re.sub(r"[^a-z0-9]", "", field_name.lower())
    identifier_fields = {
        re.sub(r"[^a-z0-9]", "", alias.lower())
        for aliases in SENSITIVE_IDENTIFIER_FIELDS.values()
        for alias in aliases
    }
    return normalized in identifier_fields


def is_secret_field(path: str) -> bool:
    """Return whether a field contains credential or session data."""
    normalized_segments = (
        re.sub(r"[^a-z0-9]", "", segment.lower())
        for segment in path.split(".")
    )
    return any(
        marker in segment
        for segment in normalized_segments
        for marker in SECRET_FIELD_MARKERS
    )


def is_sensitive_field(path: str) -> bool:
    """Return whether a raw field requires a dedicated sensitive opt-in."""
    return is_cellular_identifier_field(path) or is_secret_field(path)


def router_session_data(router) -> dict[str, object]:
    """Return active router session data without configured login values."""
    result: dict[str, object] = {}
    session_key = getattr(router, "sessionkey", None)
    if isinstance(session_key, (str, int)) and session_key != "":
        result["session_key"] = session_key

    params = getattr(router, "params", None)
    cookies = params.get("cookies") if isinstance(params, dict) else None
    if isinstance(cookies, dict):
        result["cookies"] = {
            str(name): value
            for name, value in cookies.items()
            if isinstance(value, (str, int, float, bool))
        }
    return result


def sensitive_state_value(value: object) -> object:
    """Return a recorder-safe state for a potentially long sensitive value."""
    if isinstance(value, str) and len(value) > MAX_STATE_LENGTH:
        return f"{len(value)} characters"
    return value


def sensitive_state_attributes(value: object) -> dict[str, object]:
    """Preserve a long sensitive value outside the limited state field."""
    if isinstance(value, str) and len(value) > MAX_STATE_LENGTH:
        return {"value": value}
    return {}


def cellular_records(
    data: dict,
    key: str,
    limit: int = 8,
) -> list[dict[str, object]]:
    """Return bounded cellular records containing approved diagnostic keys."""
    containers = [data.get("cellular"), data]
    for container in containers:
        if not isinstance(container, dict):
            continue
        records = container.get(key)
        if not isinstance(records, list):
            continue
        result = []
        for record in records[:limit]:
            if not isinstance(record, dict):
                continue
            filtered = {
                field: record[field]
                for field in CELL_DETAIL_KEYS
                if field in record and record[field] not in (None, "")
                and isinstance(record[field], (str, int, float, bool))
            }
            if filtered:
                result.append(filtered)
        return result
    return []


def lan_hosts(coordinator) -> dict[str, dict]:
    """Return LAN host records keyed by formatted MAC address."""
    block = (coordinator.data or {}).get("lanhosts")
    hosts = block.get("lanhosts") if isinstance(block, dict) else None
    result: dict[str, dict] = {}
    if isinstance(hosts, list):
        for host in hosts:
            if not isinstance(host, dict):
                continue
            mac = host.get("PhysAddress")
            if mac:
                result[format_mac(mac)] = host
    return result
