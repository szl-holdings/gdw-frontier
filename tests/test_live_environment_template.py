from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_live_environment_matches_master_suite_runtime_invariants() -> None:
    values = {}
    for line in (ROOT / "deployment" / ".env.live.example").read_text(
        encoding="utf-8"
    ).splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value

    assert values["GDW_WORKERS"] == "1"
    assert values["GDWX_MASTER_SUITE_SIGNING_KEY_SOURCE_FILE"] == (
        "[ABSOLUTE_PATH_TO_MASTER_SUITE_HMAC_KEY]"
    )
