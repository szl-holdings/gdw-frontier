from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.presentation import ASSET_TYPES, MAX_ASSET_BYTES, MODULE_ROOT, PUBLIC_ASSET_PATHS, read_asset, router
from ci.check_native_assets import check


def test_native_projection_and_all_public_response_bytes() -> None:
    result = check()
    assert result["canonical_assets"] == 4
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        for name, mime in ASSET_TYPES.items():
            response = client.get(f"/assets/gdw/{name}")
            assert response.status_code == 200
            assert response.content == (MODULE_ROOT / "static" / "native" / name).read_bytes()
            assert response.headers["content-type"].startswith(mime)
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.headers["cache-control"] == "no-cache"
            assert client.head(f"/assets/gdw/{name}").status_code == 200
            assert client.post(f"/assets/gdw/{name}").status_code == 405
        assert client.get("/assets/gdw/private.json").status_code == 404
    assert PUBLIC_ASSET_PATHS == {f"/assets/gdw/{name}" for name in ASSET_TYPES}


def test_asset_reader_rejects_traversal_symlinks_and_nonregular_files(tmp_path: Path) -> None:
    root = tmp_path / "app"
    native = root / "static" / "native"
    native.mkdir(parents=True)
    secret = tmp_path / "outside"
    secret.write_text("fixture-only")
    with pytest.raises(ValueError):
        read_asset("../../outside", root)
    target = native / "SOURCE.json"
    target.symlink_to(secret)
    with pytest.raises(OSError):
        read_asset("SOURCE.json", root)
    target.unlink()
    target.write_bytes(b"x" * (MAX_ASSET_BYTES + 1))
    with pytest.raises(ValueError):
        read_asset("SOURCE.json", root)
    target.unlink()
    os.mkfifo(target)
    with pytest.raises(ValueError):
        read_asset("SOURCE.json", root)
    target.unlink()
    native.rmdir()
    native.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(OSError):
        read_asset("SOURCE.json", root)
    assert secret.read_text() == "fixture-only"


def test_native_read_closes_every_open_descriptor(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "app"
    native = root / "static" / "native"
    native.mkdir(parents=True)
    (native / "SOURCE.json").write_text("{}")
    actual_open, actual_close = os.open, os.close
    opened, closed = [], []

    def track_open(*args, **kwargs):
        descriptor = actual_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    def track_close(descriptor):
        closed.append(descriptor)
        return actual_close(descriptor)

    monkeypatch.setattr(os, "open", track_open)
    monkeypatch.setattr(os, "close", track_close)
    assert read_asset("SOURCE.json", root) == b"{}"
    assert closed == list(reversed(opened))


@pytest.mark.parametrize("failure", ["root", "static", "native", "SOURCE.json", "read", "close"])
def test_native_read_closes_parents_after_failure(tmp_path: Path, monkeypatch, failure: str) -> None:
    root = tmp_path / "app"
    native = root / "static" / "native"
    native.mkdir(parents=True)
    (native / "SOURCE.json").write_text("{}")
    actual_open, actual_close, actual_read = os.open, os.close, os.read
    opened, closed = [], []

    def track_open(path, *args, **kwargs):
        if (failure == "root" and path == root) or path == failure:
            raise OSError("fixture open failure")
        descriptor = actual_open(path, *args, **kwargs)
        opened.append(descriptor)
        return descriptor

    def track_read(*args, **kwargs):
        if failure == "read":
            raise OSError("fixture read failure")
        return actual_read(*args, **kwargs)

    def track_close(descriptor):
        closed.append(descriptor)
        actual_close(descriptor)
        if failure == "close" and len(closed) == 1:
            raise OSError("fixture close failure after descriptor release")

    monkeypatch.setattr(os, "open", track_open)
    monkeypatch.setattr(os, "read", track_read)
    monkeypatch.setattr(os, "close", track_close)
    with pytest.raises(OSError, match="fixture"):
        read_asset("SOURCE.json", root)
    assert closed == list(reversed(opened))


def test_asset_package_rejects_missing_and_tampered_bytes(tmp_path: Path) -> None:
    import zipfile
    from ci.check_native_assets import FILES, ROOT

    paths = ["app/presentation.py", "app/static/calibration-console.html", "app/static/master-suite.html", *(f"app/static/native/{name}" for name in FILES)]
    valid = tmp_path / "valid.whl"
    with zipfile.ZipFile(valid, "w") as archive:
        for path in paths:
            archive.writestr(path, (ROOT / path).read_bytes())
    assert len(check(valid)["packaged"]) == 10
    broken = tmp_path / "broken.whl"
    with zipfile.ZipFile(broken, "w") as archive:
        for path in paths:
            archive.writestr(path, b"tampered" if path.endswith("szl-icon-32.svg") else (ROOT / path).read_bytes())
    with pytest.raises(ValueError, match="package/source mismatch"):
        check(broken)
