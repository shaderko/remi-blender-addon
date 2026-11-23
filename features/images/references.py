"""Selected board images, captured in board order for each image request."""
from pathlib import Path
import stat

MAX_REFERENCES = 16
MAX_REFERENCE_BYTES = 20 * 1024 * 1024


def selected_images(board):
    if board is None:
        return []
    return [board.catalog.assets[key] for key in board.order
            if key in board.selected and key in board.catalog.assets
            and board.catalog.assets[key].kind == "IMAGE"
            and board.catalog.assets[key].state == "READY"
            and not board.catalog.assets[key].hidden]


def capture(board):
    images = selected_images(board)
    if len(images) > MAX_REFERENCES:
        raise ValueError("Select up to 16 image cards to use as context.")
    references, total = [], 0
    for asset in images:
        path = Path(asset.artifact)
        try:
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or not info.st_size:
                raise OSError
        except OSError:
            raise ValueError("A selected context image is unavailable. Download it or deselect its card.") from None
        total += info.st_size
        if total > MAX_REFERENCE_BYTES:
            raise ValueError("Selected context images must total at most 20 MiB.")
        references.append({"path": str(path.resolve()), "id": asset.id,
                           "bytes": info.st_size, "mtime_ns": info.st_mtime_ns})
    return references


def for_request(context, board):
    if board is None or board.scene != context.scene:
        return []
    request = getattr(board, "image_request", None)
    if request is not None:
        if request["scope"] != context.scene.get("remi_cloud_board_scope", ""):
            raise ValueError("The queued image belongs to another project. Submit it again in this project.")
        return request["references"]
    return capture(board)
