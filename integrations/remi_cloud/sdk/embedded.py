"""Self-contained SDK session for embedded clients. Tokens stay in memory.

No bpy, provider/payment SDK, proxy discovery, environment token, or disk token
store. The host supplies its registered first-party device client identity.
"""
from datetime import datetime, timezone
from http.client import HTTPException
import json
import re
import ssl
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit, urlencode
from urllib.request import Request, HTTPRedirectHandler, HTTPSHandler, build_opener

DEFAULT_API = "https://remi3d.app/api/v1"


class CloudError(RuntimeError):
    def __init__(self, message, *, code="cloud_error", status=None, retryable=False):
        super().__init__(message)
        self.code, self.status, self.retryable = code, status, retryable


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def verified_https_context():
    """Blender's embedded OpenSSL may have no default CA paths. Use a trusted
    host-provided bundle without disabling hostname or certificate validation.
    The same opener verifies both API requests and private result downloads.
    """
    context = ssl.create_default_context()
    if not context.cert_store_stats().get("x509_ca"):
        try:
            import certifi
        except ImportError:
            certifi = None
        try:
            if certifi is not None:
                context.load_verify_locations(cafile=certifi.where())
            elif sys.platform == "darwin":
                context.load_verify_locations(cafile="/etc/ssl/cert.pem")
        except (OSError, ssl.SSLError):
            raise CloudError("Trusted HTTPS certificates could not be loaded. Repair Blender's certificate bundle and retry.") from None
        if not context.cert_store_stats().get("x509_ca"):
            raise CloudError("No trusted HTTPS certificates are available in this Python runtime. Install a trusted certificate bundle and retry.")
    return context


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
    def __init__(self, base_url=DEFAULT_API, *, client_id="remi-cli", realtime=True):
        if client_id not in ("remi-cli", "remi-blender"):
            raise CloudError("Use a registered first-party Remi device client.")
        self.client_id = client_id
        self.base_url = api_url(base_url)
        url = urlsplit(self.base_url)
        self.auth_url = url._replace(path=url.path[:-len("/api/v1")] + "/api/auth").geturl()
        self._origin = url._replace(path="", query="", fragment="").geturl()
        # No proxy discovery, cookies, redirect following, environment token or
        # plaintext persistence. A Blender session is deliberately independent.
        from urllib.request import ProxyHandler
        self._http = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=verified_https_context()))
        self._token = None
        self._expiry = None
        self.profile = None
        self._realtime_enabled = realtime
        self._socket = None
        self._socket_lock = threading.RLock()
        self.project_id = None

    def select_project(self, project_id=None):
        from .models import identifier
        project_id=identifier(project_id).lower() if project_id is not None else None
        if project_id!=self.project_id:
            if self._socket is not None:self._socket.close()
            self._socket=None
            self.project_id=project_id

    def _live(self):
        with self._socket_lock:
            if self._socket is None:
                from .realtime import RealtimeSession
                self._socket = RealtimeSession(self.base_url, lambda: self._token,
                    ssl_context=verified_https_context(), project_id=self.project_id)
            return self._socket

    def _request(self, method, path, *, auth=False, payload=None, api=False, body=None, headers=None):
        extra = headers or {}
        headers = {"Accept": "application/json", "Origin": self._origin,
                   "User-Agent": "remi-cloud-embedded/0.1"}
        headers.update(extra)
        if auth and api and self.project_id:
            headers.setdefault("X-Remi-Project",self.project_id)
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
                limit=2*1024*1024 if api and path.startswith("/board") else 262144
                raw = response.read(limit+1)
                if len(raw) > limit:
                    raise CloudError("Remi Cloud response exceeded its limit.")
            return status, json.loads(raw)
        except CloudError:
            raise
        except (URLError, OSError, ValueError, TimeoutError, HTTPException):
            # Do not copy server bodies, URLs with secrets or transport exceptions.
            raise CloudError("Could not verify the Remi Cloud response. Check the connection and API URL.", retryable=True) from None

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
            status, value = self._request("POST", "/device/code", payload={"client_id":self.client_id})
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
                    "client_id":self.client_id,"device_code":device,
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
        if self._token and self._realtime_enabled:
            status, value = 200, self.api("GET", "/capabilities")
        else:
            status, value = self._request("GET", "/capabilities", api=True)
        from .models import Capabilities
        from .errors import ProtocolError
        try:
            if status != 200: raise ValueError
            parsed = Capabilities.parse(value)
        except (ValueError,ProtocolError):
            raise CloudError("Remi Cloud returned invalid generation capabilities.")
        return {"enabled":parsed.enabled,"operations":parsed.operations,"inputModes":parsed.input_modes,
                **({"imageGeneration":parsed.image_generation} if parsed.image_generation else {}),
                **({"modelGeneration":parsed.model_generation} if parsed.model_generation else {})}

    def api(self, method, path, *, payload=None, body=None, headers=None):
        control=(method=="GET" and (path=="/capabilities" or path.startswith("/jobs"))) or (method=="POST" and (path=="/jobs" or path.endswith("/cancel") or path.endswith("/download")))
        if self._realtime_enabled and control:
            from .errors import APIError, RemiError
            try:
                content = payload if body is None else json.loads(body)
                status, value = self._live().request(method,path,body=content,key=(headers or {}).get("Idempotency-Key"))
            except APIError as error:
                if error.status == 401: self.clear()
                raise CloudError("The live Cloud connection was rejected. Sign in again or check access.",code=error.code,status=error.status) from None
            except RemiError:
                raise CloudError("The live Cloud connection was interrupted. Resume the same saved request; no work was resubmitted.", code="realtime_unavailable", retryable=True) from None
        else:
            status, value = self._request(method,path,auth=True,api=True,payload=payload,body=body,headers=headers)
        if status not in (200,201):
            messages = {401:"Your Cloud session expired or was revoked. Sign in again.",
                        402:"Cloud spending or payment access does not cover this request.",
                        403:"This Cloud session does not have permission for this request.",
                        404:"The owned Cloud resource was not found.",
                        409:"The Cloud request conflicts with its original request or state.",
                        413:"The Cloud input exceeds its size limit.",
                        415:"The Cloud input format is not supported.",
                        429:"Cloud rejected this request because a limit was reached. Resume the same saved request after the limit is cleared."}
            if status == 401: self.clear()
            # Use known codes and our own text, never arbitrary response messages
            # that could reflect credentials, signed URLs or sensitive input.
            limits = {
                "modal_test_limit_reached": ("The Cloud promotional test batch is exhausted. No new generation was started. An administrator must extend the test allocation before you can resume this request.", False),
                "too_many_jobs": ("Your account has unfinished Cloud test jobs. Wait for one to finish, then resume this same request.", True),
                "job_quota_exceeded": ("Your Cloud account has reached its open-job limit. Wait for a job to finish, then resume this same request.", True),
                "rate_limited": ("Your Cloud account has reached its request rate limit. Wait briefly, then resume this same request.", True),
                "storage_quota_exceeded": ("Your Cloud account has reached its storage limit. Resume this same request after storage is available.", False),
                "upload_busy": ("Cloud upload capacity is busy. Resume this same request shortly.", True),
            }
            error = value.get("error") if isinstance(value, dict) else None
            code = error.get("code") if isinstance(error, dict) else None
            if not isinstance(code,str):code=None
            if status == 429 and isinstance(code, str) and code in limits:
                message, retryable = limits[code]
                raise CloudError(message, code=code, status=status, retryable=retryable)
            message={"revision_conflict":"This card changed on another device. Your local revision is kept; refresh the project before applying again.","source_deleted":"This shared asset was deleted on another device.","not_found":"The project or shared asset is unavailable, or access was removed."}.get(code,messages.get(status,"Cloud could not confirm this request."))
            raise CloudError(message,code=code,status=status,retryable=status in (429,500,502,503,504))
        if not isinstance(value,dict): raise CloudError("Cloud returned an invalid API response.")
        return value

    def watch_board(self, *, timeout=86400, cancelled=lambda:False):
        from .errors import APIError, RemiError
        try:yield from self._live().watch_board(timeout=timeout,cancelled=cancelled)
        except APIError as error:
            if error.status==401:self.clear()
            raise CloudError("Shared project access changed. Choose another project or sign in again.",code=error.code,status=error.status) from None
        except (RemiError,TimeoutError):
            raise CloudError("Project live updates were interrupted.",code="realtime_unavailable",retryable=True) from None

    def watch_job(self, job_id, *, timeout=1800, cancelled=lambda: False):
        from .errors import APIError, RemiError
        try:
            yield from self._live().watch_job(job_id,timeout=timeout,cancelled=cancelled)
        except APIError as error:
            if error.status == 401: self.clear()
            raise CloudError("The live Cloud subscription was rejected. Sign in again or check access.",code=error.code,status=error.status) from None
        except (RemiError, TimeoutError):
            raise CloudError("Live updates stopped. Resume the saved request; the Cloud job was not canceled.",code="realtime_unavailable",retryable=True) from None

    def clear(self):
        if self._socket is not None: self._socket.close()
        self._socket = None
        self._token = self._expiry = self.profile = None
        self.project_id = None

    def logout(self):
        if not self._token: return True
        try:
            status, value = self._request("POST", "/sign-out", payload={}, auth=True)
            return status == 200 and isinstance(value,dict) and value.get("success") is True
        except CloudError:
            return False
        finally:
            self.clear()
