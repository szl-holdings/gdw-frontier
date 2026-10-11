"""Fixed public presentation assets; no operator data or filesystem browsing."""
from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

ASSET_SPECS = {
    "szl-tokens.css": ("szl-tokens.css", "text/css"),
    "gdw-native.css": ("gdw-native.css", "text/css"),
    "gdw-native.js": ("gdw-native.js", "text/javascript"),
    "szl-icon-32.svg": ("szl-icon-32.svg", "image/svg+xml"),
    "szl-icon-16.svg": ("szl-icon-16.svg", "image/svg+xml"),
    "SOURCE.json": ("SOURCE.json", "application/json"),
    "PROJECTION.json": ("PROJECTION.json", "application/json"),
}
ASSET_TYPES = {name: spec[1] for name, spec in ASSET_SPECS.items()}
PUBLIC_ASSET_PATHS = frozenset(f"/assets/gdw/{name}" for name in ASSET_TYPES)
MAX_ASSET_BYTES = 128 * 1024
MODULE_ROOT = Path(__file__).resolve().parent
router = APIRouter()


def read_asset(name: str, module_root: Path = MODULE_ROOT) -> bytes:
    spec = ASSET_SPECS.get(name)
    if spec is None:
        raise ValueError("unknown presentation asset")
    filename = spec[0]
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root = os.open(module_root, directory_flags)
    try:
        static = os.open("static", directory_flags, dir_fd=root)
        try:
            native = os.open("native", directory_flags, dir_fd=static)
            try:
                descriptor = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=native)
                try:
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
                    os.close(descriptor)
            finally:
                os.close(native)
        finally:
            os.close(static)
    finally:
        os.close(root)


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
