"""Project synchronization in the isolated auth process. No bpy or scene access."""
from concurrent.futures import Future
import hashlib
import json
import shutil
from pathlib import Path
import threading
import time

if __package__:
    from .sdk.embedded import CloudError, CloudSession
    from .sdk.models import identifier
    from .sdk.workspace import WorkspaceClient, card_id
    from .sdk.atomic import write_json
    from .downloads import Downloads
else:
    from sdk.embedded import CloudError, CloudSession
    from sdk.models import identifier
    from sdk.workspace import WorkspaceClient, card_id
    from sdk.atomic import write_json
    from downloads import Downloads


def scope_key(base_url,user_id):
    return hashlib.sha256((base_url+"\n"+user_id).encode()).hexdigest()


def atomic_json(path,value):
    write_json(path,value)


def upload_asset(command, client, root, snapshot, chain, uploads):
    staged = root/"sources"/(command["id"]+Path(command["path"]).suffix.lower())
    try:
        key=card_id(command["card_id"]);path=Path(command["path"]).resolve()
        if not path.is_file():raise CloudError("The local board file is missing. Its card is kept.",code="invalid_source")
        if path.stat().st_size>100*1024*1024:raise CloudError("This mesh exceeds the 100 MiB upload limit. Optimize it before sharing.",code="file_too_large")
        stat=path.stat();stamp=f"{path}:{stat.st_mtime_ns}:{stat.st_size}"
        if command.get("signature") and stamp!=command["signature"]:
            raise CloudError("The source changed before upload. Your newer local file is kept.",code="local_revision_changed")
        staged=root/"sources"/(command["id"]+path.suffix.lower())
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
        with staged.open("rb") as stream:checksum=hashlib.file_digest(stream,"sha256").hexdigest()
        receipt_path=root/("receipt-"+command["id"]+".json")
        if receipt_path.is_file():
            receipt=json.loads(receipt_path.read_text())
            if checksum!=receipt["sha256"]:raise CloudError("The pending local file changed. Your new local file was kept.",code="local_revision_changed")
            file=receipt["file"]
            client.attach(key,file["id"],command.get("title","Asset"),expected_file_id=receipt.get("expected"))
            uploads[command["id"]]={"local_id":command["local_id"],"card_id":key,"file":file,"path":str(path)}
            return uploads[command["id"]]
        current=next((a for a in snapshot.get("assets",[]) if a["id"]==key),None)
        current_file=current.get("file") if current else None
        uploaded=uploads.get(command["id"])
        if uploaded and uploaded.get("file"):
            client.attach(key,uploaded["file"]["id"],command.get("title","Asset"),expected_file_id=command.get("expected_file_id"))
            return uploads[command["id"]]
        expected=command.get("expected_file_id")
        previous=chain.get(key)
        if previous and expected in (previous["base"],previous["file_id"]):expected=previous["file_id"]
        if current_file and current_file["sha256"]==checksum:
            file=current_file
        else:
            file=client.upload(staged,"remi-board-"+hashlib.sha256((key+checksum).encode()).hexdigest())
            receipt_path=root/("receipt-"+command["id"]+".json")
            atomic_json(receipt_path,{"file":file,"expected":expected,"sha256":checksum})
            client.attach(key,file["id"],command.get("title","Asset"),expected_file_id=expected)
        chain[key]={"base":command.get("expected_file_id"),"file_id":file["id"]}
        uploads[command["id"]]={"local_id":command["local_id"],"card_id":key,"file":file,"path":str(path)}
        return uploads[command["id"]]
    finally:
        staged.unlink(missing_ok=True)
        original=root/"sources"/(command["id"]+Path(command["path"]).suffix.lower())
        original.unlink(missing_ok=True)


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
        self.transfer=None;self.chains={};self.announced=set()
        self.downloads=Downloads(self.download_file)
        self.publish_pending=False;self.last_published=0
        self.retry_downloads=threading.Event()
        for path in sorted(self.outbox.glob("*.error"))[-1000:]:
            try:self.errors[path.stem]=json.loads(path.read_text())
            except (OSError,ValueError):pass

    def start(self):
        self.thread=threading.Thread(target=self.run,daemon=True,name="remi-projects")
        self.thread.start()

    def publish(self,*,heartbeat=False):
        if self.stopped.is_set():return
        if not heartbeat:self.revision+=1
        value={"user_id":self.session.profile["user_id"] if self.session.profile else "", "base_url":self.session.base_url,
               "project_id":self.selected,"revision":self.revision,"ready":self.ready,
               "catalog":self.catalog,"snapshot":self.snapshot,"receipts":list(self.receipts)[-1000:],
               "errors":self.errors,"uploads":self.uploads,"message":self.message,"updatedAt":time.time()}
        try:atomic_json(self.ipc/"workspace.json",value)
        except OSError:
            self.publish_pending=True
            return
        self.publish_pending=False;self.last_published=time.monotonic()

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
            self.snapshot={};self.ready=False;self.chain=self.chains.setdefault(value,{})
            atomic_json(self.root/"selected-project.json",{"id":value})
            self.cancel_watch=threading.Event();self.watcher=None

    def refresh(self):
        self.catalog=self.client.catalog()
        if not any(p["id"]==self.selected for p in self.catalog["projects"]):
            self.set_project("default")
            self.message="Project access changed. Returned to My workspace."
        # Start control subscriptions before any asset bytes are fetched.
        # File transfers use a separate project-bound session below.
        if self.watcher is None:
            self.watcher=threading.Thread(target=self.watch,args=(self.cancel_watch,),daemon=True,name="remi-board-events")
            self.watcher.start()
        self.snapshot=self.client.snapshot(download_files=False,resolve_files=False);self.ready=True
        self.downloads.apply(self.selected,self.snapshot.get('assets',[]))
        self.update_transfer_message()
        self.publish()

    def execute(self,command):
        action=command.get("action")
        if action=="switch":
            self.set_project(command.get("project_id"));self.changed.set();return
        if action=="refresh":self.retry_downloads.set();self.changed.set();return
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
        if action=='retry_download':
            key=card_id(command['card_id'])
            asset=next((a for a in self.snapshot.get('assets',[]) if a['id']==key),None)
            if asset and (asset.get('file') or asset.get('fileRef')):
                self.downloads.retry(self.selected,asset.get('file') or asset['fileRef'])
        elif action=="announce":
            self.client.announce(command["card_id"],command["kind"],command.get("title","Asset"),command["upload_id"],status=command.get("status","uploading"))
        elif action=="positions":self.client.patch(command["cards"],only_missing=command.get("only_missing",False))
        elif action=="asset":
            upload_asset(command,self.client,self.root,self.snapshot,self.chain,self.uploads)
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

    def transfer_client(self, project):
        # Never share the mutable project selection or socket with a transfer.
        session=CloudSession(self.session.base_url,client_id="remi-blender",realtime=False)
        session._token=self.session._token
        session.profile=dict(self.session.profile)
        session.select_project(None if project=="default" else project)
        return WorkspaceClient(session,self.root/"files")

    def download_file(self,project,file,progress,cancelled):
        client=self.transfer_client(project)
        if cancelled():raise InterruptedError('Download cancelled')
        metadata=file if file.get('sha256') else client.resolve_file(file)
        return {'path':client.file(metadata,progress=progress,cancelled=cancelled),'file':metadata}

    def update_transfer_message(self):
        if self.transfer:self.message='Uploading board asset…';return
        states=[r['state'] for k,r in self.downloads.records.items() if k in self.downloads.wanted]
        active=sum(s in ('downloading','verifying') for s in states)
        if active:self.message=f'Downloading {active} shared asset(s)…'
        elif 'queued' in states or 'retrying' in states:self.message='Waiting to retry shared downloads…'
        elif 'failed' in states:self.message='Some downloads failed. Retry from the card menu.'
        elif not self.errors:self.message='Cloud board is up to date.'

    def begin_transfer(self, kind, project, action, **details):
        future=Future()
        self.transfer={"kind":kind,"project":project,"future":future,**details}
        def run():
            try:future.set_result(action())
            except Exception as error:future.set_exception(error)
            finally:self.changed.set()
        threading.Thread(target=run,daemon=True,name="remi-asset-transfer").start()
        self.message="Uploading board asset…" if kind=="upload" else "Downloading shared assets…"
        self.publish()

    def command_error(self,path,command,error):
        if not isinstance(error,CloudError):
            error=CloudError("A local board edit could not sync. The local file is kept.",retryable=True)
        self.message=str(error)
        if command.get("action")=="asset" and not error.retryable:
            command_id=command.get("id","")
            kind=command.get("kind") or ("image" if Path(command.get("path","")).suffix.lower() in (".png",".jpg",".jpeg",".webp") else "splat" if Path(command.get("path","")).suffix.lower() in (".npz",".splat") else "mesh")
            atomic_json(self.outbox/(command_id+"-failed.json"),{**command,"id":command_id+"-failed","action":"announce","upload_id":command_id,"kind":kind,"status":"failed"})
        if error.retryable and command.get("action") not in ("create_project","create_team","invite"):
            self.retry_at[str(path)]=time.monotonic()+5
        else:
            self.errors[path.stem]={"code":error.code,"message":str(error)}
            atomic_json(path.with_suffix(".error"),self.errors[path.stem])
            if path.exists():path.replace(path.with_suffix(".failed"))
        self.publish()

    def acknowledge(self,path,command):
        self.receipts[command["id"]]=True;self.errors.pop(command["id"],None)
        path.unlink(missing_ok=True)
        # A receipt must not make the previous snapshot authoritative. Refresh
        # small metadata before exposing it; file bytes never gate readiness.
        if command.get("project_id","default")==self.selected:self.ready=False
        self.changed.set();self.publish()

    def finish_transfer(self):
        task=self.transfer
        if not task or not task["future"].done():return
        self.transfer=None
        try:
            result=task["future"].result()
            self.uploads[task["command"]["id"]]=result
            self.acknowledge(task["path"],task["command"])
        except Exception as error:
            self.command_error(task["path"],task["command"],error)

    def process_commands(self):
        commands=[]
        for path in sorted(self.outbox.glob("*.json")):
            command={}
            try:
                if path.stat().st_size>1024*1024:raise CloudError("Project command is too large.")
                command=json.loads(path.read_text())
                if not isinstance(command,dict):raise CloudError("Invalid project command.")
                commands.append((path,command))
            except Exception:
                self.command_error(path,{},CloudError("The saved sync command is invalid. Local asset files are kept.",code="invalid_command"))
        # Selection and small edits must not sit behind a queue of big meshes.
        priority={"switch":0,"refresh":1,"create_project":1,"create_team":1,"announce":2,"asset":4}
        commands.sort(key=lambda row:priority.get(row[1].get("action"),2))
        blocked=set()
        if self.transfer and self.transfer["kind"]=="upload":
            active=self.transfer["command"]
            blocked.add((active.get("project_id","default"),active.get("card_id")))
        for path,command in commands:
            if self.stopped.is_set():break
            key=(command.get("project_id","default"),command.get("card_id"))
            if key[1] and key in blocked and command.get("action")!="positions":continue
            try:
                if self.retry_at.get(str(path),0)>time.monotonic():
                    if key[1]:blocked.add(key)
                    continue
                if command.get("user_id")!=self.session.profile["user_id"] or command.get("base_url")!=self.session.base_url:
                    raise CloudError("Project command belongs to another account.")
                if key[0]!=self.selected and command.get("action") not in ("switch","refresh","create_project","create_team"):continue
                if command.get("action")=="asset":
                    if self.transfer or not self.ready or command.get("id") not in self.announced:
                        blocked.add(key);continue
                    client=self.transfer_client(key[0])
                    snapshot=self.snapshot;chain=self.chain
                    self.begin_transfer("upload",key[0],
                        lambda c=command,cl=client,s=snapshot,ch=chain:upload_asset(c,cl,self.root,s,ch,{}),
                        path=path,command=command)
                    blocked.add(key)
                else:
                    self.execute(command);self.acknowledge(path,command)
            except Exception as error:
                self.command_error(path,command,error)
                if key[1]:blocked.add(key)

        announced_at=time.monotonic();announcing_cards=set()
        for path,command in commands:
            if time.monotonic()-announced_at>.5:break
            if not path.exists():continue
            if command.get("action")=="asset":
                card=(command.get("project_id","default"),command.get("card_id"))
                if card in announcing_cards:continue
                announcing_cards.add(card)
            if (command.get("action")!="asset" or command.get("id") in self.announced or
                    command.get("project_id","default")!=self.selected or command.get("user_id")!=self.session.profile["user_id"] or
                    command.get("base_url")!=self.session.base_url or self.retry_at.get("announce:"+str(path),0)>time.monotonic()):continue
            try:
                kind=command.get("kind") or ("image" if Path(command["path"]).suffix.lower() in (".png",".jpg",".jpeg",".webp") else "splat" if Path(command["path"]).suffix.lower() in (".npz",".splat") else "mesh")
                self.client.announce(command["card_id"],kind,command.get("title","Asset"),command["id"])
                self.announced.add(command["id"])
            except CloudError as error:
                # A previous server may not have upload cards yet. File sync
                # remains compatible while the matching Cloud deploy rolls out.
                if error.status in (404,405,503) or error.code=="capability_unavailable":self.announced.add(command["id"])
                elif error.retryable:self.retry_at["announce:"+str(path)]=time.monotonic()+5
                else:self.command_error(path,command,error)
            except Exception:self.retry_at["announce:"+str(path)]=time.monotonic()+5

    def download_next(self):
        if not self.ready:return
        if self.retry_downloads.is_set():
            self.retry_downloads.clear();self.downloads.retry(self.selected)
        if self.downloads.tick(self.selected,self.snapshot.get('assets',[])):
            self.update_transfer_message();self.publish()

    def step(self):
        self.finish_transfer()
        self.process_commands()
        if self.changed.is_set():
            self.changed.clear()
            try:self.refresh()
            except CloudError as error:
                self.message=str(error);self.publish()
                if error.code=='project_access_changed' or error.status in (403,404):
                    try:self.set_project('default')
                    except CloudError:pass
                self.stopped.wait(1);self.changed.set()
            except Exception:
                self.message='Project synchronization was interrupted. Your local files are kept.';self.publish()
                self.stopped.wait(2);self.changed.set()
        self.download_next()
        if self.publish_pending or time.monotonic()-self.last_published>=5:
            self.publish(heartbeat=True)

    def run(self):
        self.changed.set()
        try:
            selected="default"
            try:selected=json.loads((self.root/"selected-project.json").read_text())["id"]
            except (OSError,ValueError,KeyError,TypeError):pass
            try:self.set_project(selected)
            except CloudError:self.set_project("default")
            while not self.stopped.is_set() and self.session.profile:
                try:self.step()
                except Exception:
                    self.message='Cloud sync hit a local error; reconnecting. Your local files are kept.'
                    self.publish();self.changed.set();self.stopped.wait(1)
                self.stopped.wait(0.2)
        finally:self.cancel_watch.set();self.downloads.close()

    def close(self):
        self.stopped.set();self.cancel_watch.set()
        self.downloads.close()
        if self.thread:self.thread.join(timeout=2)
