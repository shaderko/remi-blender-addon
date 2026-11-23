"""Pinned local IDOL, downloader and output contract; no real GPU or large weights."""
from contextlib import nullcontext
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from integrations.idol import download
from integrations.idol.runtime import IdolGenerator
from integrations.triposplat.worker import components_for_model,create_generator
from integrations.triposplat.splat_data import load


class Array:
    def __init__(self,value):self.value=np.asarray(value,dtype=np.float32)
    def __getitem__(self,key):return Array(self.value[key])
    def float(self):return self
    def cpu(self):return self
    def numpy(self):return self.value


class IdolTests(unittest.TestCase):
    def test_source_is_exact_pinned_cloud_sdk(self):
        root=ROOT/'integrations/idol';lock=json.loads((root/'source-lock.json').read_text())
        self.assertEqual(lock['commit'],'8db700349a5570aac390e4780fa32e3eed6b587a')
        for name,metadata in lock['files'].items():
            data=(root/'vendor'/name).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(),metadata['sha256'],name)
        components=components_for_model('idol')
        self.assertIn('idol/denoiser.safetensors',components)
        self.assertNotIn('best_weights.pt',components)
        self.assertNotIn('ckpts/vae/triposplat_vae_decoder_fp16.safetensors',components)

    def test_idol_dispatch_does_not_load_ncm(self):
        with patch('integrations.idol.runtime.IdolGenerator') as idol:
            create_generator('models','idol','output')
        idol.assert_called_once_with('models',status_output='output')

    def test_verified_download_reuses_encoders_and_never_replaces_other_files(self):
        data={'idol/config.json':b'fixture config','ckpts/vae/shared.bin':b'shared encoder'}
        files={name:{'url':'https://fixture.test/'+name,'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest()} for name,body in data.items()}
        opened=[]
        def opener(url):
            opened.append(url);return io.BytesIO(data[url.removeprefix('https://fixture.test/')])
        with tempfile.TemporaryDirectory() as temporary,patch.object(download,'components',return_value=files):
            root=Path(temporary);shared=root/'ckpts/vae/shared.bin';shared.parent.mkdir(parents=True);shared.write_bytes(data['ckpts/vae/shared.bin'])
            download.download(root,lambda message:None,opener=opener)
            self.assertEqual(opened,['https://fixture.test/idol/config.json'])
            self.assertEqual(shared.read_bytes(),data['ckpts/vae/shared.bin'])
            opened.clear();download.download(root,lambda message:None,opener=opener);self.assertFalse(opened)
            (root/'idol/config.json').write_bytes(b'changed')
            with self.assertRaises(ValueError):download.download(root,lambda message:None,opener=opener)
            self.assertFalse(opened)

    def test_generation_retains_cloud_z_up_arrays_and_normal_job_files(self):
        g=SimpleNamespace(xyz=Array([[1,2,3],[4,5,6]]),rotation=Array([[1,0,0,0],[1,0,0,0]]),scaling=Array([[.01,.02,.03]]*2),opacity=Array([[.5],[.7]]),features_dc=Array([[[.1,.2,.3]],[[.4,.5,.6]]]))
        result=SimpleNamespace(gaussian=g,preprocessed_image=Image.new('RGB',(8,8),'white'))
        generator=IdolGenerator.__new__(IdolGenerator);generator.torch=SimpleNamespace(inference_mode=nullcontext)
        calls=[]
        def pipeline(image,**kwargs):calls.append(kwargs);return result
        generator.pipeline=pipeline
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'source.png';result.preprocessed_image.save(source)
            generator.generate({'image':str(source),'seed':11,'gaussians':32768,'sampler':{'steps':50}},root)
            arrays=load(root/'splats.npz');np.testing.assert_array_equal(arrays['positions'],g.xyz.numpy())
            self.assertEqual(calls[0],{'mode':'synthetic','seed':11,'steps':50,'cfg_strength':3.0,'rescale_t':3.0,'num_gaussians':32768})
            self.assertTrue(json.loads((root/'complete.json').read_text())['complete'])
            self.assertEqual(json.loads((root/'meta.json').read_text())['model'],'idol')


if __name__=='__main__':unittest.main()
