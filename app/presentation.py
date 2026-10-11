"""Fixed public presentation assets; no operator data or filesystem browsing."""
from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

ASSET_TYPES = {
    "szl-tokens.css": "text/css",
    "gdw-native.css": "text/css",
    "gdw-native.js": "text/javascript",
    "szl-icon-32.svg": "image/svg+xml",
    "szl-icon-16.svg": "image/svg+xml",
    "SOURCE.json": "application/json",
    "PROJECTION.json": "application/json",
}
PUBLIC_ASSET_PATHS = frozenset(f"/assets/gdw/{name}" for name in ASSET_TYPES)
MAX_ASSET_BYTES = 128 * 1024
MODULE_ROOT = Path(__file__).resolve().parent
router = APIRouter()


def read_asset(name: str, module_root: Path = MODULE_ROOT) -> bytes:
    if name not in ASSET_TYPES:
        raise ValueError("unknown presentation asset")
    descriptors: list[int] = []
    try:
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        root = os.open(module_root, directory_flags)
        descriptors.append(root)
        static = os.open("static", directory_flags, dir_fd=root)
        descriptors.append(static)
        native = os.open("native", directory_flags, dir_fd=static)
        descriptors.append(native)
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=native)
        descriptors.append(descriptor)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ASSET_BYTES:
            raise ValueError("presentation asset is not a bounded regular file")
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, min(65536, MAX_ASSET_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_ASSET_BYTES:
                raise ValueError("presentation asset exceeds display budget")
        return b"".join(chunks)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


@router.api_route("/assets/gdw/{name}", methods=["GET", "HEAD"], include_in_schema=False)
def presentation_asset(name: str) -> Response:
    try:
        content = read_asset(name)
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=404, detail="presentation asset unavailable") from error
    return Response(
        content=content,
        media_type=ASSET_TYPES[name],
        headers={
            "Cache-Control": "no-cache",
            "ETag": '"' + hashlib.sha256(content).hexdigest() + '"',
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )
