"""Regression tests for the device registry info (naming, model, versions)."""
from __future__ import annotations

import types

from conftest import load

build = load("device_info").build_device_info

ENTRY = types.SimpleNamespace(entry_id="test-router", data={"host": "https://192.0.2.1"})


def test_falls_back_to_host_without_data():
    for data in (None, {}, {"device": None}, {"device": {"DeviceInfo": {"ModelName": " "}}}):
        info = build(ENTRY, data)
        assert info["name"] == "Zyxel (https://192.0.2.1)"
        assert info["model"] == ""
        assert info["sw_version"] is None
        assert info["serial_number"] is None


def test_names_the_device_after_the_model():
    for source in ("device", "cardpage"):
        info = build(
            ENTRY,
            {source: {"DeviceInfo": {"ModelName": " NR7302 ", "SoftwareVersion": " 1.0 "}}},
        )
        assert info["name"] == "Zyxel NR7302"
        assert info["model"] == "NR7302"
        assert info["sw_version"] == "1.0"
        assert info["identifiers"] == {("ha_zyxel", ENTRY.entry_id)}
        assert info["configuration_url"] == ENTRY.data["host"]


def test_device_section_wins_and_cardpage_fills_the_gaps():
    info = build(
        ENTRY,
        {
            "device": {"DeviceInfo": {"ModelName": "NR7302"}},
            "cardpage": {"DeviceInfo": {"ModelName": "fallback", "SoftwareVersion": "1.0"}},
        },
    )
    assert info["name"] == "Zyxel NR7302"
    assert info["sw_version"] == "1.0"


def test_empty_device_model_falls_through_to_cardpage():
    info = build(
        ENTRY,
        {
            "device": {"DeviceInfo": {"ModelName": ""}},
            "cardpage": {"DeviceInfo": {"ModelName": "NR7302"}},
        },
    )
    assert info["model"] == "NR7302"


def test_manufacturer_hardware_and_serial_from_device_info():
    info = build(
        ENTRY,
        {
            "device": {
                "DeviceInfo": {
                    "ModelName": "EX5601-T1",
                    "Manufacturer": "Zyxel Communications",
                    "HardwareVersion": "V1.0",
                    "SerialNumber": "S2401234567",
                }
            }
        },
    )
    assert info["manufacturer"] == "Zyxel Communications"
    assert info["hw_version"] == "V1.0"
    assert info["serial_number"] == "S2401234567"


def test_manufacturer_defaults_to_zyxel():
    info = build(ENTRY, {"device": {"DeviceInfo": {"ModelName": "NR7302"}}})
    assert info["manufacturer"] == "Zyxel"
    assert info["hw_version"] is None


def test_numeric_values_are_stringified():
    info = build(ENTRY, {"device": {"DeviceInfo": {"ModelName": 7302, "SerialNumber": 123456}}})
    assert info["model"] == "7302"
    assert info["serial_number"] == "123456"


def test_whitespace_values_are_ignored():
    info = build(
        ENTRY,
        {"device": {"DeviceInfo": {"ModelName": "  ", "SerialNumber": "   ", "HardwareVersion": " "}}},
    )
    assert info["model"] == ""
    assert info["serial_number"] is None
    assert info["hw_version"] is None


def test_already_flattened_keys_are_used():
    # Sub-device contributors pass flattened data; both shapes must work.
    info = build(ENTRY, {"device.DeviceInfo.ModelName": "NR5307"})
    assert info["model"] == "NR5307"
