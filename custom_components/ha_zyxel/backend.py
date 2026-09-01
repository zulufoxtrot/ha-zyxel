"""Shared Zyxel backend helpers."""
from __future__ import annotations

import ast
import json
import logging
import re
from base64 import b64decode, b64encode
from collections.abc import Mapping
from secrets import token_bytes

import requests
from cryptography.hazmat.primitives import hashes, padding
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from requests.packages.urllib3.exceptions import InsecureRequestWarning
from homeassistant.helpers.update_coordinator import UpdateFailed

_LOGGER = logging.getLogger(__name__)
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
_IP_LIKE_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
_MODEL_LIKE_RE = re.compile(r"^[A-Z]{2,}\d[\w-]*$", re.IGNORECASE)
_LANGUAGE_LIKE = {
    "english",
    "french",
    "francais",
    "german",
    "deutsch",
    "spanish",
    "espanol",
    "italian",
    "portuguese",
}


class NWA50AXClient:
    """Minimal Zyxel NWA50AX client using the zysh CGI endpoint."""

    def __init__(self, host: str, username: str, password: str, timeout: int = 15) -> None:
        self.host = host.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": "Mozilla/5.0",
                "X-Requested-With": "XMLHttpRequest",
                "Origin": self.host,
                "Referer": f"{self.host}/",
            }
        )
        self._session.verify = False

    def login(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": "Mozilla/5.0",
                "X-Requested-With": "XMLHttpRequest",
                "Origin": self.host,
                "Referer": f"{self.host}/",
            }
        )
        self._session.verify = False
        page = self._session.get(f"{self.host}/", timeout=self.timeout)
        _LOGGER.debug("NWA50AX login page status=%s url=%s", page.status_code, page.url)
        if page.status_code >= 500:
            _LOGGER.debug("NWA50AX login page returned %s; continuing to POST login", page.status_code)
        csrf_token = (
            self._session.cookies.get("CSRFToken")
            or self._session.cookies.get("csrftok")
            or self._session.cookies.get("csrf")
        )
        payloads = [
            {"username": self.username, "pwd": self.password},
            {"username": self.username, "password": self.password},
        ]
        if csrf_token:
            for payload in payloads:
                payload["CSRFToken"] = csrf_token

        last_response = None
        for payload in payloads:
            resp = self._session.post(
                f"{self.host}/",
                data=payload,
                timeout=self.timeout,
                allow_redirects=True,
            )
            last_response = resp
            if resp.status_code >= 500:
                _LOGGER.debug(
                    "NWA50AX login POST returned %s; checking body/cookies instead of failing hard",
                    resp.status_code,
                )
            _LOGGER.debug("NWA50AX login response status=%s url=%s", resp.status_code, resp.url)
            if "login" in resp.text.lower() and "fail" in resp.text.lower():
                continue
            if "invalid" in resp.text.lower() and "password" in resp.text.lower():
                continue
            if self._session.cookies.get("authtok") or self._session.cookies.get("authtoken") or self._session.cookies.get("auth"):
                return

        _LOGGER.debug("NWA50AX login cookies after POST attempts: %s", self._session.cookies.get_dict())
        if last_response is not None and (
            "login" in last_response.text.lower() and "fail" in last_response.text.lower()
        ):
            raise UpdateFailed("Login failed")
        if last_response is not None and (
            "invalid" in last_response.text.lower() and "password" in last_response.text.lower()
        ):
            raise UpdateFailed("Login failed")
        raise UpdateFailed("Login session not established")

    def _post_cmds(self, cmds: list[str]) -> dict:
        from urllib.parse import quote_plus

        payload = "&".join(["filter=js2"] + [f"cmd={quote_plus(cmd)}" for cmd in cmds] + ["write=0"])
        resp = self._session.post(
            f"{self.host}/cgi-bin/zysh-cgi",
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
            timeout=self.timeout,
        )
        if resp.status_code >= 400:
            _LOGGER.debug("NWA50AX zysh-cgi returned %s for %s", resp.status_code, cmds[0])
        return self._parse_zysh_response(resp.text)

    @staticmethod
    def _parse_zysh_response(text: str) -> dict:
        stripped = text.strip()
        lowered = stripped.lower()
        if "login" in lowered and ("password" in lowered or "invalid" in lowered):
            raise UpdateFailed("Login failed")
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                parsed_json = json.loads(stripped)
            except json.JSONDecodeError:
                parsed_json = None
            else:
                if isinstance(parsed_json, dict):
                    return parsed_json

        result: dict = {}
        pattern = re.compile(
            r"var\s+([A-Za-z0-9_]+)\s*=\s*\[(.*?)\];\s*var\s+errno\1\s*=\s*(\d+);\s*var\s+errmsg\1\s*=\s*(['\"])(.*?)\4;?",
            re.S,
        )
        for match in pattern.finditer(text):
            data_name = match.group(1)
            raw = match.group(2)
            parsed = ast.literal_eval("[" + raw + "]")
            errno = int(match.group(3))
            errmsg = match.group(5)
            if errno != 0:
                raise UpdateFailed(errmsg or f"zysh-cgi command {data_name} failed with errno {errno}")
            result[data_name] = parsed
        if not result:
            var_pattern = re.compile(r"var\s+([A-Za-z0-9_]+)\s*=\s*(\[[^\n;]*\]|'[^']*'|\"[^\"]*\"|\d+)\s*;")
            for match in var_pattern.finditer(text):
                name = match.group(1)
                value = match.group(2)
                if not name.startswith("zyshdata"):
                    continue
                try:
                    result[name] = ast.literal_eval(value)
                except (SyntaxError, ValueError):
                    continue
        if not result:
            raise UpdateFailed(f"zysh-cgi returned no usable data: {text[:120]}")
        return result

    def get_status(self) -> dict:
        data = self._post_cmds(
            [
                "show language setting",
                "show users current",
                "show version",
                "show hybrid-mode",
                "show manager vlan",
                "show wlan all",
                "show wireless-hal current channel",
                "show wireless-hal statistic",
                "show nebula ethernet status",
                "show nebula internet status",
                "show nebula cloud status",
                "show nebula claim status",
                "show netconf proxy status",
                "show fqdn",
                "show mac",
                "show serial-number",
                "show netconf status",
                "show nebula ntp status",
                "show nebula cloud-gui status",
            ]
        )
        normalized = normalize_zysh_status(data)
        if not normalized:
            raise UpdateFailed("zysh-cgi returned an empty status payload")
        return normalized

    @staticmethod
    def get_device_name(status: Mapping) -> str | None:
        """Return the best device name we can find in zysh status data."""
        def _search(node: Mapping | list | str | object, preferred: tuple[str, ...]) -> str | None:
            if isinstance(node, str):
                candidate = node.strip()
                if not candidate:
                    return None
                lowered = candidate.lower()
                if lowered.startswith(("http://", "https://")):
                    return None
                if _IP_LIKE_RE.match(candidate):
                    return None
                if lowered in _LANGUAGE_LIKE:
                    return None
                return candidate
            if isinstance(node, Mapping):
                for key, value in node.items():
                    key_lower = key.lower() if isinstance(key, str) else ""
                    if any(token in key_lower for token in preferred):
                        if isinstance(value, str) and value.strip():
                            candidate = value.strip()
                            lowered = candidate.lower()
                            if (
                                not _IP_LIKE_RE.match(candidate)
                                and not lowered.startswith(("http://", "https://"))
                                and lowered not in _LANGUAGE_LIKE
                            ):
                                return candidate
                    nested = _search(value, preferred)
                    if nested:
                        return nested
            if isinstance(node, list):
                for item in node:
                    nested = _search(item, preferred)
                    if nested:
                        return nested
            return None

        name = _search(status, ("system name", "system_name"))
        if name:
            return name
        name = _search(status, ("fqdn", "hostname", "device_name"))
        if name:
            return name
        return None

    @staticmethod
    def get_device_model(status: Mapping) -> str | None:
        """Return a model-like identifier from zysh status data."""
        def _search(node: Mapping | list | str | object) -> str | None:
            if isinstance(node, str):
                candidate = node.strip()
                if not candidate:
                    return None
                lowered = candidate.lower()
                if lowered in _LANGUAGE_LIKE or _IP_LIKE_RE.match(candidate):
                    return None
                return candidate if _MODEL_LIKE_RE.match(candidate) else None
            if isinstance(node, Mapping):
                for key, value in node.items():
                    key_lower = key.lower() if isinstance(key, str) else ""
                    if any(token in key_lower for token in ("model", "product", "platform", "hardware")):
                        found = _search(value)
                        if found:
                            return found
                for value in node.values():
                    found = _search(value)
                    if found:
                        return found
            if isinstance(node, list):
                for item in node:
                    found = _search(item)
                    if found:
                        return found
            return None

        return _search(status)

    def reboot(self) -> None:
        self._post_cmds(["reboot"])


class EX3301T0Client:
    """Probe Zyxel EX3301-T0 stock firmware CGI endpoints."""

    # Endpoints tried without any path prefix (same level as login/key endpoints).
    # UserLoginCheck intentionally excluded — it consistently times out on this firmware.
    _ENDPOINTS = (
        "loginAccountLevel",
        "MenuList",
        "CardInfo",
    )
    # Endpoints that may live under cgi-bin/ on some firmware builds.
    # lanhosts intentionally excluded — it creates one entry per client device.
    _CGI_ENDPOINTS = (
        "DAL?oid=cardpage_status",
        "DAL?oid=lan",
        "DAL?oid=wlan",
    )
    # Validation succeeds if ANY of these return data.
    _CORE_ENDPOINTS = frozenset({
        "MenuList",
        "CardInfo",
        "UserLoginCheck",
        "DAL?oid=lan",
        "cgi-bin/DAL?oid=lan",
    })

    def __init__(self, host: str, username: str, password: str, timeout: int = 15) -> None:
        self.host = host.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": "Mozilla/5.0",
                "X-Requested-With": "XMLHttpRequest",
                "Origin": self.host,
                "Referer": f"{self.host}/",
            }
        )
        self._session.verify = False
        self._aes_key: bytes | None = None
        self._csrf_token: str | None = None

    @staticmethod
    def _aes_encrypt(plaintext: str, key: bytes, iv: bytes) -> str:
        padder = padding.PKCS7(128).padder()
        padded = padder.update(plaintext.encode("utf-8")) + padder.finalize()
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv[:16]))
        encryptor = cipher.encryptor()
        ciphertext = encryptor.update(padded) + encryptor.finalize()
        return b64encode(ciphertext).decode("ascii")

    @staticmethod
    def _aes_decrypt(ciphertext_b64: str, key: bytes, iv_b64: str) -> str:
        iv = b64decode(iv_b64)
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv[:16]))
        decryptor = cipher.decryptor()
        plaintext = decryptor.update(b64decode(ciphertext_b64)) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        return (unpadder.update(plaintext) + unpadder.finalize()).decode("utf-8")

    def _encrypt_login_payload(self, public_key_pem: str) -> dict[str, str]:
        aes_key_raw = token_bytes(32)
        aes_key_b64 = b64encode(aes_key_raw).decode("ascii")
        iv_raw = token_bytes(32)
        iv_b64 = b64encode(iv_raw).decode("ascii")
        self._aes_key = aes_key_raw
        payload = {
            "Input_Account": self.username,
            "Input_Passwd": b64encode(self.password.encode("utf-8")).decode("ascii"),
            "currLang": "en",
            "RememberPassword": 0,
            "SHA512_password": False,
        }
        encrypted = self._aes_encrypt(json.dumps(payload, separators=(",", ":")), aes_key_raw, iv_raw)
        public_key = load_pem_public_key(public_key_pem.encode("utf-8"))
        encrypted_key = public_key.encrypt(
            aes_key_b64.encode("utf-8"),
            asym_padding.PKCS1v15(),
        )
        return {
            "content": encrypted,
            "key": b64encode(encrypted_key).decode("ascii"),
            "iv": iv_b64,
        }

    def _post_login(self, login_payload: dict[str, str], raw_json_string: bool = False) -> requests.Response:
        if raw_json_string:
            return self._session.post(
                f"{self.host}/UserLogin",
                data=json.dumps(login_payload),
                timeout=self.timeout,
                allow_redirects=False,
            )
        return self._session.post(
            f"{self.host}/UserLogin",
            data=login_payload,
            timeout=self.timeout,
            allow_redirects=False,
        )

    def login(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": "Mozilla/5.0",
                "X-Requested-With": "XMLHttpRequest",
                "Origin": self.host,
                "Referer": f"{self.host}/",
            }
        )
        self._session.verify = False
        try:
            page = self._session.get(f"{self.host}/", timeout=self.timeout, allow_redirects=False)
            _LOGGER.debug("EX3301-T0 login page status=%s url=%s", page.status_code, page.url)
            if page.status_code in (301, 302, 303, 307, 308):
                location = page.headers.get("Location", "")
                _LOGGER.debug("EX3301-T0 login page redirected to %s", location)
                if location.startswith("https://"):
                    raise UpdateFailed("EX3301-T0 redirected to HTTPS during login bootstrap")
        except requests.exceptions.ReadTimeout:
            _LOGGER.debug("EX3301-T0 initial page fetch timed out; proceeding to RSA key fetch")
        except requests.exceptions.RequestException as err:
            _LOGGER.debug("EX3301-T0 initial page fetch failed (%s); proceeding to RSA key fetch", err)

        key_resp = self._session.get(
            f"{self.host}/getRSAPublickKey",
            timeout=self.timeout,
            allow_redirects=False,
        )
        key_data = self._safe_json(key_resp, "EX3301-T0 RSA key request")
        public_key = key_data["RSAPublicKey"]
        login_payload = self._encrypt_login_payload(public_key)
        resp = self._post_login(login_payload, raw_json_string=True)
        _LOGGER.debug("EX3301-T0 login status=%s url=%s", resp.status_code, resp.url)
        if resp.status_code == 401 and "Decrypt Fail" in resp.text:
            _LOGGER.debug("EX3301-T0 login decrypt failed with raw JSON payload; retrying as form")
            resp = self._post_login(login_payload, raw_json_string=False)
            _LOGGER.debug("EX3301-T0 login retry status=%s url=%s", resp.status_code, resp.url)
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location", "")
            _LOGGER.debug("EX3301-T0 login redirected to %s", location)
            if location.startswith("https://"):
                raise UpdateFailed("EX3301-T0 redirected to HTTPS during login")
        elif not resp.ok:
            snippet = resp.text.strip().replace("\n", " ")[:160]
            raise UpdateFailed(f"EX3301-T0 login returned {resp.status_code}: {snippet}")
        body = self._safe_json(resp, "EX3301-T0 login")
        if "content" in body and "iv" in body:
            if not self._aes_key:
                raise UpdateFailed("EX3301-T0 missing AES session key")
            decrypted = self._aes_decrypt(body["content"], self._aes_key, body["iv"])
            data = json.loads(decrypted)
        else:
            data = body
        session_key = (
            data.get("sessionkey")
            or data.get("sessionKey")
            or resp.cookies.get("zySessionKey")
            or resp.cookies.get("sessionkey")
            or resp.cookies.get("sessionKey")
            or self._session.cookies.get("zySessionKey")
        )
        if session_key:
            self._csrf_token = session_key
            self._session.headers.update({"CSRFToken": session_key})
            self._session.cookies.set("zySessionKey", session_key, path="/")
        if not session_key and not any(k in data for k in ("loginAccount", "loginLevel", "quickStart", "ThemeColor")):
            raise UpdateFailed("Login session not established")
        self._probe("UserLoginCheck")

    def _request(self, endpoint: str, method: str = "get") -> requests.Response:
        url = f"{self.host}/{endpoint}"
        request = getattr(self._session, method.lower())
        headers = {}
        if method.lower() in {"post", "put", "delete"} and self._csrf_token:
            headers["CSRFToken"] = self._csrf_token
        response = request(url, timeout=self.timeout, allow_redirects=False, headers=headers)
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location", "")
            _LOGGER.debug("EX3301-T0 request %s redirected to %s", endpoint, location)
            if location.startswith("https://"):
                raise UpdateFailed(f"EX3301-T0 redirected to HTTPS while requesting {endpoint}")
        return response

    _SESSION_EXPIRED_MARKERS = ("login", "Login", "unauthorized", "Unauthorized", "sessionExpired")

    def _is_session_expired(self, resp: requests.Response) -> bool:
        """Return True if the response looks like a session-expired redirect or error."""
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location", "")
            return any(m in location for m in ("login", "Login"))
        if resp.status_code in (401, 403):
            return True
        return False

    def _probe(self, endpoint: str) -> dict | str | None:
        try:
            resp = self._request(endpoint)
        except requests.exceptions.ReadTimeout:
            _LOGGER.warning("EX3301-T0 probe timed out for %s on %s", endpoint, self.host)
            return None
        except requests.exceptions.RequestException as err:
            _LOGGER.warning(
                "EX3301-T0 probe request failed for %s on %s: %s",
                endpoint,
                self.host,
                err,
            )
            return None
        _LOGGER.debug(
            "EX3301-T0 probe %s status=%s body_prefix=%r",
            endpoint,
            resp.status_code,
            resp.text[:120],
        )
        if self._is_session_expired(resp):
            raise UpdateFailed(f"EX3301-T0 session expired (probe {endpoint})")
        text = resp.text.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return text
        # Decrypt AES-wrapped responses using the session key from login.
        if isinstance(parsed, dict) and "content" in parsed and "iv" in parsed and self._aes_key:
            try:
                decrypted = self._aes_decrypt(parsed["content"], self._aes_key, parsed["iv"])
                return json.loads(decrypted)
            except Exception as err:  # pylint: disable=broad-except
                _LOGGER.debug("EX3301-T0 probe %s decrypt failed: %s", endpoint, err)
        return parsed

    @staticmethod
    def _safe_json(resp: requests.Response, context: str) -> dict:
        try:
            body = resp.json()
        except ValueError as err:
            snippet = resp.text.strip().replace("\n", " ")
            raise UpdateFailed(f"{context} returned non-JSON response: {snippet[:120]}") from err
        if not isinstance(body, dict):
            raise UpdateFailed(f"{context} returned an unexpected response shape")
        return body

    @staticmethod
    def _extract_jsonish_blob(blob: str) -> dict | list | str | None:
        blob = blob.strip()
        if not blob:
            return None
        for candidate in (blob, blob.replace("\r", "")):
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
        if blob.startswith("base64:"):
            try:
                decoded = b64decode(blob.removeprefix("base64:")).decode("utf-8", "ignore")
                return json.loads(decoded)
            except Exception:  # pylint: disable=broad-except
                return blob
        return blob

    def get_status(self) -> dict:
        data: dict[str, object] = {}
        has_core_payload = False
        for endpoint in self._ENDPOINTS:
            payload = self._probe(endpoint)
            if payload is not None:
                data[endpoint] = payload
                if endpoint in self._CORE_ENDPOINTS:
                    has_core_payload = True
        # Try DAL endpoints both with and without cgi-bin/ prefix.
        for endpoint in self._CGI_ENDPOINTS:
            payload = self._probe(endpoint)
            if payload is not None:
                data[endpoint] = payload
                if endpoint in self._CORE_ENDPOINTS:
                    has_core_payload = True
            else:
                cgi_endpoint = f"cgi-bin/{endpoint}"
                payload = self._probe(cgi_endpoint)
                if payload is not None:
                    data[endpoint] = payload
                    if cgi_endpoint in self._CORE_ENDPOINTS:
                        has_core_payload = True
        if not has_core_payload:
            raise UpdateFailed("EX3301-T0 returned no usable core CGI payloads")
        return data

    def get_device_name(self, status: Mapping) -> str | None:
        for key in ("MenuList", "CardInfo", "DAL?oid=cardpage_status", "DAL?oid=lan"):
            value = status.get(key)
            if isinstance(value, Mapping):
                for candidate_key in ("systemName", "SystemName", "host_name", "hostname", "device_name"):
                    candidate = value.get(candidate_key)
                    if isinstance(candidate, str) and candidate.strip():
                        return candidate.strip()
        return None

    def get_device_model(self, status: Mapping) -> str | None:
        for key in ("CardInfo", "MenuList", "DAL?oid=lan"):
            value = status.get(key)
            if isinstance(value, Mapping):
                for candidate_key in ("model", "Model", "model_name", "device_model"):
                    candidate = value.get(candidate_key)
                    if isinstance(candidate, str) and candidate.strip():
                        return candidate.strip()
        return None

    def reboot(self) -> None:
        self._request("UserLoginCheck")


def normalize_zysh_status(data: Mapping) -> dict:
    """Flatten the zysh response into a status dict."""
    normalized: dict = {}
    for key, value in data.items():
        if isinstance(key, str) and isinstance(value, list) and value:
            item = value[0]
            if isinstance(item, dict):
                normalized[key] = item
                for subkey, subvalue in item.items():
                    if subkey.startswith("_"):
                        normalized[subkey.lstrip("_")] = subvalue
            else:
                normalized[key] = value
    return normalized
