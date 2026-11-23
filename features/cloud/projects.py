"""Native project/team controls. Operators enqueue local IPC, never network calls."""
import bpy
from bpy.props import BoolProperty, StringProperty
from bpy.types import Operator

from ...integrations.remi_cloud.client import runtime

_ITEMS=[("default","My workspace","Private Cloud workspace"),("LOCAL","Local board","Local assets on this computer")]
_updating=False
_requested=None


def project_items(_self,_context):
    value=runtime.workspace_status();new=[("default","My workspace","Private Cloud workspace"),("LOCAL","Local board","Local assets on this computer")]
    for p in value.get("catalog",{}).get("projects",[]):
        if p["id"]=="default":continue
        title=(p.get("teamName","")+" / " if p.get("teamId") else "")+p["name"]
        new.append((p["id"],title,"Team project" if p.get("teamId") else "Private project"))
    if new!=_ITEMS:_ITEMS[:]=new
    return _ITEMS


def busy(context):
    from ..board import canvas
    board=canvas.BOARD
    from ..board.editing import editing_scene
    if editing_scene(context) is not None:return True
    return bool(context.window_manager.remi_image.busy or runtime.pending or (board and (getattr(board,"running",None) or getattr(board,"queue",None) or getattr(getattr(board,"runner",None),"job",None) is not None)))


def changed(self,context):
    global _updating,_requested
    if _updating:return
    value=runtime.workspace_status();old=value.get("project_id","default")
    if busy(context):
        _updating=True
        try:self.project=old
        finally:_updating=False
        self.message="Finish the current generation before switching projects.";return
    try:
        if self.project!="LOCAL":
            cid=runtime.workspace_command("switch",self.base_url,project_id=self.project)
            _requested=(cid,self.project)
        if hasattr(context.scene,"remi_generation"):
            context.scene.remi_generation.backend="LOCAL" if self.project=="LOCAL" else "API"
    except RuntimeError as error:self.message=str(error)


def selected_team(context):
    value=runtime.workspace_status()
    return next((p.get("teamId") for p in value.get("catalog",{}).get("projects",[]) if p["id"]==context.window_manager.remi_cloud.project),None)


class Remi_OT_CloudProject(Operator):
    bl_idname="remi.cloud_project"
    bl_label="Create Project"
    name:StringProperty(name="Project name",maxlen=80,options={"SKIP_SAVE"})
    team:BoolProperty(name="Share with current team",default=False,options={"SKIP_SAVE"})
    def invoke(self,context,_event):return context.window_manager.invoke_props_dialog(self,width=340)
    def draw(self,context):
        self.layout.prop(self,"name")
        if selected_team(context):self.layout.prop(self,"team")
    def execute(self,context):
        if not self.name.strip() or busy(context):return {"CANCELLED"}
        runtime.workspace_command("create_project",context.window_manager.remi_cloud.base_url,name=self.name.strip(),team_id=selected_team(context) if self.team else None)
        return {"FINISHED"}


class Remi_OT_CloudTeam(Operator):
    bl_idname="remi.cloud_team"
    bl_label="Create Team"
    name:StringProperty(name="Team name",maxlen=80,options={"SKIP_SAVE"})
    def invoke(self,context,_event):return context.window_manager.invoke_props_dialog(self,width=340)
    def execute(self,context):
        if not self.name.strip() or busy(context):return {"CANCELLED"}
        runtime.workspace_command("create_team",context.window_manager.remi_cloud.base_url,name=self.name.strip())
        return {"FINISHED"}


class Remi_OT_CloudInvite(Operator):
    bl_idname="remi.cloud_invite"
    bl_label="Create Team Invitation"
    email:StringProperty(name="Email",maxlen=254,options={"SKIP_SAVE"})
    def invoke(self,context,_event):return context.window_manager.invoke_props_dialog(self,width=360)
    def execute(self,context):
        team=selected_team(context)
        if not team:return {"CANCELLED"}
        runtime.workspace_command("invite",context.window_manager.remi_cloud.base_url,project_id=context.window_manager.remi_cloud.project,team_id=team,email=self.email.strip())
        self.report({"INFO"},"Creating the invitation. Its link will be copied when ready.")
        return {"FINISHED"}


class Remi_OT_CloudProjects(Operator):
    bl_idname="remi.cloud_projects"
    bl_label="Projects & Teams"
    def invoke(self,context,_event):
        team=selected_team(context)
        if team:runtime.workspace_command("team",context.window_manager.remi_cloud.base_url,project_id=context.window_manager.remi_cloud.project,team_id=team)
        return context.window_manager.invoke_popup(self,width=420)
    def execute(self,_context):return {"FINISHED"}
    def draw(self,context):
        layout=self.layout;state=context.window_manager.remi_cloud
        layout.prop(state,"project",text="Project")
        row=layout.row(align=True);row.enabled=not busy(context)
        row.operator("remi.cloud_project",icon="ADD");row.operator("remi.cloud_team",icon="COMMUNITY")
        value=runtime.workspace_status();layout.label(text=value.get("message","Connecting…"))
        from ..board import canvas
        if canvas.BOARD is not None:
            sync=getattr(canvas.BOARD,"cloud_sync",None)
            if sync and sync.notice:layout.label(text=sync.notice,icon="ERROR")
        team_id=selected_team(context)
        if team_id:
            team=next((t for t in value.get("catalog",{}).get("teams",[]) if t["id"]==team_id),None)
            if team:
                layout.label(text=f"{team['storageLimitBytes']/1073741824:g} GiB shared team storage")
                details=next((r["team"] for r in reversed(list(value.get("uploads",{}).values())) if r.get("team",{}).get("id")==team_id),None)
                if details:
                    layout.label(text=f"{details['storedBytes']/1048576:.1f} MiB used · {details['fileCount']} files")
                    for member in details["members"]:
                        row=layout.row(align=True);row.label(text=member["email"])
                        if team["role"]=="owner" and member["role"]!="owner":
                            op=row.operator("remi.cloud_team_remove",text="",icon="X")
                            op.identifier=member["userId"];op.invitation=False
                    if team["role"]=="owner":
                        for invitation in details["invitations"]:
                            row=layout.row(align=True);row.label(text=invitation["email"]+" (invited)")
                            op=row.operator("remi.cloud_team_remove",text="",icon="X")
                            op.identifier=invitation["id"];op.invitation=True
                if team["role"]=="owner":layout.operator("remi.cloud_invite",icon="USER")


_seen_links=set()
def tick(context):
    global _updating,_requested
    value=runtime.workspace_status()
    state=context.window_manager.remi_cloud
    for key,receipt in value.get("uploads",{}).items():
        if receipt.get("invitation") and key not in _seen_links:
            context.window_manager.clipboard=receipt["invitation"];_seen_links.add(key)
            state.message="Team invitation link copied. Share it with the invited account."
    if _requested:
        cid,wanted=_requested
        if value.get("project_id")==wanted or cid in value.get("errors",{}):_requested=None
        else:return
    project_items(state,context)
    desired=value.get("project_id","default")
    if value.get("ready") and state.project!="LOCAL" and state.project!=desired:
        # Do not roll back an explicit switch while its queued command connects.
        if not busy(context):
            _updating=True
            try:state.project=desired
            finally:_updating=False


class Remi_OT_CloudTeamRemove(Operator):
    bl_idname="remi.cloud_team_remove"
    bl_label="Remove Team Access"
    identifier:StringProperty(options={"SKIP_SAVE"})
    invitation:BoolProperty(options={"SKIP_SAVE"})
    def invoke(self,context,event):return context.window_manager.invoke_confirm(self,event)
    def execute(self,context):
        team=selected_team(context)
        if not team:return {"CANCELLED"}
        runtime.workspace_command("revoke_invite" if self.invitation else "remove_member",context.window_manager.remi_cloud.base_url,
            project_id=context.window_manager.remi_cloud.project,team_id=team,
            **({"invitation_id":self.identifier} if self.invitation else {"user_id":self.identifier}))
        return {"FINISHED"}


CLASSES=(Remi_OT_CloudProject,Remi_OT_CloudTeam,Remi_OT_CloudInvite,Remi_OT_CloudProjects,Remi_OT_CloudTeamRemove)
