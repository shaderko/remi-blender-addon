"""Lost acknowledgements must reuse receipts; native Gaussian sharing is lossless."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock
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

    def test_portable_gaussian_conversion_preserves_every_array(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            arrays={'positions':np.arange(9,dtype=np.float32).reshape(3,3),'scales':np.full((3,3),.01,dtype=np.float32),'rotations':np.tile([1,0,0,0],(3,1)).astype(np.float32),'opacities':np.array([0,.5,1],dtype=np.float32),'sh_dc':np.arange(9,dtype=np.float32).reshape(3,3)/10}
            save(root/'local.npz',arrays);save_native(root/'shared.splat',load(root/'local.npz'))
            decoded=load(root/'shared.splat')
            for name in arrays:np.testing.assert_array_equal(decoded[name],arrays[name])
            self.assertEqual((root/'shared.splat').stat().st_size,16+3*56)


if __name__=='__main__':unittest.main()
