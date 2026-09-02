#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Publish the curated GDW runtime to its public Hugging Face Space.

Security and integrity boundaries:
- GitHub remains canonical; only an explicit runtime allowlist is staged.
- The private GitHub repository is never mirrored wholesale to the public Hub.
- The generated Space README retains an explicit Docker/app-port contract.
- The generated Docker build argument and SOURCE_REVISION receipt bind the
  published artifact to the exact 40-character GitHub source SHA.
- Provider credentials are never sent to the public Space origin or written to
  the receipt.
- A cache-preserving restart is attempted first; a factory reboot is permitted
  only after a bounded failed convergence attempt.
- Success requires the exact Hub commit, a RUNNING runtime, anonymous 200s for
  `/` and `/healthz`, and continued anonymous rejection of an operator route.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from huggingface_hub import HfApi

SPACE_ID = "SZLHOLDINGS/gdw-frontier"
SPACE_ORIGIN = "https://szlholdings-gdw-frontier.hf.space"
SOURCE_REPOSITORY = "szl-holdings/gdw-frontier"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
TOKEN_RE = re.compile(r"hf_[A-Za-z0-9]{20,}")
TERMINAL_ERROR_STAGES = {
    "BUILD_ERROR",
    "RUNTIME_ERROR",
    "CONFIG_ERROR",
    "NO_APP_FILE",
    "ERROR",
    "FAILED",
    "CRASHED",
}
REQUIRED_PATHS = (
    "Dockerfile",
    "README.md",
    "pyproject.toml",
    "app",
    "ci",
    "gdw_x",
)
OPTIONAL_PATHS = (
    ".gitattributes",
    ".gitignore",
    "LICENSE",
    "docker-entrypoint.sh",
)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def clean_error(exc: BaseException, token: str | None = None) -> str:
    text = f"{type(exc).__name__}: {exc}"
    if token:
        text = text.replace(token, "***")
    return TOKEN_RE.sub("***", text)[:900]


def normalize_stage(value: Any) -> str:
    if value is None:
        return "UNKNOWN"
    if hasattr(value, "value"):
        value = value.value
    return str(value).strip().upper().replace("-", "_").replace(" ", "_") or "UNKNOWN"


def strip_frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    return text[end + 5 :] if end >= 0 else text


def space_readme(source_readme: str) -> str:
    body = strip_frontmatter(source_readme).lstrip()
    frontmatter = """---
title: GDW Frontier
emoji: 🧭
colorFrom: slate
colorTo: blue
sdk: docker
app_port: 8080
pinned: false
license: apache-2.0
---

"""
    return frontmatter + body


def stage_dockerfile(source: str, source_sha: str) -> str:
    anchor = "ARG GDW_SOURCE_SHA=UNAVAILABLE"
    if source.count(anchor) != 1:
        raise RuntimeError("Dockerfile must contain exactly one GDW source-SHA argument anchor")
    return source.replace(anchor, f"ARG GDW_SOURCE_SHA={source_sha}", 1)


def file_digest(path: Path) -> dict[str, Any]:
    content = path.read_bytes()
    return {
        "path": path.as_posix(),
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def copy_path(source_root: Path, stage_root: Path, relative: str) -> None:
    source = source_root / relative
    target = stage_root / relative
    if source.is_dir():
        shutil.copytree(
            source,
            target,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
        )
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def build_stage(source_root: Path, stage_root: Path, source_sha: str) -> dict[str, Any]:
    missing = [relative for relative in REQUIRED_PATHS if not (source_root / relative).exists()]
    if missing:
        raise RuntimeError(f"required deployment paths are missing: {', '.join(missing)}")

    for relative in (*REQUIRED_PATHS, *OPTIONAL_PATHS):
        if (source_root / relative).exists():
            copy_path(source_root, stage_root, relative)

    readme = (source_root / "README.md").read_text(encoding="utf-8")
    (stage_root / "README.md").write_text(space_readme(readme), encoding="utf-8")

    dockerfile = (source_root / "Dockerfile").read_text(encoding="utf-8")
    (stage_root / "Dockerfile").write_text(
        stage_dockerfile(dockerfile, source_sha), encoding="utf-8"
    )

    staged_files = sorted(path for path in stage_root.rglob("*") if path.is_file())
    manifest_entries = []
    for path in staged_files:
        relative = path.relative_to(stage_root)
        entry = file_digest(path)
        entry["path"] = relative.as_posix()
        manifest_entries.append(entry)

    manifest_body = "\n".join(
        f"{entry['path']}\0{entry['bytes']}\0{entry['sha256']}"
        for entry in manifest_entries
    ).encode("utf-8")
    revision = {
        "schema": "szl.hf-source-revision/v1",
        "source_repository": SOURCE_REPOSITORY,
        "source_sha": source_sha,
        "space_id": SPACE_ID,
        "runtime_path_count": len(manifest_entries),
        "runtime_manifest_sha256": hashlib.sha256(manifest_body).hexdigest(),
        "runtime_files": manifest_entries,
    }
    (stage_root / "SOURCE_REVISION.json").write_text(
        json.dumps(revision, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return revision


def validate_stage(stage_root: Path, source_sha: str) -> None:
    readme = (stage_root / "README.md").read_text(encoding="utf-8")
    if not readme.startswith("---\n"):
        raise RuntimeError("Space README lacks frontmatter")
    for required in ("sdk: docker", "app_port: 8080", "license: apache-2.0"):
        if required not in readme:
            raise RuntimeError(f"Space README lacks required setting: {required}")

    dockerfile = (stage_root / "Dockerfile").read_text(encoding="utf-8")
    if f"ARG GDW_SOURCE_SHA={source_sha}" not in dockerfile:
        raise RuntimeError("staged Dockerfile is not bound to the exact source SHA")
    if "COPY . ." in dockerfile:
        raise RuntimeError("staged Dockerfile violates the curated-copy boundary")

    revision = json.loads((stage_root / "SOURCE_REVISION.json").read_text(encoding="utf-8"))
    if revision.get("source_sha") != source_sha:
        raise RuntimeError("SOURCE_REVISION source SHA mismatch")
    staged_top = {path.name for path in stage_root.iterdir()}
    allowed_top = set(REQUIRED_PATHS) | set(OPTIONAL_PATHS) | {"SOURCE_REVISION.json"}
    unexpected = staged_top - allowed_top
    if unexpected:
        raise RuntimeError(f"unexpected public deployment paths: {sorted(unexpected)}")


def verify_checkout(source_root: Path, source_sha: str) -> None:
    try:
        actual = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=source_root, text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("unable to prove the checked-out Git source") from exc
    if actual != source_sha:
        raise RuntimeError(f"checkout SHA {actual} does not match requested source {source_sha}")


def probe(path: str, timeout: float = 12.0) -> dict[str, Any]:
    url = SPACE_ORIGIN + path
    headers = {
        "User-Agent": "szl-gdw-source-bound-sync/1",
        "Accept": "application/json,text/html;q=0.8,*/*;q=0.1",
        "Cache-Control": "no-cache",
    }
    try:
        response = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        content_type = response.headers.get("content-type", "").split(";", 1)[0]
        selected_json: dict[str, Any] | None = None
        if content_type == "application/json":
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    selected_json = {
                        key: payload.get(key)
                        for key in ("service", "status", "model_status", "operator_api")
                        if key in payload
                    }
            except ValueError:
                selected_json = None
        return {
            "path": path,
            "status": int(response.status_code),
            "content_type": content_type,
            "final_host": requests.utils.urlparse(response.url).hostname,
            "selected_json": selected_json,
            "error": None,
        }
    except requests.RequestException as exc:
        return {
            "path": path,
            "status": None,
            "content_type": None,
            "final_host": None,
            "selected_json": None,
            "error": type(exc).__name__,
        }


def runtime_snapshot(api: HfApi, token: str) -> dict[str, Any]:
    try:
        runtime = api.get_space_runtime(repo_id=SPACE_ID)
        return {
            "stage": normalize_stage(getattr(runtime, "stage", None)),
            "hardware": str(getattr(runtime, "hardware", None) or "") or None,
            "requested_hardware": str(
                getattr(runtime, "requested_hardware", None) or ""
            )
            or None,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "stage": "RUNTIME_LOOKUP_ERROR",
            "hardware": None,
            "requested_hardware": None,
            "error": clean_error(exc, token),
        }


def verify_live(api: HfApi, token: str) -> tuple[bool, dict[str, Any]]:
    runtime = runtime_snapshot(api, token)
    root = probe("/")
    health = probe("/healthz")
    operator = probe("/v1/gdw/state")
    root_payload = root.get("selected_json") or {}
    ok = bool(
        runtime["stage"] == "RUNNING"
        and root["status"] == 200
        and root_payload.get("service") == "gdw"
        and root_payload.get("status") == "ready"
        and root_payload.get("model_status") == "MODELED"
        and health["status"] == 200
        and operator["status"] in {401, 403}
    )
    return ok, {
        "observed_at": utcnow(),
        "runtime": runtime,
        "root": root,
        "healthz": health,
        "anonymous_operator_route": operator,
        "ok": ok,
    }


def converge_runtime(
    api: HfApi,
    token: str,
    *,
    timeout_seconds: int,
    poll_seconds: int,
    factory_reboot: bool,
) -> tuple[bool, list[dict[str, Any]], dict[str, Any]]:
    action: dict[str, Any]
    try:
        response = api.restart_space(repo_id=SPACE_ID, factory_reboot=factory_reboot)
        action = {
            "kind": "factory_reboot" if factory_reboot else "restart",
            "accepted": True,
            "response_stage": normalize_stage(getattr(response, "stage", None)),
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        action = {
            "kind": "factory_reboot" if factory_reboot else "restart",
            "accepted": False,
            "response_stage": None,
            "error": clean_error(exc, token),
        }
        return False, [], action

    observations: list[dict[str, Any]] = []
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        ok, observation = verify_live(api, token)
        observations.append(observation)
        if ok:
            return True, observations, action
        stage = observation["runtime"]["stage"]
        if stage in TERMINAL_ERROR_STAGES:
            return False, observations, action
        time.sleep(poll_seconds)
    return False, observations, action


def publish(
    source_root: Path,
    stage_root: Path,
    source_sha: str,
    output: Path,
    *,
    timeout_seconds: int,
    poll_seconds: int,
) -> int:
    token = os.environ.get("HF_ORG_TOKEN") or os.environ.get("HF_TOKEN")
    if not token:
        raise RuntimeError("HF_ORG_TOKEN/HF_TOKEN is not configured")

    report: dict[str, Any] = {
        "schema": "szl.gdw-hf-sync/v1",
        "started_at": utcnow(),
        "source_repository": SOURCE_REPOSITORY,
        "source_sha": source_sha,
        "space_id": SPACE_ID,
        "policy": {
            "private_repository_wholesale_publish": False,
            "curated_runtime_only": True,
            "hardware_changed": False,
            "storage_changed": False,
            "secrets_changed": False,
            "factory_reboot_only_after_normal_failure": True,
        },
        "stage": None,
        "hub": {},
        "actions": [],
        "observations": [],
        "terminal_green": False,
        "fatal_error": None,
    }

    try:
        verify_checkout(source_root, source_sha)
        revision = build_stage(source_root, stage_root, source_sha)
        validate_stage(stage_root, source_sha)
        report["stage"] = {
            "path_count": revision["runtime_path_count"],
            "manifest_sha256": revision["runtime_manifest_sha256"],
        }

        api = HfApi(token=token)
        identity = api.whoami(token=token)
        report["hub"]["identity"] = (
            identity.get("name") if isinstance(identity, dict) else None
        )
        api.create_repo(
            repo_id=SPACE_ID,
            repo_type="space",
            private=False,
            space_sdk="docker",
            exist_ok=True,
        )
        try:
            api.update_repo_visibility(repo_id=SPACE_ID, repo_type="space", private=False)
        except Exception as exc:  # noqa: BLE001
            report["hub"]["visibility_update_note"] = clean_error(exc, token)

        commit = api.upload_folder(
            repo_id=SPACE_ID,
            repo_type="space",
            folder_path=str(stage_root),
            commit_message=f"Deploy {SOURCE_REPOSITORY}@{source_sha}",
            ignore_patterns=["__pycache__", "*.pyc", ".pytest_cache"],
        )
        commit_oid = getattr(commit, "oid", None)
        report["hub"]["commit_oid"] = commit_oid
        report["hub"]["commit_url"] = str(getattr(commit, "commit_url", "") or "") or None

        info = api.space_info(repo_id=SPACE_ID)
        provider_sha = str(getattr(info, "sha", "") or "") or None
        report["hub"]["provider_repo_sha_after_upload"] = provider_sha
        if commit_oid and provider_sha and provider_sha != commit_oid:
            raise RuntimeError(
                f"Hub repository SHA {provider_sha} does not match upload commit {commit_oid}"
            )

        ok, observations, action = converge_runtime(
            api,
            token,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
            factory_reboot=False,
        )
        report["actions"].append(action)
        report["observations"].extend(observations)

        if not ok:
            ok, observations, action = converge_runtime(
                api,
                token,
                timeout_seconds=timeout_seconds,
                poll_seconds=poll_seconds,
                factory_reboot=True,
            )
            report["actions"].append(action)
            report["observations"].extend(observations)

        report["terminal_green"] = ok
        report["completed_at"] = utcnow()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(
            json.dumps(
                {
                    "space_id": SPACE_ID,
                    "source_sha": source_sha,
                    "provider_repo_sha": provider_sha,
                    "actions": report["actions"],
                    "terminal_green": ok,
                    "final": report["observations"][-1] if report["observations"] else None,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if ok else 2
    except Exception as exc:  # noqa: BLE001
        report["fatal_error"] = clean_error(exc, token)
        report["completed_at"] = utcnow()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(report["fatal_error"], file=sys.stderr)
        return 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, default=Path("."))
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=720)
    parser.add_argument("--poll-seconds", type=int, default=15)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_root = args.source_root.resolve()
    source_sha = args.source_sha.strip().lower()
    if not SHA_RE.fullmatch(source_sha):
        raise RuntimeError("--source-sha must be an exact 40-character lowercase Git SHA")

    with tempfile.TemporaryDirectory(prefix="gdw-hf-stage-") as temp_dir:
        stage_root = Path(temp_dir)
        if args.validate_only:
            verify_checkout(source_root, source_sha)
            revision = build_stage(source_root, stage_root, source_sha)
            validate_stage(stage_root, source_sha)
            validation = {
                "schema": "szl.gdw-hf-sync-validation/v1",
                "source_repository": SOURCE_REPOSITORY,
                "source_sha": source_sha,
                "space_id": SPACE_ID,
                "path_count": revision["runtime_path_count"],
                "runtime_manifest_sha256": revision["runtime_manifest_sha256"],
                "terminal_green": True,
            }
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(validation, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            print(json.dumps(validation, indent=2, sort_keys=True))
            return 0
        return publish(
            source_root,
            stage_root,
            source_sha,
            args.output,
            timeout_seconds=args.timeout_seconds,
            poll_seconds=args.poll_seconds,
        )


if __name__ == "__main__":
    raise SystemExit(main())
