"""Synchronous helpers for communicating with Zyxel devices."""

from __future__ import annotations

import base64
import http.cookiejar
import json
import logging
import os
import threading
import zlib
import time
from typing import Any

import requests
import urllib3
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from nr7101 import nr7101

_LOGGER = logging.getLogger(__name__)

_ENDPOINTS = (
    ("cellwan_status", "cellular"),
    ("Traffic_Status", "traffic"),
    ("cardpage_status", "cardpage"),
    ("lan", "lan"),
    ("lanhosts", "lanhosts"),
    ("wifi_easy_mesh", "wifi_mesh"),
    ("one_connect", "one_connect"),
    ("status", "device"),
)


# requests waits forever by default; a modem that accepts the connection and
# never answers would pin an executor thread (and the host lock) for good.
REQUEST_TIMEOUT = (10, 30)  # connect, read (seconds)
# The two tiny requests nr7101 sends before login: a mute modem at startup
# should not hold up Home Assistant for long.
SETUP_TIMEOUT = (5, 10)
# A poll that finds the previous operation still running gives up instead of
# queueing another executor thread behind it. The wait counts against
# POLL_BUDGET, so a long wait only shortens the poll.
LOCK_TIMEOUT = 45
# No new request or read starts once a poll has run this long, lock wait
# included. The one in progress can still take a read timeout, so a poll ends
# within POLL_BUDGET + 30 s: before the coordinator's 90 s limit abandons it.
POLL_BUDGET = 55

# The modem keeps a single login session per user: a new login invalidates the
# AES key of the previous one, so any request still in flight decrypts to
# garbage ("'utf-8' codec can't decode byte ..."). Serialize every operation
# that touches a given modem, across config flow, polling and button presses.
_HOST_LOCKS: dict[str, threading.RLock] = {}
_HOST_LOCKS_GUARD = threading.Lock()


def get_host_lock(host: str) -> threading.RLock:
    """Return the lock shared by all router instances for the same host."""
    key = host.lower().rstrip("/")
    with _HOST_LOCKS_GUARD:
        return _HOST_LOCKS.setdefault(key, threading.RLock())


class ZyxelAuthenticationError(Exception):
    """Raised when the router rejects the supplied credentials."""


class ZyxelConnectionError(Exception):
    """Raised when no usable data can be fetched from the router."""


class ZyxelBusyError(ZyxelConnectionError):
    """Raised when the previous request to the router has not finished yet."""


def create_router(host: str, username: str, password: str) -> Any:
    """Create a router with connection state isolated from other instances.

    nr7101 currently uses a mutable dictionary as its default ``params``
    argument. Passing a new dictionary explicitly prevents cookies from a
    config-flow instance being reused by the config-entry instance, where
    they would be paired with a different AES key.
    """
    # One keep-alive connection for every request, login included, instead of
    # a new TLS handshake each time. Cookies stay in router.params as before:
    # the session's jar keeps none.
    session = requests.Session()
    session.cookies.set_policy(http.cookiejar.DefaultCookiePolicy(allowed_domains=[]))
    lock = get_host_lock(host)
    # The constructor already talks to the modem: keep it out of a running poll.
    if not lock.acquire(timeout=LOCK_TIMEOUT):
        raise ZyxelBusyError("The router is busy with another request")
    try:
        # nr7101 passes these params to every request it makes.
        router = nr7101.NR7101(
            host, username, password, {"timeout": SETUP_TIMEOUT}, session=session
        )
    finally:
        lock.release()
    router.params["timeout"] = REQUEST_TIMEOUT
    router.ha_zyxel_lock = lock
    router.ha_zyxel_session = session
    router.ha_zyxel_deadline = None
    router.ha_zyxel_closed = False
    return router


def _request_timeout(router: Any) -> tuple[float, float]:
    """Return the timeout for the next request, within the poll's budget."""
    if router.ha_zyxel_deadline is None:
        return REQUEST_TIMEOUT
    left = router.ha_zyxel_deadline - time.monotonic()
    if left <= 0:
        raise requests.exceptions.Timeout("The poll ran out of time")
    return (min(REQUEST_TIMEOUT[0], left), min(REQUEST_TIMEOUT[1], left))


def close_router(router: Any) -> None:
    """Log out and close the connection, best effort (the modem may be down)."""
    # Set first: a login still in flight sees it and logs out itself.
    router.ha_zyxel_closed = True
    if not getattr(router, "sessionkey", None):
        router.ha_zyxel_session.close()  # Not logged in: nothing to wait for.
        return
    # A running poll ends within POLL_BUDGET + one read: wait, then log out.
    locked = router.ha_zyxel_lock.acquire(timeout=POLL_BUDGET + REQUEST_TIMEOUT[1])
    try:
        if locked:
            _logout(router)
        else:
            _LOGGER.debug("Zyxel router still busy, not logging out")
        router.ha_zyxel_session.close()
    finally:
        if locked:
            router.ha_zyxel_lock.release()


def _logout(router: Any) -> None:
    """Log out on a new connection, best effort (the modem may be down)."""
    _drop_kept_alive_connection(router)
    url = f"{router.url}/cgi-bin/UserLogout?sessionkey={router.sessionkey}"
    try:
        # Not nr7101's logout(): it reads the whole reply, which a trickling
        # modem can stretch forever. The reply is not needed past the headers.
        with router.ha_zyxel_session.get(url, **{**router.params, "stream": True}):
            pass
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("Zyxel logout failed: %s", err)
    router.sessionkey = None


def authenticate(router: Any) -> None:
    """Log in and verify that nr7101 received a session key."""
    if router.ha_zyxel_closed:
        raise ZyxelConnectionError("The router connection was closed")
    try:
        router.params["timeout"] = _request_timeout(router)
        _drop_kept_alive_connection(router)  # Logins are rare; never on a stale socket.
        login_success = router.login()
    except Exception as err:
        raise ZyxelConnectionError("Unable to complete router login") from err
    finally:
        router.params["timeout"] = REQUEST_TIMEOUT

    if not login_success or not getattr(router, "sessionkey", None):
        raise ZyxelAuthenticationError("The router rejected the credentials")
    if router.ha_zyxel_closed:  # Closed while this login was in flight.
        _logout(router)
        router.ha_zyxel_session.close()
        raise ZyxelConnectionError("The router connection was closed")


def _parse_traffic_object(obj: dict[str, Any] | None) -> dict[str, Any]:
    """Convert Traffic_Status interface arrays to a keyed dictionary."""
    if not obj or "ipIface" not in obj or "ipIfaceSt" not in obj:
        return {}

    return {
        interface["X_ZYXEL_IfName"]: status
        for interface, status in zip(obj["ipIface"], obj["ipIfaceSt"])
        if "X_ZYXEL_IfName" in interface
    }


def _fetch_available_endpoints(
    router: Any,
) -> tuple[dict[str, Any], Exception | None]:
    """Fetch every supported endpoint once without an unbounded retry loop."""
    result: dict[str, Any] = {}
    last_error: Exception | None = None

    for endpoint, key in _ENDPOINTS:
        try:
            objects = get_objects(router, endpoint)
            data = objects[0] if objects else None
            if endpoint == "Traffic_Status":
                data = _parse_traffic_object(data)
            if data:
                result[key] = data
        except requests.exceptions.Timeout:
            # The modem is not answering: stop here rather than pile the
            # remaining endpoints onto it.
            raise
        except Exception as err:  # Different firmware exposes different endpoints.
            last_error = err
            _LOGGER.debug("Zyxel endpoint %s is unavailable: %s", endpoint, err)

            # Invalid UTF-8 after AES decryption means the cookie/session and
            # AES key no longer match. Continuing would emit the same error for
            # every endpoint, so reauthenticate immediately.
            if "Failed to process decrypted response" in str(err):
                break

    return result, last_error


def fetch_status(router: Any) -> dict[str, Any]:
    """Return available router data, reauthenticating once when necessary."""
    start = time.monotonic()
    if not router.ha_zyxel_lock.acquire(timeout=LOCK_TIMEOUT):
        raise ZyxelBusyError("The previous request to the router is still running")
    router.ha_zyxel_deadline = start + POLL_BUDGET
    try:
        result = _fetch_status_locked(router)
        # Port forwards are a list of objects.
        rules = _optional_objects(router, "nat", "port forwards")
        if rules:
            result["nat_rules"] = rules
        # X_ZYXEL_Attribute 2 is an internal (mesh/backhaul) SSID.
        ssids = [
            s for s in _optional_objects(router, "wlan", "Wi-Fi SSIDs")
            if "Index" in s and "wlEnable" in s and s.get("X_ZYXEL_Attribute") != 2
        ]
        if ssids:
            result["wlan_ssids"] = ssids
        return result
    except requests.exceptions.Timeout:
        raise  # A slow modem: the login is still good, keep it.
    except Exception:
        router.sessionkey = None  # The next poll logs in again.
        raise
    finally:
        router.ha_zyxel_deadline = None
        router.ha_zyxel_lock.release()


def _optional_objects(router: Any, oid: str, what: str) -> list[dict[str, Any]]:
    """Objects not every model has: skip them if missing; a timeout still ends the poll."""
    try:
        return get_objects(router, oid)
    except requests.exceptions.Timeout:
        raise
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("Zyxel %s are unavailable: %s", what, err)
        return []


def _decode(router: Any, data: dict[str, Any]) -> dict[str, Any]:
    """Decrypt a router reply, tolerating plain JSON replies."""
    if router.encryption_required and "iv" in data and "content" in data:
        return router.decrypt_response(data)
    return data


def get_objects(router: Any, oid: str) -> list[dict[str, Any]]:
    """Return every object of a DAL oid (get_json_object only returns the first)."""
    url = f"{router.url}/cgi-bin/DAL?oid={oid}&sessionkey={router.sessionkey}"
    params = {
        **router.params,
        "timeout": _request_timeout(router),
        "stream": True,
        # Uncompressed, so every socket read is checked against the budget
        # below; a decompressor could read many times before returning data.
        "headers": {**router.params.get("headers", {}), "Accept-Encoding": "identity"},
    }
    try:
        response = router.ha_zyxel_session.get(url, **params)
    except requests.exceptions.ConnectionError as err:
        if isinstance(err, requests.exceptions.Timeout):
            raise
        # The modem may have closed the kept-alive connection between polls.
        # A read is safe to send again, and this one goes on a new connection.
        params["timeout"] = _request_timeout(router)
        response = router.ha_zyxel_session.get(url, **params)
    with response:
        response.raise_for_status()
        # The read timeout only limits each wait for data: a modem trickling a
        # large reply (lanhosts, nat) could run past the poll budget without
        # this. read1() without decoding makes at most one socket read, so the
        # budget is checked after every read.
        body = bytearray()
        try:
            while chunk := response.raw.read1(65536, decode_content=False):
                body += chunk
                _request_timeout(router)  # Raises once the poll is out of time.
        except urllib3.exceptions.ReadTimeoutError as err:
            raise requests.exceptions.ReadTimeout(err) from err
        if response.headers.get("Content-Encoding", "identity") != "identity":
            body = zlib.decompress(body, zlib.MAX_WBITS | 32)  # gzip or zlib
    data = _decode(router, json.loads(body))
    if data.get("result") != "ZCFG_SUCCESS":
        return []
    return data.get("Object") or []


def _encrypt_body(router: Any, payload: dict[str, Any]) -> str:
    """Encrypt a request body like the web UI does after login.

    Same AES key as the login request, fresh IV per request, and no RSA key.
    """
    iv = os.urandom(32)
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    cipher = AES.new(router.aes_key, AES.MODE_CBC, iv[:16])
    return json.dumps(
        {
            "content": base64.b64encode(cipher.encrypt(pad(body, 16))).decode(),
            "iv": base64.b64encode(iv).decode(),
        }
    )


def _drop_kept_alive_connection(router: Any) -> None:
    """Make the next request open a new connection.

    A write is not safe to send twice, so it must not go on a kept-alive
    connection the modem may have closed in the meantime.
    """
    router.ha_zyxel_session.close()  # The session stays usable.


def put_object(router: Any, oid: str, payload: dict[str, Any]) -> dict[str, Any]:
    """PUT an object to /cgi-bin/DAL?oid=..., as the web UI does.

    The modem hands out a fresh session key in each reply and rejects the old
    one with 403, so keep ``router.sessionkey`` in sync like the web UI does.
    """
    with router.ha_zyxel_lock:
        _drop_kept_alive_connection(router)
        if not getattr(router, "sessionkey", None):
            authenticate(router)
        for attempt in range(2):
            data = _encrypt_body(router, payload) if router.encryption_required else json.dumps(payload)
            headers = {
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
                "CSRFToken": str(router.sessionkey),
            }
            params = dict(router.params)
            params["headers"] = {**params.get("headers", {}), **headers}
            with router.ha_zyxel_session.put(
                f"{router.url}/cgi-bin/DAL?oid={oid}", data=data.encode("utf-8"), **params
            ) as response:
                if response.status_code in (401, 403) and attempt == 0:
                    router.sessionkey = None
                    authenticate(router)
                    continue
                response.raise_for_status()
                reply = _decode(router, response.json())
            if reply.get("sessionkey"):
                router.sessionkey = reply["sessionkey"]
            return reply
    raise ZyxelConnectionError("Router rejected the request")


def set_port_forward_enabled(router: Any, rule: dict[str, Any], enabled: bool) -> None:
    """Enable or disable one port-forward rule, keeping all its other fields."""
    reply = put_object(router, "nat", {**rule, "Enable": enabled})
    if reply.get("result") != "ZCFG_SUCCESS":
        raise ZyxelConnectionError(f"Router refused the change: {reply}")


def _fetch_status_locked(router: Any) -> dict[str, Any]:
    if not getattr(router, "sessionkey", None):
        authenticate(router)

    last_error: Exception | None = None
    for attempt in range(2):
        result, last_error = _fetch_available_endpoints(router)
        if result:
            return result

        if attempt == 0:
            router.sessionkey = None
            authenticate(router)

    raise ZyxelConnectionError(
        "The router returned no supported status data"
    ) from last_error


def set_wifi_enabled(router: Any, index: int, enabled: bool) -> None:
    """Enable or disable one SSID. Sends only the index and the flag.

    The modem applies Wi-Fi changes asynchronously and can drop a second change
    that arrives right after the first, so verify by reading the state back and
    resend once if it did not stick.
    """

    def applied() -> bool:
        with router.ha_zyxel_lock:
            for ssid in get_objects(router, "wlan"):
                if ssid.get("Index") == index:
                    return bool(ssid.get("wlEnable")) == enabled
        return False

    for attempt in range(2):
        reply = put_object(router, "wlan", {"Index": index, "wlEnable": enabled})
        if reply.get("result") != "ZCFG_SUCCESS":
            raise ZyxelConnectionError(f"Router refused the change: {reply}")
        for _ in range(4):
            time.sleep(2)
            if applied():
                return
        _LOGGER.debug("Wi-Fi change for SSID %s not applied, attempt %s", index, attempt + 1)
    raise ZyxelConnectionError(f"Router did not apply the Wi-Fi change for SSID {index}")


def reboot_router(router: Any) -> None:
    """Reboot the modem the way the web UI does.

    The UI sends ``POST /cgi-bin/Reboot`` with no body and the session key in a
    ``CSRFToken`` header. nr7101's own reboot() puts the key in the URL instead,
    which newer firmware rejects with 403.
    """
    with router.ha_zyxel_lock:
        _drop_kept_alive_connection(router)
        if not getattr(router, "sessionkey", None):
            authenticate(router)
        for attempt in range(2):
            params = dict(router.params)
            params["headers"] = {
                **params.get("headers", {}),
                "X-Requested-With": "XMLHttpRequest",
                "CSRFToken": str(router.sessionkey),
            }
            try:
                with router.ha_zyxel_session.post(f"{router.url}/cgi-bin/Reboot", **params) as response:
                    if response.status_code in (401, 403) and attempt == 0:
                        router.sessionkey = None
                        authenticate(router)
                        continue
                    response.raise_for_status()
            except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout):
                pass  # the modem went down before answering: it is rebooting
            break
        router.sessionkey = None  # the session does not survive the reboot
