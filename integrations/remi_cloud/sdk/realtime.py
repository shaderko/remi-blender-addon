"""Authenticated multiplexed control and replayable events. No mutation retries.

The WebSocket implementation is websockets, not a home-grown wire protocol.
Imports stay lazy so Blender can import the SDK before starting its worker.
"""
import json
import logging
import math
import queue
import re
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4

from .errors import APIError, AuthenticationError, NetworkError, ProtocolError
from .models import identifier

MAX_MESSAGE = 262144


def socket_url(base_url):
    p = urlsplit(base_url)
    local = p.hostname in ("127.0.0.1", "localhost", "::1")
    if (not p.hostname or p.username or p.password or p.query or p.fragment
            or not p.path.endswith("/api/v1") or len(base_url) > 2048
            or p.scheme != "https" and not (p.scheme == "http" and local)):
        raise ValueError("Use the HTTPS Remi API URL; HTTP is allowed only on loopback.")
    return p._replace(scheme="wss" if p.scheme == "https" else "ws", path=p.path+"/realtime").geturl()


class RealtimeSession:
    def __init__(self, base_url, token, *, ssl_context=None, timeout=30, connector=None, project_id=None):
        self.url = socket_url(base_url)
        self.project_id = identifier(project_id).lower() if project_id is not None else None
        if not math.isfinite(timeout) or timeout <= 0: raise ValueError("Positive realtime timeout required.")
        self.token, self.ssl_context, self.timeout = token, ssl_context, timeout
        self.connector = connector
        self._lock = threading.RLock()
        self._socket = None
        self._closed = False
        self._requests = {}
        self._subscriptions = {}

    def _connect(self):
        with self._lock:
            if self._closed: raise NetworkError("The live connection was closed.")
            if self._socket is not None: return self._socket
            try:
                if self.connector is None:
                    from ._vendor.websockets.sync.client import connect
                else: connect = self.connector
                token = self.token()
                if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,256}", token):
                    raise AuthenticationError("Sign in to Remi Cloud.", code="authentication_required", status=401)
                logger = logging.getLogger("remi.websocket.private")
                logger.disabled = True
                kwargs = dict(additional_headers={"Authorization": "Bearer "+token, **({"X-Remi-Project": self.project_id} if self.project_id else {})},
                    proxy=None, compression=None, open_timeout=min(self.timeout, 20),
                    close_timeout=2, ping_interval=20, ping_timeout=20,
                    max_size=MAX_MESSAGE, max_queue=16, logger=logger)
                if self.url.startswith("wss:"): kwargs["ssl"] = self.ssl_context
                ws = connect(self.url, **kwargs)
                value = self._decode(ws.recv(timeout=min(self.timeout, 20)))
                if value != {"v": 1, "type": "ready", "protocol": "remi.realtime.v1"}:
                    ws.close(); raise ProtocolError("Unsupported Remi realtime protocol.")
            except (APIError, ProtocolError): raise
            except Exception as error:
                status = getattr(getattr(error, "response", None), "status_code", None)
                if status in (401,403,404):
                    error_type = AuthenticationError if status == 401 else APIError
                    raise error_type("Sign in to Remi Cloud again or check access.", code="project_access_changed" if self.project_id and status in (403,404) else "invalid_credentials", status=status) from None
                raise NetworkError("Could not open the live Remi connection. Check the server supports WebSockets; no request was retried.") from None
            self._socket = ws
            threading.Thread(target=self._receive, args=(ws,), daemon=True, name="remi-realtime").start()
            return ws

    @staticmethod
    def _decode(raw):
        if not isinstance(raw, str) or len(raw.encode()) > MAX_MESSAGE:
            raise ProtocolError("Invalid realtime message.")
        try: value = json.loads(raw)
        except ValueError: raise ProtocolError("Invalid realtime message.") from None
        if not isinstance(value, dict) or value.get("v") != 1:
            raise ProtocolError("Invalid realtime message version.")
        return value

    def _fail(self, ws, error):
        with self._lock:
            if self._socket is not ws: return
            self._socket = None
            for target in self._requests.values():
                try: target.put_nowait(error)
                except queue.Full: pass
            for target in self._subscriptions.values():
                while not target.empty():
                    try: target.get_nowait()
                    except queue.Empty: break
                target.put_nowait(error)
        try: ws.close()
        except Exception: pass

    def _receive(self, ws):
        try:
            while True:
                value = self._decode(ws.recv())
                with self._lock:
                    if value.get("type") in ("response", "subscribed"):
                        target = self._requests.get(value.get("id")) or self._subscriptions.get(value.get("id"))
                    elif value.get("type") in ("job.updated", "board.changed"):
                        target = self._subscriptions.get(value.get("subscription"))
                    else: raise ProtocolError("Unexpected realtime message.")
                    if target is not None:
                        try: target.put_nowait(value)
                        except queue.Full: raise NetworkError("Live updates exceeded the client buffer; reconnect to resume.")
        except Exception as error:
            code = getattr(getattr(error, "rcvd", None), "code", None)
            failure = (AuthenticationError("Sign in to Remi Cloud again.",code="invalid_credentials",status=401) if code == 4401
                       else APIError("Project access was removed. Choose another project.",code="project_access_changed",status=403) if code == 4403
                       else error if isinstance(error, ProtocolError)
                       else NetworkError("Live connection interrupted. Resume by saved ID; no work was resubmitted."))
            self._fail(ws, failure)

    def _send(self, ws, value):
        raw = json.dumps({"v": 1, **value}, ensure_ascii=False, separators=(",", ":"))
        if len(raw.encode()) > 65536: raise ProtocolError("Realtime command exceeds its limit.")
        try:
            with self._lock:
                if self._socket is not ws: raise NetworkError("Live connection interrupted.")
                ws.send(raw)
        except Exception:
            self._fail(ws, NetworkError("Realtime acceptance is unknown. Resume the same saved request; do not create a replacement."))
            raise NetworkError("Realtime acceptance is unknown. Resume the same saved request; do not create a replacement.") from None

    def request(self, method, path, *, body=None, key=None):
        ws = self._connect(); request_id = uuid4().hex; target = queue.Queue(maxsize=1)
        with self._lock: self._requests[request_id] = target
        try:
            self._send(ws, {"type": "request", "id": request_id, "method": method, "path": path,
                **({"body": body} if body is not None else {}), **({"key": key} if key is not None else {})})
            try: result = target.get(timeout=self.timeout)
            except queue.Empty:
                raise NetworkError("Realtime response timed out. Resume the same saved request; no work was resubmitted.") from None
            if isinstance(result, Exception): raise result
            if result.get("type") != "response" or type(result.get("status")) is not int or not isinstance(result.get("value"), dict):
                raise ProtocolError("Invalid realtime response.")
            return result["status"], result["value"]
        finally:
            with self._lock: self._requests.pop(request_id, None)

    def watch_job(self, job_id, *, timeout=1800, cancelled=lambda: False):
        yield from self._watch(identifier(job_id), timeout=timeout, cancelled=cancelled)

    def watch_board(self, *, timeout=86400, cancelled=lambda: False):
        yield from self._watch(None, timeout=timeout, cancelled=cancelled)

    def _watch(self, job_id, *, timeout, cancelled):
        if not math.isfinite(timeout) or timeout <= 0: raise ValueError("Positive watch timeout required.")
        deadline = time.monotonic()+timeout; cursor = None; delay = 0.25
        while not cancelled():
            remaining = deadline-time.monotonic()
            if remaining <= 0: raise TimeoutError("Live job wait timed out.")
            sub_id = uuid4().hex; target = queue.Queue(maxsize=256); ws = None
            try:
                ws = self._connect()
                with self._lock: self._subscriptions[sub_id] = target
                self._send(ws, {"type": "subscribe", "id": sub_id, **({"jobId": job_id} if job_id else {"topic":"board"}),
                    **({"cursor": cursor} if cursor is not None else {})})
                while not cancelled():
                    remaining = deadline-time.monotonic()
                    if remaining <= 0: raise TimeoutError("Live job wait timed out.")
                    try: value = target.get(timeout=min(remaining, 0.5))
                    except queue.Empty: continue  # local cancellation check, no network query
                    if isinstance(value, Exception): raise value
                    if value.get("type") == "response":
                        error = value.get("value", {}).get("error", {})
                        raise APIError("Live subscription was rejected.", code=error.get("code", "subscription_rejected"), status=value.get("status",503))
                    sequence = value.get("cursor")
                    if not isinstance(sequence,str) or not re.fullmatch(r"0|[1-9][0-9]{0,18}",sequence):
                        raise ProtocolError("Invalid live event cursor.")
                    if value["type"] in ("job.updated","board.changed") and cursor is not None and int(sequence) <= int(cursor): continue
                    if job_id:
                        job=value.get("job")
                        if not isinstance(job,dict) or job.get("id")!=job_id:raise ProtocolError("Live update belongs to different work.")
                    else:
                        if value.get("type")=="subscribed":
                            if value.get("topic")!="board":raise ProtocolError("Invalid board subscription.")
                        elif value.get("type")!="board.changed" or type(value.get("jobsChanged")) is not bool:
                            raise ProtocolError("Invalid board event.")
                        job=value
                    cursor=sequence;delay=0.25
                    yield job
                    if job_id and job.get("status") in ("succeeded","failed","canceled"):return
            except NetworkError:
                if self._closed: raise
                remaining = deadline-time.monotonic()
                if remaining <= 0: raise TimeoutError("Live job wait timed out.") from None
                threading.Event().wait(min(delay, remaining)); delay = min(5, delay*2)
            finally:
                with self._lock: self._subscriptions.pop(sub_id, None)
                if ws is not None and self._socket is ws:
                    try: self.request_unsubscribe(ws, sub_id)
                    except NetworkError: pass

    def request_unsubscribe(self, ws, sub_id):
        self._send(ws, {"type": "unsubscribe", "id": uuid4().hex, "subscription": sub_id})

    def close(self):
        self._closed = True
        ws = self._socket
        if ws is not None: self._fail(ws, NetworkError("The live connection was closed."))
