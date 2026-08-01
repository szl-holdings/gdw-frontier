from __future__ import annotations

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
