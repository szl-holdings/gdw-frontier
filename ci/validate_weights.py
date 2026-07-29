from __future__ import annotations

import hashlib
import json
from pathlib import Path

from update_weight_manifest import MANIFEST_PATH, ROOT, tracked_paths


def main() -> int:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    entries = manifest["artifacts"]
    limits = manifest["limits"]
    findings: list[str] = []
    seen: set[str] = set()
    total = 0

    for entry in entries:
        relative = str(entry["path"])
        if relative in seen:
            findings.append(f"duplicate manifest entry: {relative}")
            continue
        seen.add(relative)
        path = (ROOT / relative).resolve()
        if ROOT.resolve() not in path.parents:
            findings.append(f"path escapes repository: {relative}")
            continue
        if not path.is_file():
            findings.append(f"missing artifact: {relative}")
            continue
        content = path.read_bytes()
        total += len(content)
        digest = hashlib.sha256(content).hexdigest()
        if digest != entry["sha256"]:
            findings.append(f"hash mismatch: {relative}")
        if len(content) != int(entry["bytes"]):
            findings.append(f"byte-count mismatch: {relative}")
        if len(content) > int(limits["max_file_bytes"]):
            findings.append(f"per-file budget exceeded: {relative}")

    actual_paths = {path.relative_to(ROOT).as_posix() for path in tracked_paths()}
    for unexpected in sorted(actual_paths - seen):
        findings.append(f"untracked governed artifact: {unexpected}")
    for stale in sorted(seen - actual_paths):
        findings.append(f"stale governed artifact entry: {stale}")
    if total > int(limits["max_total_bytes"]):
        findings.append(f"total artifact budget exceeded: {total}")

    if findings:
        print("\n".join(findings))
        return 1
    print(f"artifact weights valid: {len(entries)} files, {total} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

