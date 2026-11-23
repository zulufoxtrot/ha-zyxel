"""Regression tests against a fake Zyxel modem.

Covers the failures behind #32, #52 and #64: one keep-alive connection instead
of a TLS handshake per request, logout on unload, and bounded requests so that
a mute, slow, trickling or stalled modem cannot pin an executor thread (and,
through it, Home Assistant's UI) for good.

Needs only requests and pycryptodome; Home Assistant is not imported. Run with
``pytest tests/test_fake_modem.py`` or ``python tests/test_fake_modem.py``.

Adapted from the test suite in zulufoxtrot/ha-zyxel#76 by @host-felice.
"""
from __future__ import annotations

import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
import requests
import urllib3

import conftest
from conftest import load

# These tests drive nr7101's real request code, so they need the library (CI
# installs it from the manifest requirement). Home Assistant is never imported.
pytestmark = pytest.mark.skipif(
    not conftest.NR7101_INSTALLED,
    reason="the nr7101 library is not installed",
)
_HAS_READ1 = hasattr(urllib3.response.HTTPResponse, "read1")


def _load_api():
    return load("api")


class FakeModem:
    """An HTTP server that misbehaves on demand."""

    def __init__(self) -> None:
        self.conns = 0
        self.hits: list[str] = []
        self.cookies_seen: list[str | None] = []
        self.encodings: list[str | None] = []
        self.slow: set[str] = set()
        self.trickle: set[str] = set()
        self.drip: set[str] = set()
        self.stall: set[str] = set()
        self.gzip: set[str] = set()
        self.led: bool | None = None  # None: this firmware has no LEDStatus endpoint
        self.led_requests: list[dict] = []
        self.logout_drip = False
        self.login_gate = threading.Event()
        self.login_gate.set()
        modem = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self):
                super().setup()
                modem.conns += 1

            def log_message(self, *args):
                pass

            def _send(self, obj, cookie=None):
                body = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                if cookie:
                    self.send_header("Set-Cookie", cookie)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                url = urlparse(self.path)
                modem.hits.append(url.path + "?" + url.query)
                modem.cookies_seen.append(self.headers.get("Cookie"))
                modem.encodings.append(self.headers.get("Accept-Encoding"))
                if url.path == "/GetInfoNoLogin":
                    return self._send({}, "Session=abc; Path=/")
                if url.path == "/getRSAPublickKey":
                    return self._send({"RSAPublicKey": "None"})
                if url.path == "/cgi-bin/UserLogout":
                    if modem.logout_drip:
                        self.send_response(200)
                        self.send_header("Content-Length", "1000")
                        self.end_headers()
                        try:
                            for _ in range(1000):
                                self.wfile.write(b" ")
                                self.wfile.flush()
                                time.sleep(0.3)
                        except (BrokenPipeError, ConnectionResetError):
                            pass
                        self.close_connection = True
                        return
                    return self._send({"result": "ZCFG_SUCCESS"})
                if url.path == "/cgi-bin/LEDStatus" and modem.led is not None:
                    return self._send({"isactive": modem.led})
                if url.path == "/cgi-bin/DAL":
                    oid = parse_qs(url.query)["oid"][0]
                    if oid in modem.gzip:
                        import gzip as _gz

                        body = _gz.compress(
                            json.dumps(
                                {"result": "ZCFG_SUCCESS", "Object": [{"oid": oid}]}
                            ).encode()
                        )
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Encoding", "gzip")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                        return
                    if oid in modem.drip or oid in modem.stall:
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", "100")
                        self.end_headers()
                        self.wfile.flush()
                        try:
                            for _ in range(100):
                                if oid in modem.drip:
                                    self.wfile.write(b" ")
                                    self.wfile.flush()
                                time.sleep(0.3)
                        except (BrokenPipeError, ConnectionResetError):
                            pass
                        self.close_connection = True
                        return
                    if oid in modem.trickle:
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Transfer-Encoding", "chunked")
                        self.end_headers()
                        try:
                            for _ in range(40):
                                self.wfile.write(b"1\r\n \r\n")
                                self.wfile.flush()
                                time.sleep(0.5)
                        except (BrokenPipeError, ConnectionResetError):
                            self.close_connection = True
                            return
                        return
                    if oid in modem.slow:
                        time.sleep(3 if len(modem.slow) == 1 else 0.8)
                    return self._send(
                        {"result": "ZCFG_SUCCESS", "Object": [{"oid": oid, "Index": 1, "wlEnable": True}]},
                        "Junk=zzz; Path=/",
                    )
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_PUT(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                modem.hits.append("PUT " + self.path)
                if urlparse(self.path).path == "/cgi-bin/LEDStatus":
                    modem.led = json.loads(body)["status"]
                    modem.led_requests.append({"status": modem.led})
                    return self._send({"isactive": modem.led})
                return self._send({"result": "ZCFG_SUCCESS"})

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                modem.hits.append(self.path)
                modem.login_gate.wait()
                return self._send({"sessionkey": "k1"})

        ThreadingHTTPServer.handle_error = lambda *a: None  # clients time out on purpose
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def modem():
    fake = FakeModem()
    yield fake
    fake.close()


@pytest.fixture()
def api():
    module = _load_api()
    saved = {
        name: getattr(module, name)
        for name in ("REQUEST_TIMEOUT", "SETUP_TIMEOUT", "LOCK_TIMEOUT", "POLL_BUDGET")
    }
    yield module
    for name, value in saved.items():
        setattr(module, name, value)


def test_poll_reuses_one_connection(api, modem):
    router = api.create_router(modem.url, "admin", "pw")
    data = api.fetch_status(router)
    assert {"device", "nat_rules", "wlan_ssids"} <= data.keys()

    modem.conns = 0
    modem.hits.clear()
    api.fetch_status(router)
    dal = [hit for hit in modem.hits if hit.startswith("/cgi-bin/DAL")]
    assert len(dal) == 10
    if _HAS_READ1:
        # urllib3 2.x (Home Assistant's floor): no new TLS handshake per request.
        assert modem.conns <= 1
    api.close_router(router)


def test_session_jar_stays_empty_but_login_cookie_is_sent(api, modem):
    router = api.create_router(modem.url, "admin", "pw")
    api.fetch_status(router)
    assert len(router.ha_zyxel_session.cookies) == 0
    assert all("Session=abc" in (cookie or "") for cookie in modem.cookies_seen[-10:])
    api.close_router(router)


def test_unload_logs_out(api, modem):
    router = api.create_router(modem.url, "admin", "pw")
    api.fetch_status(router)
    modem.hits.clear()
    api.close_router(router)
    assert any(hit.startswith("/cgi-bin/UserLogout") for hit in modem.hits)
    assert router.sessionkey is None


def test_mute_modem_does_not_hang_setup(api):
    api.REQUEST_TIMEOUT = (1, 1)
    api.SETUP_TIMEOUT = (1, 1)
    mute = socket.socket()
    mute.bind(("127.0.0.1", 0))
    mute.listen(5)
    try:
        started = time.monotonic()
        with pytest.raises(requests.exceptions.Timeout):
            api.create_router(f"http://127.0.0.1:{mute.getsockname()[1]}", "admin", "pw")
        assert time.monotonic() - started < 10
    finally:
        mute.close()


def test_slow_endpoint_ends_the_poll(api, modem):
    router = api.create_router(modem.url, "admin", "pw")
    api.fetch_status(router)
    # A read timeout (not the poll budget) ends the poll: the remaining
    # endpoints must not be piled on a struggling modem.
    api.REQUEST_TIMEOUT = (10, 1)
    api.POLL_BUDGET = 55
    modem.slow.add("cellwan_status")
    modem.hits.clear()
    with pytest.raises(requests.exceptions.Timeout):
        api.fetch_status(router)
    dal = [hit for hit in modem.hits if hit.startswith("/cgi-bin/DAL")]
    assert len(dal) == 1
    assert router.ha_zyxel_lock.acquire(timeout=0)  # the lock was released
    router.ha_zyxel_lock.release()
    api.close_router(router)


def test_busy_router_fails_instead_of_queueing(api, modem):
    router = api.create_router(modem.url, "admin", "pw")
    api.LOCK_TIMEOUT = 0.3
    held = threading.Event()
    release = threading.Event()

    def holder():
        router.ha_zyxel_lock.acquire()
        held.set()
        release.wait(10)
        router.ha_zyxel_lock.release()

    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    held.wait(5)
    try:
        with pytest.raises(api.ZyxelBusyError):
            api.fetch_status(router)
    finally:
        release.set()
        thread.join(5)


def test_setup_and_login_share_the_session(api, modem):
    modem.conns = 0
    router = api.create_router(modem.url, "admin", "pw")
    api.fetch_status(router)
    assert modem.conns == 2  # setup+login on one, login itself on a fresh one
    api.close_router(router)


def test_dropped_keepalive_read_is_retried(api, modem):
    router = api.create_router(modem.url, "admin", "pw")
    original = router.ha_zyxel_session.get
    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise requests.exceptions.ConnectionError("RemoteDisconnected")
        return original(*args, **kwargs)

    router.ha_zyxel_session.get = flaky
    data = api.fetch_status(router)
    router.ha_zyxel_session.get = original
    assert "cellular" in data
    assert router.sessionkey == "k1"
    api.close_router(router)


def test_poll_budget_bounds_a_slow_router(api, modem):
    router = api.create_router(modem.url, "admin", "pw")
    api.POLL_BUDGET = 2
    modem.slow.update(endpoint for endpoint, _ in api._ENDPOINTS)
    started = time.monotonic()
    try:
        with pytest.raises(requests.exceptions.Timeout):
            api.fetch_status(router)
        assert time.monotonic() - started < 3.5
        assert router.sessionkey == "k1"
    finally:
        modem.slow.clear()
        api.close_router(router)


def test_writes_use_a_fresh_connection(api, modem):
    router = api.create_router(modem.url, "admin", "pw")
    modem.conns = 0
    api.put_object(router, "wlan", {"Index": 1, "wlEnable": True})
    assert modem.conns == 1
    api.close_router(router)


@pytest.mark.skipif(not _HAS_READ1, reason="bounded chunked reads need urllib3 2.x, as pinned by Home Assistant")
def test_trickling_response_stops_at_the_budget(api, modem):
    router = api.create_router(modem.url, "admin", "pw")
    api.POLL_BUDGET = 3
    modem.trickle.add("cellwan_status")
    started = time.monotonic()
    try:
        with pytest.raises(requests.exceptions.Timeout):
            api.fetch_status(router)
        assert time.monotonic() - started < 4.5
    finally:
        modem.trickle.clear()
        api.close_router(router)


def test_router_busy_during_creation(api, modem):
    router = api.create_router(modem.url, "admin", "pw")
    api.LOCK_TIMEOUT = 0.3
    held = threading.Event()
    release = threading.Event()

    def holder():
        router.ha_zyxel_lock.acquire()
        held.set()
        release.wait(10)
        router.ha_zyxel_lock.release()

    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    held.wait(5)
    try:
        with pytest.raises(api.ZyxelBusyError):
            api.create_router(modem.url, "admin", "pw")
    finally:
        release.set()
        thread.join(5)
        api.close_router(router)


def test_close_without_login_is_immediate(api, modem):
    router = api.create_router(modem.url, "admin", "pw")
    router.sessionkey = None
    started = time.monotonic()
    api.close_router(router)
    assert time.monotonic() - started < 0.2


def test_no_new_login_after_close(api, modem):
    router = api.create_router(modem.url, "admin", "pw")
    router.sessionkey = None
    api.close_router(router)
    with pytest.raises(api.ZyxelConnectionError):
        api.fetch_status(router)


def test_mute_modem_at_startup_uses_the_short_setup_timeout(api):
    api.SETUP_TIMEOUT = (1, 1)
    mute = socket.socket()
    mute.bind(("127.0.0.1", 0))
    mute.listen(5)
    try:
        with pytest.raises(requests.exceptions.Timeout):
            api.create_router(f"http://127.0.0.1:{mute.getsockname()[1]}", "admin", "pw")
    finally:
        mute.close()


@pytest.mark.skipif(not _HAS_READ1, reason="bounded drip reads need urllib3 2.x, as pinned by Home Assistant")
def test_dripping_body_stops_at_the_budget(api, modem):
    router = api.create_router(modem.url, "admin", "pw")
    api.fetch_status(router)
    api.POLL_BUDGET = 1.5
    modem.drip.add("cellwan_status")
    started = time.monotonic()
    try:
        with pytest.raises(requests.exceptions.Timeout):
            api.fetch_status(router)
        assert time.monotonic() - started < 3
    finally:
        modem.drip.clear()
        api.close_router(router)


def test_stalled_body_is_a_timeout_and_keeps_the_login(api, modem):
    router = api.create_router(modem.url, "admin", "pw")
    api.fetch_status(router)
    api.REQUEST_TIMEOUT = (10, 1)
    modem.stall.add("cellwan_status")
    modem.hits.clear()
    try:
        with pytest.raises(requests.exceptions.Timeout):
            api.fetch_status(router)
        dal = [hit for hit in modem.hits if hit.startswith("/cgi-bin/DAL")]
        assert len(dal) == 1
        assert router.sessionkey == "k1"
    finally:
        modem.stall.clear()
        api.close_router(router)


def test_close_during_login_logs_out(api, modem):
    router = api.create_router(modem.url, "admin", "pw")
    modem.login_gate.clear()
    modem.hits.clear()
    errors = []

    def poll():
        try:
            api.fetch_status(router)
        except Exception as err:  # noqa: BLE001
            errors.append(err)

    thread = threading.Thread(target=poll)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while "/UserLogin" not in modem.hits and time.monotonic() < deadline:
            time.sleep(0.05)
        api.close_router(router)
    finally:
        modem.login_gate.set()
        thread.join(5)
    assert router.sessionkey is None
    assert any(hit.startswith("/cgi-bin/UserLogout") for hit in modem.hits)
    assert isinstance(errors[0], api.ZyxelConnectionError)


def test_requests_ask_for_uncompressed_replies(api, modem):
    router = api.create_router(modem.url, "admin", "pw")
    api.fetch_status(router)
    assert "identity" in [enc for enc in modem.encodings if enc]
    api.close_router(router)


def test_compressed_reply_is_still_read(api, modem):
    router = api.create_router(modem.url, "admin", "pw")
    modem.gzip.add("status")
    try:
        data = api.fetch_status(router)
        assert data["device"]["oid"] == "status"
    finally:
        modem.gzip.clear()
        api.close_router(router)


def test_logout_with_dripping_reply_does_not_hang(api, modem):
    router = api.create_router(modem.url, "admin", "pw")
    api.fetch_status(router)
    modem.logout_drip = True
    started = time.monotonic()
    try:
        api.close_router(router)
        assert time.monotonic() - started < 2
        assert router.sessionkey is None
    finally:
        modem.logout_drip = False


def test_led_state_is_read_on_every_poll(api, modem):
    modem.led = True
    router = api.create_router(modem.url, "admin", "pw")
    assert api.fetch_status(router)["led"] is True
    modem.led = False
    assert api.fetch_status(router)["led"] is False
    api.close_router(router)


def test_led_is_left_out_when_the_firmware_has_no_led_endpoint(api, modem):
    router = api.create_router(modem.url, "admin", "pw")  # modem.led is None: 404
    data = api.fetch_status(router)
    assert "led" not in data
    assert "device" in data  # the rest of the poll is unaffected
    api.close_router(router)


def test_set_led_sends_the_status_flag(api, modem):
    modem.led = True
    router = api.create_router(modem.url, "admin", "pw")
    assert not router.encryption_required  # the fake modem advertises no RSA key
    api.set_led_enabled(router, False)
    api.set_led_enabled(router, True)
    assert modem.led_requests == [{"status": False}, {"status": True}]
    api.close_router(router)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
