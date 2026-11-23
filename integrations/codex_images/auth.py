"""Loopback PKCE sign-in with isolated memory and OS-protected persistence."""
import base64
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import secrets
import ssl
import sys
import time
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener
import webbrowser

ISSUER = "https://auth.openai.com"
# Public Codex browser registration used by the local prototype. This native
# endpoint is experimental, distinct from OpenAI's third-party OSS registration.
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CALLBACK = "http://localhost:1455/auth/callback"
IMAGE_URL = "https://chatgpt.com/backend-api/codex/images/generations"
MAX_RESPONSE = 96 * 1024 * 1024


class AccessDenied(RuntimeError):
    pass


class OAuthRejected(AccessDenied):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def verified_https_context():
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
            raise RuntimeError("Repair Blender's trusted HTTPS certificate bundle and retry.") from None
        if not context.cert_store_stats().get("x509_ca"):
            raise RuntimeError("No trusted HTTPS certificates are available in this Blender runtime.")
    return context


def request_json(url, *, data=None, headers=None, form=False):
    payload = None if data is None else (urlencode(data).encode() if form else json.dumps(data).encode())
    headers = {"User-Agent": "remi-local-codex-images/0.1", **(headers or {})}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
    try:
        with build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=verified_https_context())).open(Request(url, data=payload, headers=headers), timeout=300) as response:
            raw = response.read(MAX_RESPONSE + 1)
    except HTTPError as error:
        # Never persist an upstream body, which might contain credentials or prompts.
        if url == ISSUER + "/oauth/token":
            try:
                body = json.loads(error.read(8192))
                code = body.get("error")
            except (ValueError, OSError):
                code = None
            if code in {"invalid_grant", "invalid_refresh_token", "refresh_token_expired", "refresh_token_reused", "refresh_token_invalidated", "token_expired"}:
                raise OAuthRejected("ChatGPT sign-in expired or was disconnected. Continue with ChatGPT again.") from None
        if error.code in (401, 403):
            raise AccessDenied("Codex image access was denied. Sign in again; your account may not support this experimental endpoint.") from None
        if error.code == 429:
            raise RuntimeError("ChatGPT image usage is limited right now. Wait before trying again.") from None
        raise RuntimeError(f"Codex image service returned HTTP {error.code}.") from None
    if len(raw) > MAX_RESPONSE:
        raise ValueError("Codex response exceeded the image size limit.")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Codex returned an unexpected response.")
    return value


def decode64(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def claims(token):
    return json.loads(decode64(token.split(".")[1]))


def validate_identity(token, nonce, *, now=None):
    """Verify RS256 using the issuer JWKS, including PKCS#1 v1.5 padding."""
    if not isinstance(token, str) or len(token) > 65536:
        raise ValueError("Missing or invalid sign-in identity.")
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid sign-in identity.")
    header, identity = json.loads(decode64(parts[0])), claims(token)
    if header.get("alg") != "RS256":
        raise ValueError("Unexpected sign-in signature algorithm.")
    keys = request_json(ISSUER + "/.well-known/jwks.json").get("keys", [])
    key = next((key for key in keys if key.get("kid") == header.get("kid") and key.get("kty") == "RSA" and key.get("alg", "RS256") == "RS256" and key.get("use", "sig") == "sig"), None)
    if not key:
        raise ValueError("Sign-in signature key was not found.")
    modulus, exponent = int.from_bytes(decode64(key["n"]), "big"), int.from_bytes(decode64(key["e"]), "big")
    size = (modulus.bit_length() + 7) // 8
    signature = decode64(parts[2])
    if not 256 <= size <= 1024 or len(signature) != size or int.from_bytes(signature, "big") >= modulus or exponent != 65537:
        raise ValueError("Invalid sign-in signature.")
    digest = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256((parts[0] + "." + parts[1]).encode()).digest()
    expected = b"\x00\x01" + b"\xff" * (size - len(digest) - 3) + b"\x00" + digest
    actual = pow(int.from_bytes(signature, "big"), exponent, modulus).to_bytes(size, "big")
    if not hmac.compare_digest(expected, actual):
        raise ValueError("Invalid sign-in signature.")
    now = time.time() if now is None else now
    audience = identity.get("aud", [])
    audience = [audience] if isinstance(audience, str) else audience
    if (identity.get("iss") != ISSUER or CLIENT_ID not in audience or not identity.get("sub")
            or identity.get("nonce") != nonce or not isinstance(identity.get("exp"), (int, float))
            or not math.isfinite(identity["exp"]) or identity["exp"] <= now
            or not isinstance(identity.get("nbf", 0), (int, float))
            or not math.isfinite(identity.get("nbf", 0)) or identity.get("nbf", 0) > now + 30):
        raise ValueError("Invalid or expired sign-in identity.")
    return identity


class Connection:
    def __init__(self, vault=None):
        self.vault = vault
        self.persistence_notice = ""
        self.tokens = None
        self.expires = 0
        self.account_headers = {}

    def sign_in(self, progress):
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        result = {}

        class Callback(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # Callback URLs contain a one-time authorization code.

            def do_GET(self):
                url = urlsplit(self.path)
                query = parse_qs(url.query)
                valid = (self.headers.get("Host") in ("localhost:1455", "127.0.0.1:1455")
                         and url.path == "/auth/callback" and query.get("state") == [state])
                if not valid:
                    self.send_error(400, "Invalid callback")
                    return
                if query.get("error"):
                    result["error"] = "ChatGPT sign-in was declined."
                elif len(query.get("code", [])) == 1:
                    result["code"] = query["code"][0]
                else:
                    result["error"] = "ChatGPT sign-in did not return a code."
                body = b"<!doctype html><title>Remi</title><p>Return to Blender to continue.</p>"
                self.send_response(200)
                for key, value in {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'"}.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        try:
            server = HTTPServer(("127.0.0.1", 1455), Callback)
        except OSError:
            raise RuntimeError("ChatGPT sign-in needs local port 1455. Finish or close another Codex sign-in, then try again.") from None
        server.timeout = 0.25
        # Bound a local client's time to deliver its HTTP request.
        original_get_request = server.get_request
        def get_request():
            sock, address = original_get_request()
            sock.settimeout(2)
            return sock, address
        server.get_request = get_request
        url = ISSUER + "/oauth/authorize?" + urlencode({
            "client_id": CLIENT_ID, "response_type": "code", "redirect_uri": CALLBACK,
            "scope": "openid profile email offline_access", "id_token_add_organizations": "true",
            "state": state, "nonce": nonce, "code_challenge_method": "S256",
            "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("="),
        })
        try:
            progress("Continue with ChatGPT in your browser…")
            if not webbrowser.open(url):
                raise RuntimeError("Could not open your browser for ChatGPT sign-in.")
            deadline = time.monotonic() + 600
            while not result and time.monotonic() < deadline:
                server.handle_request()
        finally:
            server.server_close()
        if not result:
            raise RuntimeError("ChatGPT sign-in timed out. Try again.")
        if result.get("error"):
            raise RuntimeError(result["error"])
        tokens = request_json(ISSUER + "/oauth/token", form=True, data={
            "grant_type": "authorization_code", "client_id": CLIENT_ID,
            "code": result["code"], "code_verifier": verifier, "redirect_uri": CALLBACK,
        })
        validate_identity(tokens.get("id_token"), nonce)
        self._accept(tokens)
        self._persist()

    def _scope(self):
        return "chatgpt-codex:" + CLIENT_ID

    def _persist(self):
        if self.vault is None:
            return
        try:
            self.vault.save(self._scope(), {"tokens": self.tokens, "expires_at": self.expires})
            self.persistence_notice = ""
        except RuntimeError as error:
            self.persistence_notice = str(error)
            try:
                self.vault.clear(self._scope())
            except RuntimeError:
                pass

    def restore(self):
        if self.vault is None:
            return False
        value = self.vault.load(self._scope())
        if value is None:
            return False
        try:
            if not isinstance(value, dict) or not isinstance(value.get("tokens"), dict):
                raise ValueError
            expires = value["expires_at"]
            if not isinstance(expires, (int, float)) or not math.isfinite(expires):
                raise ValueError
            self._accept(value["tokens"])
            self.expires = expires
        except (ValueError, KeyError, TypeError):
            self.vault.clear(self._scope())
            self.tokens = None
            raise ValueError("The saved ChatGPT login is invalid. Continue with ChatGPT again.") from None
        return True

    def disconnect(self):
        if self.vault is not None:
            with self.vault.lock(self._scope()):
                self.vault.clear(self._scope())
        self.tokens = None
        self.expires = 0

    def _accept(self, tokens):
        if not isinstance(tokens.get("access_token"), str) or not tokens["access_token"]:
            raise ValueError("ChatGPT sign-in did not authorize a connection.")
        auth = claims(tokens["access_token"]).get("https://api.openai.com/auth", {})
        headers = {}
        for name, value in (("ChatGPT-Account-ID", auth.get("chatgpt_account_id")),
                            ("x-openai-internal-codex-residency", auth.get("chatgpt_data_residency") or auth.get("chatgpt_compute_residency"))):
            if value:
                if not isinstance(value, str) or len(value) > 256 or not value.isascii() or any(ord(c) < 32 or ord(c) == 127 for c in value):
                    raise ValueError("Unexpected ChatGPT account information.")
                headers[name] = value
        lifetime = float(tokens.get("expires_in", 3600))
        if not math.isfinite(lifetime) or not 0 <= lifetime <= 365 * 24 * 3600:
            raise ValueError("Unexpected ChatGPT connection lifetime.")
        self.tokens = tokens
        self.expires = time.time() + lifetime
        self.account_headers = headers

    def authorize(self, progress, *, allow_sign_in=True):
        from contextlib import nullcontext
        guard = self.vault.lock(self._scope()) if self.vault is not None else nullcontext()
        with guard:
            # Always read the latest rotation. Another Blender process may have
            # refreshed or explicitly disconnected this saved connection.
            if self.vault is not None and not self.persistence_notice:
                try:
                    restored = self.restore()
                except RuntimeError as error:
                    self.persistence_notice = str(error)
                    restored = False
                if not restored and not self.persistence_notice:
                    self.tokens = None
            if self.tokens and self.expires < time.time() + 60:
                refresh = self.tokens.get("refresh_token")
                if not refresh:
                    self.tokens = None
                    if self.vault is not None:
                        self.vault.clear(self._scope())
                else:
                    try:
                        updated = request_json(ISSUER + "/oauth/token", form=True, data={
                            "grant_type": "refresh_token", "client_id": CLIENT_ID, "refresh_token": refresh,
                        })
                    except (OAuthRejected, AccessDenied):
                        self.tokens = None
                        if self.vault is not None:
                            self.vault.clear(self._scope())
                        raise
                    except Exception:
                        # Keep rotating credentials through transient failures.
                        raise RuntimeError("ChatGPT connection could not be refreshed. Check your connection and try again.") from None
                    self._accept({**self.tokens, **updated})
                    self._persist()
            if not self.tokens:
                if not allow_sign_in:
                    return None
                self.sign_in(progress)
            return {"Authorization": "Bearer " + self.tokens["access_token"], **self.account_headers}
