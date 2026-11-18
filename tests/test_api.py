"""Regression tests for the API helpers that do not need a modem."""
from __future__ import annotations

import base64
import json
import threading

import pytest

from conftest import load

api = load("api")


class FakeCipherRouter:
    """Enough of an NR7101 for the encryption helpers."""

    def __init__(self):
        from Crypto.Cipher import AES

        self.key = b"0" * 16
        self.aes_key = self.key
        self.AES = AES
        self.encryption_required = True
        self.sessionkey = "k1"
        self.url = "https://192.0.2.1"
        self.params = {}

    def decrypt_response(self, data):
        iv = base64.b64decode(data["iv"])
        cipher = self.AES.new(self.key, self.AES.MODE_CBC, iv[:16])
        plain = cipher.decrypt(base64.b64decode(data["content"]))
        return json.loads(plain[:-plain[-1]].decode())


def test_parse_traffic_object_keys_by_interface():
    obj = {
        "ipIface": [{"X_ZYXEL_IfName": "br0"}, {"NoName": 1}],
        "ipIfaceSt": [{"BytesSent": 10}, {"BytesSent": 20}],
    }
    assert api._parse_traffic_object(obj) == {"br0": {"BytesSent": 10}}


def test_parse_traffic_object_tolerates_missing_shape():
    assert api._parse_traffic_object(None) == {}
    assert api._parse_traffic_object({}) == {}
    assert api._parse_traffic_object({"ipIface": []}) == {}


def test_endpoints_are_unique_and_ordered():
    endpoints = [endpoint for endpoint, _ in api._ENDPOINTS]
    assert len(endpoints) == len(set(endpoints))
    assert "cellwan_status" in endpoints
    assert ("Traffic_Status", "traffic") in api._ENDPOINTS


def test_host_lock_is_shared_per_host():
    first = api.get_host_lock("https://192.0.2.1")
    same = api.get_host_lock("HTTPS://192.0.2.1/")
    other = api.get_host_lock("https://192.0.2.2")
    assert first is same
    assert first is not other


def test_host_lock_serialises_threads():
    lock = api.get_host_lock("https://192.0.2.9")
    events = []

    def worker():
        with lock:
            events.append("in")
            threading.Event().wait(0.05)
            events.append("out")

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert events == ["in", "out", "in", "out", "in", "out"]


def test_encrypt_body_round_trips():
    router = FakeCipherRouter()
    payload = {"oid": "wlan", "Index": 2, "wlEnable": True}
    body = api._encrypt_body(router, payload)
    assert json.loads(body).keys() == {"content", "iv"}
    assert api._decode(router, json.loads(body)) == payload


def test_encrypt_body_uses_a_fresh_iv():
    router = FakeCipherRouter()
    first = json.loads(api._encrypt_body(router, {"a": 1}))["iv"]
    second = json.loads(api._encrypt_body(router, {"a": 1}))["iv"]
    assert first != second


def test_decode_accepts_plain_json():
    router = FakeCipherRouter()
    router.encryption_required = False
    assert api._decode(router, {"result": "ZCFG_SUCCESS"}) == {"result": "ZCFG_SUCCESS"}


def test_request_timeout_shrinks_to_the_poll_budget():
    import time

    router = FakeCipherRouter()
    router.ha_zyxel_deadline = None
    assert api._request_timeout(router) == api.REQUEST_TIMEOUT

    router.ha_zyxel_deadline = time.monotonic() + 5
    connect, read = api._request_timeout(router)
    assert 0 < connect <= 5
    assert 0 < read <= 5

    router.ha_zyxel_deadline = time.monotonic() - 1
    with pytest.raises(api.requests.exceptions.Timeout):
        api._request_timeout(router)


def test_default_timeouts_are_set():
    # requests has no default timeout; every call must be bounded.
    assert api.REQUEST_TIMEOUT[0] > 0 and api.REQUEST_TIMEOUT[1] > 0
    assert api.SETUP_TIMEOUT[0] > 0 and api.SETUP_TIMEOUT[1] > 0
    assert api.LOCK_TIMEOUT > 0
    assert api.POLL_BUDGET > 0
    # A poll must finish inside the coordinator timeout (__init__.py: 90 s).
    assert api.POLL_BUDGET + api.REQUEST_TIMEOUT[1] <= 90


def test_set_wifi_enabled_sends_only_index_and_flag():
    calls = []

    class Router(FakeCipherRouter):
        ha_zyxel_lock = threading.RLock()

    router = Router()

    def fake_put(object_router, oid, payload):
        calls.append((oid, payload))
        return {"result": "ZCFG_SUCCESS"}

    real_put = api.put_object
    real_objects = api.get_objects
    api.put_object = fake_put
    # Report the change as applied so the retry loop exits on the first pass.
    api.get_objects = lambda router, oid: [{"Index": 1, "wlEnable": True}]
    try:
        api.set_wifi_enabled(router, 1, True)
    finally:
        api.put_object = real_put
        api.get_objects = real_objects
    assert calls == [("wlan", {"Index": 1, "wlEnable": True})]


def test_set_port_forward_keeps_the_other_fields():
    class Router(FakeCipherRouter):
        ha_zyxel_lock = threading.RLock()

    router = Router()
    calls = []

    def fake_put(object_router, oid, payload):
        calls.append((oid, payload))
        return {"result": "ZCFG_SUCCESS"}

    real_put = api.put_object
    api.put_object = fake_put
    try:
        rule = {"Index": 3, "Description": "web", "Enable": True, "Protocol": "TCP"}
        api.set_port_forward_enabled(router, rule, False)
    finally:
        api.put_object = real_put
    assert calls == [("nat", {"Index": 3, "Description": "web", "Enable": False, "Protocol": "TCP"})]


def test_set_port_forward_raises_when_refused():
    class Router(FakeCipherRouter):
        ha_zyxel_lock = threading.RLock()

    router = Router()
    real_put = api.put_object
    api.put_object = lambda *args, **kwargs: {"result": "ZCFG_ERROR"}
    try:
        with pytest.raises(api.ZyxelConnectionError):
            api.set_port_forward_enabled(router, {"Index": 1, "Enable": False}, True)
    finally:
        api.put_object = real_put
