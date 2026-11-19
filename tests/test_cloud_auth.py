"""Real HTTP and isolated-process tests; no Blender, Modal or customer account."""
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from integrations.remi_cloud.protocol import CloudSession, CloudError, api_url
from integrations.remi_cloud.client import CloudProcess

TOKEN = "fixture_remi_blender_session_secret_12345"
DEVICE = "fixture_remi_blender_device_secret_12345"


class Server(ThreadingHTTPServer):
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_GET(self): self.requested()
    def do_POST(self): self.requested()
    def requested(self):
        server = self.server
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length",0))) or b"null")
        server.requests.append((self.path,dict(self.headers),body))
        self.assert_no_cookie = "Cookie" not in self.headers
        status = 200
        if self.path.endswith("/device/code"):
            if body != {"client_id":"remi-blender"}: status,value = 400,{}
            else: value = {"device_code":DEVICE,"user_code":"ABCDEFGH","verification_uri":server.base+"/auth/device","verification_uri_complete":server.base+"/auth/device?user_code=ABCDEFGH","expires_in":30,"interval":1}
            if server.bad_origin: value["verification_uri_complete"] = "https://evil.test/auth/device?user_code=ABCDEFGH"
        elif self.path.endswith("/device/token"):
            if server.denied: status,value = 400,{"error":"access_denied","error_description":TOKEN+DEVICE}
            elif body.get("device_code") != DEVICE or body.get("client_id") != "remi-blender": status,value = 400,{}
            else: value = {"access_token":TOKEN,"token_type":"Bearer"}
        elif self.path.startswith("/api/auth/get-session"):
            if self.headers.get("Authorization") != "Bearer "+TOKEN or server.revoked: status,value = 401,{}
            else: value = {"user":{"id":"fixture-user","email":"test@example.test"},"session":{"id":"fixture-session","token":TOKEN,"expiresAt":(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()}}
        elif self.path.endswith("/sign-out"):
            server.revoked = True
            value = {"success":True}
        elif self.path.endswith("/capabilities"):
            value = {"apiVersion":"v1","enabled":False,"operations":{"generate":False,"generate_image":False,"reconstruct":False,"optimize":False,"pipeline":False},"inputModes":{"single":True,"batch":True,"multiview":False},"billing":{"currency":"EUR","spendCapsRequired":True}}
            if server.capabilities_enabled:
                value["enabled"] = True
                value["operations"]["generate_image"] = True
            if server.capabilities_fault == "missing": status, value = 404, {}
            if server.capabilities_fault == "malformed": value = {"enabled":True}
            if server.capabilities_fault == "html":
                self.send_response(503); self.end_headers(); self.wfile.write(b"<html>Not deployed</html>"); return
        elif self.path.endswith("/redirect"):
            self.send_response(302); self.send_header("Location",server.base+"/leak"); self.end_headers(); return
        else: status,value = 404,{}
        encoded = json.dumps(value).encode()
        self.send_response(status); self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(encoded))); self.end_headers(); self.wfile.write(encoded)


class CloudAuthTests(unittest.TestCase):
    def setUp(self):
        self.server = Server(("127.0.0.1",0),Handler)
        self.server.base = "http://127.0.0.1:"+str(self.server.server_port)
        self.server.requests = []
        self.server.bad_origin = self.server.denied = self.server.revoked = False
        self.server.capabilities_fault = None
        self.server.capabilities_enabled = False
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.api = self.server.base+"/api/v1"
    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
    def wait(self,predicate):
        deadline = time.monotonic()+8
        while time.monotonic()<deadline:
            value = predicate()
            if value: return value
            time.sleep(.02)
        self.fail("Timed out waiting for the owned Cloud subprocess")
    def test_device_login_profile_and_revoke_with_real_http(self):
        session = CloudSession(self.api); published = []
        profile = session.login(published.append)
        self.assertEqual(profile["email"],"test@example.test")
        self.assertNotIn(TOKEN,json.dumps(published)); self.assertNotIn(DEVICE,json.dumps(published))
        self.assertFalse(session.capabilities()["enabled"])
        self.assertTrue(session.logout()); self.assertTrue(self.server.revoked)
        for path,headers,body in self.server.requests:
            self.assertNotIn("Cookie",headers)
            self.assertEqual(headers.get("Origin"),self.server.base)
            if "/get-session" in path or "/sign-out" in path: self.assertEqual(headers["Authorization"],"Bearer "+TOKEN)
        self.assertIsNone(session.profile)
    def test_revocation_prevents_reusing_the_profile(self):
        session = CloudSession(self.api); session.login(lambda value:None)
        self.server.revoked = True
        with self.assertRaises(CloudError): session.status()
        self.assertIsNone(session.profile)
    def test_cross_origin_approval_is_rejected_before_polling(self):
        self.server.bad_origin = True
        with self.assertRaises(CloudError): CloudSession(self.api).login(lambda value:self.fail("unsafe challenge"))
        self.assertFalse(any(path.endswith("/device/token") for path,_,_ in self.server.requests))
    def test_redirect_is_not_followed_and_session_secret_is_not_forwarded(self):
        session = CloudSession(self.api)
        with self.assertRaises(CloudError): session._request("GET","/redirect")
        self.assertFalse(any(path.endswith("/leak") for path,_,_ in self.server.requests))
    def test_denial_never_echoes_server_error_or_saves_a_session(self):
        self.server.denied = True
        session = CloudSession(self.api)
        with self.assertRaises(CloudError) as caught: session.login(lambda value:None)
        self.assertNotIn(TOKEN,str(caught.exception)); self.assertNotIn(DEVICE,str(caught.exception)); self.assertIsNone(session.profile)
    def test_worker_keeps_secrets_out_of_status_files_and_restarts_signed_out(self):
        process = CloudProcess(sys.executable)
        try:
            process.command("login",self.api)
            self.wait(lambda: process.status().get("state")=="approval")
            self.wait(lambda: process.status().get("state")=="signed_in")
            directory = process.directory
            for path in directory.iterdir():
                self.assertNotIn(TOKEN,path.read_text()); self.assertNotIn(DEVICE,path.read_text())
            self.assertFalse(process.pending)
            process.command("logout",self.api)
            self.wait(lambda: process.status().get("state")=="signed_out" and not process.pending)
            self.assertTrue(self.server.revoked)
            process.cleanup(); self.assertFalse(directory.exists())
            process.start(); self.assertNotEqual(process.status().get("state"),"signed_in")
        finally: process.cleanup()
    def test_cancel_stops_an_owned_login_process(self):
        process = CloudProcess(sys.executable)
        try:
            process.command("login",self.api)
            self.wait(lambda: process.status().get("state")=="approval")
            child,directory = process.stop()
            child.wait(timeout=4); process.reap()
            self.assertFalse(directory.exists()); self.assertEqual(process.status()["state"],"signed_out")
        finally: process.cleanup()
    def test_login_and_refresh_survive_unavailable_generation_discovery(self):
        for fault in ("missing", "malformed", "html"):
            with self.subTest(fault=fault):
                self.server.revoked = False
                self.server.capabilities_fault = fault
                process = CloudProcess(sys.executable)
                try:
                    process.command("login", self.api)
                    value = self.wait(lambda: (v if (v := process.status()).get("state") == "signed_in" else None))
                    self.assertTrue(value["signed_in"])
                    self.assertEqual(value["email"], "test@example.test")
                    self.assertNotIn("capabilities", value)
                    self.assertIn("could not be checked", value["message"])
                    self.server.capabilities_fault = None
                    self.server.capabilities_enabled = True
                    process.command("status", self.api)
                    value = self.wait(lambda: (v if (v := process.status()).get("state") == "signed_in" else None))
                    self.assertTrue(value["capabilities"]["enabled"])
                    self.server.capabilities_fault = fault
                    process.command("status", self.api)
                    value = self.wait(lambda: (v if (v := process.status()).get("state") == "signed_in" else None))
                    self.assertNotIn("capabilities", value)  # No stale admission.
                    process.command("logout", self.api)
                    self.wait(lambda: process.status().get("state") == "signed_out" and not process.pending)
                    self.assertTrue(self.server.revoked)
                finally: process.cleanup()
    def test_worker_refresh_revocation_clears_account(self):
        process = CloudProcess(sys.executable)
        try:
            process.command("login", self.api)
            self.wait(lambda: process.status().get("state") == "signed_in")
            self.server.revoked = True
            process.command("status", self.api)
            value = self.wait(lambda: (v if (v := process.status()).get("state") == "error" else None))
            self.assertFalse(value["signed_in"])
            self.assertNotIn("email", value)
        finally: process.cleanup()
    def test_invalid_api_locations_and_environment_tokens_are_never_used(self):
        for value in ["http://evil.test/api/v1","https://user:secret@remi.test/api/v1","https://remi.test/api/v1?token=abc","https://remi.test/api/v1#secret","https://remi.test/other"]:
            with self.assertRaises(CloudError): api_url(value)
        with patch.dict("os.environ",{"REMI_ACCESS_TOKEN":TOKEN,"HTTPS_PROXY":"http://evil.test"}):
            with self.assertRaises(CloudError): CloudSession(self.api).status()
        self.assertEqual(self.server.requests,[])


if __name__=="__main__": unittest.main()
