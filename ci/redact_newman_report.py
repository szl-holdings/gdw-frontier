from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable


def redact_secret(value: Any, secret: str) -> Any:
    if isinstance(value, dict):
        return {key: redact_secret(item, secret) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_secret(item, secret) for item in value]
    if isinstance(value, str):
        return value.replace(secret, "[REDACTED]")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Remove a bearer token from a Newman JSON report before publication."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--secret-file", type=Path, required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    secret = args.secret_file.read_text(encoding="utf-8").strip()
    if len(secret) < 32:
        raise ValueError("refusing to redact with an invalid bearer token")
    report = json.loads(args.input.read_text(encoding="utf-8"))
    redacted = redact_secret(report, secret)
    serialized = json.dumps(redacted, indent=2, sort_keys=True) + "\n"
    if secret in serialized:
        raise RuntimeError("the Newman report still contains the bearer token")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(serialized, encoding="utf-8", newline="\n")
    print(f"wrote redacted Newman report to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
