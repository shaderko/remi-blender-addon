"""Partial file availability must not block the shared board or lose card edits."""
import importlib.util
import os
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
    remote['download']={'state':'failed','error':'Connection interrupted'}
    value['revision']=6;sync.apply(bpy.context,value)
    assert asset.state=='ERROR' and 'retry' in asset.status
    value['revision']=7;sync.apply(bpy.context,value)
    assert asset.state=='ERROR', 'A failed new revision appeared ready using the old file'
    remote['download']={'state':'queued'}
    value['revision']=8;sync.apply(bpy.context,value)
    assert asset.state=='SYNCING' and 'Queued' in asset.status
    new=root/"new.glb";new.write_bytes(b"new verified fixture");remote["path"]=str(new)
    value["revision"]=6;sync.apply(bpy.context,value)
    assert asset.state=="READY" and asset.artifact==str(new) and old.is_file()
    # A cached file that arrives at the same path still clears a saved pending state.
    asset.state='SYNCING';asset.settings['cloud']['download_pending']=True
    asset.settings['cloud']['signature']='older cached revision'
    stamp=new.stat();remote['pathStamp']=[stamp.st_mtime_ns,stamp.st_size]
    sync.apply(bpy.context,value)
    assert asset.state=='READY'
    # Local edits must still win over metadata and downloads of a remote change.
    new.write_bytes(b"my local edited mesh")
    stamp=new.stat();os.utime(new,ns=(stamp.st_atime_ns,stamp.st_mtime_ns+2_000_000_000))
    remote["path"]=str(old);remote["file"]={**file,"id":str(uuid4())}
    value["revision"]=7;sync.apply(bpy.context,value)
    assert asset.artifact==str(new) and new.read_bytes()==b"my local edited mesh"
    # Another device sees an upload card before a file exists.
    board.catalog=Catalog();board.order=[];board.positions={};board.scales={}
    sync=BoardSync(board);sync.scope="fixture"
    remote={"id":"pending","title":"Queued mesh","kind":"mesh","uploadStatus":"uploading","file":None}
    value={"snapshot":{"assets":[remote],"cards":{"pending":{"x":12,"y":8}},"inputs":[]},"revision":1}
    sync.apply(bpy.context,value)
    pending=board.catalog.assets["cloud-pending"]
    assert pending.state=="SYNCING" and "Uploading" in pending.status and pending.kind=="MESH"
    assert pending.settings["cloud"]["input"] and not pending.artifact
    board.positions[pending.id]=[900,100]
    value.update({"base_url":"https://fixture.test/api/v1","user_id":"fixture","project_id":"default","ready":True})
    from unittest.mock import patch
    from remi.features.cloud import board_sync as adapter
    sync.scope=adapter.scope(value)
    with patch.object(adapter,"current",return_value=value),patch.object(sync,"command",return_value="move-placeholder") as command:
        sync.collect(bpy.context)
    assert command.call_args.args[1]=="positions", "Moving a placeholder queued a nonexistent file upload"
    assert command.call_args.kwargs["cards"]["pending"]==adapter.web_position([900,100])
    remote["uploadStatus"]="failed";value["revision"]=2;sync.apply(bpy.context,value)
    assert pending.state=="ERROR" and "Upload failed" in pending.status
    # A locally ready file wins over its own uploading announcement.
    pending.artifact=str(new);pending.state="READY"
    sync.asset_pending[pending.id]="local-upload"
    remote["uploadStatus"]="uploading";value["revision"]=3;sync.apply(bpy.context,value)
    assert pending.state=="READY" and pending.artifact==str(new)
    # Generated file tickets are resolved by the download queue, not board loading.
    board.catalog=Catalog();board.order=[];board.positions={};board.scales={}
    sync=BoardSync(board);sync.scope='fixture'
    reference={'id':str(uuid4()),'kind':'splat'}
    image_reference={'id':str(uuid4()),'kind':'image'}
    image={'id':'source-image','title':'Source image','file':None,'fileRef':image_reference}
    remote={'id':'generated','title':'Generated source','file':None,'fileRef':reference,
            'job':{'status':'succeeded','operation':'generate','progress':1,'inputFileIds':[image_reference['id']]}}
    value={'snapshot':{'assets':[image,remote],'cards':{},'inputs':[]},'revision':1}
    sync.apply(bpy.context,value)
    generated=board.catalog.assets['cloud-generated']
    assert generated.state=='SYNCING' and generated.kind=='SPLAT'
    assert generated.parent=='cloud-source-image', 'Pending file tickets lost the image/source link'
    value['stale']=True;sync.apply(bpy.context,value)
    assert generated.state=='ERROR' and 'reconnect' in generated.status
    value['stale']=False;sync.apply(bpy.context,value)
    assert generated.state=='SYNCING'
print("REMI_CLOUD_SYNC_RESPONSIVENESS_OK")
