"""Shared helpers for the Zyxel integration."""
from __future__ import annotations

from homeassistant.helpers.device_registry import format_mac


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
