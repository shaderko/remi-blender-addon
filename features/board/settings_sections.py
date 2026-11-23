"""One set of RNA-backed settings for the overview and tool windows."""
from dataclasses import dataclass, field


SECTIONS = {
    "general": ("General", "Your account and generation setup."),
    "image": ("Image generation", "Defaults for the board's image composer."),
    "generate3d": ("Generate 3D", "Defaults for turning images into 3D sources."),
    "mesh": ("Create mesh", "Defaults for converting 3D sources into meshes."),
    "flow": ("Remi Flow", "Choose a preset or configure each processing stage."),
    "texture": ("Texture mesh", "Defaults for projecting appearance onto a mesh."),
    "lods": ("Create LODs", "Geometry reduction using the original texture maps."),
}


@dataclass
class Setting:
    kind: str
    label: str
    owner: object = None
    name: str = ""
    operator: str = ""
    kwargs: dict = field(default_factory=dict)
    enabled: bool = True

    @property
    def rna(self):
        return self.owner.bl_rna.properties[self.name]

    @property
    def value(self):
        return getattr(self.owner, self.name)

    def assign(self, value):
        setattr(self.owner, self.name, value)


class SettingsList:
    """Collect feature-owned automatic settings without duplicating their fields."""
    def __init__(self, items=None):
        self.items = [] if items is None else items
        self.enabled = True

    def prop(self, owner, name, **kwargs):
        self.items.append(Setting("property", kwargs.get("text") or owner.bl_rna.properties[name].name,
                                  owner, name, enabled=self.enabled))

    def label(self, *, text="", **kwargs):
        if text:
            self.items.append(Setting("note", text))

    def row(self, **kwargs):
        child = SettingsList(self.items)
        child.enabled = self.enabled
        return child

    column = box = row

    def separator(self, **kwargs):
        pass


def choices(setting, context):
    if setting.name == "preset":
        from ...app.flow import preset_items
        return [(item[0], item[1]) for item in preset_items(None, context)]
    return [(item.identifier, item.name) for item in setting.rna.enum_items if item.identifier]


def settings_items(context, section):
    import bpy

    items = []
    scene = context.scene
    generation = scene.remi_generation
    image = scene.remi_image
    cloud = generation.backend == "API"

    def heading(text): items.append(Setting("heading", text))
    def note(text): items.append(Setting("note", text))
    def prop(owner, name, label=None):
        items.append(Setting("property", label or owner.bl_rna.properties[name].name, owner, name))
    def action(label, operator): items.append(Setting("action", label, operator=operator))

    if section == "general":
        heading("Account")
        account = context.window_manager.remi_cloud
        if account.signed_in:
            note(account.email)
            action("Open Remi Cloud", "remi.cloud_account")
            action("Log out", "remi.cloud_logout")
        else:
            action("Sign in to Remi Cloud", "remi.cloud_account")
        heading("Generation")
        prop(generation, "use_cloud", "Use Remi Cloud")
        if image.provider == "CODEX":
            note("Images use the local Codex connection.")
            note("3D generation runs in Remi Cloud." if cloud else "3D generation runs on this computer.")
        else:
            note("Images and 3D generation run in Remi Cloud." if cloud else "Images and 3D generation run on this computer.")
        heading("Local tools")
        prop(generation, "python", "Python executable")
        note("Used by local generation, mesh conversion and texture tools.")
    elif section == "image":
        prop(image, "provider")
        codex = image.provider == "CODEX"
        if codex:
            from ...integrations.codex_images.client import session
            note("Uses ChatGPT locally. Generated images share with the selected project.")
            note("Experimental access; returned image size may differ from the request.")
            prop(context.preferences.system, "use_online_access", "Allow Online Access")
            if not bpy.app.online_access:
                note("Enable Allow Online Access before signing in to ChatGPT.")
            action("Continue with ChatGPT", "remi.codex_sign_in")
            if session.signed_in:
                note("ChatGPT connected for this Blender session.")
                action("Disconnect ChatGPT", "remi.codex_sign_out")
            prop(image, "codex_quality")
        else:
            prop(image, "model")
        for name in ("size", "count"):
            prop(image, name)
        if not codex:
            prop(image, "seed")
            if image.model == "base":
                for name in ("steps", "cfg_scale", "negative_prompt"):
                    prop(image, name)
        prop(image, "output")
        if not cloud and not codex:
            heading("Local model")
            for name in ("models", "memory_mode", "cache_minutes"):
                prop(image, name)
            action("Download image model", "remi.download_iris")
    elif section == "generate3d":
        for name in ("model", "gaussians", "seed", "output"):
            prop(generation, name)
        if generation.model == "triposplat":
            for name in ("triposplat_steps", "triposplat_guidance", "triposplat_shift"):
                prop(generation, name)
        if not cloud:
            heading("Local model")
            prop(generation, "models")
            prop(generation, "model_cache_minutes", "Keep model loaded (min)")
            action("Download 3D model", "remi.download_models")
    elif section == "mesh":
        prop(generation, "mesh_quality", "Quality")
        prop(generation, "mesh_output", "Output folder")
        note("Mesh conversion runs on this computer.")
    elif section == "texture":
        for name in ("texture_size", "match_distance", "layer_width", "normal_smoothing"):
            prop(scene.remi_texture, name)
    elif section == "lods":
        for name in ("lod_levels", "lod_ratio"):
            prop(scene.remi_settings, name)
        note("Reuses the original materials and texture maps.")
        note("The source stays as LOD0. Reduced copies start hidden.")
    elif section == "flow":
        from ...app.flow import automatic_actions, initialize_steps, selected_document
        flow = scene.remi_flow
        if not flow.steps:
            initialize_steps(flow)
        prop(flow, "preset", "Preset")
        if flow.preset == "CURRENT":
            for registered in automatic_actions():
                step = next(s for s in flow.steps if s.action_id == registered.id)
                heading(registered.action.name)
                prop(step, "enabled", "Include this stage")
                if step.enabled:
                    registered.feature.draw_automatic_settings(SettingsList(items), context, registered.action)
        else:
            try:
                document = selected_document(context)
                for index, name in enumerate(document["actions"], 1):
                    note(f"{index}. {name.replace('_', ' ').title()}")
                action("Customize this preset", "remi.edit_flow_preset")
            except (ValueError, OSError, KeyError) as error:
                note(str(error))
        action("Save as preset", "remi.save_flow_preset")
    return items


def settings_locked(context):
    wm = context.window_manager
    return any(getattr(getattr(wm, group, None), name, False) for group, name in (
        ("remi_image", "busy"), ("remi_generation", "busy"), ("remi_generation", "mesh_busy"),
        ("remi_texture", "busy"), ("remi_session", "busy"), ("remi_session", "automatic")))
