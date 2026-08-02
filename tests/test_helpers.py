"""Tests for Zyxel integration helpers."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import unittest


def _load_helpers():
    """Load helpers with the small Home Assistant dependency stubbed."""
    homeassistant = ModuleType("homeassistant")
    helpers = ModuleType("homeassistant.helpers")
    device_registry = ModuleType("homeassistant.helpers.device_registry")
    device_registry.format_mac = lambda mac: mac.lower().replace("-", ":")
    sys.modules["homeassistant"] = homeassistant
    sys.modules["homeassistant.helpers"] = helpers
    sys.modules["homeassistant.helpers.device_registry"] = device_registry

    path = (
        Path(__file__).parents[1]
        / "custom_components"
        / "ha_zyxel"
        / "helpers.py"
    )
    spec = importlib.util.spec_from_file_location("ha_zyxel_helpers", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LanHostsTest(unittest.TestCase):
    """Verify LAN host extraction."""

    def test_normalizes_and_keys_valid_hosts(self):
        module = _load_helpers()
        coordinator = type(
            "Coordinator",
            (),
            {
                "data": {
                    "lanhosts": {
                        "lanhosts": [
                            {
                                "PhysAddress": "AA-BB-CC-DD-EE-FF",
                                "Active": True,
                            }
                        ]
                    }
                }
            },
        )()

        self.assertEqual(
            module.lan_hosts(coordinator),
            {
                "aa:bb:cc:dd:ee:ff": {
                    "PhysAddress": "AA-BB-CC-DD-EE-FF",
                    "Active": True,
                }
            },
        )

    def test_ignores_malformed_records(self):
        module = _load_helpers()
        coordinator = type(
            "Coordinator",
            (),
            {
                "data": {
                    "lanhosts": {
                        "lanhosts": [None, "invalid", {}, {"Active": True}]
                    }
                }
            },
        )()

        self.assertEqual(module.lan_hosts(coordinator), {})

    def test_handles_missing_data(self):
        module = _load_helpers()
        coordinator = type("Coordinator", (), {"data": None})()

        self.assertEqual(module.lan_hosts(coordinator), {})


class PollingBackoffTrackerTest(unittest.TestCase):
    """Verify polling transition notifications are emitted once."""

    def test_reports_only_backoff_and_recovery_transitions(self):
        module = _load_helpers()
        tracker = module.PollingBackoffTracker()

        self.assertTrue(tracker.enter())
        self.assertFalse(tracker.enter())
        self.assertTrue(tracker.recover())
        self.assertFalse(tracker.recover())


class SelectUniqueFieldsTest(unittest.TestCase):
    """Verify canonical router field selection."""

    def test_prefers_cellular_and_collapses_duplicates(self):
        module = _load_helpers()
        data = {
            "device_info": {"Cell": {"Status": "stale"}},
            "device": {"Cell": {"Status": "duplicate"}},
            "cardpage": {"Cell": {"Status": "Up"}},
            "cellular": {"Status": "Connected"},
        }

        self.assertEqual(
            module.select_unique_fields(data, {"Status"}),
            {"Status": ("cellular.Status", "Connected")},
        )

    def test_skips_unrequested_and_non_scalar_values(self):
        module = _load_helpers()
        data = {
            "cellular": {
                "Band": "LTE_BC20",
                "Networks": ["LTE", "NR"],
                "Details": {"CellId": 1234},
            }
        }

        self.assertEqual(
            module.select_unique_fields(data, {"Band", "Networks"}),
            {"Band": ("cellular.Band", "LTE_BC20")},
        )

    def test_falls_back_when_preferred_source_is_empty(self):
        module = _load_helpers()
        data = {
            "cellular": {"UpTime": None},
            "cardpage": {"DeviceInfo": {"UpTime": 3600}},
        }

        self.assertEqual(
            module.select_unique_fields(data, {"UpTime"}),
            {"UpTime": ("cardpage.DeviceInfo.UpTime", 3600)},
        )


class DeviceMetadataTest(unittest.TestCase):
    """Verify safe router metadata extraction."""

    def test_selects_metadata_without_sensitive_identifiers(self):
        module = _load_helpers()
        data = {
            "device": {
                "DeviceInfo": {
                    "ModelName": "NR7101",
                    "SoftwareVersion": "1.2.3",
                    "HardwareVersion": "A1",
                    "SerialNumber": "S123",
                    "IMEI": "secret-imei",
                    "USIM_IMSI": "secret-imsi",
                }
            }
        }

        self.assertEqual(
            module.device_metadata(data),
            {
                "model": "NR7101",
                "sw_version": "1.2.3",
                "hw_version": "A1",
                "serial_number": "S123",
            },
        )


class SensitiveIdentifiersTest(unittest.TestCase):
    """Verify cellular identifier extraction remains strictly allowlisted."""

    def test_selects_identifiers_without_credentials(self):
        module = _load_helpers()
        data = {
            "device": {
                "IMEI": "123456789012345",
                "USIM_IMSI": "234150999999999",
                "USIM_ICCID": "8944100000000000000",
                "Password": "do-not-expose",
                "SessionKey": "do-not-expose",
                "Cookie": "do-not-expose",
            }
        }

        self.assertEqual(
            module.sensitive_identifiers(data),
            {
                "imei": ("device.IMEI", "123456789012345"),
                "imsi": ("device.USIM_IMSI", "234150999999999"),
                "iccid": ("device.USIM_ICCID", "8944100000000000000"),
            },
        )

    def test_blocks_identifiers_and_secrets_from_generic_sensors(self):
        module = _load_helpers()

        for path in (
            "device.IMEI",
            "device.USIM_IMSI",
            "device.USIM_ICCID",
            "auth.Password",
            "auth.Session_Key",
            "auth.AccessToken",
            "auth.Cookie",
        ):
            with self.subTest(path=path):
                self.assertTrue(module.is_sensitive_field(path))

        self.assertFalse(module.is_sensitive_field("cellular.INTF_RSRP"))

    def test_classifies_sensitive_field_categories_independently(self):
        module = _load_helpers()

        self.assertTrue(module.is_cellular_identifier_field("device.IMEI"))
        self.assertFalse(module.is_secret_field("device.IMEI"))
        self.assertTrue(module.is_secret_field("auth.Session_Key"))
        self.assertFalse(
            module.is_cellular_identifier_field("auth.Session_Key")
        )

    def test_blocks_secrets_named_by_parent_path_segments(self):
        module = _load_helpers()

        for path in (
            "auth.credentials.value",
            "headers.authorization.value",
            "client.api_key.value",
            "session.session_id.value",
            "account.pwd.value",
        ):
            with self.subTest(path=path):
                self.assertTrue(module.is_secret_field(path))

    def test_selects_runtime_session_data_without_login_parameters(self):
        module = _load_helpers()
        router = type(
            "Router",
            (),
            {
                "sessionkey": "active-session",
                "params": {
                    "cookies": {"Session": "cookie-value"},
                    "timeout": 5,
                },
                "login_params": {
                    "Input_Account": "admin",
                    "Input_Passwd": "encoded-password",
                },
            },
        )()

        self.assertEqual(
            module.router_session_data(router),
            {
                "session_key": "active-session",
                "cookies": {"Session": "cookie-value"},
            },
        )

    def test_moves_long_sensitive_values_out_of_state(self):
        module = _load_helpers()
        long_value = "x" * 256

        self.assertEqual(
            module.sensitive_state_value(long_value), "256 characters"
        )
        self.assertEqual(
            module.sensitive_state_attributes(long_value),
            {"value": long_value},
        )
        self.assertEqual(module.sensitive_state_value("short"), "short")
        self.assertEqual(module.sensitive_state_attributes("short"), {})


class CellularRecordsTest(unittest.TestCase):
    """Verify bounded advanced cellular diagnostics."""

    def test_filters_sensitive_and_unknown_fields(self):
        module = _load_helpers()
        data = {
            "cellular": {
                "SCC_Info": [
                    {
                        "Band": "LTE_BC3",
                        "RSRP": -91,
                        "IMEI": "secret",
                        "Unknown": "ignored",
                        "ConnectionMode": {"nested": "ignored"},
                    }
                ]
            }
        }

        self.assertEqual(
            module.cellular_records(data, "SCC_Info"),
            [{"Band": "LTE_BC3", "RSRP": -91}],
        )

    def test_caps_record_count(self):
        module = _load_helpers()
        data = {"NBR_Info": [{"PhyCellID": value} for value in range(20)]}

        self.assertEqual(len(module.cellular_records(data, "NBR_Info")), 8)


if __name__ == "__main__":
    unittest.main()
