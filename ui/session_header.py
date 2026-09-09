"""Current mesh identity, statistics, and recovery status."""


def draw_session_header(layout, state):
    header = layout.row(align=True)
    header.label(text="REMI MODE", icon="LOCKED")
    header.label(text=state.object_name)

    stats = layout.row(align=True)
    stats.label(text=f"{state.current_vertices:,} verts · {state.current_faces:,} faces")
    if state.source_faces:
        ratio = state.current_faces / state.source_faces
        stats.label(text=f"{ratio:.0%}")

    status = layout.box()
    status.label(text=f"Current · {state.current_step}", icon="INFO")
    status.label(text=state.status or "Ready")
    if state.checkpoint_megabytes:
        status.label(text=f"Recovery on disk · {state.checkpoint_megabytes:.1f} MB")
