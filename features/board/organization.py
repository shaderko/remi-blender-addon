"""Board-only naming, nested group membership and geometry (no Blender IDs)."""

from dataclasses import asdict, dataclass, field
import math
import uuid


PADDING = 28.0
HEADER = 38.0
CAPTION = 24.0


def clean_name(value):
    value = " ".join(str(value).split()).strip()
    if not value:
        raise ValueError("Choose a non-empty name")
    return value[:256]


@dataclass
class Group:
    id: str
    title: str = "Group"
    parent: str = ""
    anchor: list = field(default_factory=lambda: [0.0, 0.0])


class Organization:
    def __init__(self, documents=(), membership=None):
        self.groups = {}
        for value in documents if isinstance(documents, list) else ():
            try:
                identifier, title, parent = value["id"], clean_name(value["title"]), value.get("parent", "")
                anchor = value.get("anchor", [0, 0])
                if (not isinstance(identifier, str) or not identifier.startswith("group:")
                        or not isinstance(parent, str) or not isinstance(anchor, (list, tuple)) or len(anchor) != 2
                        or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in anchor)):
                    continue
                self.groups[identifier] = Group(identifier, title, parent, list(anchor))
            except (KeyError, TypeError, ValueError):
                continue
        for group in self.groups.values():
            if group.parent not in self.groups:
                group.parent = ""
        # Repair malformed/cyclic saved documents instead of hanging the UI.
        for group in self.groups.values():
            current, seen = group.id, set()
            while current:
                if current in seen:
                    group.parent = ""
                    break
                seen.add(current)
                current = self.groups[current].parent
        self.membership = {key: group for key, group in (membership or {}).items()
                           if isinstance(key, str) and isinstance(group, str) and group in self.groups} if isinstance(membership, dict) else {}

    def documents(self):
        return [asdict(group) for group in self.groups.values()]

    def ancestors(self, identifier):
        seen = set()
        while identifier in self.groups and identifier not in seen:
            seen.add(identifier)
            yield identifier
            identifier = self.groups[identifier].parent

    def descendants(self, identifier):
        children = {}
        for group in self.groups.values():
            children.setdefault(group.parent, []).append(group.id)
        pending, found = [identifier], set()
        while pending:
            current = pending.pop()
            if current in found or current not in self.groups:
                continue
            found.add(current)
            pending.extend(children.get(current, ()))
        return found

    def members(self, identifier):
        subtree = self.descendants(identifier)
        return {asset for asset, parent in self.membership.items() if parent in subtree}

    def roots(self, groups):
        wanted = set(groups) & self.groups.keys()
        return {key for key in wanted if not (set(self.ancestors(self.groups[key].parent)) & wanted)}

    def selection(self, cards, groups):
        roots = self.roots(groups)
        covered = {card for group in roots for card in self.members(group)}
        return set(cards)-covered, roots

    def unique_name(self):
        names = {group.title for group in self.groups.values()}
        name, index = "Group", 2
        while name in names:
            name, index = f"Group {index}", index+1
        return name

    def create(self, cards, groups=(), title=""):
        cards, roots = self.selection(cards, groups)
        if not cards and not roots:
            raise ValueError("Select cards or group headers first")
        parents = [self.membership.get(card, "") for card in cards]
        parents += [self.groups[key].parent for key in roots]
        # Nest inside the nearest common ancestor, independent of asset lineage.
        chains = [list(self.ancestors(parent)) + [""] for parent in parents]
        parent = next(key for key in chains[0] if all(key in chain for chain in chains[1:]))
        identifier = "group:" + uuid.uuid4().hex
        group = Group(identifier, clean_name(title) if title else self.unique_name(), parent)
        self.groups[identifier] = group
        for card in cards:
            self.membership[card] = identifier
        for key in roots:
            self.groups[key].parent = identifier
        return group

    def move_into(self, cards, groups, target):
        if target and target not in self.groups:
            raise ValueError("That group is no longer available")
        cards, roots = self.selection(cards, groups)
        if any(target in self.descendants(key) for key in roots):
            raise ValueError("A group cannot be placed inside itself or its descendants")
        for card in cards:
            if target:
                self.membership[card] = target
            else:
                self.membership.pop(card, None)
        for key in roots:
            self.groups[key].parent = target

    def ungroup(self, identifiers):
        for key in list(identifiers):
            group = self.groups.get(key)
            if group is None:
                continue
            for card, parent in list(self.membership.items()):
                if parent == key:
                    if group.parent:
                        self.membership[card] = group.parent
                    else:
                        self.membership.pop(card)
            for child in self.groups.values():
                if child.parent == key:
                    child.parent = group.parent
            del self.groups[key]

    def path(self, key):
        return " / ".join(self.groups[item].title for item in reversed(list(self.ancestors(key))))

    def bounds(self, card_rects, *, zoom=1.0):
        """Bottom-up auto-fit: each parent includes complete child frames."""
        zoom = max(zoom, .01)
        header, caption = max(HEADER, 18/zoom), max(CAPTION, 18/zoom)
        direct = {key: [] for key in self.groups}
        for card, parent in self.membership.items():
            if card in card_rects and parent in direct:
                x, y, w, h = card_rects[card]
                direct[parent].append((x, y-caption, w, h+caption))
        result = {}
        ordered = sorted(self.groups, key=lambda key: len(list(self.ancestors(key))), reverse=True)
        for key in ordered:
            rectangles = direct[key]
            if rectangles:
                left = min(rect[0] for rect in rectangles)-PADDING
                bottom = min(rect[1] for rect in rectangles)-PADDING
                right = max(rect[0]+rect[2] for rect in rectangles)+PADDING
                top = max(rect[1]+rect[3] for rect in rectangles)+PADDING+header
                rect = (left, bottom, right-left, top-bottom)
            else:
                x, y = self.groups[key].anchor
                rect = (x, y, 240.0, 100.0+header)
            result[key] = rect
            parent = self.groups[key].parent
            if parent in direct:
                direct[parent].append(rect)
        return result


def organizer(board):
    if not hasattr(board, "organization"):
        board.organization = Organization()
    if not hasattr(board, "group_selected"):
        board.group_selected = set()
    return board.organization


def card_rects(board, *, exclude=()):
    hidden = {asset.id for asset in board.catalog.assets.values() if asset.hidden}
    return {key: (pos[0], pos[1], 300*board.scales.get(key, 1.0), 300*board.scales.get(key, 1.0))
            for key, pos in board.positions.items() if key not in hidden and key not in exclude}


def group_bounds(board):
    return organizer(board).bounds(card_rects(board), zoom=getattr(board, "zoom", 1.0))


@dataclass(frozen=True)
class LayerRow:
    id: str
    title: str
    kind: str
    depth: int = 0
    state: str = ""


def layer_rows(board, *, query="", collapsed=()):
    """A live, ordered tree, never a second copy of asset/group membership."""
    model = organizer(board)
    pending = {item["key"]: item.get("prompt", "") for item in getattr(board, "pending", ())}
    cards = {}
    ordered = dict.fromkeys(list(board.order) + list(board.catalog.assets))
    for key in ordered:
        asset = board.catalog.assets.get(key)
        if asset is not None and not asset.hidden:
            title = asset.label or asset.title or {"IMAGE": "Image", "SPLAT": "3D source", "MESH": "Mesh"}[asset.kind]
            cards[key] = LayerRow(key, title, asset.kind, state=asset.state)
        elif asset is None and key in pending:
            cards[key] = LayerRow(key, pending[key] or "Generating image", "PENDING", state="QUEUED")
    children, members = {}, {}
    for group in model.groups.values():
        children.setdefault(group.parent, []).append(group.id)
    for key in cards:
        members.setdefault(model.membership.get(key, ""), []).append(key)
    query = query.strip().casefold()
    wanted = set()
    if query:
        matched_groups = {key for key, group in model.groups.items() if query in group.title.casefold()}
        subtree = {child for key in matched_groups for child in model.descendants(key)}
        wanted = {key for key, row in cards.items() if query in row.title.casefold()
                  or model.membership.get(key) in subtree}
        wanted |= subtree
        for key in list(wanted):
            parent = model.groups[key].parent if key in model.groups else model.membership.get(key, "")
            wanted.update(model.ancestors(parent))
    result, stack = [], [("", -1)]
    while stack:
        key, depth = stack.pop()
        if key:
            if query and key not in wanted:
                continue
            if key in cards:
                row = cards[key]
                result.append(LayerRow(key, row.title, row.kind, depth, row.state))
                continue
            result.append(LayerRow(key, model.groups[key].title, "GROUP", depth))
            if not query and key in collapsed:
                continue
        entries = children.get(key, []) + members.get(key, [])
        stack.extend((child, depth+1) for child in reversed(entries))
    return result


def place_in_view(board, identifier):
    """Center-biased free space search at the current pan/zoom, never origin."""
    zoom = max(.01, getattr(board, "zoom", 1.0))
    width, height = board.region
    bar = getattr(board, "bar_rect", (0, 0, 0, 0))
    left_px, right_px = 80.0, max(140.0, width-24.0)
    bottom_px, top_px = max(150.0, bar[1]+bar[3]+72.0), max(210.0, height-28.0)
    caption = max(CAPTION, 18/zoom)
    available_w, available_h = max(60, right_px-left_px), max(60, top_px-bottom_px-caption*zoom)
    scale = min(1.0, available_w/(300*zoom), available_h/(300*zoom))
    scale = max(.25, scale)
    board.scales[identifier] = scale
    w = h = 300*scale
    left, bottom = (left_px-board.pan[0])/zoom, (bottom_px-board.pan[1])/zoom
    right, top = (right_px-board.pan[0])/zoom, (top_px-board.pan[1])/zoom
    bottom += caption
    center = ((left+right-w)/2, (bottom+top-h)/2)
    rectangles = card_rects(board, exclude={identifier})
    obstacles = [(x, y-caption, rw, rh+caption) for x, y, rw, rh in rectangles.values()]
    # Empty frames are obstacles too. Excluding the new card prevents its
    # temporary legacy slot from pushing placement away from the current view.
    obstacles += list(organizer(board).bounds(rectangles, zoom=zoom).values())
    gap = 24.0

    def nearest(xs, x_limits, y_limits):
        """Sweep X edges and merge blocked Y intervals; no quadratic point grid."""
        if x_limits[0] > x_limits[1] or y_limits[0] > y_limits[1]:
            return None
        best, distance = None, math.inf
        for x in sorted(set(xs), key=lambda x: (abs(x-center[0]), x)):
            if not x_limits[0] <= x <= x_limits[1]:
                continue
            dx2 = (x-center[0])**2
            if dx2 > distance:
                break
            blocked = sorted((b-h-gap, b+d+gap+caption) for a, b, c, d in obstacles
                             if not (x+w+gap <= a or x >= a+c+gap))
            merged = []
            for low, high in blocked:
                if merged and low < merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], high)
                else:
                    merged.append([low, high])
            y0 = max(y_limits[0], min(y_limits[1], center[1]))
            choices = [y0]
            for low, high in merged:
                if low < y0 < high:
                    choices = [low, high]
                    break
            for y in choices:
                score = dx2+(y-center[1])**2
                if y_limits[0] <= y <= y_limits[1] and score < distance:
                    best, distance = [x, y], score
        return best

    # Rectangle edges find even narrow free slots; a sampled grid can miss them.
    nearby = [(a, b, c, d) for a, b, c, d in obstacles
              if a+c+gap >= left and a-gap <= right and b+d+gap >= bottom-caption and b-gap <= top]
    xs = [center[0], left, right-w]
    for a, b, c, d in nearby:
        xs.extend((a-w-gap, a+c+gap))
    position = nearest(xs, (left, right-w), (bottom, top-h))
    if position is not None:
        board.positions[identifier] = position
        return
    # A packed viewport cannot fit another card. Search the closest boundary
    # slots; global extrema guarantee a result even beside very large groups.
    xs = [center[0]]
    for a, b, c, d in obstacles:
        xs.extend((a-w-gap, a+c+gap))
    board.positions[identifier] = nearest(xs, (-math.inf, math.inf), (-math.inf, math.inf))
