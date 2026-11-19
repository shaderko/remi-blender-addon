"""Isolated stdlib network process. Never writes a session/device secret to disk."""
import json
from pathlib import Path
import signal
import sys

# Isolated Python (-I) does not import sibling files automatically.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from protocol import CloudError, CloudSession
from jobs import CloudRequest
from contract.errors import ProtocolError

stopping = False


def stop(_signal, _frame):
    global stopping
    stopping = True
    raise SystemExit


def main(directory):
    session = None
    root = Path(directory)
    request_id = 0
    request_path = None
    caps = None
    def publish(value):
        # This file may contain public profile/code metadata, never a bearer,
        # secret device_code, raw HTTP body or exception traceback.
        value = {**({"signed_in":True,**session.profile} if session is not None and session.profile else {"signed_in":False}),
                 **({"request_path":request_path} if request_path else {}),
                 **({"capabilities":caps} if caps else {}),**value,"request_id":request_id}
        allowed = {key:value[key] for key in ("request_id","state","message","user_code","verification_url","email","user_id","capabilities","signed_in","request_path","operation","job_id","progress","billing","output_path") if key in value}
        temporary = root / "status.partial.json"
        temporary.write_text(json.dumps(allowed),encoding="utf-8")
        temporary.replace(root / "status.json")
    try:
        publish({"state":"signed_out","message":"Sign in to Remi Cloud."})
        for line in sys.stdin:
            if stopping: break
            try:
                if len(line)>16384: raise CloudError("Cloud command exceeds its limit.")
                command = json.loads(line)
                request_id = command.get("request_id",0)
                action = command.get("action")
                if action == "close": break
                if action == "login":
                    if session is not None and session.base_url != command["base_url"]:
                        if not session.logout(): raise CloudError("Previous session revocation was not confirmed. Review your Remi Cloud sessions.")
                        session = None
                    session = session or CloudSession(command["base_url"])
                    profile = session.login(publish,lambda:stopping)
                    caps = session.capabilities()
                    publish({"state":"signed_in",**profile,"capabilities":caps,
                             "message":"Cloud connected." if caps["enabled"] else "Signed in. Cloud generation is not enabled on this server yet."})
                elif action == "status":
                    if session is None: raise CloudError("Sign in to Remi Cloud first.")
                    profile = session.status()
                    caps = session.capabilities()
                    publish({"state":"signed_in",**profile,"capabilities":caps,"message":"Cloud connected." if caps["enabled"] else "Cloud generation is not enabled on this server yet."})
                elif action == "logout":
                    revoked = session.logout() if session is not None else True
                    session = None
                    publish({"state":"signed_out","message":"Signed out." if revoked else "Local login cleared; server revocation was not confirmed. Review your Remi Cloud sessions."})
                elif action in ("generate","resume","poll"):
                    if session is None or session.base_url != command["base_url"]:
                        raise CloudError("Sign in to the original Remi Cloud server first.")
                    session.status()
                    if action == "generate":
                        caps = session.capabilities()
                        operation=command.get("operation")
                        if not caps["enabled"] or not caps["operations"].get(operation,False):
                            raise CloudError("Cloud generation is not enabled on this server. No new work was submitted.")
                        work=CloudRequest.create(session,command.get("output"),operation,
                            prompt=command.get("prompt"),image=command.get("image"),options=command.get("options"),max_cost_eur=command.get("max_cost_eur",""))
                    else:
                        work=CloudRequest(command["request_path"],session)
                    request_path=str(work.path)
                    publish({"state":"working","message":"Checking the saved Cloud request."})
                    result=work.poll() if action=="poll" else work.submit()
                    publish(result)
                else: raise CloudError("Unsupported Cloud command.")
            except CloudError as error:
                publish({"state":"error","message":str(error)})
            except (OSError,ValueError,KeyError,TypeError,ProtocolError):
                publish({"state":"error","message":"The Cloud request failed. No result was imported."})
    finally:
        if session is not None: session.logout()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM,stop)
    main(sys.argv[1])
