#!/usr/bin/env python3
"""Validate the RNG data bundle and (on main) rewrite manifest.json.

    python scripts/publish.py            check everything, change nothing (run this before you push)
    python scripts/publish.py --write    check, then bump bundleVersion and rewrite manifest.json
                                         if any bundle file changed (the publish workflow does this)

Checks, in order. Any error fails the run, so a broken edit never reaches players:
  1. every bundle/*.json has a schema/<name>.schema.json and every required file exists
  2. each file is UTF-8 JSON (no BOM, no NaN, no duplicate keys) and at most 5 MiB
  3. each file is valid against its JSON Schema (draft 2020-12)
  4. cross-file consistency: unique ids, every referenced item / source exists
The manifest lists sha256 + size of the exact bytes in bundle/, which is what players download and verify.

Needs Python 3.10+ and `jsonschema` (pip install jsonschema).
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUNDLE = ROOT / "bundle"
SCHEMA = ROOT / "schema"
MANIFEST = ROOT / "manifest.json"
REQUIRED = ("sources.json", "items.json", "drops.json", "patterns.json", "signatures.json")
MAX_BYTES = 5 * 1024 * 1024
SCHEMA_VERSION = 1
DEFAULT_MIN_MOD_VERSION = "0.1.0-alpha"
MAX_ERRORS_PER_FILE = 25

errors: list[str] = []
warnings: list[str] = []


def error(msg: str) -> None:
    errors.append(msg)
    print(f"::error::{msg}" if os.environ.get("GITHUB_ACTIONS") else f"ERROR   {msg}")


def warn(msg: str) -> None:
    warnings.append(msg)
    print(f"::warning::{msg}" if os.environ.get("GITHUB_ACTIONS") else f"warning {msg}")


def pointer(path) -> str:
    return "/" + "/".join(str(p) for p in path) if path else "(root)"


def no_duplicates(pairs):
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise ValueError(f"duplicate key {k!r}")
        seen[k] = v
    return seen


def reject_constant(name):
    raise ValueError(f"{name} is not valid JSON")


def load_json(path: Path, raw: bytes):
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError("starts with a UTF-8 BOM; save the file as UTF-8 without BOM")
    text = raw.decode("utf-8")
    return json.loads(text, object_pairs_hook=no_duplicates, parse_constant=reject_constant)


def validator_for(schema_path: Path):
    from jsonschema import Draft202012Validator

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def check_files() -> dict[str, dict]:
    """Validates bundle/*.json and returns {name: parsed json} for the files that parsed."""
    data: dict[str, dict] = {}
    names = sorted(p.name for p in BUNDLE.glob("*.json"))
    for req in REQUIRED:
        if req not in names:
            error(f"bundle/{req} is missing")
    for name in names:
        path = BUNDLE / name
        schema_path = SCHEMA / name.replace(".json", ".schema.json")
        raw = path.read_bytes()
        if len(raw) > MAX_BYTES:
            error(f"bundle/{name} is {len(raw):,} bytes; the limit is {MAX_BYTES:,}")
            continue
        if len(raw) == 0:
            error(f"bundle/{name} is empty")
            continue
        try:
            doc = load_json(path, raw)
        except (UnicodeDecodeError, ValueError) as e:
            error(f"bundle/{name} is not valid JSON: {e}")
            continue
        if not schema_path.is_file():
            error(f"bundle/{name} has no schema (add schema/{schema_path.name})")
            continue
        validator = validator_for(schema_path)
        errs = sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path))
        for e in errs[:MAX_ERRORS_PER_FILE]:
            error(f"bundle/{name} {pointer(e.absolute_path)}: {e.message}")
        if len(errs) > MAX_ERRORS_PER_FILE:
            error(f"bundle/{name}: {len(errs) - MAX_ERRORS_PER_FILE} more schema errors")
        if not errs:
            data[name] = doc
            print(f"ok      bundle/{name} ({len(raw):,} bytes)")
    return data


def check_consistency(data: dict[str, dict]) -> None:
    """Cross-file references the schemas cannot express."""
    if not all(n in data for n in ("sources.json", "items.json", "drops.json")):
        return
    sources = data["sources.json"]["sources"]
    items = data["items.json"]["items"]
    drops = data["drops.json"]["drops"]

    def dupes(values, what):
        seen = set()
        for v in values:
            if v in seen:
                error(f"duplicate {what} {v!r}")
            seen.add(v)
        return seen

    source_ids = dupes([s["id"] for s in sources], "source id in sources.json")
    item_ids = dupes([i["id"] for i in items], "item id in items.json")
    dupes([d["itemId"] for d in drops], "itemId in drops.json")
    for s in sources:
        for e in s["lootPool"]:
            if e["itemId"] not in item_ids:
                error(f"sources.json {s['id']}: loot item {e['itemId']!r} has no entry in items.json")
        bust = s.get("bustItemId")
        if bust and bust not in item_ids:
            error(f"sources.json {s['id']}: bustItemId {bust!r} has no entry in items.json")
    for d in drops:
        if d["itemId"] not in item_ids:
            error(f"drops.json {d['itemId']!r} has no entry in items.json")
        for c in d["sources"]:
            if c["source"] not in source_ids:
                error(f"drops.json {d['itemId']}: unknown source {c['source']!r}")
    if "signatures.json" in data:
        for sig in data["signatures.json"]["signatures"]:
            for item in sig.get("itemIds", []):
                if item not in item_ids:
                    warn(f"signatures.json {sig['ending']}: item {item!r} is not in items.json (fine for variants)")


def file_entries() -> dict[str, dict]:
    out = {}
    for path in sorted(BUNDLE.glob("*.json")):
        raw = path.read_bytes()
        out[path.name] = {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}
    return out


def read_manifest():
    try:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def published_max() -> int:
    """Highest bundleVersion ever committed in manifest.json (so the version never goes backwards)."""
    best = 0
    try:
        commits = subprocess.run(["git", "log", "--format=%H", "--", "manifest.json"], cwd=ROOT, capture_output=True, text=True,
                                 check=True).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        warn("git history unavailable; bundleVersion is based on manifest.json only")
        return 0
    for c in commits:
        r = subprocess.run(["git", "show", f"{c}:./manifest.json"], cwd=ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            continue
        try:
            best = max(best, int(json.loads(r.stdout).get("bundleVersion", 0)))
        except (ValueError, TypeError, AttributeError):
            continue
    return best


def write_manifest() -> None:
    files = file_entries()
    head = read_manifest() if MANIFEST.is_file() else None
    head_version = head.get("bundleVersion", 0) if isinstance(head, dict) and isinstance(head.get("bundleVersion"), int) else 0
    history = published_max()
    if isinstance(head, dict) and head.get("files") == files and head_version >= history and head.get("schemaVersion") == SCHEMA_VERSION:
        print(f"manifest.json is up to date (bundleVersion {head_version})")
        set_output(changed="false", version=str(head_version))
        return
    version = max(head_version, history) + 1
    min_mod = head.get("minModVersion") if isinstance(head, dict) and isinstance(head.get("minModVersion"), str) else DEFAULT_MIN_MOD_VERSION
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "bundleVersion": version,
        "minModVersion": min_mod,
        "updatedAt": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files": files,
    }
    errs = list(validator_for(SCHEMA / "manifest.schema.json").iter_errors(manifest))
    for e in errs:
        error(f"manifest.json {pointer(e.absolute_path)}: {e.message}")
    if errs:
        return
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"manifest.json: bundleVersion {head_version} -> {version}")
    set_output(changed="true", version=str(version))


def set_output(**values) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            for k, v in values.items():
                f.write(f"{k}={v}\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="bump bundleVersion and rewrite manifest.json if the bundle changed")
    args = ap.parse_args()
    try:
        import jsonschema  # noqa: F401
    except ImportError:
        print("jsonschema is not installed: python -m pip install jsonschema", file=sys.stderr)
        return 2

    data = check_files()
    check_consistency(data)
    if errors:
        print(f"\n{len(errors)} problem(s) found; nothing was published.")
        return 1
    if args.write:
        write_manifest()
    else:
        head = read_manifest()
        if head is None:
            warn("manifest.json is missing or unreadable; the publish workflow will create it")
        else:
            errs = list(validator_for(SCHEMA / "manifest.schema.json").iter_errors(head))
            for e in errs:
                error(f"manifest.json {pointer(e.absolute_path)}: {e.message}")
            if not errs and head.get("files") != file_entries():
                print("note    bundle changed since manifest.json was written; the publish workflow bumps the version on push")
    if errors:
        return 1
    print(f"\nAll checks passed ({len(warnings)} warning(s)).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
