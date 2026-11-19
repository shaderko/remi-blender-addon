"""Standard-library Remi device login. Sessions live only in worker memory."""
from datetime import datetime, timezone
from http.client import HTTPException
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit, urlencode
from urllib.request import Request, HTTPRedirectHandler, build_opener

CLIENT_ID = "remi-blender"
DEFAULT_API = "https://remi3d.app/api/v1"


class CloudError(RuntimeError):
    def __init__(self, message, *, code="cloud_error", status=None):
        super().__init__(message)
        self.code, self.status = code, status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def api_url(value):
    try:
        url = urlsplit(value)
        local = url.hostname in ("localhost", "127.0.0.1", "::1")
        if (not isinstance(value, str) or len(value) > 2048 or not url.hostname
                or (url.scheme != "https" and not (url.scheme == "http" and local))
                or url.username or url.password or url.query or url.fragment
                or not url.path.endswith("/api/v1") or "%" in url.netloc):
            raise ValueError
        _ = url.port
    except (ValueError, TypeError, AttributeError):
        raise CloudError("Use the HTTPS Remi Cloud /api/v1 URL; HTTP is allowed only on loopback.") from None
    return url.geturl()


def origin(value):
    url = urlsplit(value)
    return url.scheme, url.hostname, url.port or (443 if url.scheme == "https" else 80)


class CloudSession:
    def __init__(self, base_url=DEFAULT_API):
        self.base_url = api_url(base_url)
        url = urlsplit(self.base_url)
        self.auth_url = url._replace(path=url.path[:-len("/api/v1")] + "/api/auth").geturl()
        self._origin = url._replace(path="", query="", fragment="").geturl()
        # No proxy discovery, cookies, redirect following, environment token or
        # plaintext persistence. A Blender session is deliberately independent.
        from urllib.request import ProxyHandler
        self._http = build_opener(ProxyHandler({}), NoRedirect())
        self._token = None
        self._expiry = None
        self.profile = None

    def _request(self, method, path, *, auth=False, payload=None, api=False, body=None, headers=None):
        extra = headers or {}
        headers = {"Accept": "application/json", "Origin": self._origin,
                   "User-Agent": "remi-blender-cloud/0.1"}
        headers.update(extra)
        if auth:
            if not self._token:
                raise CloudError("Sign in to Remi Cloud first.")
            headers["Authorization"] = "Bearer " + self._token
        if payload is not None:
            body = json.dumps(payload,ensure_ascii=False,separators=(",",":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request((self.base_url if api else self.auth_url) + path,
                          data=body, headers=headers, method=method)
        try:
            try:
                response = self._http.open(request, timeout=20)
            except HTTPError as error:
                response = error
            with response:
                status = response.status
                if 300 <= status < 400:
                    raise CloudError("Remi Cloud returned a redirect. Check the API URL.")
                raw = response.read(262145)
                if len(raw) > 262144:
                    raise CloudError("Remi Cloud response exceeded its limit.")
            return status, json.loads(raw)
        except CloudError:
            raise
        except (URLError, OSError, ValueError, TimeoutError, HTTPException):
            # Do not copy server bodies, URLs with secrets or transport exceptions.
            raise CloudError("Could not verify the Remi Cloud response. Check the connection and API URL.") from None

    def status(self):
        status, value = self._request("GET", "/get-session?disableCookieCache=true&disableRefresh=true", auth=True)
        if status in (401,403) or (status == 200 and value is None):
            self.clear()
            raise CloudError("Your Remi Cloud session expired or was revoked. Sign in again.")
        if status != 200:
            raise CloudError("Remi Cloud could not verify this session.")
        try:
            user, session = value["user"], value["session"]
            expiry = datetime.fromisoformat(session["expiresAt"].replace("Z", "+00:00"))
            if (not all(isinstance(user.get(key),str) and user[key] for key in ("id","email"))
                    or expiry.tzinfo is None or expiry <= datetime.now(timezone.utc)):
                raise ValueError
            self._expiry = expiry
            self.profile = {"email": user["email"], "user_id": user["id"]}
            return dict(self.profile)
        except (KeyError,TypeError,AttributeError,ValueError):
            raise CloudError("Remi Cloud returned an invalid account session.") from None

    def login(self, publish, cancelled=lambda: False):
        if self._token:
            return self.status()
        code = None
        try:
            status, value = self._request("POST", "/device/code", payload={"client_id":CLIENT_ID})
            if status != 200:
                raise CloudError("Remi Cloud could not start Blender sign-in. The server may need an update.")
            device, code = value["device_code"], value["user_code"]
            verification, complete = value["verification_uri"], value["verification_uri_complete"]
            expiry, interval = value["expires_in"], value.get("interval",5)
            if (not isinstance(device,str) or not 16 <= len(device) <= 191
                    or not isinstance(code,str) or not re.fullmatch(r"[A-HJ-NP-Z2-9]{8}",code)
                    or type(expiry) is not int or not 1 <= expiry <= 1800
                    or type(interval) is not int or not 1 <= interval <= 60):
                raise ValueError
            expected = urlsplit(self.auth_url).path[:-len("/api/auth")] + "/auth/device"
            checked = api_url(self.base_url)  # Validate once more before a displayable challenge.
            for url in (verification,complete):
                parsed = urlsplit(url)
                if (origin(url) != origin(checked) or parsed.path != expected or parsed.username
                        or parsed.password or parsed.fragment or len(url)>2048):
                    raise ValueError
            if urlsplit(verification).query or parse_qs(urlsplit(complete).query) != {"user_code":[code]}:
                raise ValueError
            publish({"state":"approval", "user_code":code, "verification_url":complete,
                     "message":"Open Remi Cloud and approve the code shown in Blender."})
            deadline = time.monotonic() + expiry
            while time.monotonic() < deadline:
                wait_until = min(deadline, time.monotonic()+interval)
                while time.monotonic() < wait_until:
                    if cancelled(): raise CloudError("Sign-in cancelled.")
                    time.sleep(min(0.2, max(0,wait_until-time.monotonic())))
                if cancelled(): raise CloudError("Sign-in cancelled.")
                if time.monotonic() >= deadline: break
                status, token = self._request("POST", "/device/token", payload={
                    "client_id":CLIENT_ID,"device_code":device,
                    "grant_type":"urn:ietf:params:oauth:grant-type:device_code"})
                if status == 400 and isinstance(token,dict):
                    if token.get("error") == "authorization_pending": continue
                    if token.get("error") == "slow_down":
                        interval += 5
                        continue
                if status != 200:
                    raise CloudError("Sign-in was denied, expired or already used. Start sign-in again.")
                secret = token.get("access_token")
                if (not isinstance(secret,str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,256}",secret)
                        or str(token.get("token_type","")).lower() != "bearer"):
                    raise ValueError
                self._token = secret
                profile = self.status()
                if cancelled(): raise CloudError("Sign-in cancelled.")
                return profile
            raise CloudError("The sign-in code expired. Start sign-in again.")
        except CloudError:
            if self._token:
                self.logout()
            raise
        except (KeyError,TypeError,AttributeError,ValueError):
            if self._token:
                self.logout()
            raise CloudError("Remi Cloud returned an invalid sign-in response.") from None

    def capabilities(self):
        status, value = self._request("GET", "/capabilities", api=True)
        if __package__:
            from .contract.models import Capabilities
            from .contract.errors import ProtocolError
        else:
            from contract.models import Capabilities
            from contract.errors import ProtocolError
        try:
            if status != 200: raise ValueError
            parsed = Capabilities.parse(value)
        except (ValueError,ProtocolError):
            raise CloudError("Remi Cloud returned invalid generation capabilities.")
        return {"enabled":parsed.enabled,"operations":parsed.operations,"inputModes":parsed.input_modes}

    def api(self, method, path, *, payload=None, body=None, headers=None):
        status, value = self._request(method,path,auth=True,api=True,payload=payload,body=body,headers=headers)
        if status not in (200,201):
            messages = {401:"Your Cloud session expired or was revoked. Sign in again.",
                        402:"Cloud spending or payment access does not cover this request.",
                        403:"This Cloud session does not have permission for this request.",
                        404:"The owned Cloud resource was not found.",
                        409:"The Cloud request conflicts with its original request or state.",
                        413:"The Cloud input exceeds its size limit.",
                        415:"The Cloud input format is not supported.",
                        429:"Cloud capacity is busy. Resume this same request later."}
            if status == 401: self.clear()
            raise CloudError(messages.get(status,"Cloud could not confirm this request. Resume the same saved request."),status=status)
        if not isinstance(value,dict): raise CloudError("Cloud returned an invalid API response.")
        return value

    def clear(self):
        self._token = self._expiry = self.profile = None

    def logout(self):
        if not self._token: return True
        try:
            status, value = self._request("POST", "/sign-out", payload={}, auth=True)
            return status == 200 and isinstance(value,dict) and value.get("success") is True
        except CloudError:
            return False
        finally:
            self.clear()
