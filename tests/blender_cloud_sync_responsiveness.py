"""Partial file availability must not block the shared board or lose card edits."""
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from uuid import uuid4
import bpy

ROOT=Path(__file__).resolve().parents[1]
if "remi" not in sys.modules:
    spec=importlib.util.spec_from_file_location("remi",ROOT/"__init__.py",submodule_search_locations=[str(ROOT)])
    remi=importlib.util.module_from_spec(spec);sys.modules["remi"]=remi;spec.loader.exec_module(remi)
from remi.features.board.assets import Catalog
from remi.features.cloud.board_sync import BoardSync,signature

with tempfile.TemporaryDirectory() as temporary:
    root=Path(temporary)
    board=SimpleNamespace(catalog=Catalog(),order=[],positions={},scales={},dirty_previews=set(),
        navigating=lambda:False,unload_preview=lambda key:None,release_texture=lambda key:None,
        place_asset=lambda key,parent="":None)
    sync=BoardSync(board);sync.scope="fixture"
    file={"id":str(uuid4()),"kind":"mesh","sha256":"a"*64}
    remote={"id":"mesh","title":"Large mesh","file":file}
    snapshot={"assets":[remote],"cards":{"mesh":{"x":4,"y":7}},"inputs":[{"id":"mesh"}]}
    value={"snapshot":snapshot,"revision":1}
    sync.apply(bpy.context,value)
    asset=board.catalog.assets["cloud-mesh"]
    assert asset.state=="SYNCING" and not signature(asset)
    assert board.positions[asset.id]==[4*300/224,-7*300/224]
    assert Catalog(board.catalog.documents()).assets[asset.id].state=="SYNCING"
    remote["downloadError"]="Download interrupted; retrying."
    value["revision"]=2;sync.apply(bpy.context,value)
    assert asset.state=="SYNCING" and "interrupted" in asset.status
    path=root/"downloaded.glb";path.write_bytes(b"verified fixture")
    remote["path"]=str(path);value["revision"]=3;sync.apply(bpy.context,value)
    assert asset.state=="READY" and asset.artifact==str(path)
    # A new remote revision remains unavailable until its own file arrives.
    old=path
    remote.pop("path");remote.pop("downloadError")
    file={**file,"id":str(uuid4()),"sha256":"b"*64};remote["file"]=file
    value["revision"]=4;sync.apply(bpy.context,value)
    assert asset.state=="SYNCING" and asset.artifact==str(old)
    value["revision"]=5;sync.apply(bpy.context,value)
    assert asset.state=="SYNCING"
    new=root/"new.glb";new.write_bytes(b"new verified fixture");remote["path"]=str(new)
    value["revision"]=6;sync.apply(bpy.context,value)
    assert asset.state=="READY" and asset.artifact==str(new) and old.is_file()
    # Local edits must still win over metadata and downloads of a remote change.
    new.write_bytes(b"my local edited mesh")
    remote["path"]=str(old);remote["file"]={**file,"id":str(uuid4())}
    value["revision"]=7;sync.apply(bpy.context,value)
    assert asset.artifact==str(new) and new.read_bytes()==b"my local edited mesh"
print("REMI_CLOUD_SYNC_RESPONSIVENESS_OK")
