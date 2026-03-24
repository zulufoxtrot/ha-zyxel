#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import logging
import json
import base64
import os
from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.Util.Padding import pad, unpad
from Crypto.PublicKey import RSA

import ssl
import aiohttp
import asyncio
from aiohttp import ClientResponseError

import requests
import urllib3

logger = logging.getLogger(__name__)


class NR7101Exception(Exception):
    def __init__(self, error):
        self.error = error


class NR7101:
    def __init__(self, url, username, password, params={}):
        self.url = url
        self.params = params
        self.rsa_key = None
        self.encryption_required = False
        self.last_status_data = None

        
        self.sessionkey = None

        self.username = username
        self.password_b64 = base64.b64encode(password.encode("utf-8")).decode("utf-8")
        
        self.aes_key = None
        self.iv = None

        self.cookiejar = aiohttp.CookieJar(unsafe=True)  # accetta self-signed cert
        self.session = aiohttp.ClientSession(cookie_jar=self.cookiejar, connector=aiohttp.TCPConnector(ssl=False))

    async def close(self):
        await self.session.close()

    async def _get(self, path, headers=None, params=None, asText=False):
        url = self.url + path
        async with self.session.get(url, headers=headers, **(params or {})) as r:
            r.raise_for_status()
            if asText:
                return await r.text()
            return await r.json()

    async def _post(self, path, data=None, headers=None, params=None):
        url = self.url + path
        async with self.session.post(url, data=data, headers=headers, **(params or {})) as r:
            r.raise_for_status()
            return await r.json()


    async def initialize(self):
        """Step 1 e Step 2: GetInfoNoLogin + RSA key"""            
        # NR7101 is using by default self-signed certificates, so ignore the warnings
        
        #self.params["verify"] = False
        #urllib3.disable_warnings()

        # Step 1: Call GetInfoNoLogin to establish session
        info_headers = {
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Accept-Language': 'en-GB,en;q=0.5',
            'X-Requested-With': 'XMLHttpRequest',
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:142.0) Gecko/20100101 Firefox/142.0'
        }
        
        await self._get("/GetInfoNoLogin", headers=info_headers, asText=True)

        # Step 2: Get RSA public key
        rsa_headers = {
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Accept-Language': 'en-GB,en;q=0.5',
            'X-Requested-With': 'XMLHttpRequest',
            'If-Modified-Since': 'Thu, 01 Jun 1970 00:00:00 GMT',
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:142.0) Gecko/20100101 Firefox/142.0'
        }

        try:
            r = await self._get("/getRSAPublickKey", headers=rsa_headers)
            self.rsa_key = r.get("RSAPublicKey", None)
            if self.rsa_key == "None":
                self.rsa_key = None
            self.encryption_required = bool(self.rsa_key)
            logger.debug(f"getRSAPublickKey, rsa_key: {self.rsa_key}, encryption_required: {self.encryption_required}")
        except Exception as e:
            logger.debug(f"Error getRSAPublickKey, error: {e}")
            self.rsa_key = None
            self.encryption_required = False

        self.aes_key = os.urandom(32)  # 256-bit AES key
        self.iv = os.urandom(32)       # 32-byte IV to match browser behavior


    async def login(self):
        await self.initialize()

        # Login parameters
        login_params = {
            "Input_Account": self.username,
            "Input_Passwd": self.password_b64,
            "currLang": "en",
            "RememberPassword": 0,
        }

        logger.debug(f"login info: encryption_required: {self.encryption_required}")

        if self.encryption_required:
            login_json = self.encrypt_request(login_params)
        else:
            login_json = json.dumps(login_params)

        headers = {
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Accept-Language': 'en-GB,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br, zstd',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'If-Modified-Since': 'Thu, 01 Jun 1970 00:00:00 GMT',
            'X-Requested-With': 'XMLHttpRequest',
            'Origin': self.url,
            'DNT': '1',
            'Sec-GPC': '1',
            'Connection': 'keep-alive',
            'Referer': f'{self.url}/login',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:142.0) Gecko/20100101 Firefox/142.0'
        }

        r = await self._post("/UserLogin", data=login_json.encode("utf-8"), headers=headers)
        if self.encryption_required:
            response_data = self.decrypt_response(r)
        else:
            response_data = r
        
        logger.debug(f"login info: response_data: {response_data}")

        self.sessionkey = response_data["sessionkey"]
        return True

    async def logout(self, sessionkey=None):
        if sessionkey is None:
            sessionkey = self.sessionkey
        await self._get(f"/cgi-bin/UserLogout?sessionkey={sessionkey}")

    async def connect(self):
        if self.sessionkey is None:
            await self.login()
        r = await self.get_status()
        if r is None:
            raise NR7101Exception("Connection failure")
        # Check login
        #await self._get("/UserLoginCheck")

    async def get_status(self, retries=2):

        # Define endpoint priorities based on router type
        endpoints_to_try = [
            ("cellwan_status", "cellular"),
            ("Traffic_Status", "traffic"),
            ("cardpage_status", "cardpage"),
            ("lan", "lan"),
            ("lanhosts", "lanhosts"),
            ("wifi_easy_mesh", "wifi_mesh"),
            ("one_connect", "one_connect"),
            ("cellwan_sms", "sms"),
            ("status", "device"),
        ]

        while retries > 0:
            try:
                result = {}
                successful_endpoints = 0

                for endpoint, key in endpoints_to_try:
                    try:
                        data = await self.get_json_object(endpoint)
                        if data:
                            # Special handling for traffic data
                            if endpoint == "Traffic_Status":
                                data = parse_traffic_object(data)
                            result[key] = data
                            successful_endpoints += 1
                    except ClientResponseError as e:
                        logger.debug(f"Error get_status, url: {endpoint} , error: {e}")
                        if e.status == 401:
                            # Re-raise 401 to trigger login retry
                            raise
                    except Exception as e:
                        logger.debug(f"Error get_status, url: {endpoint} , error: {e}")
                        continue

                if successful_endpoints > 0:
                    return result

            except ClientResponseError as e:
                logger.debug(f"Error get_status, error: {e}")
                if e.status == 401:
                    # Unauthorized - attempt login
                    login_success = await self.login()
                    if not login_success:
                        break
                elif e.status == 500:
                    # Internal server error - retry without cookies
                    await self.clear_cookies()
                    await self.login()
                retries -= 1
        return None

    async def probe_available_endpoints(self):
        """Probe which endpoints are available on this router for debugging."""
        endpoints_to_probe = [
            "cellwan_status",
            "cellwan_sms",
            "Traffic_Status",
            "cardpage_status",
            "lan",
            "lanhosts",
            "wifi_easy_mesh",
            "one_connect",
            "status",
            "paren_ctl",
            "wlan_status",
            "eth_status"
        ]

        available_endpoints = []
        for endpoint in endpoints_to_probe:
            try:
                data = await self.get_json_object(endpoint)
                if data:
                    available_endpoints.append(endpoint)
            except Exception:
                continue

        return available_endpoints

    async def clear_cookies(self):
        """Cancella i cookie dalla sessione e dal dizionario interno."""
        if self.session and hasattr(self.session, "cookie_jar"):
            self.session.cookie_jar.clear()
            
    async def get_json_object(self, oid):
        if not self.sessionkey:
            await self.login()

        path = f"/cgi-bin/DAL?oid={oid}"
        if self.sessionkey:
            path += f"&sessionkey={self.sessionkey}"

        try:
            r = await self._get(path)
        except ClientResponseError as e:
            logger.debug(f"Error get_json_object, url: {path} , error: {e}")
            if e.status in (401, 500):
                await self.clear_cookies()
                await self.initialize()
                await self.login()
                path = f"/cgi-bin/DAL?oid={oid}"
                if self.sessionkey:
                    path += f"&sessionkey={self.sessionkey}"
                r = await self._get(path)
            else:
                raise
        
        if self.encryption_required:
            j = self.decrypt_response(r)
        else:
            j = r

        if j.get("result") != "ZCFG_SUCCESS" or not j.get("Object"):
            return None
        return j["Object"][0]

    async def reboot(self):
        if self.sessionkey is None:
            await self.login()
        j = await self._post(f"/cgi-bin/Reboot?sessionkey={self.sessionkey}")
        assert j["result"] == "ZCFG_SUCCESS"

    def encrypt_request(self, json_data: dict) -> str:
        # Use compact JSON formatting to match browser behavior
        json_body = json.dumps(json_data, separators=(',', ':')).encode('utf-8')
        padded = pad(json_body, 16)

        # Encrypt the login parameters using AES (use only first 16 bytes of IV for CBC)
        cipher = AES.new(self.aes_key, AES.MODE_CBC, self.iv[:16])
        ciphertext = cipher.encrypt(padded)
        content_b64 = base64.b64encode(ciphertext).decode()

        if not self.rsa_key:
            raise Exception("No RSA key available for encryption")

        try:
            rsa_key = RSA.import_key(self.rsa_key.encode('utf-8'))
            cipher_rsa = PKCS1_v1_5.new(rsa_key)

            # Encrypt the base64-encoded AES key
            base64_encoded_key = base64.b64encode(self.aes_key)
            encrypted_key = cipher_rsa.encrypt(base64_encoded_key)
            key_b64 = base64.b64encode(encrypted_key).decode()

            iv_b64 = base64.b64encode(self.iv).decode()

            return json.dumps({
                "content": content_b64,
                "key": key_b64,
                "iv": iv_b64
            })

        except Exception as e:
            logger.error(f"Encryption failed: {e}")
            raise

    def decrypt_response(self, encrypted_json: dict) -> dict:
        # Decode base64 values
        response_iv = base64.b64decode(encrypted_json["iv"])
        ciphertext = base64.b64decode(encrypted_json["content"])

        # Use the response IV for decryption (first 16 bytes for AES-CBC)
        iv_for_decrypt = response_iv[:16]

        # Decrypt with AES (CBC mode) using the same key as request encryption
        cipher = AES.new(self.aes_key, AES.MODE_CBC, iv_for_decrypt)
        decrypted_padded = cipher.decrypt(ciphertext)

        # Try standard unpadding first
        try:
            decrypted_data = unpad(decrypted_padded, 16)
        except ValueError:
            # Fallback for routers that don't use proper PKCS7 padding
            try:
                # Remove trailing null bytes
                decrypted_data = decrypted_padded.rstrip(b'\x00')
                if len(decrypted_data) == len(decrypted_padded) and len(decrypted_padded) > 0:
                    # Try manual PKCS7 unpadding
                    padding_length = decrypted_padded[-1]
                    if 0 < padding_length <= 16:
                        decrypted_data = decrypted_padded[:-padding_length]
                    else:
                        decrypted_data = decrypted_padded
            except Exception:
                # Last resort: use raw decrypted data
                decrypted_data = decrypted_padded

        # Decode and parse as JSON
        try:
            json_string = decrypted_data.decode("utf-8")
            return json.loads(json_string)
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            logger.error(f"Error processing JSON response: {e}")
            raise Exception(f"Failed to process decrypted response: {e}")


def parse_traffic_object(obj):
    ret = {}
    if obj and "ipIface" in obj and "ipIfaceSt" in obj:
        for iface, iface_st in zip(obj["ipIface"], obj["ipIfaceSt"]):
            if "X_ZYXEL_IfName" in iface and iface["X_ZYXEL_IfName"]:
                ret[iface["X_ZYXEL_IfName"]] = iface_st
    return ret