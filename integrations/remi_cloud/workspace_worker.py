"""Project synchronization in the isolated auth process. No bpy or scene access."""
import hashlib
import json
import shutil
from pathlib import Path
import threading
import time

if __package__:
    from .sdk.embedded import CloudError
    from .sdk.models import identifier
    from .sdk.workspace import WorkspaceClient, card_id
else:
    from sdk.embedded import CloudError
    from sdk.models import identifier
    from sdk.workspace import WorkspaceClient, card_id


def scope_key(base_url,user_id):
    return hashlib.sha256((base_url+"\n"+user_id).encode()).hexdigest()


def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+".partial")
    temporary.write_text(json.dumps(value,allow_nan=False),encoding="utf-8")
    temporary.replace(path)


class WorkspaceWorker:
    def __init__(self,session,ipc,cache):
        self.session=session
        self.ipc=Path(ipc)
        self.root=Path(cache)/scope_key(session.base_url,session.profile["user_id"])
        self.outbox=self.root/"outbox";self.outbox.mkdir(parents=True,exist_ok=True)
        self.client=WorkspaceClient(session,self.root/"files")
        self.stopped=threading.Event();self.changed=threading.Event()
        self.cancel_watch=threading.Event()
        self.watcher=None;self.thread=None
        self.catalog={"projects":[],"teams":[]};self.snapshot={}
        self.receipts={};self.errors={};self.uploads={};self.chain={}
        self.selected="default";self.revision=0;self.message="Connecting to shared projects…"
        self.retry_at={};self.ready=False

    def start(self):
        self.thread=threading.Thread(target=self.run,daemon=True,name="remi-projects")
        self.thread.start()

    def publish(self):
        if self.stopped.is_set():return
        self.revision+=1
        value={"user_id":self.session.profile["user_id"] if self.session.profile else "", "base_url":self.session.base_url,
               "project_id":self.selected,"revision":self.revision,"ready":self.ready,
               "catalog":self.catalog,"snapshot":self.snapshot,"receipts":list(self.receipts)[-1000:],
               "errors":self.errors,"uploads":self.uploads,"message":self.message}
        atomic_json(self.ipc/"workspace.json",value)

    def watch(self,cancel):
        while not self.stopped.is_set() and not cancel.is_set():
            try:
                last=None
                for event in self.session.watch_board(cancelled=lambda:self.stopped.is_set() or cancel.is_set()):
                    cursor=event.get("cursor")
                    if cursor!=last:self.changed.set()
                    last=cursor
            except CloudError as error:
                if self.stopped.is_set() or cancel.is_set():return
                self.message=str(error);self.changed.set()
                if not error.retryable:return
                cancel.wait(1)

    def set_project(self,value):
        value="default" if value in (None,"default") else identifier(value).lower()
        if value!=self.selected or self.watcher is None:
            self.cancel_watch.set()
            self.session.select_project(None if value=="default" else value)
            if self.watcher:self.watcher.join(timeout=2)
            self.cancel_watch=threading.Event();self.selected=value
            self.snapshot={};self.ready=False;self.chain={}
            atomic_json(self.root/"selected-project.json",{"id":value})
            self.cancel_watch=threading.Event();self.watcher=None

    def refresh(self):
        self.catalog=self.client.catalog()
        if not any(p["id"]==self.selected for p in self.catalog["projects"]):
            self.set_project("default")
            self.message="Project access changed. Returned to My workspace."
        self.snapshot=self.client.snapshot();self.ready=True
        # Older/auth-only servers may have no project transport. Do not attach
        # a board subscription to their generation socket before discovery.
        if self.watcher is None:
            self.watcher=threading.Thread(target=self.watch,args=(self.cancel_watch,),daemon=True,name="remi-board-events")
            self.watcher.start()
        if not self.errors:self.message="Cloud board is up to date."
        self.publish()

    def execute(self,command):
        action=command.get("action")
        if action=="switch":
            self.set_project(command.get("project_id"));self.changed.set();return
        if action=="refresh":self.changed.set();return
        if action in ("create_project","create_team","invite","team","remove_member","revoke_invite"):
            if action=="create_project":
                payload={"name":command["name"],**({"teamId":identifier(command["team_id"])} if command.get("team_id") else {})}
                value=self.session.api("POST","/projects",payload=payload);self.set_project(value["id"])
            elif action=="create_team":
                value=self.session.api("POST","/teams",payload={"name":command["name"]});self.set_project(value["projectId"])
            else:
                team_id=identifier(command["team_id"])
                if action=="invite":
                    token=self.session.api("POST","/teams/"+team_id+"/invitations",payload={"email":command["email"]})["token"]
                    # Only the explicit invitation action produces a link. No email is sent.
                    self.uploads[command["id"]]={"invitation":self.session._origin+"/app/generate?join="+token}
                elif action=="team":self.uploads[command["id"]]={"team":self.session.api("GET","/teams/"+team_id)}
                elif action=="remove_member":self.session.api("DELETE","/teams/"+team_id+"/members/"+command["user_id"])
                else:self.session.api("DELETE","/teams/"+team_id+"/invitations/"+identifier(command["invitation_id"]))
            if action in ("invite","remove_member","revoke_invite"):
                self.uploads["team-"+team_id]={"team":self.session.api("GET","/teams/"+team_id)}
            self.changed.set();return
        if command.get("project_id","default")!=self.selected:raise CloudError("Select the original project before syncing this edit.",code="project_changed")
        if action=="positions":self.client.patch(command["cards"],only_missing=command.get("only_missing",False))
        elif action=="asset":
            key=card_id(command["card_id"]);path=Path(command["path"]).resolve(strict=True)
            if not path.is_file() or path.stat().st_size>100*1024*1024:raise CloudError("The board file is missing or too large.")
            stat=path.stat();stamp=f"{path}:{stat.st_mtime_ns}:{stat.st_size}"
            if command.get("signature") and stamp!=command["signature"]:
                raise CloudError("The source changed before upload. Your newer local file is kept.",code="local_revision_changed")
            staged=self.root/"sources"/(command["id"]+path.suffix.lower())
            staged.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(path,staged)
            after=path.stat()
            if (after.st_mtime_ns,after.st_size)!=(stat.st_mtime_ns,stat.st_size):
                raise CloudError("The source changed during upload preparation. Your newer local file is kept.",code="local_revision_changed")
            if staged.suffix.lower()==".npz":
                try:
                    if __package__:
                        from ..triposplat.splat_data import load,save_native
                    else:
                        import sys
                        sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
                        from triposplat.splat_data import load,save_native
                    portable=staged.with_suffix(".splat")
                    save_native(portable,load(staged));staged=portable
                except (OSError,ValueError,ImportError):
                    raise CloudError("The local Gaussian archive could not be prepared for sharing.",code="invalid_source") from None
            checksum=hashlib.sha256(staged.read_bytes()).hexdigest()
            receipt_path=self.root/("receipt-"+command["id"]+".json")
            if receipt_path.is_file():
                receipt=json.loads(receipt_path.read_text())
                if checksum!=receipt["sha256"]:raise CloudError("The pending local file changed. Your new local file was kept.",code="local_revision_changed")
                file=receipt["file"]
                self.client.attach(key,file["id"],command.get("title","Asset"),expected_file_id=receipt.get("expected"))
                self.uploads[command["id"]]={"local_id":command["local_id"],"card_id":key,"file":file,"path":str(path)}
                self.changed.set();return
            current=next((a for a in self.snapshot.get("assets",[]) if a["id"]==key),None)
            current_file=current.get("file") if current else None
            uploaded=self.uploads.get(command["id"])
            if uploaded and uploaded.get("file"):
                self.client.attach(key,uploaded["file"]["id"],command.get("title","Asset"),expected_file_id=command.get("expected_file_id"))
                self.changed.set();return
            expected=command.get("expected_file_id")
            chain=self.chain.get(key)
            if chain and expected in (chain["base"],chain["file_id"]):expected=chain["file_id"]
            if current_file and current_file["sha256"]==checksum:
                file=current_file
            else:
                file=self.client.upload(staged,"remi-board-"+hashlib.sha256((key+checksum).encode()).hexdigest())
                receipt_path=self.root/("receipt-"+command["id"]+".json")
                atomic_json(receipt_path,{"file":file,"expected":expected,"sha256":checksum})
                self.client.attach(key,file["id"],command.get("title","Asset"),expected_file_id=expected)
            self.chain[key]={"base":command.get("expected_file_id"),"file_id":file["id"]}
            self.uploads[command["id"]]={"local_id":command["local_id"],"card_id":key,"file":file,"path":str(path)}
        elif action=="delete":
            key=card_id(command["card_id"])
            if command.get("input",False):
                try:self.client.remove(key)
                except CloudError as error:
                    if error.status!=404:raise
            if command.get("job",False):
                try:self.client.remove(key,job=True)
                except CloudError as error:
                    if error.status!=404:raise
        else:raise CloudError("Unsupported project command.")
        self.changed.set()

    def run(self):
        self.changed.set()
        try:
            selected="default"
            try:selected=json.loads((self.root/"selected-project.json").read_text())["id"]
            except (OSError,ValueError,KeyError,TypeError):pass
            try:self.set_project(selected)
            except CloudError:self.set_project("default")
            while not self.stopped.is_set() and self.session.profile:
                if self.changed.is_set():
                    self.changed.clear()
                    try:self.refresh()
                    except CloudError as error:
                        self.message=str(error);self.publish()
                        if error.code=="project_access_changed" or error.status in (403,404):
                            try:self.set_project("default")
                            except CloudError:pass
                        self.stopped.wait(1);self.changed.set()
                    except Exception:
                        self.message="Project synchronization was interrupted. Your local files are kept.";self.publish()
                        self.stopped.wait(2);self.changed.set()
                blocked_cards=set()
                for path in sorted(self.outbox.glob("*.json")):
                    if self.stopped.is_set():break
                    command={}
                    try:
                        if path.stat().st_size>1024*1024:raise CloudError("Project command is too large.")
                        command=json.loads(path.read_text())
                        card=command.get("card_id")
                        if card and card in blocked_cards:continue
                        if self.retry_at.get(str(path),0)>time.monotonic():
                            if card:blocked_cards.add(card)
                            continue
                        if command.get("user_id")!=self.session.profile["user_id"] or command.get("base_url")!=self.session.base_url:raise CloudError("Project command belongs to another account.")
                        if command.get("project_id","default")!=self.selected and command.get("action") not in ("switch","refresh","create_project","create_team"):continue
                        self.execute(command)
                        self.receipts[command["id"]]=True;self.errors.pop(command["id"],None)
                        path.unlink()
                        # A receipt must never make a stale snapshot authoritative:
                        # the new card/revision may not exist in that snapshot yet.
                        self.ready=False;self.changed.set();self.publish()
                    except CloudError as error:
                        if error.retryable and command.get("action") not in ("create_project","create_team","invite"):
                            self.message=str(error);self.retry_at[str(path)]=time.monotonic()+5
                            if command.get("card_id"):blocked_cards.add(command["card_id"])
                        else:
                            self.message=str(error)
                            self.errors[path.stem]={"code":error.code,"message":str(error)}
                            path.rename(path.with_suffix(".failed"));self.changed.set()
                        self.publish()
                    except Exception:
                        self.message="A local board edit could not sync. The local file is kept."
                        self.retry_at[str(path)]=time.monotonic()+5
                        if command.get("card_id"):blocked_cards.add(command["card_id"])
                        self.publish()
                self.stopped.wait(0.2)  # Local IPC scan only; remote refreshes are event driven.
        finally:self.cancel_watch.set()

    def close(self):
        self.stopped.set();self.cancel_watch.set()
        if self.thread:self.thread.join(timeout=2)
