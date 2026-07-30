from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Iterable

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate one non-overwriting ECDSA P-256 receipt-signing key."
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(private_pem)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        args.output.unlink(missing_ok=True)
        raise
    print(f"generated protected signing key: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
