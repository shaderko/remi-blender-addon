"""Lightweight 2D texture-transfer feedback. Never load/render Gaussian data here."""

import math


MINT = (0.30, 0.94, 0.78)
PALETTE = (MINT, (0.38, 0.69, 1.0), (1.0, 0.65, 0.30), (0.78, 0.56, 1.0))


def _blend():
    # BLF text drawing can leave blending disabled. Every effect pass restores
    # alpha explicitly, otherwise a subtle highlight becomes an opaque panel.
    import gpu
    gpu.state.blend_set("ALPHA")


def update(board, now):
    """Only celebrate completed work, and stop animating failed/cancelled jobs."""
    changed = False
    for identifier, effect in list(board.texture_effects.items()):
        asset = board.catalog.assets.get(identifier)
        if asset is None or asset.hidden:
            board.texture_effects.pop(identifier, None)
            changed = True
            continue
        if asset.state not in {"QUEUED", "RUNNING"}:
            if effect["finished"] is None:
                effect["finished"] = now
                changed = True
            duration = 1.15 if asset.state == "READY" else 0.35
            if now - effect["finished"] > duration:
                board.texture_effects.pop(identifier, None)
                changed = True
    return changed


def _frame(rect, color=MINT, opacity=1.0, glow=True):
    from .canvas import draw_rects
    _blend()
    x, y, w, h = rect
    layers = ((9, 1, 0.05), (5, 2, 0.10), (1, 1.4, 0.8)) if glow else ((0, 1.4, 0.8),)
    for spread, thickness, alpha in layers:
        xx, yy, ww, hh = x-spread, y-spread, w+2*spread, h+2*spread
        draw_rects(((xx, yy, ww, thickness), (xx, yy+hh-thickness, ww, thickness),
                    (xx, yy, thickness, hh), (xx+ww-thickness, yy, thickness, hh)),
                   (*color, alpha*opacity))


def _arc(cx, cy, radius, angle, amount, opacity=1.0):
    from .canvas import flat_shader, batch_for_shader
    _blend()
    vertices = []
    for index in range(48):
        a, b = angle+amount*index/48, angle+amount*(index+1)/48
        points = [(cx+math.cos(t)*r, cy+math.sin(t)*r)
                  for t, r in ((a, radius-1), (a, radius+1), (b, radius+1), (b, radius-1))]
        vertices.extend((points[0], points[1], points[2], points[0], points[2], points[3]))
    shader = flat_shader()
    shader.bind()
    shader.uniform_float("color", (*MINT, opacity))
    batch_for_shader(shader, "TRIS", {"pos": vertices}).draw(shader)


def _thumbnail(board, identifier, rect):
    from .canvas import draw_image
    cached = board.texture(identifier)
    if cached is None:
        return
    texture, iw, ih, premultiplied = cached
    x, y, w, h = rect
    scale = min(w/iw, h/ih)
    ww, hh = iw*scale, ih*scale
    draw_image(texture, x+(w-ww)/2, y+(h-hh)/2, ww, hh, premultiplied)


def draw_drop(board, now):
    from .canvas import draw_rect, draw_rects, draw_text, text_width
    drop = board.texture_drop
    if drop is None:
        return
    _blend()
    rect = board.card_rect(drop["target"])
    x, y, w, h = rect
    pulse = 0.7+0.3*math.sin(now*5)
    draw_rect(x, y, w, h, (*MINT, 0.035+0.025*pulse))
    _frame(rect, opacity=pulse)
    corners = []
    length = min(22, w*0.15)
    for xx, yy, sx, sy in ((x, y, 1, 1), (x+w, y, -1, 1),
                            (x, y+h, 1, -1), (x+w, y+h, -1, -1)):
        corners += [(min(xx, xx+sx*length), yy-1, length, 2),
                    (xx-1, min(yy, yy+sy*length), 2, length)]
    draw_rects(corners, (*MINT, 0.9))
    label = "Release to texture"
    label_w = text_width(label, 12)+28
    lx, ly = x+(w-label_w)/2, y+h+12
    draw_rect(lx, ly, label_w, 30, (0.055, 0.12, 0.115, 0.98))
    draw_text(label, lx+14, ly+10, 12, (*MINT, 1))
    draw_text("Ctrl: move only", x, y-32, 10, (0.65, 0.76, 0.73, 1))
    # Lift a small cached thumbnail under the cursor; don't cover the mesh with
    # a full-size dragged card and never decode an image in this draw callback.
    mx, my = drop["cursor"]
    gx, gy, size = mx+18, my-22, 60
    _blend()
    draw_rect(gx-4, gy-4, size+8, size+8, (0.045, 0.055, 0.065, 0.95))
    _thumbnail(board, drop["source"], (gx, gy, size, size))
    _frame((gx, gy, size, size), opacity=0.7, glow=False)


def draw_working_card(board, asset, rect, now):
    from .canvas import draw_rect, draw_rects, draw_text, clip_text
    _blend()
    x, y, w, h = rect
    draw_rect(x, y, w, h, (0.065, 0.085, 0.095, 1))
    _thumbnail(board, asset.parent, rect)
    draw_rect(x, y, w, h, (0.025, 0.05, 0.065, 0.70))
    # A restrained holographic raster and travelling light band over the mesh.
    rows = [(x+1, y+h*i/12, w-2, 0.6) for i in range(1, 12)]
    draw_rects(rows, (*MINT, 0.055))
    scan = (now*0.22) % 1
    band = []
    for index in range(12):
        yy = y+scan*h-index*2.5
        if y+30 < yy < y+h-26:
            band.append((x+2, yy, w-4, 2.0))
    draw_rects(band, (*MINT, 0.055))
    if y+30 < y+scan*h < y+h-26:
        draw_rect(x+2, y+scan*h, w-4, 1.5, (*MINT, 0.65))
    cx, cy = x+w/2, y+h/2+4
    radius = max(12, min(23, w*0.12))
    _arc(cx, cy, radius, -now*2.2, math.tau*0.72, 0.85)
    _arc(cx, cy, radius+6, now*1.1, math.tau*0.25, 0.3)
    draw_text(clip_text("APPEARANCE TRANSFER", 9, w-22), x+11, y+h-20, 9, (*MINT, 0.95))
    label = "Queued · appearance transfer" if asset.state == "QUEUED" else asset.status
    draw_text(clip_text(label, 11, w-22), x+11, y+14, 11, (0.78, 0.88, 0.89, 1))
    draw_rect(x, y, w, 2, (0.17, 0.23, 0.25, 1))
    draw_rect(x, y, w*max(0, min(1, asset.progress)), 2, (*MINT, 1))
    _frame(rect, opacity=0.65)


def _point(start, end, t):
    bend = max(30, abs(end[0]-start[0])*0.5)
    s = 1-t
    return (s*s*s*start[0]+3*s*s*t*(start[0]+bend)+3*s*t*t*(end[0]-bend)+t*t*t*end[0],
            (s*s*s+3*s*s*t)*start[1]+(3*s*t*t+t*t*t)*end[1])


def draw_transfers(board, now):
    from .canvas import draw_curve, draw_rects
    if not board.texture_effects:
        return
    _blend()
    particles = [[] for _ in PALETTE]
    for identifier, effect in list(board.texture_effects.items())[-6:]:
        if identifier not in board.positions:
            continue
        asset = board.catalog.assets[identifier]
        x, y, w, h = board.card_rect(identifier)
        if x+w < 0 or x > board.region[0] or y+h < 0 or y > board.region[1]:
            continue
        finished = effect["finished"]
        if finished is not None:
            age = now-finished
            if asset.state != "READY":
                _frame((x, y, w, h), (0.9, 0.4, 0.3), max(0, 1-age/0.35))
                continue
            fade = max(0, 1-age/1.15)
            _frame((x, y, w, h), opacity=fade)
            for index in range(28):
                angle = index*2.399963
                radius = (0.15+age*0.6)*min(w, h)
                px = x+w/2+math.cos(angle)*radius
                py = y+h/2+math.sin(angle)*radius
                size = (1+index%3)*fade
                particles[index%4].append((px-size/2, py-size/2, size, size))
            continue
        source = effect["source"]
        if source not in board.positions:
            continue
        sx, sy, sw, sh = board.card_rect(source)
        start, end = (sx+sw+4, sy+sh*0.5), (x-5, y+h*0.5)
        draw_curve(start, end, (*MINT, 0.16), 0.7)
        age = now-effect["started"]
        speed = 0.13 if asset.state == "QUEUED" else 0.32
        for index in range(52):
            t = (index/52+age*speed) % 1
            px, py = _point(start, end, t)
            spread = math.sin(math.pi*t)*11
            py += math.sin(index*2.399963+age*2)*spread
            size = 1.2+(index%4)*0.45
            particles[index%4].append((px-size/2, py-size/2, size, size))
    for color, rects in zip(PALETTE, particles):
        draw_rects([(x-2, y-2, w+4, h+4) for x, y, w, h in rects], (*color, 0.08))
        draw_rects(rects, (*color, 0.85))
