from __future__ import annotations

from ci.update_weight_manifest import canonical_artifact_bytes


def test_governed_text_hash_input_is_newline_portable() -> None:
    assert canonical_artifact_bytes(b"alpha\r\nbeta\rgamma\n") == b"alpha\nbeta\ngamma\n"


def test_governed_binary_hash_input_is_unchanged() -> None:
    content = b"\x89PNG\r\n\x1a\n\x00payload"
    assert canonical_artifact_bytes(content) == content
