"""Durable local Cloud requests. Only Remi; no provider/payment credentials.

New submission is explicit. Watching never uploads or creates a job. After an
uncertain POST, explicit resume reuses the frozen bytes and original keys.
"""
import hashlib
from http.client import HTTPException
import json
import math
import os
from pathlib import Path
import re
import stat
import struct
import tempfile
import time
import zlib
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request
from uuid import uuid4

from .embedded import CloudError, api_url
from .models import CloudFile, Job, identifier, integer, digest
from .errors import ProtocolError
from .compression import ACCEPT_COMPRESSION, read_artifact

INPUT_LIMIT = 20 * 1024 * 1024
OUTPUT_LIMIT = 16 * 1024 * 1024


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode("utf-8")


def sha(body):
    return hashlib.sha256(body).hexdigest()


def sync_directory(directory):
    if os.name == "posix":
        fd=os.open(directory,os.O_RDONLY|getattr(os,"O_DIRECTORY",0))
        try: os.fsync(fd)
        finally: os.close(fd)


def read_file(path, limit, *, private=True):
    path=Path(path)
    if path.is_symlink(): raise CloudError("Cloud recovery files must not be symbolic links.")
    fd=os.open(path,os.O_RDONLY|getattr(os,"O_NOFOLLOW",0))
    with os.fdopen(fd,"rb") as source:
        info=os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
            raise CloudError("Cloud recovery file is empty, too large or not a regular file.")
        if private and os.name == "posix" and (info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise CloudError("Cloud recovery files must be private to your account.")
        body=source.read(limit+1)
        if len(body)>limit: raise CloudError("Cloud recovery file exceeds its size limit.")
        return body


def put_once(path, body):
    """fsync + atomic no-clobber publication. Never replace a different fact."""
    path=Path(path)
    fd,name=tempfile.mkstemp(prefix=".partial-",dir=path.parent)
    temporary=Path(name)
    try:
        with os.fdopen(fd,"wb") as target:
            target.write(body);target.flush();os.fsync(target.fileno())
        try: os.link(temporary,path)
        except FileExistsError:
            if read_file(path,max(len(body),1)) != body:
                raise CloudError("A different Cloud recovery file already exists. Nothing was overwritten.") from None
        sync_directory(path.parent)
    finally: temporary.unlink(missing_ok=True)


def put_json(path,value):
    body=canonical(value)
    put_once(path,canonical({"value":value,"sha256":sha(body)}))


def get_json(path):
    try:
        envelope=json.loads(read_file(path,262144))
        value=envelope["value"]
        if set(envelope)!={"value","sha256"} or sha(canonical(value)) != envelope["sha256"]:
            raise ValueError
        return value
    except (ValueError,TypeError,KeyError):
        raise CloudError("The saved Cloud request is corrupt. No request was sent.") from None


def cost_micros(value):
    if not isinstance(value,str) or len(value)>24 or not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,6})?",value):
        raise CloudError("Enter a positive EUR spending cap with at most six decimal places.")
    whole,_,fraction=value.partition(".")
    amount=int(whole)*1000000+int(fraction.ljust(6,"0") or "0")
    if not 0 < amount <= 9007199254740991:
        raise CloudError("The EUR spending cap is outside the API range.")
    return amount


def validate_input(body,content_type):
    valid={"image/png":body.startswith(b"\x89PNG\r\n\x1a\n"),
           "image/jpeg":body.startswith(b"\xff\xd8\xff"),
           "image/webp":body[:4]==b"RIFF" and body[8:12]==b"WEBP"}
    if not body or len(body)>INPUT_LIMIT or not valid.get(content_type):
        raise CloudError("Choose a PNG, JPEG or WebP image up to 20 MiB. Cloud fully validates it before compute.")


def checked_spec(operation,prompt,options,cap):
    if operation not in ("generate_image","generate") or not isinstance(options,dict):
        raise CloudError("Choose image or custom 3D Cloud generation.")
    allowed={"seed","width","height"} if operation=="generate_image" else {"seed","gaussianCount"}
    if set(options)-allowed or type(cap) is not int or not 0<cap<=9007199254740991:
        raise CloudError("Invalid Cloud generation settings or spending cap.")
    for key,value in options.items():
        if type(value) is not int or not (
                0<=value<=2147483647 if key=="seed" else
                value in (32768,65536,131072,262144) if key=="gaussianCount" else
                256<=value<=1024 and value%16==0):
            raise CloudError("Cloud generation options are outside the supported range.")
    if operation=="generate_image":
        if (not isinstance(prompt,str) or not prompt.strip() or len(prompt)>2000
                or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]",prompt)):
            raise CloudError("Enter an image prompt of 1–2000 characters.")
        return prompt.strip()
    if prompt is not None: raise CloudError("Custom 3D generation uses a selected image.")
    return None


class CloudRequest:
    def __init__(self,path,session):
        self.session=session
        path=Path(path).expanduser()
        if path.name!="request.json" or path.is_symlink() or path.parent.is_symlink():
            raise CloudError("Choose the saved Cloud request.json file.")
        self.directory=path.parent.resolve(strict=True)
        if os.name=="posix" and self.directory.stat().st_mode & 0o077:
            raise CloudError("The Cloud request directory must be private to your account.")
        self.path=self.directory/"request.json"
        self.intent=get_json(self.path)
        try:
            x=self.intent
            if (set(x)-{"project_id"}!={"version","id","base_url","user_id","operation","prompt","options","max_cost_micros","source"}
                    or x["version"]!=1 or self.directory.name!="remi-cloud-"+identifier(x["id"])
                    or api_url(x["base_url"])!=session.base_url
                    or not session.profile or x["user_id"]!=session.profile["user_id"]
                    or x.get("project_id")!=getattr(session,"project_id",None)):
                raise ValueError
            if checked_spec(x["operation"],x["prompt"],x["options"],x["max_cost_micros"])!=x["prompt"]:
                raise ValueError
            source=x["source"]
            if x["operation"]=="generate_image":
                if source is not None: raise ValueError
            else:
                if not isinstance(source,dict) or set(source)!={"filename","content_type","bytes","sha256"}:
                    raise ValueError
                if {"image/png":"input.png","image/jpeg":"input.jpg","image/webp":"input.webp"}.get(source["content_type"])!=source["filename"]:
                    raise ValueError
                integer(source["bytes"],minimum=1);digest(source["sha256"])
                if source["bytes"]>INPUT_LIMIT: raise ValueError
                self.input_bytes()  # Check frozen bytes even when the original file moved.
        except (KeyError,TypeError,ValueError,ProtocolError):
            raise CloudError("Cloud recovery requires the original API, signed-in account and intact request. Nothing was submitted.") from None

    @classmethod
    def create(cls,session,output,operation,*,prompt=None,image=None,options=None,max_cost_eur=""):
        if not session.profile: raise CloudError("Sign in to Remi Cloud first.")
        options=dict(options or {})
        cap=cost_micros(max_cost_eur)
        prompt=checked_spec(operation,prompt,options,cap)
        source=None;body=None
        if operation=="generate":
            if not isinstance(image,str) or not image: raise CloudError("Choose an image for Cloud 3D generation.")
            suffix=Path(image).suffix.lower()
            content_type={".png":"image/png",".jpg":"image/jpeg",".jpeg":"image/jpeg",".webp":"image/webp"}.get(suffix)
            body=read_file(image,INPUT_LIMIT,private=False)
            validate_input(body,content_type)
            source={"filename":{"image/png":"input.png","image/jpeg":"input.jpg","image/webp":"input.webp"}[content_type],"content_type":content_type,"bytes":len(body),"sha256":sha(body)}
        if not isinstance(output,str) or not output.strip(): raise CloudError("Choose a local folder for Cloud results and recovery records.")
        root=Path(output).expanduser().resolve(strict=True)
        if not root.is_dir(): raise CloudError("Choose an existing local output folder.")
        request_id=str(uuid4());directory=root/("remi-cloud-"+request_id)
        directory.mkdir(mode=0o700);sync_directory(root)
        if source: put_once(directory/source["filename"],body)
        put_json(directory/"request.json",{"version":1,"id":request_id,"base_url":session.base_url,"user_id":session.profile["user_id"],**({"project_id":session.project_id} if getattr(session,"project_id",None) else {}),"operation":operation,"prompt":prompt,"options":options,"max_cost_micros":cap,"source":source})
        return cls(directory/"request.json",session)

    def input_bytes(self):
        source=self.intent["source"]
        body=read_file(self.directory/source["filename"],INPUT_LIMIT)
        if len(body)!=source["bytes"] or sha(body)!=source["sha256"]:
            raise CloudError("The frozen Cloud input changed. Nothing was submitted.")
        validate_input(body,source["content_type"])
        return body

    def key(self,stage):
        return "remi-blender:"+self.intent["id"]+":"+stage

    def payload(self):
        x=self.intent;ids=[]
        if x["source"]:
            file=CloudFile.parse(get_json(self.directory/"upload.json"))
            source=x["source"]
            if (file.kind!="image" or file.sha256!=source["sha256"] or file.bytes!=source["bytes"] or file.content_type!=source["content_type"]):
                raise CloudError("The saved Cloud upload does not match the frozen input.")
            ids=[file.id]
        return {"operation":x["operation"],"inputMode":"single","inputFileIds":ids,
                **({"prompt":x["prompt"]} if x["prompt"] is not None else {}),
                "budget":{"currency":"EUR","maxCostMicros":x["max_cost_micros"]},"options":x["options"]}

    def checked_job(self,value,expected_id=None):
        job=Job.parse(value);payload=self.payload()
        if (expected_id is not None and job.id!=expected_id or job.operation!=payload["operation"]
                or job.input_mode!="single" or list(job.input_file_ids)!=payload["inputFileIds"]
                or len(job.output_file_ids)!=(1 if job.status=="succeeded" else 0)
                or job.billing is not None and int(job.billing.max_cost_micros)>self.intent["max_cost_micros"]):
            raise CloudError("Cloud returned a job that does not match the saved request.")
        return job

    def submit(self):
        if (self.directory/"job.json").exists(): return self.poll()
        caps=self.session.capabilities()
        if not caps["enabled"] or not caps["operations"][self.intent["operation"]] or not caps["inputModes"]["single"]:
            raise CloudError("Cloud generation is not enabled on this server. No new work was submitted.")
        if self.intent["source"] and not (self.directory/"upload.json").exists():
            source=self.intent["source"]
            value=self.session.api("POST","/files?"+urlencode({"filename":source["filename"]}),body=self.input_bytes(),headers={"Content-Type":source["content_type"],"Idempotency-Key":self.key("upload")})
            file=CloudFile.parse(value.get("file"))
            if file.kind!="image" or file.filename!=source["filename"] or file.bytes!=source["bytes"] or file.sha256!=source["sha256"] or file.content_type!=source["content_type"]:
                raise CloudError("Cloud upload verification did not match the original image.")
            created=datetime.fromisoformat(file.created_at.replace("Z","+00:00"))
            if created.tzinfo is None: raise CloudError("Cloud returned an invalid upload timestamp.")
            put_json(self.directory/"upload.json",{"id":file.id,"kind":file.kind,"filename":source["filename"],
                "bytes":file.bytes,"sha256":file.sha256,"contentType":file.content_type,
                "createdAt":created.astimezone(timezone.utc).isoformat()})
        payload=self.payload();put_json(self.directory/"payload.json",payload)
        value=self.session.api("POST","/jobs",body=canonical(payload),headers={"Idempotency-Key":self.key("job"),"Content-Type":"application/json"})
        job=self.checked_job(value.get("job"))
        put_json(self.directory/"job.json",{"id":job.id,"payload_sha256":sha(canonical(payload))})
        return self.finish(job)

    def poll(self):
        job_id=self.receipt_id()
        return self.finish(self.checked_job(self.session.api("GET","/jobs/"+job_id).get("job"),job_id))

    def receipt_id(self):
        receipt=get_json(self.directory/"job.json")
        if (set(receipt)!={"id","payload_sha256"} or receipt["payload_sha256"]!=sha(canonical(self.payload()))
                or get_json(self.directory/"payload.json")!=self.payload()):
            raise CloudError("The saved Cloud job no longer matches its exact submitted request.")
        return identifier(receipt["id"])

    def watch(self, *, timeout=1800, cancelled=lambda: False):
        """Receive pushed state; completion still requires verified result bytes."""
        job_id=self.receipt_id()
        for value in self.session.watch_job(job_id,timeout=timeout,cancelled=cancelled):
            job=self.checked_job(value,job_id)
            yield self.finish(job)
            if job.terminal: return

    def finish(self,job):
        value={"request_path":str(self.path),"operation":job.operation,"job_id":job.id,"progress":job.progress,
               "billing":None if job.billing is None else {"status":job.billing.status,"max_cost_micros":job.billing.max_cost_micros,"actual_cost_micros":job.billing.actual_cost_micros,"currency":"EUR"}}
        if job.status=="succeeded":
            path=self.download(job.output_file_ids[0])
            message="Cloud result ready. Billing details unavailable." if job.billing is None else "Cloud result ready. Billing pending." if job.billing.status=="pending" else "Cloud result ready."
            return {**value,"state":"job_ready","output_path":str(path),"message":message}
        if job.terminal:
            return {**value,"state":"job_failed","message":"Cloud generation ended without a usable result. No charge." if job.billing is not None and job.billing.status=="no_charge" else "Cloud generation ended without a usable result. Review billing in Remi Cloud."}
        return {**value,"state":"job_running","message":"Cloud generation is running."}

    def download(self,file_id):
        ticket=self.session.api("POST","/files/"+identifier(file_id)+"/download?compression="+ACCEPT_COMPRESSION)
        size=integer(ticket.get("bytes"),minimum=1);checksum=digest(ticket.get("sha256"))
        content_type="image/png" if self.intent["operation"]=="generate_image" else "application/octet-stream"
        if size>OUTPUT_LIMIT or ticket.get("contentType")!=content_type:
            raise CloudError("Cloud result has an unsupported format or exceeds 16 MiB.")
        path=self.directory/("generation.png" if content_type=="image/png" else "generation.splat")
        fact={"id":file_id,"bytes":size,"sha256":checksum,"content_type":content_type,"filename":path.name}
        put_json(self.directory/"artifact.json",fact)
        if path.exists() or path.is_symlink():
            body=read_file(path,OUTPUT_LIMIT)
        else:
            url=ticket.get("url")
            try:
                parts=urlsplit(url);base=urlsplit(self.session.base_url)
                local=parts.hostname in ("localhost","127.0.0.1","::1") and base.scheme=="http" and base.hostname in ("localhost","127.0.0.1","::1")
                if (not isinstance(url,str) or len(url)>16384 or not parts.hostname or parts.username or parts.password or parts.fragment
                        or re.search(r"[\x00-\x20\x7f]",url) or (parts.scheme!="https" and not (parts.scheme=="http" and local))): raise ValueError
                _=parts.port
            except (ValueError,TypeError,AttributeError): raise CloudError("Cloud returned an unsafe download location.") from None
            try:
                headers={"Accept-Encoding":"identity","User-Agent":"remi-cloud-embedded/0.1"}
                if ticket.get("transport")==ACCEPT_COMPRESSION:
                    url=self.session.base_url+"/files/"+identifier(file_id)+"/content"
                    headers.update({"Authorization":"Bearer "+self.session._token,
                                    "Origin":self.session._origin,
                                    "X-Remi-Accept-Compression":ACCEPT_COMPRESSION})
                    if self.session.project_id:headers["X-Remi-Project"]=self.session.project_id
                request=Request(url,headers=headers,method="GET")
                with self.session._http.open(request,timeout=30) as response:
                    if response.status!=200 or response.headers.get("Content-Encoding","identity").lower() not in ("","identity"):
                        raise CloudError("Cloud result download was not verified. Resume downloading this request.")
                    try:body=read_artifact(response,size)
                    except (ValueError,zlib.error):
                        raise CloudError("Cloud result compression could not be verified. Resume downloading this request.") from None
            except HTTPError as error:
                error.close()
                raise CloudError("Cloud result download was interrupted. Resume this request, not a new generation.") from None
            except (URLError,OSError,TimeoutError,HTTPException):
                raise CloudError("Cloud result download was interrupted. Resume this request, not a new generation.") from None
        if len(body)!=size or sha(body)!=checksum:
            raise CloudError("Cloud result size or SHA-256 verification failed. Nothing was imported.")
        validate_output(body,self.intent)
        put_once(path,body)
        put_json(self.directory/"complete.json",fact)
        return path


def validate_output(body,intent):
    if intent["operation"]=="generate_image":
        if len(body)<33 or body[:8]!=b"\x89PNG\r\n\x1a\n" or body[12:16]!=b"IHDR":
            raise CloudError("Cloud image does not have a valid PNG envelope.")
        width,height=struct.unpack_from(">II",body,16)
        if (width,height)!=(intent["options"].get("width",1024),intent["options"].get("height",1024)):
            raise CloudError("Cloud image dimensions do not match the saved request.")
    else:
        if len(body)<16 or body[:8]!=b"REMIGAUS": raise CloudError("Cloud 3D result is not native Remi Gaussian data.")
        version,count=struct.unpack_from("<II",body,8)
        if version!=1 or count!=intent["options"].get("gaussianCount",65536) or len(body)!=16+56*count:
            raise CloudError("Cloud 3D count or length does not match the saved request.")
        for record in struct.iter_unpack("<14f",body[16:]):
            norm=sum(value*value for value in record[6:10])
            if not all(math.isfinite(value) for value in record) or min(record[3:6])<=0 or not 0<=record[10]<=1 or abs(math.sqrt(norm)-1)>0.00101:
                raise CloudError("Cloud Gaussian attributes failed validation.")
