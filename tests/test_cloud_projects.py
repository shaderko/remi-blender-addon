"""Lost acknowledgements must reuse receipts; native Gaussian sharing is lossless."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,MagicMock,patch
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from integrations.remi_cloud.workspace_worker import WorkspaceWorker
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
        response=session._http.open.return_value.__enter__.return_value;response.status=200;response.headers={};response.read.return_value=body
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


if __name__=='__main__':unittest.main()
