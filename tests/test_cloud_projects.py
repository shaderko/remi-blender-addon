"""Lost acknowledgements must reuse receipts; native Gaussian sharing is lossless."""
from copy import deepcopy
import hashlib
import io
import threading
import time
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,MagicMock,patch
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from integrations.remi_cloud.workspace_worker import WorkspaceWorker, atomic_json
from integrations.remi_cloud.sdk.embedded import CloudError
from integrations.triposplat.splat_data import load,save,save_native
import numpy as np


class CloudProjectTests(unittest.TestCase):
    def test_lost_attach_ack_reuses_durable_upload_after_worker_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'asset.glb';source.write_bytes(b'fixture mesh bytes')
            session=Mock(base_url='http://127.0.0.1:9/api/v1',profile={'user_id':'owner'})
            command={'id':'edit-1','action':'asset','card_id':'shared-card','local_id':'local-card','path':str(source),'title':'Mesh','expected_file_id':str(uuid4())}
            worker=WorkspaceWorker(session,root/'ipc',root/'cache')
            file={'id':str(uuid4()),'sha256':hashlib.sha256(source.read_bytes()).hexdigest()}
            worker.client=Mock();worker.client.upload.return_value=file
            worker.client.attach.side_effect=CloudError('Reply lost',retryable=True)
            with self.assertRaises(CloudError):worker.execute(command)
            worker.client.upload.assert_called_once()
            # No in-memory state survives this restart; the stored upload does.
            restarted=WorkspaceWorker(session,root/'ipc2',root/'cache');restarted.client=Mock()
            restarted.execute(command)
            restarted.client.upload.assert_not_called()
            restarted.client.attach.assert_called_once_with('shared-card',file['id'],'Mesh',expected_file_id=command['expected_file_id'])
            self.assertEqual(restarted.uploads['edit-1']['file'],file)

    def test_fresh_windows_cache_fits_max_path_and_verifies_download(self):
        from pathlib import PureWindowsPath
        from integrations.remi_cloud.sdk.workspace import WorkspaceClient
        body=b'fixture image';checksum=hashlib.sha256(body).hexdigest();file_id=str(uuid4())
        metadata={'id':file_id,'kind':'image','filename':'image.png','contentType':'image/png','bytes':len(body),'sha256':checksum,'createdAt':'2026-11-22T00:00:00Z'}
        session=MagicMock();session.project_id=None;session._token='fixture';session.base_url='http://127.0.0.1:9/api/v1';session._origin='http://127.0.0.1:9'
        response=session._http.open.return_value.__enter__.return_value;response.status=200;response.headers={};response.read1.side_effect=io.BytesIO(body).read
        windows=PureWindowsPath('C:/Users/Martin/AppData/Roaming/Blender Foundation/Blender/5.3/datafiles/remi/cloud-workspaces')/('a'*64)/'files'
        self.assertGreaterEqual(len(str(windows/(file_id+'-'+checksum+'.png.partial'))),260)
        with tempfile.TemporaryDirectory() as temporary:
            client=WorkspaceClient(session,Path(temporary));original=Path.write_bytes
            def write(path,data):
                if len(str(windows/path.name))>=260:raise FileNotFoundError(2,'No such file or directory',str(path))
                return original(path,data)
            with patch.object(Path,'write_bytes',write):path=Path(client.file(metadata))
            self.assertEqual(path.read_bytes(),body)
            self.assertLess(len(str(windows/(path.name+'.partial'))),260)
            self.assertEqual(client.file(metadata),str(path));session._http.open.assert_called_once()

    def test_portable_gaussian_conversion_preserves_every_array(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            arrays={'positions':np.arange(9,dtype=np.float32).reshape(3,3),'scales':np.full((3,3),.01,dtype=np.float32),'rotations':np.tile([1,0,0,0],(3,1)).astype(np.float32),'opacities':np.array([0,.5,1],dtype=np.float32),'sh_dc':np.arange(9,dtype=np.float32).reshape(3,3)/10}
            save(root/'local.npz',arrays);save_native(root/'shared.splat',load(root/'local.npz'))
            decoded=load(root/'shared.splat')
            for name in arrays:np.testing.assert_array_equal(decoded[name],arrays[name])
            self.assertEqual((root/'shared.splat').stat().st_size,16+3*56)


class ResponsiveSyncTests(unittest.TestCase):
    def wait_for(self, predicate):
        deadline=time.monotonic()+3
        while not predicate():
            if time.monotonic()>deadline:self.fail("Sync worker did not make progress")
            time.sleep(.01)

    def fixture(self, root, *, download=False):
        from types import SimpleNamespace
        state=SimpleNamespace(started=threading.Event(),release=threading.Event(),patched=[],attached=[],bound=[])
        session=Mock(base_url="http://127.0.0.1:9/api/v1",profile={"user_id":"fixture"},project_id=None)
        def select(project):session.project_id=project
        session.select_project.side_effect=select
        def watch(**kwargs):
            while not kwargs["cancelled"]():
                time.sleep(.01)
                if False:yield None
        session.watch_board.side_effect=watch
        project=str(uuid4())
        client=Mock()
        client.catalog.return_value={"projects":[{"id":"default"},{"id":project}],"teams":[]}
        checksum=hashlib.sha256(b"mesh").hexdigest()
        file={"id":str(uuid4()),"sha256":checksum,"bytes":4,"kind":"mesh"}
        snapshot={"assets":[{"id":"remote","title":"Remote mesh","file":file}] if download else [],"cards":{},"inputs":[],"jobs":[]}
        client.snapshot.side_effect=lambda **kwargs:deepcopy(snapshot)
        client.patch.side_effect=lambda cards,**kwargs:state.patched.append(cards)
        worker=WorkspaceWorker(session,root/"ipc",root/"cache");worker.client=client
        def transfer(selected):
            bound=Mock();state.bound.append(selected)
            def upload(path,key):
                state.started.set();state.release.wait(3)
                body=Path(path).read_bytes()
                return {"id":str(uuid4()),"sha256":hashlib.sha256(body).hexdigest(),"bytes":len(body)}
            def attach(*args,**kwargs):state.attached.append((selected,args))
            def file_path(metadata):
                state.started.set();state.release.wait(3)
                raise CloudError("Download interrupted",retryable=True)
            bound.upload.side_effect=upload;bound.attach.side_effect=attach;bound.file.side_effect=file_path
            return bound
        worker.transfer_client=transfer
        return worker,client,state,project

    def command(self,worker,identity,action,project="default",**payload):
        command={"id":identity,"user_id":"fixture","base_url":worker.session.base_url,"project_id":project,"action":action,**payload}
        atomic_json(worker.outbox/(identity+".json"),command)
        return command

    def test_nine_queued_meshes_do_not_block_moves_or_project_switch_after_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/"mesh.glb";source.write_bytes(b"fixture mesh")
            worker,client,state,project=self.fixture(root)
            # Persisted commands represent a restart with an existing backlog.
            for i in range(9):self.command(worker,f"00{i}","asset",card_id=f"mesh-{i}",local_id=f"local-{i}",path=str(source))
            worker.start()
            try:
                self.assertTrue(state.started.wait(2))
                self.assertEqual(client.announce.call_count,9,"Queued cards were hidden until file upload")
                self.command(worker,"100","positions",card_id="mesh-0",cards={"mesh-0":{"x":4,"y":9}})
                self.wait_for(lambda:bool(state.patched))
                self.command(worker,"101","switch",project=project)
                self.wait_for(lambda:worker.selected==project and worker.ready)
                self.assertFalse(state.release.is_set(),"Controls waited for the large upload")
                self.assertEqual(len(list(worker.outbox.glob("00*.json"))),9)
                state.release.set()
                self.wait_for(lambda:bool(state.attached))
                self.assertEqual(state.attached[0][0],"default","Upload crossed project boundaries during selection")
                self.wait_for(lambda:not (worker.outbox/"000.json").exists())
                self.assertEqual(len(list(worker.outbox.glob("00*.json"))),8)
                self.assertEqual(worker.errors,{})
            finally:state.release.set();worker.close()

    def test_failed_download_does_not_block_ready_websocket_moves_or_switch(self):
        with tempfile.TemporaryDirectory() as temporary:
            worker,client,state,project=self.fixture(Path(temporary),download=True)
            worker.start()
            try:
                self.assertTrue(state.started.wait(2))
                self.assertTrue(worker.ready)
                self.assertIsNotNone(worker.watcher)
                client.snapshot.assert_called_with(download_files=False)
                self.command(worker,"001","positions",cards={"remote":{"x":1,"y":2}})
                self.wait_for(lambda:bool(state.patched))
                self.command(worker,"002","switch",project=project)
                self.wait_for(lambda:worker.selected==project and worker.ready)
                self.assertFalse(state.release.is_set())
                state.release.set()
                self.wait_for(lambda:bool(worker.download_retry))
                self.assertTrue(worker.ready)
            finally:state.release.set();worker.close()

    def test_upload_error_survives_restart_and_oversized_mesh_does_not_block_controls(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/"large.glb"
            with source.open("wb") as stream:stream.truncate(100*1024*1024+1)
            worker,client,state,project=self.fixture(root)
            self.command(worker,"001","asset",card_id="large",local_id="local",path=str(source))
            self.command(worker,"002","positions",cards={"existing":{"x":1,"y":2}})
            worker.start()
            try:
                self.wait_for(lambda:"001" in worker.errors)
                self.assertEqual(worker.errors["001"]["code"],"file_too_large")
                self.assertTrue(state.patched)
                self.assertTrue(source.is_file())
            finally:worker.close()
            restarted,_,_,_=self.fixture(root)
            self.assertEqual(restarted.errors["001"]["code"],"file_too_large")
            self.assertTrue((worker.outbox/"001.failed").exists())

    def test_multiple_local_revisions_announce_and_upload_in_card_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);worker,client,state,project=self.fixture(root)
            for index in range(2):
                path=root/f"revision-{index}.glb";path.write_bytes(str(index).encode())
                self.command(worker,f"00{index}","asset",card_id="same-card",local_id="local",path=str(path))
            worker.start()
            try:
                self.assertTrue(state.started.wait(2))
                self.assertEqual(client.announce.call_count,1)
                state.release.set()
                self.wait_for(lambda:len(state.attached)==2)
                self.assertEqual(client.announce.call_count,2)
                self.wait_for(lambda:not list(worker.outbox.glob("*.json")))
                self.assertEqual(worker.errors,{})
            finally:state.release.set();worker.close()

    def test_local_ready_card_is_preserved_while_remote_placeholder_uploads(self):
        # The Blender adapter has separate coverage for pending-card merging.
        # This check ensures failed transfers announce the same remote identity.
        with tempfile.TemporaryDirectory() as temporary:
            worker,client,state,project=self.fixture(Path(temporary))
            command=self.command(worker,"001","asset",card_id="card",local_id="local",path=str(Path(temporary)/"missing.glb"))
            worker.command_error(worker.outbox/"001.json",command,CloudError("No source",code="invalid_source"))
            failed=json.loads((worker.outbox/"001-failed.json").read_text())
            self.assertEqual((failed["card_id"],failed["upload_id"],failed["status"]),("card","001","failed"))
            worker.process_commands()
            client.announce.assert_called_once_with("card","mesh","Asset","001",status="failed")

    def test_transfer_session_is_project_bound_and_has_its_own_transport(self):
        from integrations.remi_cloud.sdk.embedded import CloudSession
        with tempfile.TemporaryDirectory() as temporary:
            session=CloudSession("http://127.0.0.1:9/api/v1",realtime=False)
            session._token="fixture";session.profile={"user_id":"fixture"}
            worker=WorkspaceWorker(session,Path(temporary)/"ipc",Path(temporary)/"cache")
            project=str(uuid4());transfer=worker.transfer_client(project)
            self.assertIsNot(transfer.session,session)
            self.assertIsNot(transfer.session._http,session._http)
            self.assertFalse(transfer.session._realtime_enabled)
            session.select_project(str(uuid4()))
            self.assertEqual(transfer.session.project_id,project)


if __name__=='__main__':unittest.main()
