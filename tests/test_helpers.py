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


if __name__ == "__main__":
    unittest.main()
