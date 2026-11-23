"""Local Codex auth and image protocol regressions; no real account or GPU."""
import base64
from http.client import HTTPConnection
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
import zlib

ROOT = Path(__file__).resolve().parents[1] / "integrations" / "codex_images"
def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

auth = load("remi_codex_auth_test", "auth.py")
with patch.dict(sys.modules, {"auth": auth}):
    worker = load("remi_codex_worker_test", "worker.py")

# Synthetic signing fixture, never used as a real credential.
N = int("ad7a607a0a243a29855b00b7f4a9c6f9dba3276622eb2e8e29c5a2baa20b9cd29d967f532758548a8a46a3d519288cd2e7fc4c2269ed01f629599b95c92ddc8fa35468f9f19cba18eac2b2f247263f6ebd54edce8c23577b9f512c56d07eaddcdc45f55a20aa77dc5407d9cedd62f6ff0f9213269edc45dfe1ee69fa77acd2bf82d64dfeb92f3955daf4576d38fc80152be4f38083a9a992b5dd4817ee9209305b47da8c5d2e9f68eb12eeb33876ebabb86be45d28849ddf071987042409c8f61e4c473d57f52d1f66fb432cbca6efbb750e2cca82292ac87a85a841e86b3d05156caf466c193e81939bdc71a6ac97ba19ded3c3a6951e94dea1d75c453b2931", 16)
D = int("064bd8e4d109a9d1403972741ac58f36da1788ba6aa5c7cba9dd3617e941fbe4028b77ed5212210662b8a7e157ff8a65ecbcd1dc15656062f972fac443d81de75a3599f649644776584deefdff62565ecbf1214cf3f5e3ead518568043a2b7677ad8b0ac1fdfa4084b85f9ec29c999de9dc76ed9c1de0b8cb1c7ec1186c6af62ebef6554fdb6365840cbedaa4d477d5cabeb163cc99dd304c2b270f7216badeb0ef3a08a897cf5452fc4eb4bd24ced7af072fba919b639e71dd5ff8b603080eef9a563ec765376a73d131a3a967cda6dd366af198bae9e3438a1d5093c166ae082c8b1ca68f21e8c60003149078e0bff2b3ff3789e7a97e1f267cb7aaf8e3609", 16)
def b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")
def token(nonce="nonce", **overrides):
    header = b64(json.dumps({"kid": "test", "alg": "RS256"}).encode())
    claims = {"iss": auth.ISSUER, "aud": auth.CLIENT_ID, "sub": "synthetic-user", "nonce": nonce, "exp": time.time() + 600, **overrides}
    payload = b64(json.dumps(claims).encode())
    message = header + "." + payload
    digest = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(message.encode()).digest()
    block = b"\x00\x01" + b"\xff" * (256 - len(digest) - 3) + b"\x00" + digest
    return message + "." + b64(pow(int.from_bytes(block, "big"), D, N).to_bytes(256, "big"))
def keys():
    return {"keys": [{"kid": "test", "kty": "RSA", "n": b64(N.to_bytes(256, "big")), "e": "AQAB"}]}
def png(width=8, height=6):
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress((b"\x00" + b"\x88" * width * 3) * height)) + chunk(b"IEND", b"")


class CodexTests(unittest.TestCase):
    def test_embedded_python_loads_host_certificates_without_disabling_verification(self):
        context=Mock()
        context.cert_store_stats.side_effect=[{"x509_ca":0},{"x509_ca":1}]
        with patch.object(auth.ssl,"create_default_context",return_value=context), patch.object(auth.sys,"platform","darwin"), patch.dict(sys.modules,{"certifi":None}):
            self.assertIs(auth.verified_https_context(),context)
        context.load_verify_locations.assert_called_once_with(cafile="/etc/ssl/cert.pem")

    def test_access_denied_never_echoes_a_token_or_upstream_body(self):
        opener=Mock()
        opener.open.side_effect=HTTPError("https://chatgpt.com",403,"synthetic-secret",{},None)
        with patch.object(auth,"build_opener",return_value=opener),patch.object(auth,"verified_https_context",return_value=None):
            with self.assertRaises(auth.AccessDenied) as raised:
                auth.request_json(auth.IMAGE_URL,headers={"Authorization":"Bearer synthetic-secret"})
        self.assertNotIn("synthetic-secret",str(raised.exception))

    def test_identity_signature_nonce_audience_and_expiry(self):
        with patch.object(auth, "request_json", return_value=keys()):
            self.assertEqual(auth.validate_identity(token(), "nonce")["sub"], "synthetic-user")
            for value, nonce in [(token(), "wrong"), (token(aud="other"), "nonce"), (token(exp=0), "nonce"), (token(exp=float("nan")), "nonce"), (token(nbf=time.time()+3600), "nonce")]:
                with self.assertRaises(ValueError): auth.validate_identity(value, nonce)
            parts = token().split(".")
            parts[1] = b64(json.dumps({"sub": "tampered"}).encode())
            with self.assertRaises(ValueError): auth.validate_identity(".".join(parts), "nonce")

    def test_real_loopback_pkce_rejects_wrong_state_and_validates_identity(self):
        callbacks = []
        attempt = {}
        def callback(url):
            parsed = urlsplit(url); attempt.update({key: value[0] for key, value in parse_qs(parsed.query).items()})
            def send():
                for state in ("wrong-state", attempt["state"]):
                    c=HTTPConnection("127.0.0.1",1455,timeout=5)
                    c.request("GET", "/auth/callback?code=synthetic-code&state=" + state, headers={"Host": "localhost:1455"})
                    response=c.getresponse();callbacks.append(response.status);response.read();c.close()
            thread = threading.Thread(target=send); thread.start();attempt["thread"] = thread
            return True
        def request(url, **options):
            if url.endswith("jwks.json"): return keys()
            data=options["data"]
            self.assertEqual(data["client_id"], auth.CLIENT_ID)
            self.assertEqual(data["redirect_uri"], auth.CALLBACK)
            self.assertEqual(b64(hashlib.sha256(data["code_verifier"].encode()).digest()), attempt["code_challenge"])
            return {"id_token": token(attempt["nonce"]), "access_token": token(), "refresh_token": "synthetic-refresh", "expires_in": 600}
        connection=auth.Connection()
        with patch.object(auth.webbrowser,"open",side_effect=callback), patch.object(auth,"request_json",side_effect=request):
            connection.sign_in(lambda message:None)
        attempt["thread"].join(5)
        self.assertEqual(callbacks,[400,200])
        self.assertEqual(connection.tokens["refresh_token"],"synthetic-refresh")

    def test_refresh_rotation_and_failed_refresh_forgets_connection(self):
        connection=auth.Connection();connection._accept({"access_token":token(),"refresh_token":"old","expires_in":0})
        with patch.object(auth,"request_json",return_value={"access_token":token(),"refresh_token":"new","expires_in":600}):
            self.assertIn("Authorization",connection.authorize(lambda message:None))
        self.assertEqual(connection.tokens["refresh_token"],"new")
        connection.expires=0
        with patch.object(auth,"request_json",side_effect=RuntimeError("denied")):
            with self.assertRaises(RuntimeError):connection.authorize(lambda message:None)
        self.assertIsNone(connection.tokens)

    def test_images_use_actual_dimensions_and_keep_secrets_out_of_metadata(self):
        class Connection:
            def authorize(self,progress):return {"Authorization":"Bearer synthetic-secret"}
        calls=[]
        def generate(url,**options):
            calls.append(options)
            return {"data":[{"b64_json":base64.b64encode(png()).decode()}]}
        with tempfile.TemporaryDirectory() as directory, patch.object(worker,"request_json",side_effect=generate):
            root=Path(directory)
            worker.generate(Connection(),{"directory":directory,"prompt":"Synthetic cube","count":2,"size":"1024x1024","quality":"low"},lambda phase:None)
            meta=json.loads((root/"meta.json").read_text())
            self.assertEqual((meta["width"],meta["height"]),(8,6))
            self.assertEqual(len(meta["images"]),2)
            self.assertTrue(json.loads((root/"complete.json").read_text())["complete"])
            self.assertTrue(all(call["data"]["n"]==1 and "images" not in call["data"] for call in calls))
            self.assertNotIn("synthetic-secret","".join(p.read_text() for p in root.glob("*.json")))

    def test_reference_edit_sends_ordered_bytes_and_freezes_them_for_batch(self):
        class Connection:
            def authorize(self,progress):return {}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);job=root/"job";job.mkdir()
            a=root/"a.png";b=root/"b.png";a.write_bytes(png(8,6));b.write_bytes(png(9,7))
            references=[{"path":str(p),"id":p.stem,"bytes":p.stat().st_size,"mtime_ns":p.stat().st_mtime_ns} for p in (b,a)]
            calls=[]
            def generate(url,**options):
                calls.append((url,options["data"]))
                # Source deletion after the first image cannot change this batch.
                a.unlink(missing_ok=True);b.unlink(missing_ok=True)
                return {"data":[{"b64_json":base64.b64encode(png()).decode()}]}
            with patch.object(worker,"request_json",side_effect=generate):
                worker.generate(Connection(),{"directory":str(job),"prompt":"Use image 1 geometry and image 2 colors","count":2,"references":references},lambda phase:None)
            self.assertEqual(len(calls),2)
            self.assertTrue(all(url.endswith("/images/edits") for url,_ in calls))
            expected=[png(9,7),png(8,6)]
            for _,payload in calls:
                self.assertEqual([base64.b64decode(item["image_url"].split(",",1)[1]) for item in payload["images"]],expected)
            meta=json.loads((job/"meta.json").read_text())
            self.assertEqual([item["id"] for item in meta["references"]],["b","a"])
            for item,data in zip(meta["references"],expected):
                self.assertEqual((job/item["file"]).read_bytes(),data)
                self.assertEqual(item["sha256"],hashlib.sha256(data).hexdigest())
            self.assertNotIn("data:image",(job/"meta.json").read_text())

    def test_invalid_missing_changed_and_excessive_context_never_calls_service(self):
        class Connection:
            def authorize(self,progress):raise AssertionError("Invalid context started sign-in")
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/"reference.png";source.write_bytes(png())
            valid={"path":str(source),"bytes":source.stat().st_size,"mtime_ns":source.stat().st_mtime_ns}
            invalid=[[{"path":str(root/"missing.png")}],[{**valid,"bytes":1}],[valid]*17]
            bad=root/"bad.png";bad.write_bytes(b"not an image");invalid.append([{"path":str(bad)}])
            for index,references in enumerate(invalid):
                job=root/str(index);job.mkdir()
                with patch.object(worker,"request_json",side_effect=AssertionError("Invalid context sent")):
                    with self.assertRaises(ValueError):worker.generate(Connection(),{"directory":str(job),"prompt":"Cube","count":1,"references":references},lambda phase:None)
                self.assertFalse((job/"complete.json").exists())
            with patch.object(worker,"MAX_REFERENCE_BYTES",len(png())+1):
                with self.assertRaises(ValueError):worker.prepare_references(root,[valid,valid])

    def test_corrupt_truncated_and_oversized_png_rejected(self):
        for data in (png()[:-2],png()[:40],b"not an image",png(8193,1)):
            with self.assertRaises(ValueError):worker.png_dimensions(data)
        corrupt=bytearray(png());corrupt[45]^=1
        with self.assertRaises(ValueError):worker.png_dimensions(corrupt)
        self.assertEqual(worker.png_dimensions(png()),(8,6))

    def test_provider_failure_does_not_confirm_completion(self):
        class Connection:
            def authorize(self,progress):return {}
        with tempfile.TemporaryDirectory() as directory,patch.object(worker,"request_json",return_value={"data":[{"url":"https://untrusted.example/image"}]}):
            with self.assertRaises(ValueError):worker.generate(Connection(),{"directory":directory,"prompt":"Cube","count":1},lambda phase:None)
            self.assertFalse((Path(directory)/"complete.json").exists())


if __name__ == "__main__":
    unittest.main()
