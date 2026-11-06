"""Synchronous helpers for communicating with Zyxel devices."""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
from typing import Any

import requests
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


def create_router(host: str, username: str, password: str) -> Any:
    """Create a router with connection state isolated from other instances.

    nr7101 currently uses a mutable dictionary as its default ``params``
    argument. Passing a new dictionary explicitly prevents cookies from a
    config-flow instance being reused by the config-entry instance, where
    they would be paired with a different AES key.
    """
    router = nr7101.NR7101(host, username, password, {})
    router.ha_zyxel_lock = get_host_lock(host)
    return router


def authenticate(router: Any) -> None:
    """Log in and verify that nr7101 received a session key."""
    try:
        login_success = router.login()
    except Exception as err:
        raise ZyxelConnectionError("Unable to complete router login") from err

    if not login_success or not getattr(router, "sessionkey", None):
        raise ZyxelAuthenticationError("The router rejected the credentials")


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
            data = router.get_json_object(endpoint)
            if endpoint == "Traffic_Status":
                data = _parse_traffic_object(data)
            if data:
                result[key] = data
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
    with router.ha_zyxel_lock:
        result = _fetch_status_locked(router)
        # Port forwards are a list of objects, which get_json_object() (first
        # object only) cannot return. Optional: not every model has them.
        try:
            rules = get_objects(router, "nat")
            if rules:
                result["nat_rules"] = rules
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Zyxel port forwards are unavailable: %s", err)
        try:
            # X_ZYXEL_Attribute 2 is an internal (mesh/backhaul) SSID.
            ssids = [
                s for s in get_objects(router, "wlan")
                if "Index" in s and "wlEnable" in s and s.get("X_ZYXEL_Attribute") != 2
            ]
            if ssids:
                result["wlan_ssids"] = ssids
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Zyxel Wi-Fi SSIDs are unavailable: %s", err)
        return result


def _decode(router: Any, response: requests.Response) -> dict[str, Any]:
    """Decrypt a router response, tolerating plain JSON replies."""
    data = response.json()
    if router.encryption_required and "iv" in data and "content" in data:
        return router.decrypt_response(data)
    return data


def get_objects(router: Any, oid: str) -> list[dict[str, Any]]:
    """Return every object of a DAL oid (get_json_object only returns the first)."""
    url = f"{router.url}/cgi-bin/DAL?oid={oid}&sessionkey={router.sessionkey}"
    with requests.get(url, **router.params) as response:
        response.raise_for_status()
        data = _decode(router, response)
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


def put_object(router: Any, oid: str, payload: dict[str, Any]) -> dict[str, Any]:
    """PUT an object to /cgi-bin/DAL?oid=..., as the web UI does.

    The modem hands out a fresh session key in each reply and rejects the old
    one with 403, so keep ``router.sessionkey`` in sync like the web UI does.
    """
    with router.ha_zyxel_lock:
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
            with requests.put(
                f"{router.url}/cgi-bin/DAL?oid={oid}", data=data.encode("utf-8"), **params
            ) as response:
                if response.status_code in (401, 403) and attempt == 0:
                    router.sessionkey = None
                    authenticate(router)
                    continue
                response.raise_for_status()
                reply = _decode(router, response)
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
                with requests.post(f"{router.url}/cgi-bin/Reboot", **params) as response:
                    if response.status_code in (401, 403) and attempt == 0:
                        router.sessionkey = None
                        authenticate(router)
                        continue
                    response.raise_for_status()
            except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout):
                pass  # the modem went down before answering: it is rebooting
            break
        router.sessionkey = None  # the session does not survive the reboot
