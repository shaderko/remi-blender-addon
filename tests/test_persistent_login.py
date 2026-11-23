"""Restart, explicit logout, vault boundaries and refresh rotation without user accounts."""
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tests'))
from integrations.credentials.vault import Vault,StorageError
from integrations.remi_cloud.credentials import save,restore,scope
from integrations.remi_cloud.protocol import CloudSession,CloudError
from integrations.remi_cloud.client import CloudProcess
from test_cloud_auth import Server,Handler,TOKEN,DEVICE
from test_codex_images import auth,token


class MemoryBackend:
    def __init__(self):self.items={}
    def load(self,key):return self.items.get(key)
    def save(self,key,data):self.items[key]=bytes(data)
    def clear(self,key):self.items.pop(key,None)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.server=Server(('127.0.0.1',0),Handler)
        self.server.base='http://127.0.0.1:'+str(self.server.server_port)
        self.server.requests=[]
        self.server.bad_origin=self.server.denied=self.server.revoked=False
        self.server.capabilities_fault=None;self.server.capabilities_enabled=False
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.api=self.server.base+'/api/v1'
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
    def wait(self,predicate):
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            value=predicate()
            if value:return value
            time.sleep(.02)
        self.fail('Persistence fixture timed out')

    def test_actual_worker_restart_restores_saved_login_and_logout_removes_it(self):
        if sys.platform not in ('darwin','win32'):self.skipTest('Native storage unavailable')
        with tempfile.TemporaryDirectory() as directory:
            first=CloudProcess(sys.executable);second=CloudProcess(sys.executable);third=CloudProcess(sys.executable)
            for process in (first,second,third):process.credential_root=Path(directory)
            try:
                first.command('login',self.api)
                self.wait(lambda:first.status().get('state')=='signed_in')
                first.cleanup();self.assertFalse(self.server.revoked)
                second.command('restore',self.api)
                value=self.wait(lambda:(v if (v:=second.status()).get('state')=='signed_in' else None))
                self.assertEqual(value['email'],'test@example.test')
                self.assertEqual(sum(path.endswith('/device/token') for path,_,_ in self.server.requests),1)
                for path in Path(directory).rglob('*'):
                    if path.is_file():self.assertNotIn(TOKEN.encode(),path.read_bytes())
                second.command('logout',self.api)
                self.wait(lambda:second.status().get('state')=='signed_out' and not second.pending)
                self.assertTrue(self.server.revoked)
                third.command('restore',self.api)
                self.wait(lambda:third.status().get('state')=='signed_out' and not third.pending)
            finally:
                first.cleanup();second.cleanup();third.cleanup()
                Vault(directory).clear(scope(self.api))

    def test_verified_session_restores_without_a_second_browser_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            backend=MemoryBackend();vault=Vault(directory,backend=backend)
            first=CloudSession(self.api);first.login(lambda value:None)
            save(vault,first);first.clear()
            second=restore(vault,self.api)
            self.assertEqual(second.profile['user_id'],'fixture-user')
            self.assertEqual(sum(path.endswith('/device/token') for path,_,_ in self.server.requests),1)
            self.assertFalse(self.server.revoked)
            second.logout();vault.clear(scope(self.api))
            self.assertIsNone(restore(vault,self.api))
            self.assertTrue(self.server.revoked)

    def test_wrong_endpoint_or_revoked_session_cannot_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            vault=Vault(directory,backend=MemoryBackend())
            session=CloudSession(self.api);session.login(lambda value:None);save(vault,session);session.clear()
            self.assertIsNone(restore(vault,self.api+'/other'))
            self.server.revoked=True
            self.assertIsNone(restore(vault,self.api))
            self.assertIsNone(vault.load(scope(self.api)))

    def test_temporary_outage_preserves_the_stored_session(self):
        with tempfile.TemporaryDirectory() as directory:
            vault=Vault(directory,backend=MemoryBackend())
            session=CloudSession(self.api);session.login(lambda value:None);save(vault,session);session.clear()
            with patch('integrations.remi_cloud.credentials.CloudSession.status',side_effect=CloudError('temporary',retryable=True)):
                with self.assertRaises(CloudError):restore(vault,self.api)
            self.assertEqual(vault.load(scope(self.api))['token'],TOKEN)

    def test_owned_worker_shutdown_does_not_revoke_session(self):
        process=CloudProcess(sys.executable)
        try:
            process.command('login',self.api)
            self.wait(lambda:process.status().get('state')=='signed_in')
            process.cleanup()
            self.assertFalse(self.server.revoked)
            self.assertFalse(any(path.endswith('/sign-out') for path,_,_ in self.server.requests))
        finally:process.cleanup()


class VaultTests(unittest.TestCase):
    def test_no_plaintext_fallback_and_scope_integrity(self):
        with tempfile.TemporaryDirectory() as directory:
            backend=MemoryBackend();vault=Vault(directory,backend=backend)
            vault.save('server-a',{'token':'synthetic-secret'})
            self.assertEqual(vault.load('server-a')['token'],'synthetic-secret')
            self.assertIsNone(vault.load('server-b'))
            self.assertFalse(list(Path(directory).glob('*')))
            backend.items['server-b']=backend.items['server-a']
            with self.assertRaises(StorageError):vault.load('server-b')
            vault.clear('server-a');self.assertIsNone(vault.load('server-a'))
        with tempfile.TemporaryDirectory() as directory,patch('integrations.credentials.vault.sys.platform','linux'):
            with self.assertRaises(StorageError):Vault(directory).save('scope',{'token':'synthetic-secret'})
            self.assertFalse(list(Path(directory).glob('*')))

    def test_auto_restore_is_once_per_process_and_does_not_replace_active_login(self):
        process=CloudProcess(sys.executable);process.credential_root=Path('synthetic-config')
        with patch.object(process,'command') as command:
            process.restore_once('https://example.test/api/v1')
            process.restore_once('https://example.test/api/v1')
            command.assert_called_once_with('restore','https://example.test/api/v1')
        process.restore_attempted.clear()
        process.process=type('Process',(),{'poll':lambda self:None})()
        with patch.object(process,'command') as command:
            process.restore_once('https://example.test/api/v1')
            command.assert_not_called()
        process.process=None

    def test_lock_serializes_updates_and_never_contains_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            vault=Vault(directory,backend=MemoryBackend());entered=[]
            def second():
                with vault.lock('scope'):
                    entered.append('second')
            with vault.lock('scope'):
                thread=threading.Thread(target=second);thread.start();time.sleep(.08)
                self.assertEqual(entered,[])
            thread.join(2);self.assertEqual(entered,['second'])
            self.assertEqual([p.read_bytes() for p in Path(directory).glob('*.lock')],[b'0'])

    def test_chatgpt_restart_refresh_rotation_and_disconnect(self):
        with tempfile.TemporaryDirectory() as directory:
            vault=Vault(directory,backend=MemoryBackend())
            first=auth.Connection(vault);first._accept({'access_token':token(),'refresh_token':'first-refresh','expires_in':0});first._persist()
            second=auth.Connection(vault)
            with patch.object(auth,'request_json',return_value={'access_token':token(),'refresh_token':'rotated-refresh','expires_in':3600}) as refresh:
                self.assertIn('Authorization',second.authorize(lambda phase:None,allow_sign_in=False))
            self.assertEqual(refresh.call_args.kwargs['data']['refresh_token'],'first-refresh')
            self.assertEqual(vault.load(second._scope())['tokens']['refresh_token'],'rotated-refresh')
            # An older process reloads the latest rotation instead of reusing it.
            self.assertIn('Authorization',first.authorize(lambda phase:None,allow_sign_in=False))
            self.assertEqual(first.tokens['refresh_token'],'rotated-refresh')
            second.disconnect()
            self.assertIsNone(first.authorize(lambda phase:None,allow_sign_in=False))
            self.assertIsNone(vault.load(first._scope()))

    def test_chatgpt_temporary_refresh_failure_keeps_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            vault=Vault(directory,backend=MemoryBackend());connection=auth.Connection(vault)
            connection._accept({'access_token':token(),'refresh_token':'synthetic-refresh','expires_in':0});connection._persist()
            with patch.object(auth,'request_json',side_effect=RuntimeError('temporary')):
                with self.assertRaises(RuntimeError):connection.authorize(lambda phase:None)
            self.assertEqual(vault.load(connection._scope())['tokens']['refresh_token'],'synthetic-refresh')
            with patch.object(auth,'request_json',side_effect=auth.OAuthRejected('expired')):
                with self.assertRaises(auth.OAuthRejected):connection.authorize(lambda phase:None)
            self.assertIsNone(vault.load(connection._scope()))

    def test_subprocess_restart_uses_os_protected_synthetic_record(self):
        if sys.platform not in ('darwin','win32'):self.skipTest('Native secure storage requires Windows or macOS')
        with tempfile.TemporaryDirectory() as directory:
            scope_value='remi-test-'+uuid4().hex
            child="import sys;sys.path.insert(0,sys.argv[1]);from integrations.credentials.vault import Vault;v=Vault(sys.argv[2]);s=sys.argv[3];v.save(s,{'value':'synthetic-test-secret'}) if sys.argv[4]=='save' else (print('NATIVE_VAULT_RESTART_OK') if v.load(s)=={'value':'synthetic-test-secret'} else sys.exit(1))"
            try:
                subprocess.run([sys.executable,'-I','-B','-c',child,str(ROOT),directory,scope_value,'save'],check=True,timeout=20,capture_output=True)
                result=subprocess.run([sys.executable,'-I','-B','-c',child,str(ROOT),directory,scope_value,'load'],check=True,timeout=20,capture_output=True,text=True)
                self.assertIn('NATIVE_VAULT_RESTART_OK',result.stdout)
                for path in Path(directory).iterdir():self.assertNotIn(b'synthetic-test-secret',path.read_bytes())
            finally:Vault(directory).clear(scope_value)


if __name__=='__main__':unittest.main()
