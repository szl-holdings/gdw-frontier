"""Verify fixed source/package native bytes without importing operator services."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "app" / "static" / "native"
CANONICAL = {
    "szl-tokens.css": "81f7c9baeef0b435e6e3f9d0c4e0d70e40fa17df5d1f30697f623cd3ef5f8eab",
    "SOURCE.json": "967a77235227f1d75852fb61873e46336e67403cbc0e7e12b141e31c83fbd93d",
    "szl-icon-32.svg": "6e2b7d72a3af3d9602b79de41df0194ede6496a9661f057108ec224c0efc92e6",
    "szl-icon-16.svg": "6e2dc7c9794dc452fbccb44ffadbd168c2584361904e8bcaeba542427d9e1a3a",
}
FILES = (*CANONICAL, "PROJECTION.json", "gdw-native.css", "gdw-native.js")


def check(wheel: Path | None = None) -> dict:
    sys.path.insert(0, str(ROOT))
    from app.presentation import ASSET_TYPES, read_asset

    if set(FILES) != set(ASSET_TYPES):
        raise ValueError("closed native path set differs")
    source = {name: read_asset(name, ROOT / "app") for name in FILES}
    for name, expected in CANONICAL.items():
        if hashlib.sha256(source[name]).hexdigest() != expected:
            raise ValueError(f"canonical byte mismatch: {name}")
    projection = json.loads(source["PROJECTION.json"])
    if projection.get("canonical_repository") != "https://github.com/szl-holdings/szl-brand":
        raise ValueError("canonical owner differs")
    if projection.get("consumer") != "szl-holdings/gdw-frontier":
        raise ValueError("projection consumer differs")
    if projection.get("token_source_revision") != "27d12e77fc0f9f1a0e5fae8b34adf2380d51d070":
        raise ValueError("token source revision differs")
    listed = projection.get("assets")
    if not isinstance(listed, list) or len(listed) != len(CANONICAL):
        raise ValueError("projection set differs")
    if {row.get("name"): row.get("sha256") for row in listed} != CANONICAL:
        raise ValueError("projection hashes differ")
    for row in listed:
        icon = row["name"].endswith(".svg")
        expected_revision = "fd62fe5856ed8e5f660eaae5a9c52957cd71b0a7" if icon else "4309f6fce89179331b0b3ed8c99494e76c5be256"
        expected_source = ("kit/logos/orbit-v2/suite/" if icon else "kanchay/") + row["name"]
        if row.get("revision") != expected_revision or row.get("source") != expected_source:
            raise ValueError("native source attribution differs")
    packaged = []
    if wheel is not None:
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ValueError("duplicate package path")
            package_paths = ["app/presentation.py", "app/static/calibration-console.html", "app/static/master-suite.html", *(f"app/static/native/{name}" for name in FILES)]
            for path in package_paths:
                if path not in names or archive.getinfo(path).file_size > 256 * 1024:
                    raise ValueError(f"missing or oversized package file: {path}")
                body = archive.read(path)
                if body != (ROOT / path).read_bytes():
                    raise ValueError(f"package/source mismatch: {path}")
                packaged.append({"path": path, "sha256": hashlib.sha256(body).hexdigest()})
    return {"kind": "UNSIGNED_SOURCE_PACKAGE_ASSET_CHECK", "canonical_assets": len(CANONICAL), "served_paths": len(FILES), "packaged": packaged}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path)
    args = parser.parse_args()
    print(json.dumps(check(args.wheel), indent=2))
