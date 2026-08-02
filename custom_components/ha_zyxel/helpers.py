"""Shared helpers for the Zyxel integration."""
from __future__ import annotations

from homeassistant.helpers.device_registry import format_mac


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
