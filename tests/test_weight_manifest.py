from __future__ import annotations

import json

from ci import update_weight_manifest
from ci.update_weight_manifest import ROOT, canonical_artifact_bytes


def test_governed_text_hash_input_is_newline_portable() -> None:
    content = b"alpha\r\nbeta\rgamma\n"
    assert canonical_artifact_bytes(ROOT / "fixture.txt", content) == b"alpha\nbeta\ngamma\n"


def test_utf8_decodable_binary_hash_input_is_unchanged() -> None:
    content = b"GIF89a\r\n"
    assert canonical_artifact_bytes(ROOT / "fixture.gif", content) == content


def test_governed_binary_hash_input_is_unchanged() -> None:
    content = b"\x89PNG\r\n\x1a\n\x00payload"
    assert canonical_artifact_bytes(ROOT / "fixture.png", content) == content


def test_checked_in_manifest_binds_all_governed_artifacts() -> None:
    assert update_weight_manifest.MANIFEST_PATH.read_text(encoding="utf-8") == (
        update_weight_manifest.serialized_manifest()
    )


def test_check_rejects_same_length_dependency_drift_without_rewriting(
    tmp_path, monkeypatch, capsys
) -> None:
    manifest_path = tmp_path / "weights" / "manifest.json"
    manifest_path.parent.mkdir()
    project = tmp_path / "pyproject.toml"
    original = b'[project]\ndependencies = ["fixture-only==1.0.0"]\n'
    changed = original.replace(b"1.0.0", b"1.0.1")
    assert len(original) == len(changed)
    project.write_bytes(original)
    monkeypatch.setattr(update_weight_manifest, "ROOT", tmp_path)
    monkeypatch.setattr(update_weight_manifest, "MANIFEST_PATH", manifest_path)
    recorded = update_weight_manifest.serialized_manifest()
    manifest_path.write_text(recorded, encoding="utf-8")
    assert update_weight_manifest.main(["--check"]) == 0

    project.write_bytes(changed)
    assert update_weight_manifest.main(["--check"]) == 1
    assert "manifest is stale" in capsys.readouterr().out
    assert manifest_path.read_text(encoding="utf-8") == recorded
    before = json.loads(recorded)["artifacts"][0]
    after = update_weight_manifest.build_manifest()["artifacts"][0]
    assert before["path"] == after["path"] == "pyproject.toml"
    assert before["bytes"] == after["bytes"]
    assert before["sha256"] != after["sha256"]


def test_check_rejects_unrecorded_code_without_rewriting(tmp_path, monkeypatch) -> None:
    manifest_path = tmp_path / "weights" / "manifest.json"
    manifest_path.parent.mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    monkeypatch.setattr(update_weight_manifest, "ROOT", tmp_path)
    monkeypatch.setattr(update_weight_manifest, "MANIFEST_PATH", manifest_path)
    recorded = update_weight_manifest.serialized_manifest()
    manifest_path.write_text(recorded, encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "unrecorded.py").write_text("# unreviewed fixture code\n", encoding="utf-8")

    assert update_weight_manifest.main(["--check"]) == 1
    assert manifest_path.read_text(encoding="utf-8") == recorded
