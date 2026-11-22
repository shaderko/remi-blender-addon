"""Shared project assets and board edits for first-party embedded clients.

All file paths are local cache paths chosen here, never names supplied by the
server. Credentials stay in the CloudSession; no network runs inside Blender.
"""
import hashlib
import json
import math
from pathlib import Path
import re
from urllib.parse import urlencode
from urllib.request import Request

from .embedded import CloudError
from .models import CloudFile, Job, identifier

CARD_ID=re.compile(r"[A-Za-z0-9_-]{1,100}")

def card_id(value):
    if not isinstance(value,str) or not CARD_ID.fullmatch(value) or value in {"__proto__","prototype","constructor"}:raise CloudError("Invalid board asset identity.")
    return value


def positions(cards):
    if not isinstance(cards,dict) or len(cards)>5000:raise CloudError("Invalid saved board positions.")
    out={}
    for key,p in cards.items():
        card_id(key)
        if p is None:out[key]=None;continue
        if not isinstance(p,dict) or set(p)-{"x","y","parent"}:raise CloudError("Invalid saved board positions.")
        if any(type(p.get(k)) not in (int,float) or not math.isfinite(p[k]) or abs(p[k])>1e6 for k in ("x","y")):raise CloudError("Invalid saved board positions.")
        out[key]={"x":p["x"],"y":p["y"],**({"parent":card_id(p["parent"])} if "parent" in p else {})}
    return out


class WorkspaceClient:
    def __init__(self,session,cache):
        self.session=session
        self.cache=Path(cache).expanduser().resolve()
        self.cache.mkdir(parents=True,exist_ok=True)
        self._verified={}
        self._outputs={}

    def catalog(self):
        # Project discovery must work even after access to the selected project
        # was removed. Its authorization is the real signed-in session.
        status,value=self.session._request("GET","/projects",auth=True,api=True,headers={"X-Remi-Project":""})
        if status!=200 or not isinstance(value,dict) or not isinstance(value.get("projects"),list) or not isinstance(value.get("teams"),list):
            raise CloudError("Projects could not be loaded. Sign in again or retry.",status=status,retryable=status>=500)
        for p in value["projects"]:
            if not isinstance(p,dict) or not isinstance(p.get("name"),str) or len(p["name"])>80 or p.get("role") not in ("owner","member"):raise CloudError("Invalid project catalog.")
            if p.get("id")!="default":identifier(p.get("id"))
            if p.get("teamId") is not None:identifier(p["teamId"])
        return value

    def board(self):
        value=self.session.api("GET","/board")
        positions(value.get("cards"))
        if not isinstance(value.get("inputs"),list) or not isinstance(value.get("deletedInputIds"),list):raise CloudError("Invalid board response.")
        for asset in value["inputs"]:
            card_id(asset.get("id"));CloudFile.parse(asset.get("file"))
            if not isinstance(asset.get("title"),str) or len(asset["title"])>255:raise CloudError("Invalid asset name.")
        for key in value["deletedInputIds"]:card_id(key)
        return value

    def patch(self,cards,*,only_missing=False):
        return self.session.api("PATCH","/board/cards",payload={"cards":positions(cards),"onlyMissing":bool(only_missing)})

    def upload(self,path,key):
        path=Path(path).resolve(strict=True)
        content_type={".png":"image/png",".jpg":"image/jpeg",".jpeg":"image/jpeg",".webp":"image/webp",".glb":"model/gltf-binary",".ply":"application/ply",".splat":"application/octet-stream"}.get(path.suffix.lower())
        limit=(20 if content_type and content_type.startswith("image/") else 16 if path.suffix.lower()==".splat" else 100)*1024*1024
        if not content_type or not path.is_file() or not 0<path.stat().st_size<=limit:raise CloudError("Choose a supported image, mesh or Gaussian file within its size limit.")
        body=path.read_bytes()
        if len(body)>limit:raise CloudError("The source grew past its upload limit.")
        checksum=hashlib.sha256(body).hexdigest()
        value=self.session.api("POST","/files?"+urlencode({"filename":"asset"+path.suffix.lower()}),body=body,headers={"Content-Type":content_type,"Idempotency-Key":key})
        file=value.get("file");parsed=CloudFile.parse(file)
        if parsed.sha256!=checksum or parsed.bytes!=len(body):raise CloudError("The upload receipt did not match the source file.")
        return file

    def attach(self,key,file_id,title,*,expected_file_id=None):
        body={"fileId":identifier(file_id),"title":str(title).strip()[:255] or "Asset"}
        if expected_file_id:body["expectedFileId"]=identifier(expected_file_id)
        value=self.session.api("PUT","/board/inputs/"+card_id(key),payload=body)
        asset=value.get("input")
        if not isinstance(asset,dict) or asset.get("id")!=key or CloudFile.parse(asset.get("file")).id!=file_id:raise CloudError("The saved asset receipt did not match the upload.")
        return asset

    def remove(self,key,*,job=False):
        return self.session.api("DELETE",("/jobs/"+identifier(key)) if job else ("/board/inputs/"+card_id(key)))

    def file(self,metadata):
        file=CloudFile.parse(metadata)
        limit=(20 if file.kind=="image" else 16 if file.kind=="splat" else 100)*1024*1024
        if file.bytes>limit:raise CloudError("The shared asset exceeds its file limit.")
        suffix={"image/png":".png","image/jpeg":".jpg","image/webp":".webp","model/gltf-binary":".glb","application/ply":".ply","application/octet-stream":".splat"}.get(file.content_type)
        if suffix is None:raise CloudError("Unsupported shared asset format.")
        path=self.cache/(file.id+"-"+file.sha256+suffix)
        if path.is_file():
            stamp=(path.stat().st_mtime_ns,path.stat().st_size)
            if self._verified.get(str(path))==stamp:return str(path)
            if stamp[1]==file.bytes and hashlib.sha256(path.read_bytes()).hexdigest()==file.sha256:
                self._verified[str(path)]=stamp;return str(path)
        headers={"Authorization":"Bearer "+self.session._token,"Accept-Encoding":"identity",
                 "User-Agent":"remi-cloud-embedded/0.1","Origin":self.session._origin}
        if self.session.project_id:headers["X-Remi-Project"]=self.session.project_id
        try:
            request=Request(self.session.base_url+"/files/"+file.id+"/content",headers=headers)
            with self.session._http.open(request,timeout=30) as response:
                if response.status!=200 or response.headers.get("Content-Encoding","identity").lower() not in ("","identity"):raise ValueError
                body=response.read(file.bytes+1)
            if len(body)!=file.bytes or hashlib.sha256(body).hexdigest()!=file.sha256:raise ValueError
            temporary=path.with_suffix(suffix+".partial")
            temporary.write_bytes(body);temporary.replace(path)
            self._verified[str(path)]=(path.stat().st_mtime_ns,path.stat().st_size)
        except Exception:
            raise CloudError("The shared asset download could not be verified. Retry syncing.",code="download_interrupted",retryable=True) from None
        return str(path)

    def snapshot(self):
        board=self.board();jobs=[];cursor=None;seen=set()
        while True:
            page=self.session.api("GET","/jobs"+("?"+urlencode({"cursor":cursor}) if cursor else ""))
            for row in page.get("jobs",[]):Job.parse(row);jobs.append(row)
            cursor=page.get("nextCursor")
            if cursor is None:break
            identifier(cursor)
            if cursor in seen or len(jobs)>5000:raise CloudError("Cloud history exceeds the project limit.")
            seen.add(cursor)
        inputs={a["id"]:a for a in board["inputs"]}
        assets=[]
        for row in jobs:
            override=inputs.pop(row["id"],None)
            metadata=override["file"] if override else None
            if metadata is None and row["status"]=="succeeded":
                output=identifier(row["outputFileIds"][0])
                metadata=self._outputs.get(output)
                if metadata is None:
                    ticket=self.session.api("POST","/files/"+output+"/download")
                    kind="image" if row["operation"]=="generate_image" else "splat" if row["operation"]=="generate" else "mesh"
                    metadata={k:ticket[k] for k in ("filename","bytes","sha256","contentType")}
                    metadata.update({"id":output,"kind":kind,"createdAt":row["createdAt"]})
                    CloudFile.parse(metadata)
                    self._outputs[output]=metadata
            assets.append({"id":row["id"],"title":override["title"] if override else ("Generated image" if row["operation"]=="generate_image" else "3D source" if row["operation"]=="generate" else "Mesh"),"job":row,"file":metadata})
        assets.extend({"id":a["id"],"title":a["title"],"file":a["file"]} for a in inputs.values())
        for asset in assets:
            if asset["file"]:asset["path"]=self.file(asset["file"])
        return {**board,"assets":assets,"jobs":jobs}
