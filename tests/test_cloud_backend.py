"""Optional real backend interop: REMI_CLOUD_CHECKOUT=/path/to/remi-cloud.

Uses that checkout's installed tsx, Better Auth and first-party auth plugin.
No production env, database, deployment, account or paid compute is used.
"""
import json
import os
import queue
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from integrations.remi_cloud.client import CloudProcess


@unittest.skipUnless(os.environ.get("REMI_CLOUD_CHECKOUT"), "Set REMI_CLOUD_CHECKOUT for real backend interop")
class BackendAuthTests(unittest.TestCase):
    def test_actual_backend_browser_approval_worker_profile_disabled_compute_and_revocation(self):
        cloud = Path(os.environ["REMI_CLOUD_CHECKOUT"]).resolve()
        fixture = subprocess.Popen([
            str(cloud / "apps/backend/node_modules/.bin/tsx"),
            str(ROOT / "tests/cloud_auth_backend_fixture.mts"), str(cloud),
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        process = CloudProcess(sys.executable)
        try:
            startup = queue.Queue()
            threading.Thread(target=lambda: startup.put(fixture.stdout.readline()), daemon=True).start()
            line = startup.get(timeout=15).strip()
            self.assertTrue(line.startswith("REMI_AUTH_FIXTURE "), line)
            base = line.split(" ", 1)[1]
            def request(path, body=None, cookie=None):
                headers = {"Origin": base}
                if cookie: headers["Cookie"] = cookie
                if body is not None: headers["Content-Type"] = "application/json"
                return urlopen(Request(base + "/api/auth" + path,
                    data=None if body is None else json.dumps(body).encode(), headers=headers), timeout=10)
            with request("/sign-up/email", {"email":"blender@example.test", "name":"Blender", "password":"test-only-remi-password-123"}) as response:
                cookie = response.headers["Set-Cookie"].split(";", 1)[0]
                user_id = json.load(response)["user"]["id"]
            def wait(state):
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    value = process.status()
                    if value.get("state") == state and (state == "approval" or not process.pending): return value
                    if value.get("state") == "error": self.fail(value["message"])
                    time.sleep(.02)
                self.fail(f"Backend interop did not reach {state}")
            process.command("login", base + "/api/v1")
            approval = wait("approval")
            self.assertFalse(approval["signed_in"])
            self.assertEqual(approval["verification_url"], base + "/auth/device?user_code=" + approval["user_code"])
            # Simulate the signed-in website's explicit review/approval, using its
            # separate browser cookie. The worker receives no cookie or password.
            with request("/device?user_code=" + approval["user_code"], cookie=cookie) as response:
                self.assertEqual(json.load(response)["client_id"], "remi-blender")
            with request("/device/approve", {"userCode":approval["user_code"]}, cookie) as response:
                self.assertEqual(response.status, 200)
            signed_in = wait("signed_in")
            self.assertEqual(signed_in["user_id"], user_id)
            self.assertEqual(signed_in["email"], "blender@example.test")
            self.assertFalse(signed_in["capabilities"]["enabled"])
            self.assertIn("not enabled", signed_in["message"])
            process.command("status", base + "/api/v1")
            self.assertTrue(wait("signed_in")["signed_in"])
            process.command("logout", base + "/api/v1")
            self.assertFalse(wait("signed_out")["signed_in"])
            with request("/get-session", cookie=cookie) as response:
                self.assertEqual(json.load(response)["user"]["id"], user_id)
            self.assertFalse(any("token" in key for key in signed_in))
        finally:
            process.cleanup()
            fixture.terminate()
            try: fixture.communicate(timeout=5)
            except subprocess.TimeoutExpired: fixture.kill(); fixture.communicate()


if __name__ == "__main__": unittest.main()
