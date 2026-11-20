"""Real loopback HTTP/process evidence; no provider, Stripe or production state."""
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
from uuid import uuid4
import zlib

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from integrations.remi_cloud.protocol import CloudSession,CloudError
from integrations.remi_cloud.jobs import CloudRequest,get_json,put_json,canonical,sha,cost_micros
from integrations.remi_cloud.client import CloudProcess
from integrations.remi_cloud.contract.errors import ProtocolError

TOKEN="fixture_cloud_session_secret_123456789"
SIGNATURE="fixture_private_signed_download_secret"
USER="00000000-0000-4000-8000-000000000001"
OTHER="00000000-0000-4000-8000-000000000099"


def png_image(width=256,height=256):
    def chunk(kind,body):return struct.pack(">I",len(body))+kind+body+struct.pack(">I",zlib.crc32(kind+body)&0xffffffff)
    return b"\x89PNG\r\n\x1a\n"+chunk(b"IHDR",struct.pack(">IIBBBBB",width,height,8,2,0,0,0))+chunk(b"IDAT",zlib.compress((b"\0"+b"\0\0\xff"*width)*height))+chunk(b"IEND",b"")


def native_gaussians(count=32768):
    return b"REMIGAUS"+struct.pack("<II",1,count)+struct.pack("<14f",0,0,0,.01,.01,.01,1,0,0,0,.7,.1,.2,.3)*count


class FixtureServer(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self):
        super().__init__(("127.0.0.1",0),Handler)
        self.base="http://127.0.0.1:"+str(self.server_port)
        self.api=self.base+"/api/v1"
        self.requests=[];self.uploads={};self.jobs={};self.by_id={}
        self.user=USER;self.enabled=True;self.revoked=False;self.result_state="succeeded"
        self.fault=None;self.block=threading.Event();self.release=threading.Event()
        self.artifact=png_image();self.artifact_url=None;self.artifact_headers={};self.job_override={};self.upload_override={};self.ticket_override={}
        self.thread=threading.Thread(target=self.serve_forever,daemon=True);self.thread.start()
    def close(self):
        self.release.set();self.shutdown();self.server_close();self.thread.join()


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):self.requested()
    def do_POST(self):self.requested()
    def requested(self):
        s=self.server;raw=self.rfile.read(int(self.headers.get("Content-Length",0)))
        path=urlsplit(self.path).path
        s.requests.append({"method":self.command,"path":path,"headers":dict(self.headers),"body":raw})
        payload=json.loads(raw) if raw and self.headers.get("Content-Type")=="application/json" else None
        status=200
        if path=="/api/auth/device/code":
            value={"device_code":"fixture_device_secret_123456","user_code":"ABCDEFGH","verification_uri":s.base+"/auth/device","verification_uri_complete":s.base+"/auth/device?user_code=ABCDEFGH","expires_in":30,"interval":1}
        elif path=="/api/auth/device/token":value={"access_token":TOKEN,"token_type":"Bearer"}
        elif path=="/api/auth/get-session":
            if s.revoked or self.headers.get("Authorization")!="Bearer "+TOKEN:status,value=401,{}
            else:value={"user":{"id":s.user,"email":"fixture@example.test"},"session":{"expiresAt":"2099-01-01T00:00:00+00:00","token":TOKEN}}
        elif path=="/api/auth/sign-out":s.revoked=True;value={"success":True}
        elif path=="/api/v1/capabilities":
            value={"apiVersion":"v1","enabled":s.enabled,"operations":{name:s.enabled and name in ("generate_image","generate") for name in ("generate_image","generate","reconstruct","optimize","pipeline")},"inputModes":{"single":True,"batch":True,"multiview":False},"billing":{"currency":"EUR","spendCapsRequired":True}}
        elif path=="/artifact":
            body=s.artifact
            if s.fault=="corrupt":body=b"bad artifact"
            if s.fault=="redirect":
                self.send_response(302);self.send_header("Location",s.base+"/leak");self.end_headers();return
            self.send_response(200)
            for key,value in s.artifact_headers.items():self.send_header(key,value)
            self.send_header("Content-Length",str(len(body)));self.end_headers()
            try:self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError):pass
            return
        elif not path.startswith("/api/v1/"):status,value=404,{}
        elif s.revoked or self.headers.get("Authorization")!="Bearer "+TOKEN:status,value=401,{"error":{"message":TOKEN}}
        elif s.fault in ("402","403"):status,value=int(s.fault),{"error":{"message":TOKEN+SIGNATURE}}
        elif path=="/api/v1/files":
            key=self.headers.get("Idempotency-Key")
            if key in s.uploads:
                old,value=s.uploads[key]
                if old!=raw:status,value=409,{}
            else:
                item={"id":str(uuid4()),"kind":"image","filename":"input.png","contentType":"image/png","bytes":len(raw),"sha256":sha(raw),"createdAt":"2026-11-05T00:00:00Z",**s.upload_override}
                value={"file":item};s.uploads[key]=(raw,value)
            if s.fault=="lost_upload":s.fault=None;self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
        elif path=="/api/v1/jobs":
            key=self.headers.get("Idempotency-Key")
            if key in s.jobs:
                old,value=s.jobs[key]
                if old!=raw:status,value=409,{}
            else:
                item={"id":str(uuid4()),"operation":payload["operation"],"inputMode":"single","inputFileIds":payload["inputFileIds"],"status":"queued","stage":"queued","progress":0,"outputFileIds":[],"error":None,"createdAt":"2026-11-05T00:00:00Z","updatedAt":"2026-11-05T00:00:00Z","billing":{"status":"pending","currency":"EUR","maxCostMicros":"100","actualCostMicros":None}}
                value={"job":item};s.jobs[key]=(raw,value);s.by_id[item["id"]]=item
            if s.fault=="blocked_job":s.block.set();s.release.wait(8)
            if s.fault=="lost_job":s.fault=None;self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
        elif path.endswith("/download"):
            content_type="application/octet-stream" if s.artifact.startswith(b"REMIGAUS") else "image/png"
            value={"url":s.artifact_url or s.base+"/artifact?signature="+SIGNATURE,"expiresIn":300,"filename":"../../escape.secret","contentType":content_type,"bytes":len(s.artifact),"sha256":sha(s.artifact),**s.ticket_override}
        elif path.startswith("/api/v1/jobs/"):
            item=s.by_id.get(path.rsplit("/",1)[-1])
            if item is None:status,value=404,{}
            else:
                terminal=s.result_state in ("succeeded","failed","canceled")
                value={"job":{**item,"status":s.result_state,"stage":"complete" if terminal else "generating","progress":1 if terminal else .3,"outputFileIds":["00000000-0000-4000-8000-000000000007"] if s.result_state=="succeeded" else [],**s.job_override}}
        else:status,value=404,{}
        body=json.dumps(value).encode()
        self.send_response(status);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(body)));self.end_headers()
        try:self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError):pass


class CloudGenerationTests(unittest.TestCase):
    def setUp(self):
        self.server=FixtureServer();self.temp=tempfile.TemporaryDirectory(prefix="remi-cloud-test-");self.root=Path(self.temp.name)
        self.session=CloudSession(self.server.api);self.session._token=TOKEN;self.session.status()
    def tearDown(self):self.server.close();self.temp.cleanup()
    def create(self,operation="generate_image"):
        args={"prompt":"A blue teapot","options":{"width":256,"height":256,"seed":7}} if operation=="generate_image" else {"image":str(self.image()),"options":{"gaussianCount":32768,"seed":7}}
        return CloudRequest.create(self.session,str(self.root),operation,max_cost_eur="0.001",**args)
    def image(self):
        path=self.root/"original.png";path.write_bytes(png_image());return path
    def wait(self,fn):
        until=time.monotonic()+10
        while time.monotonic()<until:
            value=fn()
            if value:return value
            time.sleep(.01)
        self.fail("Timed out waiting for the owned Cloud fixture")
    def test_shared_sdk_response_validators_are_pinned_and_unmodified(self):
        root=ROOT/"integrations/remi_cloud/sdk";source=json.loads((root/"provenance.json").read_text())
        for name,expected in source["files"].items():self.assertEqual(sha((root/name).read_bytes()),expected)
    def test_addon_uses_the_shared_sdk_workflow_and_thin_session_adapter(self):
        from integrations.remi_cloud.sdk.embedded import CloudSession as SdkSession
        from integrations.remi_cloud.sdk.workflow import CloudRequest as SdkRequest
        self.assertIs(CloudRequest, SdkRequest)
        self.assertIsInstance(self.session, SdkSession)
        self.assertEqual(self.session.client_id, "remi-blender")
    def test_image_request_download_and_pending_bill_use_only_authenticated_remi_api(self):
        work=self.create();self.assertEqual(work.submit()["state"],"job_running")
        result=work.poll();self.assertEqual(result["state"],"job_ready");self.assertIsNone(result["billing"]["actual_cost_micros"])
        self.assertEqual(Path(result["output_path"]).read_bytes(),self.server.artifact)
        self.assertEqual(work.poll()["output_path"],result["output_path"])
        self.assertEqual(len(self.server.jobs),1)
        for request in self.server.requests:
            if request["path"].startswith("/api/v1/") and not request["path"].endswith("capabilities"):
                self.assertEqual(request["headers"].get("Authorization"),"Bearer "+TOKEN)
            if request["path"]=="/artifact":self.assertNotIn("Authorization",request["headers"])
            self.assertNotIn("Cookie",request["headers"])
        self.assertFalse((self.root/"escape.secret").exists())
        for path in work.directory.iterdir():
            self.assertNotIn(TOKEN.encode(),path.read_bytes());self.assertNotIn(SIGNATURE.encode(),path.read_bytes())
            if os.name=="posix":self.assertEqual(path.stat().st_mode&0o077,0)
    def test_upload_receipt_persists_only_checked_contract_fields(self):
        work=self.create("generate")
        self.server.upload_override={"token":TOKEN,"signed_url":SIGNATURE}
        work.submit()
        saved=get_json(work.directory/"upload.json")
        self.assertNotIn("token",saved);self.assertNotIn("signed_url",saved)
        for path in work.directory.iterdir():
            self.assertNotIn(TOKEN.encode(),path.read_bytes());self.assertNotIn(SIGNATURE.encode(),path.read_bytes())
    def test_signed_download_to_another_origin_has_no_api_authorization(self):
        storage=FixtureServer()
        try:
            work=self.create();work.submit()
            self.server.artifact_url=storage.base+"/artifact?signature="+SIGNATURE
            self.assertEqual(work.poll()["state"],"job_ready")
            self.assertEqual(len(storage.requests),1)
            self.assertNotIn("Authorization",storage.requests[0]["headers"])
            self.assertNotIn("Cookie",storage.requests[0]["headers"])
        finally:storage.close()
    def test_download_publication_race_never_clobbers_another_file(self):
        work=self.create();work.submit();original=os.link
        def race(source,target,*args,**kwargs):
            if Path(target).name=="generation.png":
                Path(target).write_bytes(b"created by another process");Path(target).chmod(0o600)
            return original(source,target,*args,**kwargs)
        with patch("integrations.remi_cloud.jobs.os.link",side_effect=race):
            with self.assertRaises(CloudError):work.poll()
        self.assertEqual((work.directory/"generation.png").read_bytes(),b"created by another process")
        self.assertFalse((work.directory/"complete.json").exists())
    def test_correct_hash_does_not_make_invalid_image_or_native_attributes_usable(self):
        work=self.create();work.submit();self.server.artifact=png_image(512,256)
        with self.assertRaises(CloudError):work.poll()
        self.assertFalse((work.directory/"complete.json").exists())
        for change in ("count","nonfinite","scale","rotation","opacity"):
            with self.subTest(change=change):
                work=self.create("generate");work.submit()
                body=bytearray(native_gaussians())
                if change=="count":body=native_gaussians(32767)
                elif change=="nonfinite":struct.pack_into("<f",body,16,float("nan"))
                elif change=="scale":struct.pack_into("<f",body,16+3*4,0)
                elif change=="rotation":struct.pack_into("<f",body,16+6*4,2)
                elif change=="opacity":struct.pack_into("<f",body,16+10*4,2)
                self.server.artifact=bytes(body)
                with self.assertRaises(CloudError):work.poll()
                self.assertFalse((work.directory/"complete.json").exists())
    def test_custom_3d_upload_is_verified_and_source_mutation_does_not_change_recovery(self):
        self.server.artifact=native_gaussians();work=self.create("generate")
        self.server.fault="lost_upload"
        with self.assertRaises(CloudError):work.submit()
        (self.root/"original.png").write_bytes(b"changed after submission")
        resumed=CloudRequest(work.path,self.session);resumed.submit();result=resumed.poll()
        self.assertEqual(Path(result["output_path"]).read_bytes(),native_gaussians())
        self.assertEqual(len(self.server.uploads),1);self.assertEqual(len(self.server.jobs),1)
        posted=[r for r in self.server.requests if r["path"]=="/api/v1/files"]
        self.assertEqual(posted[0]["body"],posted[1]["body"])
        self.assertEqual(posted[0]["headers"]["Idempotency-Key"],posted[1]["headers"]["Idempotency-Key"])
    def test_lost_job_reply_preserves_exact_wire_bytes_and_key_without_automatic_resubmit(self):
        work=self.create();self.server.fault="lost_job"
        with self.assertRaises(CloudError):work.submit()
        self.assertEqual(len(self.server.jobs),1)
        self.assertFalse((work.directory/"job.json").exists())
        before=len(self.server.requests)
        with self.assertRaises(OSError):work.poll()
        self.assertEqual(len(self.server.requests),before)
        CloudRequest(work.path,self.session).submit()
        posted=[r for r in self.server.requests if r["path"]=="/api/v1/jobs"]
        self.assertEqual(len(posted),2);self.assertEqual(posted[0]["body"],posted[1]["body"])
        self.assertEqual(posted[0]["body"],canonical(get_json(work.directory/"payload.json")))
        self.assertEqual(len(self.server.jobs),1)
    def test_lost_local_job_receipt_recovers_by_original_key(self):
        work=self.create();original=put_json
        def interrupted(path,value):
            if path.name=="job.json":raise OSError("interrupted local receipt")
            return original(path,value)
        with patch("integrations.remi_cloud.jobs.put_json",side_effect=interrupted):
            with self.assertRaises(OSError):work.submit()
        self.assertEqual(len(self.server.jobs),1)
        self.assertEqual(CloudRequest(work.path,self.session).submit()["state"],"job_running")
        self.assertEqual(len(self.server.jobs),1)
    def test_known_job_history_and_download_do_not_need_compute_capability(self):
        work=self.create();work.submit();self.server.enabled=False
        self.assertEqual(CloudRequest(work.path,self.session).submit()["state"],"job_ready")
        self.assertEqual(len([r for r in self.server.requests if r["method"]=="POST" and r["path"]=="/api/v1/jobs"]),1)
    def test_disabled_capability_never_uploads_or_submits(self):
        work=self.create("generate");self.server.enabled=False
        with self.assertRaises(CloudError):work.submit()
        self.assertEqual(self.server.uploads,{});self.assertEqual(self.server.jobs,{})
    def test_wrong_account_or_api_cannot_resume_another_request(self):
        work=self.create();self.session.profile["user_id"]=OTHER
        with self.assertRaises(CloudError):CloudRequest(work.path,self.session)
        other=CloudSession("http://localhost:9/api/v1");other.profile={"user_id":USER}
        with self.assertRaises(CloudError):CloudRequest(work.path,other)
        self.assertEqual(self.server.jobs,{})
    def test_revocation_and_payment_or_scope_failure_preserve_recovery_without_retry(self):
        for fault in ("402","403"):
            with self.subTest(fault=fault):
                work=self.create();self.server.fault=fault
                with self.assertRaises(CloudError) as caught:work.submit()
                self.assertNotIn(TOKEN,str(caught.exception));self.assertEqual(self.server.jobs,{})
                self.assertTrue(work.path.exists())
        self.server.fault=None;self.server.revoked=True;work=self.create()
        with self.assertRaises(CloudError):work.submit()
        self.assertIsNone(self.session.profile)
    def test_mismatched_job_identity_inputs_outputs_or_cap_are_rejected(self):
        for override in ({"id":str(uuid4())},{"operation":"generate"},{"inputFileIds":[str(uuid4())]},
                         {"outputFileIds":[str(uuid4()),str(uuid4())]},
                         {"billing":{"status":"pending","currency":"EUR","maxCostMicros":"1001","actualCostMicros":None}}):
            with self.subTest(override=override):
                work=self.create();work.submit();self.server.job_override=override
                with self.assertRaises((CloudError,ProtocolError)):work.poll()
                self.assertFalse((work.directory/"complete.json").exists())
        self.server.job_override={}
    def test_failed_unknown_billing_is_not_advertised_as_zero(self):
        work=self.create();work.submit();self.server.result_state="failed"
        result=work.poll();self.assertEqual(result["state"],"job_failed");self.assertIn("Review billing",result["message"])
        self.server.job_override={"billing":{"status":"no_charge","currency":"EUR","maxCostMicros":"100","actualCostMicros":"0"}}
        self.assertIn("No charge",work.poll()["message"])
    def test_corrupt_redirected_or_encoded_download_never_publishes(self):
        for fault in ("corrupt","redirect","encoding"):
            with self.subTest(fault=fault):
                work=self.create();work.submit();self.server.fault=fault
                self.server.artifact_headers={"Content-Encoding":"gzip"} if fault=="encoding" else {}
                with self.assertRaises(CloudError):work.poll()
                self.assertFalse((work.directory/"generation.png").exists());self.assertFalse((work.directory/"complete.json").exists())
                self.assertFalse(any(r["path"]=="/leak" for r in self.server.requests))
        self.server.fault=None;self.server.artifact_headers={}
    def test_wrong_media_size_or_unsafe_download_location_is_rejected(self):
        for override in ({"contentType":"text/html"},{"bytes":16*1024*1024+1},{"url":"http://evil.test/leak?secret="+SIGNATURE},{"url":"https://user:secret@evil.test/a"}):
            with self.subTest(override=override):
                work=self.create();work.submit();self.server.ticket_override=override
                with self.assertRaises(CloudError) as caught:work.poll()
                self.assertNotIn(SIGNATURE,str(caught.exception));self.assertFalse((work.directory/"complete.json").exists())
        self.server.ticket_override={}
    def test_existing_output_is_rechecked_and_never_overwritten(self):
        work=self.create();work.submit();path=work.directory/"generation.png";path.write_bytes(b"unrelated file");path.chmod(0o600)
        with self.assertRaises(CloudError):work.poll()
        self.assertEqual(path.read_bytes(),b"unrelated file")
    def test_frozen_input_and_manifest_corruption_prevent_submission(self):
        work=self.create("generate");(work.directory/"input.png").write_bytes(b"corrupt")
        with self.assertRaises(CloudError):CloudRequest(work.path,self.session)
        other=self.create();value=json.loads(other.path.read_text());value["value"]["prompt"]="changed";other.path.write_text(json.dumps(value))
        with self.assertRaises(CloudError):CloudRequest(other.path,self.session)
        self.assertEqual(self.server.jobs,{})
    def test_source_and_output_symlinks_are_rejected(self):
        if os.name!="posix":self.skipTest("POSIX symlink semantics")
        work=self.create();work.submit();(work.directory/"generation.png").symlink_to(self.image())
        with self.assertRaises(CloudError):work.poll()
    def test_invalid_or_inexact_caps_and_unsupported_options_fail_before_network(self):
        for cap in ("", "0", "0.0000001", "1e3", "NaN", "1.0000000", "9007199254.740992", .1,True):
            with self.assertRaises(CloudError):cost_micros(cap)
        self.assertEqual(cost_micros("0.000001"),1);self.assertEqual(cost_micros("9007199254.740991"),9007199254740991)
        for options in ({"width":257},{"seed":True},{"steps":100}):
            with self.assertRaises(CloudError):CloudRequest.create(self.session,str(self.root),"generate_image",prompt="A teapot",options=options,max_cost_eur="1")
        self.assertEqual(self.server.jobs,{})
    def test_actual_worker_interruption_after_acceptance_recovers_without_a_second_job(self):
        process=CloudProcess(sys.executable)
        try:
            process.command("login",self.server.api);self.wait(lambda:process.status().get("state")=="signed_in")
            self.server.fault="blocked_job"
            process.command("generate",self.server.api,output=str(self.root),operation="generate_image",prompt="A teapot",options={"width":256,"height":256,"seed":7},max_cost_eur="0.001")
            self.assertTrue(self.server.block.wait(5))
            path=next(self.root.glob("remi-cloud-*/request.json"));self.assertTrue((path.parent/"payload.json").exists())
            self.assertFalse((path.parent/"job.json").exists())
            process.process.kill();process.process.wait(timeout=3)
            self.server.release.set();self.server.fault=None
            process.command("login",self.server.api);self.wait(lambda:process.status().get("state")=="signed_in")
            process.command("resume",self.server.api,request_path=str(path));self.wait(lambda:process.status().get("state")=="job_running")
            process.command("poll",self.server.api,request_path=str(path));result=self.wait(lambda:process.status() if process.status().get("state")=="job_ready" else None)
            self.assertIsNone(result["billing"]["actual_cost_micros"]);self.assertEqual(len(self.server.jobs),1)
            for item in (*path.parent.iterdir(),*process.directory.iterdir()):
                self.assertNotIn(TOKEN.encode(),item.read_bytes());self.assertNotIn(SIGNATURE.encode(),item.read_bytes())
        finally:process.cleanup()


if __name__=="__main__":unittest.main()
