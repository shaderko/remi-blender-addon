"""Test-only adapter: real WebSockets over existing loopback HTTP fixtures.

Uses the bundled upstream WebSocket implementation, never a second wire codec.
Commands delegate to fixture HTTP routes; result-state changes push snapshots.
"""
import http.client
import json
import logging
import threading

from integrations.remi_cloud.sdk._vendor.websockets.server import ServerProtocol
from integrations.remi_cloud.sdk._vendor.websockets.sync.server import ServerConnection


def websocket(handler):
    if handler.path != '/api/v1/realtime' or handler.headers.get('Upgrade', '').lower() != 'websocket':
        return False
    handler.close_connection = True
    logger = logging.getLogger('remi.fixture.websocket'); logger.disabled = True
    protocol = ServerProtocol(max_size=65536, logger=logger)
    raw = (handler.requestline+'\r\n'+''.join(k+': '+v+'\r\n' for k,v in handler.headers.items())+'\r\n').encode()
    protocol.receive_data(raw)
    events = protocol.events_received()
    connection = ServerConnection(handler.connection, protocol, ping_interval=None, close_timeout=1)
    for event in events: connection.process_event(event)
    server = handler.server
    headers = {'Authorization': handler.headers.get('Authorization', ''), 'Content-Type': 'application/json', 'Origin': server.base}
    lock = getattr(server, '_live_lock', None)
    if lock is None:
        server._live_lock = lock = threading.RLock()
        server._live_subscriptions = {}
        server._live_sequence = 0

    def request(method, path, body=None, key=None):
        client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        try:
            client.request(method, '/api/v1'+path, body=None if body is None else json.dumps(body,separators=(',',':')).encode(),
                           headers={**headers, **({'Idempotency-Key': key} if key else {})})
            response = client.getresponse()
            return response.status, json.loads(response.read())
        finally: client.close()

    def send(value): connection.send(json.dumps({'v': 1, **value}))
    def snapshot(sub, job_id, first=False):
        status, value = request('GET', '/jobs/'+job_id)
        if status != 200: return send({'type':'response','id':sub,'status':status,'value':value})
        with lock:
            server._live_sequence += 1
            send({'type': 'subscribed' if first else 'job.updated',
                  'id' if first else 'subscription': sub, 'cursor': str(server._live_sequence), 'job':value['job']})
    try:
        connection.handshake()
        send({'type':'ready','protocol':'remi.realtime.v1'})
        for raw in connection:
            message = json.loads(raw); identity = message['id']
            if message['type'] == 'request':
                status,value=request(message['method'],message['path'],message.get('body'),message.get('key'))
                send({'type':'response','id':identity,'status':status,'value':value})
            elif message['type'] == 'subscribe':
                with lock: server._live_subscriptions[(connection, identity)] = lambda s=identity,j=message['jobId']: snapshot(s,j)
                snapshot(identity,message['jobId'],True)
            elif message['type'] == 'unsubscribe':
                with lock: server._live_subscriptions.pop((connection,message['subscription']),None)
                send({'type':'response','id':identity,'status':200,'value':{}})
    except Exception:
        connection.close(1012)
    finally:
        with lock:
            for key in list(server._live_subscriptions):
                if key[0] is connection:server._live_subscriptions.pop(key,None)
        connection.close()
    return True


def changed(server):
    lock = getattr(server,'_live_lock',None)
    if lock is None:return
    with lock: callbacks=list(server._live_subscriptions.values())
    for callback in callbacks:
        try:callback()
        except Exception:pass
