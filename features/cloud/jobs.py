"""Blender controls for the authenticated Cloud request client.

The worker owns network I/O. Blender only requests bounded steps and imports
verified local files on an explicit operator action. No scene data holds secrets.
"""
from pathlib import Path

import bpy
from bpy.types import Operator

from ...integrations.remi_cloud.client import runtime
from ...integrations.remi_cloud.protocol import CloudError


def available(context):
    state=context.window_manager.remi_cloud
    return state.signed_in and runtime.status().get("signed_in") is True and not runtime.pending and not runtime.following


class Remi_OT_CloudGenerate(Operator):
    bl_idname="remi.cloud_generate"
    bl_label="Generate on Cloud"
    bl_description="Submit selected inputs to Remi Cloud using this account and a required EUR spending cap"

    @classmethod
    def poll(cls,context):
        state=getattr(context.window_manager,"remi_cloud",None)
        return bool(state and available(context) and state.generation_enabled and state.max_cost_eur.strip()
                    and (not state.request_path or state.request_path==runtime.completed_request))

    def execute(self,context):
        state=context.window_manager.remi_cloud
        try:
            operation=state.operation
            options={"seed":state.seed}
            if operation=="generate_image": options.update(width=state.width,height=state.height)
            else: options["gaussianCount"]=int(state.gaussians)
            runtime.command("generate",state.base_url,operation=operation,
                prompt=state.prompt if operation=="generate_image" else None,
                image=bpy.path.abspath(state.image) if operation=="generate" else None,
                output=bpy.path.abspath(state.output),options=options,max_cost_eur=state.max_cost_eur)
            state.output_path=""
            state.result_operation=""
            runtime.following=True
        except (CloudError,RuntimeError,OSError,ValueError) as error:
            self.report({"ERROR"},str(error));return {"CANCELLED"}
        return {"FINISHED"}


class Remi_OT_CloudResume(Operator):
    bl_idname="remi.cloud_resume"
    bl_label="Resume Saved Request"
    bl_description="Use this request's original input, spending cap and request keys; an interrupted submission may be sent again with those same keys"

    @classmethod
    def poll(cls,context):
        state=getattr(context.window_manager,"remi_cloud",None)
        return bool(state and available(context) and state.request_path)

    def execute(self,context):
        state=context.window_manager.remi_cloud
        try:
            runtime.command("resume",state.base_url,request_path=bpy.path.abspath(state.request_path))
            state.output_path="";state.result_operation=""
            runtime.following=True
        except (CloudError,RuntimeError,OSError) as error:
            self.report({"ERROR"},str(error));return {"CANCELLED"}
        return {"FINISHED"}


class Remi_OT_CloudStopWaiting(Operator):
    bl_idname="remi.cloud_stop_waiting"
    bl_label="Stop Waiting"
    bl_description="Stop local waiting only. Cloud execution and its spending reservation remain; resume the saved request later"

    def execute(self,context):
        runtime.following=False
        if runtime.pending: runtime.stop()
        runtime.paused=True
        context.window_manager.remi_cloud.message="Stopped waiting locally. Cloud work may continue; resume the saved request."
        return {"FINISHED"}


class Remi_OT_CloudImport(Operator):
    bl_idname="remi.cloud_import"
    bl_label="Import Cloud Result"
    bl_description="Recheck the downloaded file and load the image or native custom-model Gaussians"
    bl_options={"REGISTER","UNDO"}

    @classmethod
    def poll(cls,context):
        state=getattr(context.window_manager,"remi_cloud",None)
        if not state or not state.output_path or runtime.pending: return False
        from ..generation.operators import can_generate
        return can_generate(context) and not context.window_manager.remi_generation.busy

    def execute(self,context):
        from ...integrations.remi_cloud.jobs import get_json,read_file,sha,validate_output,OUTPUT_LIMIT
        state=context.window_manager.remi_cloud
        try:
            # Use the worker-owned path and fixed filename, not a remote filename
            # or an editable scene property. Offline import after restart is manual.
            current=runtime.status()
            if current.get("state")!="job_ready" or current.get("output_path")!=state.output_path:
                raise CloudError("Resume the saved request to verify its result before importing.")
            directory=Path(state.request_path).resolve().parent
            intent=get_json(directory/"request.json")
            fact=get_json(directory/"complete.json")
            expected="generation.png" if intent["operation"]=="generate_image" else "generation.splat"
            path=directory/expected
            if path!=Path(state.output_path) or fact["filename"]!=expected:
                raise CloudError("The saved Cloud output identity changed.")
            body=read_file(path,OUTPUT_LIMIT)
            if len(body)!=fact["bytes"] or sha(body)!=fact["sha256"]:
                raise CloudError("The downloaded Cloud output changed. Nothing was imported.")
            validate_output(body,intent)
            if intent["operation"]=="generate":
                from ...blender.gaussian_splats import import_splats
                obj=import_splats(context,path)
                obj["remi_cloud_job_id"]=current["job_id"]
                obj["remi_source_image"]=str(directory/intent["source"]["filename"])
                message=f"Imported {obj['remi_splat_count']:,} custom-model Gaussians."
            else:
                image=bpy.data.images.load(str(path),check_existing=False)
                try:
                    if tuple(image.size)!=(intent["options"].get("width",1024),intent["options"].get("height",1024)) or len(image.pixels)!=image.size[0]*image.size[1]*image.channels:
                        raise CloudError("The Cloud PNG failed Blender decoding.")
                except Exception:
                    bpy.data.images.remove(image);raise
                context.window_manager.remi_image.selected=str(path)
                state.image=str(path)
                message="Cloud image loaded. File kept in the Cloud output folder."
            state.message=message
            self.report({"INFO"},message)
        except (CloudError,OSError,ValueError,KeyError,RuntimeError) as error:
            self.report({"ERROR"},str(error));return {"CANCELLED"}
        return {"FINISHED"}


CLASSES=(Remi_OT_CloudGenerate,Remi_OT_CloudResume,Remi_OT_CloudStopWaiting,Remi_OT_CloudImport)
