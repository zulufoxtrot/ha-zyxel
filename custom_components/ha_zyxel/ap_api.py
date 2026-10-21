"""Read-only NWA50AX zysh-cgi client (experimental)."""
from __future__ import annotations

import ast
from html.parser import HTMLParser
import re
from threading import Lock
from urllib.parse import urlsplit

import requests

from .api import ZyxelAuthenticationError, ZyxelConnectionError

# Only commands confirmed against the device UI belong in this list.
COMMANDS = (
    "show version",
    "show wlan all",
    "show wireless-hal current channel",
    "show wireless-hal statistic",
    "show fqdn",
    "show nebula cloud status",
)
MAX_RESPONSE = 2_000_000


class LoginForm(HTMLParser):
    """Read the CSRF token without retaining password fields."""
    def __init__(self):
        super().__init__()
        self.csrf = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("name") == "CSRFToken":
            self.csrf = attrs.get("value", "")


def parse_response(text):
    """Parse data literals, never execute JavaScript received from a device."""
    if len(text) > MAX_RESPONSE:
        raise ZyxelConnectionError("AP response exceeds size limit")
    result = {}
    # Responses contain one assignment per line; literals may contain semicolons.
    for line in text.splitlines():
        match = re.fullmatch(r"\s*var\s+(zyshdata\d+|errno\d+|errmsg\d+)\s*=\s*(.*?)\s*;\s*", line)
        if not match:
            continue
        try:
            result[match[1]] = ast.literal_eval(match[2])
        except (SyntaxError, ValueError, RecursionError) as err:
            raise ZyxelConnectionError("Unsupported AP response format") from err
    if not any(key.startswith("zyshdata") for key in result):
        raise ZyxelConnectionError("No zysh status data in AP response")
    return result


def normalize_status(parsed):
    """Expose only explicitly supported device and radio values."""
    result = {"device": {}, "radios": {}}
    fields = {"_model": "model", "_firmware_version": "firmware",
              "_host_name": "name", "_IP_Address": "ip_address",
              "_Nebula_Cloud_status": "nebula_status"}
    numbers = {"_wlanReceivedByte": "BytesReceived", "_wlanTransmittedByte": "BytesSent",
               "_ReceivedPktCount": "PacketsReceived", "_TransmittedPktCount": "PacketsSent",
               "_TxPower": "TxPower", "_Channel_Utilization": "ChannelUtilization"}
    for key, rows in parsed.items():
        if not key.startswith("zyshdata") or not isinstance(rows, list):
            continue
        index = key[len("zyshdata"):]
        if parsed.get("errno" + index) != 0:
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            for source, target in fields.items():
                value = row.get(source)
                if isinstance(value, str):
                    result["device"][target] = value
            for slot, band in (("1", "radio_2g"), ("2", "radio_5g")):
                value = row.get("_Slot" + slot)
                if isinstance(value, str):
                    result["radios"].setdefault(band, {})["Channel"] = value
            for radio in row.get("_Slot", []):
                if not isinstance(radio, dict):
                    continue
                band = {"1": "radio_2g", "2": "radio_5g"}.get(radio.get("__name"))
                if band is None:
                    continue
                values = result["radios"].setdefault(band, {})
                for source, target in numbers.items():
                    try:
                        value = int(radio[source])
                    except (KeyError, ValueError, TypeError):
                        continue
                    if value >= 0:
                        values[target] = value
    if result["device"].get("model") != "NWA50AX":
        raise ZyxelConnectionError("Response does not identify a supported NWA50AX")
    return result


class NWA50AXClient:
    """One isolated session per AP; bounded retries and serialized requests."""
    def __init__(self, host, username, password):
        parsed = urlsplit(host)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ("", "/")):
            raise ZyxelConnectionError("Host must be an HTTP(S) origin without a path")
        self.host = host.rstrip("/")
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session.trust_env = False
        self.sessionkey = None
        self._lock = Lock()

    def _request(self, method, path, **kwargs):
        try:
            response = self.session.request(method, self.host + path, timeout=(5, 10),
                                            allow_redirects=False, **kwargs)
            response.raise_for_status()
            if len(response.content) > MAX_RESPONSE:
                raise ZyxelConnectionError("AP response exceeds size limit")
            return response
        except requests.RequestException:
            # Avoid exposing credentials, tokens or response bodies in logs.
            raise ZyxelConnectionError("AP HTTP request failed") from None

    def _login(self):
        self.sessionkey = None
        self.session.cookies.clear()
        page = self._request("GET", "/")
        form = LoginForm()
        form.feed(page.text)
        if form.csrf is None:
            raise ZyxelConnectionError("AP login page has no CSRFToken field")
        response = self._request("POST", "/", data={
            "username": self.username, "pwd": self.password,
            "password": self.password, "CSRFToken": form.csrf,
            "fake_safari_username": "", "fake_safari_password": "",
        })
        if response.status_code not in (302, 303) or not any(
                cookie.name == "authtok" and cookie.value for cookie in self.session.cookies):
            raise ZyxelAuthenticationError("AP rejected login")
        self.sessionkey = True

    def fetch_status(self):
        with self._lock:
            for attempt in range(2):
                if not self.sessionkey:
                    self._login()
                response = self._request("POST", "/cgi-bin/zysh-cgi", data=[
                    ("filter", "js2"), *[("cmd", command) for command in COMMANDS], ("write", "0")])
                if response.status_code in (301, 302, 303, 307, 308) or "name=\"CSRFToken\"" in response.text:
                    self.sessionkey = None
                    if attempt == 0:
                        continue
                    raise ZyxelAuthenticationError("AP session expired after login")
                return normalize_status(parse_response(response.text))
            raise ZyxelConnectionError("AP returned no data")

    def close(self):
        with self._lock:
            self.session.close()
