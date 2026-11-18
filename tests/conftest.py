"""Shared helpers for the standalone tests.

The tests run without Home Assistant. Integration modules are loaded straight
from their file paths (so the package __init__ never runs), and only the small
Home Assistant surface they touch at import time is faked.
"""
from __future__ import annotations

import importlib.abc
import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG_DIR = ROOT / "custom_components" / "ha_zyxel"
PKG_NAME = "_ha_zyxel_tests"


class Stub:
    """Base class for faked Home Assistant types."""

    def __init__(self, *args, **kwargs):
        pass


def stub_module(name: str, **attrs):
    """Register a module (and its parents) in sys.modules."""
    parts = name.split(".")
    for index in range(1, len(parts) + 1):
        full = ".".join(parts[:index])
        if full not in sys.modules:
            module = types.ModuleType(full)
            module.__path__ = []  # pretend to be a package for children
            sys.modules[full] = module
    module = sys.modules[name]
    for attr, value in attrs.items():
        setattr(module, attr, value)
    return module


def install_home_assistant_stubs() -> None:
    """Fake the Home Assistant names imported at module load."""
    stub_module("homeassistant")
    stub_module(
        "homeassistant.exceptions",
        ConfigEntryNotReady=type("ConfigEntryNotReady", (Exception,), {}),
        HomeAssistantError=type("HomeAssistantError", (Exception,), {}),
    )
    stub_module(
        "homeassistant.const",
        CONF_HOST="host",
        CONF_PASSWORD="password",
        CONF_USERNAME="username",
    )
    stub_module("homeassistant.config_entries", ConfigEntry=Stub)
    stub_module("homeassistant.core", HomeAssistant=Stub)
    stub_module("homeassistant.helpers.entity", DeviceInfo=dict)
    stub_module("homeassistant.helpers.entity_platform", AddEntitiesCallback=Stub)
    stub_module(
        "homeassistant.helpers.update_coordinator",
        CoordinatorEntity=type("CoordinatorEntity", (Stub,), {}),
        DataUpdateCoordinator=Stub,
        UpdateFailed=Exception,
    )
    sensor = stub_module("homeassistant.components.sensor")
    sensor.SensorEntity = type("SensorEntity", (Stub,), {})
    sensor.SensorDeviceClass = types.SimpleNamespace(
        SIGNAL_STRENGTH="signal_strength",
        TEMPERATURE="temperature",
        DATA_SIZE="data_size",
    )
    sensor.SensorStateClass = types.SimpleNamespace(
        MEASUREMENT="measurement",
        TOTAL_INCREASING="total_increasing",
    )
    stub_module("homeassistant.components.button", ButtonEntity=Stub)
    stub_module("homeassistant.components.switch", SwitchEntity=Stub)
    stub_module("async_timeout")
    stub_module("voluptuous", Schema=Stub, Required=lambda *args, **kwargs: None)


_NR7101_AVAILABLE: bool | None = None


def _has_nr7101() -> bool:
    global _NR7101_AVAILABLE
    if _NR7101_AVAILABLE is None:
        _NR7101_AVAILABLE = importlib.util.find_spec("nr7101") is not None
    return _NR7101_AVAILABLE


NR7101_INSTALLED = _has_nr7101()


def install_nr7101_stub() -> None:
    """Provide the nr7101 library when it is not installed."""
    if NR7101_INSTALLED:
        return
    lib = stub_module("nr7101")
    lib.__path__ = []
    inner = stub_module("nr7101.nr7101")
    inner.NR7101 = Stub
    lib.nr7101 = inner


class _SiblingFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Resolve custom_components.ha_zyxel.X to the file, skipping __init__.py."""

    prefix = "custom_components.ha_zyxel."

    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith(self.prefix):
            return None
        candidate = PKG_DIR / f"{fullname[len(self.prefix):]}.py"
        if not candidate.exists():
            return None
        return importlib.util.spec_from_file_location(fullname, candidate, loader=self)

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        origin = Path(module.__spec__.origin)
        exec(compile(origin.read_text(), str(origin), "exec"), module.__dict__)


def _install_sibling_imports() -> None:
    stub_module("custom_components").__path__ = [str(ROOT / "custom_components")]
    stub_module("custom_components.ha_zyxel").__path__ = [str(PKG_DIR)]
    if not any(isinstance(finder, _SiblingFinder) for finder in sys.meta_path):
        sys.meta_path.insert(0, _SiblingFinder())


def load(module: str):
    """Load custom_components/ha_zyxel/<module>.py without its package __init__."""
    install_home_assistant_stubs()
    install_nr7101_stub()
    _install_sibling_imports()
    full = f"{PKG_NAME}.{module}"
    if full in sys.modules:
        return sys.modules[full]
    if PKG_NAME not in sys.modules:
        pkg = types.ModuleType(PKG_NAME)
        pkg.__path__ = [str(PKG_DIR)]
        sys.modules[PKG_NAME] = pkg
    spec = importlib.util.spec_from_file_location(full, PKG_DIR / f"{module}.py")
    module_obj = importlib.util.module_from_spec(spec)
    sys.modules[full] = module_obj
    spec.loader.exec_module(module_obj)
    return module_obj
