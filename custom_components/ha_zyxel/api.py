"""Synchronous helpers for communicating with Zyxel devices."""

from __future__ import annotations

import logging
from typing import Any

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


class ZyxelAuthenticationError(Exception):
    """Raised when the router rejects the supplied credentials."""


class ZyxelConnectionError(Exception):
    """Raised when no usable data can be fetched from the router."""


class ZyxelSmsError(Exception):
    """Raised when a message cannot be sent as written."""


def create_router(host: str, username: str, password: str) -> Any:
    """Create a router with connection state isolated from other instances.

    nr7101 currently uses a mutable dictionary as its default ``params``
    argument. Passing a new dictionary explicitly prevents cookies from a
    config-flow instance being reused by the config-entry instance, where
    they would be paired with a different AES key.
    """
    return nr7101.NR7101(host, username, password, {})


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


def validate_sms(number: str, message: str) -> None:
    """Reject a message the router cannot send before touching the network.

    nr7101 builds the ``cellwan_sms`` body without any request, so the same
    call reports a non-GSM character, an over-long message or an empty
    recipient up front instead of after a failed send and a reauthentication.
    """
    builder = getattr(nr7101, "build_sms_object", None)
    if builder is None:
        _LOGGER.debug("The installed nr7101 cannot validate SMS locally")
        return

    try:
        body = builder(number, message)
    except Exception as err:
        raise ZyxelSmsError(str(err)) from err

    if not body["SMS_To"]:
        raise ZyxelSmsError("The recipient number contains no digits")


def send_sms(router: Any, number: str, message: str) -> None:
    """Send one SMS, reauthenticating once when the session has expired."""
    validate_sms(number, message)

    if not hasattr(router, "send_sms"):
        raise ZyxelSmsError(
            "The installed nr7101 version does not support sending SMS"
        )

    if not getattr(router, "sessionkey", None):
        authenticate(router)

    last_error: Exception | None = None
    for attempt in range(2):
        try:
            router.send_sms(number, message)
            return
        except Exception as err:
            last_error = err
            _LOGGER.debug("Zyxel SMS attempt %s failed: %s", attempt + 1, err)

            if attempt == 0:
                router.sessionkey = None
                authenticate(router)

    raise ZyxelConnectionError(
        f"The router did not accept the message: {last_error}"
    ) from last_error
