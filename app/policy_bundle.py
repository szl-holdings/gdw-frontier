from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .persistence import canonical_json


@dataclass(frozen=True)
class PolicyBundle:
    schema_version: str
    revision: str
    default_decision: str
    actions: dict[str, dict[str, Any]]
    digest: str


def load_policy_bundle(path: Path | None = None) -> PolicyBundle:
    bundle_path = path or Path(__file__).with_name("policies") / "gdw-policy-bundle.json"
    raw = json.loads(bundle_path.read_text(encoding="utf-8"))
    required = {"schema_version", "revision", "default_decision", "actions"}
    if set(raw) != required:
        raise ValueError("policy bundle fields do not match gdw.policy.v1")
    if raw["schema_version"] != "gdw.policy.v1" or raw["default_decision"] != "withhold":
        raise ValueError("policy bundle must use gdw.policy.v1 and fail closed")
    if not isinstance(raw["actions"], dict) or not raw["actions"]:
        raise ValueError("policy bundle must define at least one action")
    for action_type, policy in raw["actions"].items():
        if set(policy) != {"approval_required", "execution_mode", "risk_class"}:
            raise ValueError(f"invalid policy fields for {action_type}")
        if policy["risk_class"] not in {"low", "medium", "high", "critical"}:
            raise ValueError(f"invalid risk class for {action_type}")
    digest = hashlib.sha256(canonical_json(raw)).hexdigest()
    return PolicyBundle(
        schema_version=str(raw["schema_version"]),
        revision=str(raw["revision"]),
        default_decision=str(raw["default_decision"]),
        actions=raw["actions"],
        digest=digest,
    )
