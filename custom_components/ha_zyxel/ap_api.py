"""Read-only zysh-cgi client for Zyxel NWA standalone access points (experimental)."""
from __future__ import annotations

import ast
from html.parser import HTMLParser
import re
from threading import Lock
from urllib.parse import urlsplit

import hashlib
import ssl
import warnings

import requests
from requests.adapters import HTTPAdapter

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
# Models that share the NWA standalone web UI and zysh-cgi API.
SUPPORTED_MODELS = {"NWA50AX", "NWA50BE"}
SLOT_BANDS = {"1": "radio_2g", "2": "radio_5g", "3": "radio_6g"}


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
              "_Nebula_Cloud_status": "nebula_status",
              "_Zyxel_Cloud": "nebula_status"}  # NWA50BE firmware V7.30 name
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
            for slot, band in SLOT_BANDS.items():
                value = row.get("_Slot" + slot)
                if isinstance(value, str):
                    result["radios"].setdefault(band, {})["Channel"] = value
            for radio in row.get("_Slot", []):
                if not isinstance(radio, dict):
                    continue
                band = SLOT_BANDS.get(radio.get("__name"))
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
    model = result["device"].get("model", "").strip().upper()
    if model not in SUPPORTED_MODELS:
        raise ZyxelConnectionError(
            f"Unsupported access point model {model!r}; supported: {sorted(SUPPORTED_MODELS)}")
    return result


def fetch_cert_fingerprint(host):
    """Return the SHA-256 fingerprint of the certificate an HTTPS host presents.

    NWA access points use a self-signed certificate issued to a device name, not
    the IP, so normal validation always fails. Callers record this fingerprint
    when the device is added and pin it afterwards.
    """
    parsed = urlsplit(host)
    try:
        pem = ssl.get_server_certificate((parsed.hostname, parsed.port or 443), timeout=10)
    except OSError:
        raise ZyxelConnectionError("Could not read the AP's HTTPS certificate") from None
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()


class _PinnedAdapter(HTTPAdapter):
    """Accept exactly one certificate, identified by its SHA-256 fingerprint."""
    def __init__(self, fingerprint):
        self._fingerprint = fingerprint
        super().__init__()

    def init_poolmanager(self, *args, **kwargs):
        kwargs["assert_fingerprint"] = self._fingerprint
        super().init_poolmanager(*args, **kwargs)

    def send(self, request, **kwargs):
        # urllib3 warns about verify=False even though the pin is enforced.
        warnings.filterwarnings("ignore", message="Unverified HTTPS request")
        # The CA/hostname check can never pass for this self-signed cert; the
        # fingerprint check above replaces it and still rejects any other cert.
        kwargs["verify"] = False
        return super().send(request, **kwargs)


class NWA50AXClient:
    """One isolated session per AP; bounded retries and serialized requests."""
    def __init__(self, host, username, password, cert_fingerprint=None):
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
        self.cert_fingerprint = cert_fingerprint
        if cert_fingerprint:
            self.session.mount("https://", _PinnedAdapter(cert_fingerprint))
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

    def _follow_https_redirect(self, location):
        """Accept only an HTTP -> HTTPS upgrade on the same host (NWA50BE does this)."""
        current, target = urlsplit(self.host), urlsplit(location)
        if (current.scheme != "http" or target.scheme != "https"
                or target.hostname != current.hostname or target.path not in ("", "/")):
            raise ZyxelConnectionError("AP redirected the login page unexpectedly")
        self.host = f"https://{target.netloc}"
        if not self.cert_fingerprint:
            raise ZyxelConnectionError(
                f"AP requires HTTPS; use {self.host} so its certificate can be pinned")

    def _login(self):
        self.sessionkey = None
        self.session.cookies.clear()
        page = self._request("GET", "/")
        if page.status_code in (301, 302, 303, 307, 308):
            self._follow_https_redirect(page.headers.get("Location", ""))
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
