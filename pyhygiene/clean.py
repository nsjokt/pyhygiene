"""Execute a cleanup plan — safely.

Safety model, in order:
  1. dry-run is the default; mutation requires an explicit `apply=True`.
  2. the protected set is recomputed from the report passed in; the CLI passes a
     freshly-taken audit at apply time, so a stale plan can't delete something
     that came into use between planning and applying.
  3. a backup manifest is written before anything is removed.
  4. root-owned targets are NEVER deleted with sudo — a handoff script is written
     for the user to run themselves, and the target path is handed to it via a
     data file (never interpolated into shell, so no quoting/injection risk).
"""
from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from .plan import _h, compute_protected, is_protected

HOME = Path.home()


def _package_manifest(site_dir: str) -> list[str]:
    """A reinstallable list parsed from *.dist-info — no interpreter needed.

    dist-info usually lives in a nested ``site-packages/`` (e.g.
    ~/.local/lib/python3.11/site-packages/ or
    ~/Library/Python/3.9/lib/python/site-packages/), so search recursively
    rather than only the top level.
    """
    pkgs: set[str] = set()
    base = Path(site_dir)
    for di in list(base.rglob("*.dist-info")) + list(base.rglob("*.egg-info")):
        stem = di.name.rsplit(".", 1)[0]
        if "-" in stem:
            name, _, ver = stem.partition("-")
            pkgs.add(f"{name}=={ver}")
        else:
            pkgs.add(stem)
    return sorted(pkgs)


def make_backup(items: list[dict], stamp: str) -> Path:
    bk = HOME / f"python_cleanup_backup_{stamp}"
    bk.mkdir(parents=True, exist_ok=True)
    try:
        bk.chmod(0o700)  # manifest enumerates installed packages — keep it private
    except OSError:
        pass
    removed = ["category\tpath\tsize"]
    for c in items:
        removed.append(f"{c['category']}\t{c['path']}\t{c.get('size_h', '')}")
        if c["category"] == "user_packages":
            manifest = bk / (Path(c["path"]).name + "_packages.txt")
            try:
                manifest.write_text("\n".join(_package_manifest(c["path"])) + "\n")
            except Exception:
                pass
    (bk / "REMOVED.tsv").write_text("\n".join(removed) + "\n")
    (bk / "README.txt").write_text(
        "pyhygiene backup manifest.\n"
        "REMOVED.tsv lists what was removed. *_packages.txt files let you\n"
        "reinstall a removed --user site with: pip install -r <file> (inside a venv).\n"
        f"created: {stamp}\n")
    return bk


def write_handoff(bk: Path, item: dict) -> Path:
    """A self-documenting sudo script for a root-owned target. Never executed.

    The target path is written to a sidecar data file and read by the script at
    run time, so it never passes through shell parsing — no quoting/injection
    risk even for paths with spaces, ``$()``, backticks, or quotes. The
    dangling-symlink cleanup removes only NOW-BROKEN /usr/local/bin links that
    pointed into the removed tree (specific path-fragment match), never unrelated
    links such as a Homebrew interpreter that merely shares a version number.
    """
    target = item["action"].get("target", item["path"])
    slug = re.sub(r"[^A-Za-z0-9._-]", "_", Path(target).name) or "target"
    data = bk / f"sudo_remove_{slug}.target"
    data.write_text(target + "\n")
    script = bk / f"sudo_remove_{slug}.sh"
    body = (
        "#!/usr/bin/env bash\n"
        "# Handoff script — REQUIRES sudo, run it YOURSELF:\n"
        f"#   sudo bash {shlex.quote(str(script))}\n"
        "# pyhygiene never runs sudo on your behalf and never takes a password.\n"
        "set -uo pipefail\n"
        'if [[ $EUID -ne 0 ]]; then echo "run with: sudo bash $0"; exit 1; fi\n'
        'here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
        f'IFS= read -r target < "$here/{data.name}"\n'
        'if [[ -z "$target" || "$target" != /* ]]; then echo "refusing: bad target"; exit 1; fi\n'
        'echo "[remove] $target"\n'
        'rm -rf -- "$target"\n'
        "# remove only NOW-BROKEN /usr/local/bin symlinks that pointed into it:\n"
        'frag="${target#/}"\n'
        'for link in /usr/local/bin/*; do\n'
        '  [[ -L "$link" && ! -e "$link" ]] || continue\n'
        '  case "$(readlink "$link")" in *"$frag"*) echo "[unlink] $link"; rm -f -- "$link";; esac\n'
        "done\n"
        'echo "done."\n'
    )
    script.write_text(body)
    script.chmod(0o700)
    return script


def default_selection(plan: dict) -> set[int]:
    """IDs a blanket `clean` (no --id/--only) acts on: low-risk, non-surprising
    categories only. Redundant interpreters and expensive model caches are
    excluded so a routine cleanup never silently nukes a 13 GB model cache or an
    interpreter — those require explicit selection."""
    return {c["id"] for c in plan["candidates"]
            if c["category"] in ("user_packages", "broken_venv")
            or (c["category"] == "cache" and c["risk"] == "low")}


def execute(plan: dict, selected_ids: set[int], report: dict, *,
            apply: bool = False, include_interpreters: bool = False) -> dict:
    protected = compute_protected(report)  # fresh re-validation
    chosen = [c for c in plan["candidates"] if c["id"] in selected_ids]

    safe: list[dict] = []
    refused: list[dict] = []
    for c in chosen:
        why = is_protected(c["path"], protected)
        if why:
            refused.append({**c, "refused_reason": f"now protected: {why}"})
            continue
        if c["category"] == "redundant_interpreter" and not include_interpreters:
            refused.append({**c, "refused_reason": "needs --include-interpreters"})
            continue
        safe.append(c)

    if not apply:
        # reclaim reflects what WOULD actually be removed (the selected+safe set),
        # not the whole plan — otherwise a gated blanket run overstates the total.
        return {"dry_run": True, "would_remove": safe, "refused": refused,
                "reclaim_h": _h(sum(c.get("size_kb", 0) for c in safe))}

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bk = make_backup(safe, stamp)
    results: list[dict] = []
    for c in safe:
        try:
            if c["owner"] == "root" or c["action"]["type"] == "handoff":
                script = write_handoff(bk, c)
                results.append({**c, "status": "handoff", "script": str(script)})
            elif c["action"]["type"] == "rmtree":
                shutil.rmtree(c["action"]["target"])
                results.append({**c, "status": "removed"})
            elif c["action"]["type"] == "command":
                r = subprocess.run(c["action"]["command"], capture_output=True, text=True)
                results.append({**c, "status": "ran" if r.returncode == 0 else "failed",
                                "cmd": " ".join(c["action"]["command"]),
                                "stderr": r.stderr[-300:] if r.returncode else ""})
        except Exception as e:  # noqa: BLE001 — surface, don't crash mid-clean
            results.append({**c, "status": "error", "error": str(e)})
    return {"applied": True, "backup": str(bk), "results": results, "refused": refused}


def render_result(result: dict) -> str:
    L: list[str] = []
    if result.get("dry_run"):
        L.append("################ Clean (DRY-RUN — nothing deleted) ################")
        L.append(f"would reclaim up to: {result['reclaim_h']}")
        if not result["would_remove"]:
            L.append("  (no items selected/safe to remove)")
        for c in result["would_remove"]:
            tag = "→ handoff script (you run sudo)" if c["owner"] == "root" else f"→ {c['action']['type']}"
            L.append(f"  [#{c['id']}] {c['category']}  {c['size_h']}  {tag}")
            L.append(f"        {c['path'].replace(str(HOME), '~')}")
        for c in result["refused"]:
            L.append(f"  [skip #{c['id']}] {c['refused_reason']}: {c['path'].replace(str(HOME), '~')}")
        L.append("\n  Re-run with --apply to execute (a backup manifest is written first).")
        return "\n".join(L)

    L.append("################ Clean (APPLIED) ################")
    L.append(f"backup manifest: {result['backup'].replace(str(HOME), '~')}")
    for c in result["results"]:
        if c["status"] == "handoff":
            L.append(f"  [#{c['id']}] root-owned → run yourself:  sudo bash {c['script']}")
        else:
            L.append(f"  [#{c['id']}] {c['status']}: {c['path'].replace(str(HOME), '~')} ({c.get('size_h','')})")
    for c in result["refused"]:
        L.append(f"  [skip #{c['id']}] {c['refused_reason']}")
    L.append("\n  Verify: `python3 -V`, your automation still runs, `brew doctor` clean.")
    return "\n".join(L)
