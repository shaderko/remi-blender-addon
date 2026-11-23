"""Download pinned IDOL files and reuse the existing local image encoders."""
import hashlib
import json
from pathlib import Path
import uuid

HERE=Path(__file__).parent


def components():
    model=json.loads((HERE/"model-lock.json").read_text())
    shared=json.loads((HERE/"encoder-lock.json").read_text())
    return {**{"idol/"+name:value for name,value in model["files"].items()},**shared}


def download(models,report,*,opener=None):
    from ..triposplat.download import checksum,destination,download_chunks,install_verified,open_url,partial_path
    root=Path(models).resolve();files=components();missing=[]
    opener=opener or open_url
    for name,item in files.items():
        path=destination(root,name)
        if path.exists():
            report("Verifying "+Path(name).name)
            if not path.is_file() or path.stat().st_size!=item["bytes"] or checksum(path)!=item["sha256"]:
                raise ValueError("Existing IDOL component differs; choose another Model Folder: "+name)
        else:missing.append(name)
    total=sum(files[name]["bytes"] for name in missing);received=0;token=uuid.uuid4().hex
    for name in missing:
        item=files[name];path=destination(root,name);path.parent.mkdir(parents=True,exist_ok=True)
        temporary=partial_path(root,name,token);digest=hashlib.sha256();loaded=0
        try:
            with temporary.open("xb") as stream:
                for chunk in download_chunks(item["url"],item["bytes"],opener):
                    loaded+=len(chunk)
                    if loaded>item["bytes"]:raise ValueError("IDOL component exceeded its size: "+name)
                    stream.write(chunk);digest.update(chunk);received+=len(chunk)
                    report(f"Downloading IDOL · {received/max(total,1):.0%}")
            if loaded!=item["bytes"] or digest.hexdigest()!=item["sha256"]:raise ValueError("IDOL component checksum mismatch: "+name)
            install_verified(temporary,path,item["sha256"])
        finally:temporary.unlink(missing_ok=True)
    report("IDOL models downloaded and verified")
    return root
