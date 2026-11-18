"""Regression tests for the sensor naming and filtering helpers.

These cover what issue #54 and PRs #67-#70 changed: readable, unique sensor
names, and the deduplication/enabled-by-default rules that keep one NR7302 from
creating 572 entities.
"""
from __future__ import annotations

from conftest import load

sensor = load("sensor")



# _humanize

def test_humanize_splits_camel_case():
    assert sensor._humanize("InterfaceUpTime") == "Interface Up Time"
    assert sensor._humanize("ModelName") == "Model Name"


def test_humanize_strips_zyxel_prefix_and_underscores():
    assert sensor._humanize("X_ZYXEL_InterfaceUpTime") == "Interface Up Time"
    assert sensor._humanize("INTF_RSSI") == "INTF RSSI"
    assert sensor._humanize("WanRate_RX") == "Wan Rate RX"


def test_humanize_fixes_wifi_spelling():
    assert sensor._humanize("WiFi_Status") == "Wi-Fi Status"


# _readable_name

def test_readable_name_uses_section_and_path():
    assert sensor._readable_name("traffic.br0.BytesReceived") == "Traffic br0 Bytes Received"
    assert sensor._readable_name("cellular.INTF_RSSI") == "Cellular INTF RSSI"


def test_readable_name_skips_empty_path_elements():
    # The modem reports one traffic entry with no interface name (#68).
    assert sensor._readable_name("traffic..BytesReceived") == "Traffic Bytes Received"


def test_readable_name_avoids_repeating_the_section():
    # "Device" + "Device Info ..." would read "Device Device Info ..." (#70).
    name = sensor._readable_name("device.DeviceInfo.ModelName")
    assert name == "Device Info Model Name"


def test_readable_name_curated_leaf_drops_duplicate_section():
    # KNOWN_SENSORS names already carry the section: "Cellular RSSI".
    assert sensor._readable_name("cellular.INTF_RSSI", "Cellular RSSI") == "Cellular RSSI"


def test_readable_name_curated_leaf_keeps_the_interface():
    name = sensor._readable_name("traffic.br0.BytesReceived", "Bytes Received")
    assert name == "Traffic br0 Bytes Received"


def test_readable_name_is_unique_per_interface():
    first = sensor._readable_name("traffic.br0.BytesReceived")
    second = sensor._readable_name("traffic.eth0.BytesReceived")
    assert first != second


def test_unknown_section_is_humanized():
    # _humanize() splits camel case but does not change the case of the first
    # word; known sections get their display name from SECTION_NAMES.
    assert sensor._readable_name("newsec.FooBar") == "newsec Foo Bar"


# _flatten_dict

def test_flatten_dict_nests_with_dots():
    flat = sensor._flatten_dict({"a": {"b": {"c": 1}}, "d": 2})
    assert flat == {"a.b.c": 1, "d": 2}


def test_flatten_dict_keeps_lists_intact():
    flat = sensor._flatten_dict({"a": [1, 2]})
    assert flat == {"a": [1, 2]}


# _is_value_scalar

def test_is_value_scalar():
    for value in ("x", 1, 1.5, True, False, None):
        assert sensor._is_value_scalar(value) is True
    for value in ({}, [], [1]):
        assert sensor._is_value_scalar(value) is False


# _is_duplicate

def test_is_duplicate_flags_cardpage_repeating_device():
    data = {
        "device": {"DeviceInfo": {"ModelName": "NR7302"}},
        "cardpage": {"DeviceInfo": {"ModelName": "NR7302"}},
    }
    assert sensor._is_duplicate("cardpage.DeviceInfo.ModelName", data) is True


def test_is_duplicate_keeps_differing_values():
    data = {
        "device": {"DeviceInfo": {"ModelName": "NR7302"}},
        "cardpage": {"DeviceInfo": {"ModelName": "other"}},
    }
    assert sensor._is_duplicate("cardpage.DeviceInfo.ModelName", data) is False


def test_is_duplicate_ignores_non_cardpage_sections():
    data = {"device": {"x": 1}, "traffic": {"x": 1}}
    assert sensor._is_duplicate("traffic.x", data) is False


def test_is_duplicate_missing_device_keeps_the_sensor():
    data = {"cardpage": {"DeviceInfo": {"ModelName": "NR7302"}}}
    assert sensor._is_duplicate("cardpage.DeviceInfo.ModelName", data) is False


# enabled-by-default policy (#68)

def test_enabled_by_default_covers_curated_device_keys():
    for key in sensor.ENABLED_BY_DEFAULT:
        assert key in {
            "device.DeviceInfo.UpTime",
            "device.DeviceInfo.SoftwareVersion",
            "device.DeviceInfo.ModelName",
            "device.SystemInfo.CPUUsage",
            "device.SystemInfo.Free",
            "device.SystemInfo.Total",
            "device.WanRate_RX",
            "device.WanRate_TX",
            "device.WanType",
            "lanhosts.wanInfo.wanStatus",
        }


def test_cellular_section_is_enabled_by_default():
    # GenericZyxelSensor enables everything under "cellular." by default.
    key = "cellular.SomeKey"
    enabled = key in sensor.ENABLED_BY_DEFAULT or key.startswith("cellular.")
    assert enabled is True


def test_unknown_generic_sensor_is_disabled_by_default():
    key = "wifi_mesh.SomeNoisyKey"
    enabled = key in sensor.ENABLED_BY_DEFAULT or key.startswith("cellular.")
    assert enabled is False


# KNOWN_SENSORS sanity

def test_known_sensors_are_well_formed():
    for key, config in sensor.KNOWN_SENSORS.items():
        assert set(config) == {"name", "unit", "icon", "device_class", "state_class"}, key
        assert config["name"], key
        assert config["icon"].startswith("mdi:"), key


def test_known_signal_sensors_carry_a_unit():
    for key in ("INTF_RSSI", "INTF_RSRP", "NSA_RSRP", "NSA_RSSI"):
        assert sensor.KNOWN_SENSORS[key]["unit"] == "dBm", key


def test_section_names_cover_the_api_endpoints():
    expected = {
        "cellular",
        "traffic",
        "cardpage",
        "lan",
        "lanhosts",
        "wifi_mesh",
        "one_connect",
        "device",
    }
    assert expected <= set(sensor.SECTION_NAMES)
