"""Remi Cloud browser approval UI. Credentials never enter Blender data blocks."""
import webbrowser
from pathlib import Path

import bpy
from bpy.props import BoolProperty, PointerProperty, StringProperty, EnumProperty, IntProperty, FloatProperty
from bpy.types import Operator, Panel, PropertyGroup
from bpy.app.handlers import persistent

from ...integrations.remi_cloud.client import runtime
from ...integrations.remi_cloud.protocol import DEFAULT_API, CloudError
from .jobs import CLASSES as JOB_CLASSES
from . import projects,board_sync
from .sign_in import Remi_OT_CloudSignIn, close_dialog

needs_reset = False


class RemiCloudState(PropertyGroup):
    project:EnumProperty(name="Project",items=projects.project_items,update=projects.changed,options={"SKIP_SAVE"})
    base_url: StringProperty(name="Cloud API",default=DEFAULT_API,options={"SKIP_SAVE"})
    busy: BoolProperty(default=False,options={"SKIP_SAVE"})
    signed_in: BoolProperty(default=False,options={"SKIP_SAVE"})
    email: StringProperty(options={"SKIP_SAVE"})
    message: StringProperty(default="Sign in to Remi Cloud.",options={"SKIP_SAVE"})
    user_code: StringProperty(options={"SKIP_SAVE"})
    verification_url: StringProperty(options={"SKIP_SAVE"})
    operation: EnumProperty(name="Generate",items=[("generate_image","Image","Iris-3B Turbo"),("generate","3D","IDOL Cloud native Gaussians")],default="generate_image",options={"SKIP_SAVE"})
    prompt: StringProperty(name="Prompt",maxlen=2000,options={"SKIP_SAVE"})
    image: StringProperty(name="Input Image",subtype="FILE_PATH",options={"SKIP_SAVE"})
    seed: IntProperty(name="Seed",default=0,min=0,max=2147483647,options={"SKIP_SAVE"})
    width: IntProperty(name="Width",default=1024,min=256,max=1024,step=16,options={"SKIP_SAVE"})
    height: IntProperty(name="Height",default=1024,min=256,max=1024,step=16,options={"SKIP_SAVE"})
    gaussians: EnumProperty(name="Gaussians",items=[(str(n),f"{n:,}","Native Gaussian count") for n in (32768,65536,131072,262144)],default="65536",options={"SKIP_SAVE"})
    max_cost_eur: StringProperty(name="Maximum EUR",default="1.00",description="Required spending cap for this request; the server sets the actual reservation and price",options={"SKIP_SAVE"})
    output: StringProperty(name="Output Folder",subtype="DIR_PATH",options={"SKIP_SAVE"})
    request_path: StringProperty(name="Saved Request",subtype="FILE_PATH",options={"SKIP_SAVE"})
    output_path: StringProperty(options={"SKIP_SAVE"})
    result_operation: StringProperty(options={"SKIP_SAVE"})
    job_id: StringProperty(options={"SKIP_SAVE"})
    billing_message: StringProperty(options={"SKIP_SAVE"})
    progress: FloatProperty(default=0,min=0,max=1,options={"SKIP_SAVE"})
    generation_enabled: BoolProperty(default=False,options={"SKIP_SAVE"})


def tick():
    global needs_reset
    if needs_reset:
        if not reset_connection(): return 0.25
        needs_reset=False
    state = getattr(bpy.context.window_manager,"remi_cloud",None)
    if state is None: return None
    board_sync.configure_cache()
    if not bpy.app.background and bpy.app.online_access:
        try:
            runtime.restore_once(state.base_url)
        except (CloudError, RuntimeError, OSError) as error:
            state.message = str(error)
    value = runtime.status()
    state.busy = runtime.pending
    state.signed_in = value.get("signed_in",value.get("state")=="signed_in")
    state.message = value.get("message","")
    state.email = value.get("email","") if state.signed_in else ""
    state.user_code = value.get("user_code","")
    state.verification_url = value.get("verification_url","")
    caps=value.get("capabilities",{})
    state.generation_enabled=bool(caps.get("enabled") and caps.get("operations",{}).get(state.operation))
    if "request_path" in value: state.request_path=value["request_path"]
    if "job_id" in value: state.job_id=value["job_id"]
    state.output_path=value.get("output_path","")
    state.result_operation=value.get("operation","")
    state.progress=value.get("progress",0)
    billing=value.get("billing")
    state.billing_message="Billing details unavailable." if value.get("job_id") and billing is None else "Billing pending; spending hold retained." if billing and billing.get("status")=="pending" else ""
    if billing and billing.get("status")=="no_charge": state.billing_message="No customer charge."
    if billing and billing.get("status")=="settled":
        whole,fraction=divmod(int(billing["actual_cost_micros"]),1000000)
        amount=f"{whole}.{fraction:06d}".rstrip("0").rstrip(".")
        state.billing_message="Final usage cost: EUR "+amount
    if state.signed_in:
        projects.tick(bpy.context)
    board_sync.tick(bpy.context)
    if bpy.context.screen:
        for area in bpy.context.screen.areas:
            if area.type in {"VIEW_3D", "IMAGE_EDITOR"}: area.tag_redraw()
    return 0.25 if runtime.pending or runtime.retired else 1.0


class Remi_OT_CloudLogin(Operator):
    bl_idname = "remi.cloud_login"
    bl_label = "Sign in to Remi Cloud"
    bl_description = "Approve this Blender device in your browser using the same Remi Cloud account"

    def execute(self,context):
        board_sync.configure_cache()
        state = context.window_manager.remi_cloud
        state.user_code = ""
        state.verification_url = ""
        try: runtime.command("login",context.window_manager.remi_cloud.base_url)
        except (CloudError,RuntimeError,OSError) as error:
            self.report({"ERROR"},str(error))
            return {"CANCELLED"}
        context.window_manager.remi_cloud.busy = True
        return {"FINISHED"}


class Remi_OT_CloudRefresh(Operator):
    bl_idname = "remi.cloud_refresh"
    bl_label = "Check Cloud Connection"

    def execute(self,context):
        try: runtime.command("status",context.window_manager.remi_cloud.base_url)
        except (CloudError,RuntimeError,OSError) as error:
            self.report({"ERROR"},str(error)); return {"CANCELLED"}
        context.window_manager.remi_cloud.busy = True
        return {"FINISHED"}


class Remi_OT_CloudLogout(Operator):
    bl_idname = "remi.cloud_logout"
    bl_label = "Sign Out"
    bl_description = "Revoke this Cloud session; no Blender files or local models are affected"

    def execute(self,context):
        try:
            if runtime.pending: runtime.stop()
            runtime.command("logout",context.window_manager.remi_cloud.base_url)
        except (CloudError,RuntimeError,OSError) as error:
            self.report({"ERROR"},str(error)); return {"CANCELLED"}
        return {"FINISHED"}


class Remi_OT_CloudOpenApproval(Operator):
    bl_idname = "remi.cloud_open_approval"
    bl_label = "Open Sign-in Page"

    def execute(self,context):
        url = context.window_manager.remi_cloud.verification_url
        if not url: return {"CANCELLED"}
        # Use the worker-validated URL, not an editable property from a .blend.
        value = runtime.status()
        if value.get("verification_url") != url: return {"CANCELLED"}
        webbrowser.open(url,new=2)
        return {"FINISHED"}


class Remi_PT_Cloud(Panel):
    bl_label = "Remi Cloud"
    bl_idname = "Remi_PT_Cloud"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Remi"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_account(self,context):
        import textwrap
        state = context.window_manager.remi_cloud
        layout = self.layout
        failed = runtime.status().get("state") == "error"
        if failed:
            for line in textwrap.wrap(state.message,width=32): layout.label(text=line)
        if state.user_code:
            layout.label(text=f"Code: {state.user_code[:4]}-{state.user_code[4:]}")
            layout.operator("remi.cloud_open_approval",icon="URL")
        elif state.busy and not state.signed_in and not runtime.following:
            layout.label(text="Connecting…",icon="TIME")
        row = layout.row(align=True)
        generation_active = context.window_manager.remi_generation.busy or context.window_manager.remi_image.busy
        row.enabled = not generation_active
        if state.busy:
            if runtime.following: row.operator("remi.cloud_stop_waiting")
            else: row.operator("remi.cloud_logout",text="Sign Out" if state.signed_in else "Cancel Sign-in")
        elif runtime.following:
            row.operator("remi.cloud_stop_waiting")
        elif state.signed_in:
            row.label(text=state.email,icon="USER")
            row.operator("remi.cloud_logout",text="",icon="QUIT")
        else: row.operator("remi.cloud_login",text="Sign In",icon="USER")
        if failed and state.signed_in:
            layout.operator("remi.cloud_refresh",text="Retry",icon="FILE_REFRESH")

    def draw(self,context):
        Remi_PT_Cloud.draw_account(self,context)
        state = context.window_manager.remi_cloud
        generation_active = context.window_manager.remi_generation.busy or context.window_manager.remi_image.busy
        if not generation_active and state.request_path and state.request_path != runtime.completed_request:
            self.layout.operator("remi.cloud_resume", text="Resume Generation", icon="FILE_REFRESH")


class Remi_PT_BoardCloud(Panel):
    bl_label = "Remi Account"
    bl_idname = "Remi_PT_BoardCloud"
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "HEADER"
    bl_ui_units_x = 14

    def draw(self, context):
        Remi_PT_Cloud.draw(self, context)


class Remi_OT_CloudAccount(Operator):
    bl_idname = "remi.cloud_account"
    bl_label = "Remi Account"
    bl_description = "Sign in or open your Remi Cloud account in your browser"

    def invoke(self, context, event):
        state = context.window_manager.remi_cloud
        if not state.signed_in:
            result = bpy.ops.remi.cloud_sign_in("INVOKE_DEFAULT")
            return {"CANCELLED"} if result == {"CANCELLED"} else {"FINISHED"}
        return self.execute(context)

    def execute(self, context):
        webbrowser.open("https://remi3d.app/app/profile", new=2)
        return {"FINISHED"}


CLASSES = (RemiCloudState,Remi_OT_CloudLogin,Remi_OT_CloudRefresh,Remi_OT_CloudLogout,Remi_OT_CloudOpenApproval,Remi_OT_CloudSignIn,Remi_OT_CloudAccount,*JOB_CLASSES,Remi_PT_Cloud,Remi_PT_BoardCloud,*projects.CLASSES)


@persistent
def before_load(_unused):
    close_dialog()
    runtime.stop()
    reset_connection()


def reset_connection():
    # Deleting an RNA pointer alone leaves its ID-property values in memory.
    # addon_utils also forbids data access while enabling an installed extension.
    managers=getattr(bpy.data,"window_managers",None)
    if managers is None: return False
    for manager in managers:
        state=getattr(manager,"remi_cloud",None)
        if state is None: continue
        for name in ("busy","signed_in","email","user_code","verification_url","output_path","result_operation","job_id","billing_message","progress","generation_enabled","message","project"):
            state.property_unset(name)
    return True


def register():
    global needs_reset
    for cls in CLASSES: bpy.utils.register_class(cls)
    bpy.types.WindowManager.remi_cloud = PointerProperty(type=RemiCloudState)
    needs_reset=not reset_connection()
    bpy.app.timers.register(tick,first_interval=0.25,persistent=True)
    if before_load not in bpy.app.handlers.load_pre: bpy.app.handlers.load_pre.append(before_load)


def unregister():
    close_dialog()
    if bpy.app.timers.is_registered(tick): bpy.app.timers.unregister(tick)
    if before_load in bpy.app.handlers.load_pre: bpy.app.handlers.load_pre.remove(before_load)
    runtime.cleanup()
    reset_connection()
    if hasattr(bpy.types.WindowManager,"remi_cloud"): del bpy.types.WindowManager.remi_cloud
    for cls in reversed(CLASSES): bpy.utils.unregister_class(cls)
