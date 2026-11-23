"""Pinned IDOL SDK with the same offline encoders used by Cloud IDOL."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time

HERE=Path(__file__).parent


class IdolGenerator:
    model_id="idol"

    def __init__(self,models,model="idol",status_output=None):
        from ..triposplat.worker import status
        from .download import components
        root=Path(models)
        status(status_output,"Verifying IDOL components")
        for name,item in components().items():
            path=root/name
            if not path.is_file() or path.stat().st_size!=item["bytes"]:
                raise ValueError("Missing IDOL models. Use Download Models in generation settings.")
            with path.open("rb") as stream:digest=hashlib.file_digest(stream,"sha256").hexdigest()
            if digest!=item["sha256"]:raise ValueError("IDOL component checksum mismatch: "+name)
        source=json.loads((HERE/"source-lock.json").read_text())
        for name,item in source["files"].items():
            if hashlib.sha256((HERE/"vendor"/name).read_bytes()).hexdigest()!=item["sha256"]:
                raise ValueError("The packaged IDOL runtime is damaged. Reinstall Remi.")
        os.environ.update(HF_HUB_OFFLINE="1",TRANSFORMERS_OFFLINE="1",HF_HUB_DISABLE_TELEMETRY="1")
        sys.path.insert(0,str(HERE/"vendor"))
        sys.path.insert(0,str(HERE.parent/"triposplat"/"vendor"))
        try:
            import torch
            from idol import IdolPipeline
            from triposplat import load_dinov3,load_vae_encoder
        except ImportError as error:
            raise RuntimeError("Local IDOL needs the external PyTorch environment and IDOL dependencies. Install integrations/idol/requirements.txt there, or select IDOL Cloud.") from error
        if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
            raise RuntimeError("Local IDOL currently needs a CUDA GPU with bfloat16 support. Select IDOL Cloud on other devices.")
        from types import SimpleNamespace
        class Dino(torch.nn.Module):
            def __init__(self):
                super().__init__();self.encoder=load_dinov3(str(root/"ckpts/clip_vision/dino_v3_vit_h.safetensors"),device="cuda",dtype=torch.bfloat16)
            def forward(self,pixel_values):return SimpleNamespace(last_hidden_state=self.encoder(pixel_values=pixel_values))
        class Flux(torch.nn.Module):
            def __init__(self):
                super().__init__();self.encoder=load_vae_encoder(str(root/"ckpts/vae/flux2-vae.safetensors"),device="cuda",dtype=torch.bfloat16)
                self.bn=self.encoder.bn;self.config=SimpleNamespace(batch_norm_eps=self.bn.eps)
            def encode(self,images):
                mean,logvar=self.encoder.quant_conv(self.encoder.encoder(images)).chunk(2,dim=1)
                return SimpleNamespace(latent_dist=SimpleNamespace(sample=lambda:mean+torch.exp(.5*logvar)*torch.randn_like(mean)))
        status(status_output,"Loading IDOL and image encoders")
        # Eager mode avoids a compiler/toolchain requirement on fresh Windows.
        self.pipeline=IdolPipeline.from_pretrained(str(root/"idol"),device="cuda",optimize=False,image_encoders={"dinov3":Dino(),"flux2_vae":Flux()})
        self.torch=torch

    def generate(self,request,output):
        from ..triposplat.worker import status
        from ..triposplat.splat_data import save
        from PIL import Image,ImageOps
        torch=self.torch;started=time.perf_counter();seed=int(request["seed"]);count=int(request["gaussians"])
        if count not in (32768,65536,131072,262144) or not 0<=seed<=2147483647:raise ValueError("Invalid IDOL generation settings")
        steps=int((request.get("sampler") or {}).get("steps",20))
        if not 1<=steps<=100:raise ValueError("IDOL steps must be between 1 and 100")
        status(output,"Generating IDOL Gaussians")
        with Image.open(request["image"]) as source:
            image=ImageOps.exif_transpose(source).copy()
        with torch.inference_mode():
            result=self.pipeline(image,mode="synthetic",seed=seed,steps=steps,cfg_strength=3.0,rescale_t=3.0,num_gaussians=count)
            g=result.gaussian
            arrays={"positions":g.xyz.float().cpu().numpy(),"rotations":g.rotation.float().cpu().numpy(),
                    "scales":g.scaling.float().cpu().numpy(),"opacities":g.opacity[:,0].float().cpu().numpy(),"sh_dc":g.features_dc[:,0].float().cpu().numpy()}
        status(output,"Saving IDOL Gaussians")
        save(output/"splats.npz",arrays)
        result.preprocessed_image.save(output/"input.png")
        (output/"meta.json").write_text(json.dumps({"schema":1,"model":"idol","model_label":"IDOL","seed":seed,"gaussians":len(arrays["positions"]),"image":request["image"],"steps":steps,"elapsed_seconds":time.perf_counter()-started}),encoding="utf-8")
        (output/"complete.json").write_text(json.dumps({"schema":1,"complete":True}),encoding="utf-8")
        status(output,"Complete",complete=True)
