"""Main-thread board adapter. Network and verified file caching live in the worker."""
import hashlib
import json
from pathlib import Path
import uuid

import bpy

from ...integrations.remi_cloud.client import runtime
from ..board.assets import Asset, Catalog

PROJECT_KEY="remi_cloud_project"
SCOPE_KEY="remi_cloud_board_scope"
# Blender board units are 300 wide and Y-up; the web board is 224 wide and Y-down.
RATIO=300.0/224.0


def web_position(position):
    return {"x":round(float(position[0])/RATIO,6),"y":round(-float(position[1])/RATIO,6)}


def local_position(point):
    return [float(point["x"])*RATIO,-float(point["y"])*RATIO]


def configure_cache():
    if runtime.credential_root is None and not bpy.app.background:
        runtime.credential_root = Path(bpy.utils.user_resource("CONFIG", path="remi/credentials", create=True))
    if runtime.workspace_cache is None:
        runtime.workspace_cache=Path(bpy.utils.user_resource("DATAFILES",path="remi/cloud-workspaces",create=True))


def current(context):
    value=runtime.workspace_status()
    state=runtime.status()
    if getattr(context.window_manager.remi_cloud,"project","default")=="LOCAL":return {}
    if state.get("signed_in") is not True or not value.get("user_id") or state.get("user_id")!=value.get("user_id"):return {}
    if value.get("base_url")!=context.window_manager.remi_cloud.base_url:return {}
    return value


def scope(value):
    return f"{value['base_url']}|{value['user_id']}|{value['project_id']}"


def marker(asset):
    value=asset.settings.get("cloud",{})
    return value if isinstance(value,dict) else {}


def signature(asset):
    if not asset.artifact:return ""
    path=Path(asset.artifact).resolve()
    if not path.is_file():return ""
    try:stamp=path.stat();return f"{path}:{stamp.st_mtime_ns}:{stamp.st_size}"
    except OSError:return ""


class BoardSync:
    def __init__(self,board):
        self.board=board;self.scope="";self.revision=0;self.applying=False
        self.last_positions={};self.pending={};self.asset_pending={};self.deleted_pending={}
        self.remote_ids=set();self.notice="";self.previous_connected=False

    def switch(self,context,value):
        from ..board import canvas
        board=self.board;wanted=scope(value)
        if wanted==self.scope:return True
        if context.window_manager.remi_image.busy or board.running or board.queue or board.runner.job is not None:
            self.notice="Finish local jobs before switching the shared project.";return False
        if self.scope:
            canvas.save_layout(context)
        elif board.catalog.assets:
            canvas.save_layout(context)  # Preserve the original local gallery before entering Cloud.
        self.scope=wanted;self.revision=0;self.last_positions={};self.pending={};self.asset_pending={};self.deleted_pending={};self.remote_ids=set()
        # Restore unsent moves before accepting the first remote snapshot.
        from ...integrations.remi_cloud.workspace_worker import scope_key
        outbox=Path(runtime.workspace_cache)/scope_key(value["base_url"],value["user_id"])/"outbox"
        for path in sorted([*outbox.glob("*.json"), *outbox.glob("*.failed")]):
            try:
                command=json.loads(path.read_text())
                if command.get("project_id","default")!=value["project_id"]:continue
                if command.get("action")=="positions":
                    for key,point in command["cards"].items():
                        self.pending[command["id"]]={"kind":"position","card_id":key,"point":point}
                        if point:self.last_positions[key]=point
                elif command.get("action")=="asset":
                    stamp=command.get("signature","")
                    self.pending[command["id"]]={"kind":"asset","local_id":command["local_id"],"signature":stamp,"card_id":command["card_id"]}
                    self.asset_pending[command["local_id"]]=stamp
                elif command.get("action")=="delete":
                    self.deleted_pending[command["local_id"]]=command["id"]
            except (OSError,ValueError,KeyError,TypeError):continue
        self.applying=True
        try:
            generation = getattr(board.scene, "remi_generation", None)
            if generation is not None and generation.three_d_backend == "INHERIT":
                # A fresh account/project defaults to its advertised Cloud model;
                # an explicit local IDOL/NCM choice survives project switches.
                generation.backend = "API"
            board.cleanup()
            board.scene[SCOPE_KEY]=wanted
            board.scene[PROJECT_KEY]=value["project_id"]
            board.load(context,[])
            board.selected.clear();board.group_selected.clear()
            board.queue=[];board.pending=[];board.running=""
        finally:self.applying=False
        return True

    def command(self,context,action,**payload):
        value=current(context)
        if not value or not value.get("ready") or self.scope!=scope(value):return None
        return runtime.workspace_command(action,value["base_url"],project_id=value["project_id"],**payload)

    def collect(self,context):
        if self.applying:return
        value=current(context)
        if not value or not value.get("ready") or scope(value)!=self.scope:return
        board=self.board
        # Existing local/gallery assets never get bulk-uploaded by sign-in.
        # Only assets created in this shared project, or explicit board imports,
        # enter this isolated board namespace.
        for asset in list(board.catalog.assets.values()):
            meta=marker(asset)
            if asset.hidden:
                if meta.get("card_id") and asset.id not in self.deleted_pending:
                    cid=self.command(context,"delete",local_id=asset.id,card_id=meta["card_id"],input=bool(meta.get("input")),job=bool(meta.get("job")))
                    if cid:self.pending[cid]={"kind":"delete","local_id":asset.id};self.deleted_pending[asset.id]=cid
                continue
            key=meta.get("card_id") or "asset-"+hashlib.sha256((self.scope+asset.id).encode()).hexdigest()[:40]
            stamp=signature(asset)
            uploadable=asset.state=="READY" and bool(stamp) and not (meta.get("job") and not meta.get("file_id"))
            if uploadable and stamp!=meta.get("signature") and self.asset_pending.get(asset.id)!=stamp:
                cid=self.command(context,"asset",local_id=asset.id,card_id=key,path=asset.artifact,signature=stamp,title=asset.label or asset.title or asset.kind.title(),expected_file_id=meta.get("file_id"))
                if cid:
                    self.pending[cid]={"kind":"asset","local_id":asset.id,"signature":stamp,"card_id":key}
                    self.asset_pending[asset.id]=stamp
                    asset.settings["cloud"]={**meta,"scope":self.scope,"card_id":key,"input":meta.get("input",True),"job":meta.get("job",False)}
            # Positions can sync before an import finishes; it never starts compute.
            position=board.positions.get(asset.id)
            if position is None:continue
            point=web_position(position)
            parent=board.catalog.assets.get(asset.parent)
            parent_id=marker(parent).get("card_id") if parent else None
            if parent_id:point["parent"]=parent_id
            if self.last_positions.get(key)!=point:
                cid=self.command(context,"positions",cards={key:point},only_missing=key not in self.remote_ids and key not in self.last_positions)
                if cid:self.pending[cid]={"kind":"position","card_id":key,"point":point};self.last_positions[key]=point

    def apply(self,context,value):
        board=self.board;snapshot=value.get("snapshot",{})
        receipts=set(value.get("receipts",[]));errors=value.get("errors",{})
        for cid,receipt in value.get("uploads",{}).items():
            pending=self.pending.get(cid)
            if not pending or pending["kind"]!="asset" or not receipt.get("file"):continue
            asset=board.catalog.assets.get(pending["local_id"])
            if asset is None:continue
            file=receipt["file"]
            asset.settings["cloud"]={"scope":self.scope,"card_id":receipt["card_id"],"file_id":file["id"],"sha256":file["sha256"],"input":True,"job":marker(asset).get("job",False),"signature":pending["signature"]}
        for cid,pending in list(self.pending.items()):
            if cid not in receipts and cid not in errors:continue
            if cid in errors:
                self.notice=errors[cid]["message"]
                if pending["kind"]=="position":self.last_positions.pop(pending["card_id"],None)
            if pending["kind"]=="asset":
                if cid not in errors and self.asset_pending.get(pending["local_id"])==pending["signature"]:self.asset_pending.pop(pending["local_id"],None)
                # Conflicted local file stays on its card; no automatic overwrite/retry.
            self.pending.pop(cid,None)
        # Prefer the local job/import card over an event-created placeholder.
        ordered=sorted(board.catalog.assets.values(),key=lambda a:not a.id.startswith("cloud-"))
        aliases={marker(a).get("card_id"):a.id for a in ordered if marker(a).get("scope")==self.scope}
        for asset in ordered:
            meta=marker(asset)
            if meta.get("scope")==self.scope and aliases.get(meta.get("card_id"))!=asset.id:
                board.forget(asset.id);board.catalog.assets.pop(asset.id,None)
        incoming={a["id"]:a for a in snapshot.get("assets",[])}
        self.remote_ids=set(incoming)
        file_owners={a.get("file",{}).get("id"):key for key,a in incoming.items() if a.get("file")}
        for key,remote in incoming.items():
            file=remote.get("file");job=remote.get("job")
            local_id=aliases.get(key,"cloud-"+key);asset=board.catalog.assets.get(local_id)
            if asset and local_id in self.deleted_pending:continue
            if asset is None:
                kind=(file or {}).get("kind") or remote.get("kind") or ("image" if job and job["operation"]=="generate_image" else "splat" if job and job["operation"]=="generate" else "mesh")
                asset=Asset(local_id,{"image":"IMAGE","splat":"SPLAT","mesh":"MESH"}[kind],title=remote["title"],operation="")
                board.catalog.assets[local_id]=asset;aliases[key]=local_id
            meta=marker(asset)
            dirty=bool(meta.get("signature") and signature(asset) and signature(asset)!=meta["signature"])
            if meta.get("job") and not meta.get("file_id"):dirty=False
            if not dirty and local_id not in self.asset_pending:
                path=remote.get("path","")
                waiting=asset.state=="SYNCING"
                if path and path!=asset.artifact and (not file or meta.get("file_id")!=file["id"] or not signature(asset) or asset.state=="SYNCING"):
                    board.unload_preview(local_id);board.release_texture(local_id)
                    asset.artifact=path;asset.native_artifact=asset.package_artifact=asset.preview="";asset.preview_view=[]
                    board.dirty_previews.add(local_id)
                asset.title=remote["title"]
                state=job["status"] if job else "succeeded"
                asset.state={"succeeded":"READY","failed":"ERROR","canceled":"CANCELLED","queued":"QUEUED","running":"RUNNING"}.get(state,"RUNNING")
                asset.status="Ready" if asset.state=="READY" else job.get("stage","Working") if job else "Ready"
                asset.progress=float(job.get("progress",0)) if job else 1.0
                if file and state=="succeeded" and not path and (
                        not signature(asset) or meta.get("file_id")!=file["id"] or waiting):
                    asset.state="SYNCING"
                    asset.status=remote.get("downloadError") or "Downloading shared asset…"
                    asset.progress=0.0
                if remote.get("uploadStatus"):
                    asset.state="SYNCING" if remote["uploadStatus"]=="uploading" else "ERROR"
                    asset.status="Uploading on another device…" if asset.state=="SYNCING" else "Upload failed on the source device; its local file is kept."
                    asset.progress=0.0
                if file:
                    asset.kind={"image":"IMAGE","mesh":"MESH","splat":"SPLAT"}[file["kind"]]
                asset.settings["cloud"]={"scope":self.scope,"card_id":key,"file_id":file["id"] if file else None,"sha256":file["sha256"] if file else None,"input":bool(remote.get("uploadStatus")) or key in {i["id"] for i in snapshot.get("inputs",[])},"job":bool(job),"signature":signature(asset)}
            if local_id not in board.order:board.order.append(local_id)
        pending_positions={p["card_id"] for p in self.pending.values() if p["kind"]=="position"}
        for key,remote in incoming.items():
            local_id=aliases.get(key);asset=board.catalog.assets.get(local_id)
            if not asset:continue
            point=snapshot.get("cards",{}).get(key)
            parent_key=(point or {}).get("parent")
            if not parent_key and remote.get("job"):
                parent_key=next((file_owners.get(k) for k in remote["job"]["inputFileIds"] if file_owners.get(k)),None)
            asset.parent=aliases.get(parent_key,"")
            if point and key not in pending_positions and not board.navigating():
                board.positions[local_id]=local_position(point);board.scales[local_id]=1.0
                self.last_positions[key]=point
            elif key in pending_positions:
                pending=next((p["point"] for p in reversed(list(self.pending.values())) if p["kind"]=="position" and p["card_id"]==key),None)
                if pending:board.positions[local_id]=local_position(pending)
            elif local_id not in board.positions:board.place_asset(local_id,asset.parent)
        for asset in list(board.catalog.assets.values()):
            meta=marker(asset)
            if meta.get("scope")==self.scope and meta.get("card_id") not in incoming and asset.id not in self.asset_pending and asset.id not in self.deleted_pending:
                if (meta.get("job") and not meta.get("file_id")) or (signature(asset) and signature(asset)!=meta.get("signature")):
                    self.notice="A shared card was deleted while you had local edits. Your local file is kept.";continue
                board.forget(asset.id);board.catalog.assets.pop(asset.id,None)
        self.revision=value["revision"]

    def tick(self,context):
        value=current(context)
        if not value or not value.get("ready"):
            account=runtime.status()
            if self.scope and (not account.get("signed_in") or getattr(context.window_manager.remi_cloud,"project",None)=="LOCAL" or
                    self.scope.split("|")[1:2]!=[account.get("user_id")]):
                from ..board import canvas
                self.applying=True
                try:
                    canvas.save_layout(context);self.board.cleanup()
                    self.board.scene.pop(SCOPE_KEY,None);self.board.scene.pop(PROJECT_KEY,None)
                    self.board.load(context,canvas.gallery_entries(context));self.scope="";self.revision=0
                finally:self.applying=False
            return
        if not self.switch(context,value):return
        if self.revision!=value.get("revision") and not self.board.navigating() and self.board.orbit_drag is None:
            self.applying=True
            try:
                self.apply(context,value)
                from ..board import canvas
                canvas.save_layout(context)
            finally:self.applying=False
        self.collect(context)


def get_sync(board):
    if not hasattr(board,"cloud_sync"):board.cloud_sync=BoardSync(board)
    return board.cloud_sync


def tick(context):
    from ..board import canvas
    board=canvas.BOARD
    if board is not None:
        # The user may be editing in a separate scene while Cloud events arrive.
        with context.temp_override(scene=board.scene,view_layer=board.view_layer):
            get_sync(board).tick(context)


def saved(context):
    from ..board import canvas
    board=canvas.BOARD
    if board is not None:
        with context.temp_override(scene=board.scene,view_layer=board.view_layer):
            get_sync(board).collect(context)


def gallery(context):
    # Keep editable snapshots in a local filesystem namespace that includes
    # account/project identity. Existing local galleries retain their old path.
    original=str(Path(bpy.path.abspath(context.scene.remi_image.output)).resolve())
    key=context.scene.get(SCOPE_KEY)
    value=current(context)
    if not value or key!=scope(value):key=None
    return str(Path(original)/".remi-cloud"/hashlib.sha256(key.encode()).hexdigest()) if key else original
