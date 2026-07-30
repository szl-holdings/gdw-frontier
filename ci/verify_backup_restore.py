from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

from app.persistence import ReceiptLedger


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Restore the latest signed GDW backup into an isolated path and verify it."
    )
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--restore-path", type=Path, required=True)
    parser.add_argument("--signing-key-file", type=Path, required=True)
    args = parser.parse_args(argv)

    backups = sorted(
        path
        for path in args.backup_dir.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    )
    if not backups:
        raise SystemExit("no completed backup exists")
    verifier = ReceiptLedger(
        args.restore_path,
        signing_key_path=args.signing_key_file,
        signing_required=True,
    )
    proof = verifier.verify_and_restore_backup(backups[-1], args.restore_path)
    print(json.dumps(proof, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
