"""Turn an audit into a risk-ranked cleanup plan — without changing anything.

The whole value of this module is the *protected set*: the things that must
never be deleted because automation runs them, the OS owns them, or a protected
virtualenv was built from them. Candidates are everything else, and even then
each carries its risk and the reason it's considered safe. `clean` recomputes
this same protected set at execution time, so a stale plan can never cause harm.
"""
from __future__ import annotations

import os
import subprocess
from collections import Counter
from pathlib import Path

HOME = Path.home()


def _real(p: str | Path) -> str:
    try:
        return os.path.realpath(str(p))
    except Exception:
        return str(p)


def _size_kb(path: str | Path) -> int:
    try:
        out = subprocess.run(["du", "-sk", str(path)], capture_output=True,
                             text=True, timeout=20).stdout
        return int(out.split("\t")[0].strip())
    except Exception:
        return 0


def _h(kb: int) -> str:
    if kb >= 1024 * 1024:
        return f"{kb / 1024 / 1024:.1f}G"
    if kb >= 1024:
        return f"{kb / 1024:.0f}M"
    return f"{kb}K"


def _minor(ver: str) -> str:
    parts = ver.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else ver


def _venv_dir_from_ref(ref: str) -> str | None:
    """An automation ref like '/x/venv/bin/python' → the venv dir '/x/venv'."""
    for marker in ("/.venv/bin/", "/venv/bin/"):
        if marker in ref:
            return ref.split("/bin/")[0]
    return None


def compute_protected(report: dict) -> dict:
    """Paths that must never be deleted, with the reason for each."""
    paths: set[str] = set()
    reasons: dict[str, str] = {}

    def protect(p: str | Path, why: str) -> None:
        sp = _real(p)
        paths.add(sp)
        reasons.setdefault(sp, why)

    # OS-managed interpreters
    for it in report["interpreters"]:
        if it.get("protected"):
            protect(it["path"], "system interpreter (OS-managed)")

    # Anything automation runs — plus the venv it lives in and that venv's base.
    venv_by_real = {_real(v["path"]): v for v in report["venvs"]}
    for src, refs in report["automation"].items():
        for ref in refs:
            protect(ref, f"referenced by {src}")
            vdir = _venv_dir_from_ref(ref)
            if not vdir:
                continue
            protect(vdir, f"venv used by {src}")
            v = venv_by_real.get(_real(vdir))
            if v and v.get("base"):
                protect(v["base"], f"base interpreter of venv used by {src}")
                protect(Path(v["base"]).parent, f"interpreter of venv used by {src}")
    return {"paths": paths, "reasons": reasons}


def is_protected(path: str | Path, protected: dict) -> str | None:
    """Return the protection reason if `path` overlaps the protected set."""
    p = _real(path)
    for pp in protected["paths"]:
        if p == pp or p.startswith(pp.rstrip("/") + "/") or pp.startswith(p.rstrip("/") + "/"):
            return protected["reasons"].get(pp, "in use")
    return None


def _bases_in_use(report: dict) -> set[str]:
    bases: set[str] = set()
    for v in report["venvs"]:
        b = v.get("base", "")
        if b:
            bases.add(_real(b))
            bases.add(_real(Path(b).parent))
    return bases


def build_plan(report: dict) -> dict:
    protected = compute_protected(report)
    candidates: list[dict] = []
    cid = 0

    def add(cat, path, owner, risk, reason, action, **extra):
        nonlocal cid
        cid += 1
        kb = _size_kb(path)
        candidates.append({"id": cid, "category": cat, "path": path,
                           "owner": owner, "risk": risk, "reason": reason,
                           "size_kb": kb, "size_h": _h(kb), "action": action,
                           **extra})

    # 1) Orphaned --user site-packages: byte-compiled for one interpreter,
    #    invisible to venvs. Removable, but flag the one thing to verify.
    for u in report["user_site_packages"]:
        if is_protected(u["path"], protected):
            continue
        add("user_packages", u["path"], "user", "medium",
            "--user packages are tied to one interpreter version and are not "
            "used by any virtualenv",
            {"type": "rmtree", "target": u["path"]},
            verify="confirm no script runs that bare (non-venv) interpreter "
                   "relying on these packages")

    # 2) Broken venvs: the interpreter they were built from is gone, so they
    #    can't run. (Protected venvs are skipped — automation names them.)
    for v in report["venvs"]:
        base = v.get("base", "")
        if base and Path(base).exists():
            continue
        if is_protected(v["path"], protected):
            continue
        add("broken_venv", v["path"], "user", "low",
            f"virtualenv's base interpreter is missing ({base or '?'}) — it can "
            f"no longer run", {"type": "rmtree", "target": v["path"]})

    # 3) Redundant interpreters: no venv was built from it, nothing automated
    #    uses it, and another interpreter already provides the same X.Y.
    bases = _bases_in_use(report)
    minor_counts = Counter(_minor(it["version"]) for it in report["interpreters"])
    for it in report["interpreters"]:
        if it.get("protected") or is_protected(it["path"], protected):
            continue
        it_real = _real(it["path"])
        used = any(it_real == b or b.startswith(it_real + "/") or it_real.startswith(b.rstrip("/") + "/")
                   for b in bases)
        if used:
            continue
        if minor_counts[_minor(it["version"])] <= 1:
            continue  # only redundant if a sibling provides the same minor
        owner = "root" if it["path"].startswith("/Library/") else "user"
        if it["kind"] == "homebrew":
            action = {"type": "command", "command": ["brew", "uninstall", it["label"]]}
        elif it["kind"] == "pyenv":
            action = {"type": "command", "command": ["pyenv", "uninstall", "-f", it["label"]]}
        else:  # python.org / other → root handoff
            action = {"type": "handoff", "target": it["path"]}
        add("redundant_interpreter", it["path"], owner, "medium",
            f"{it['kind']} {it['version']} — no venv built from it and another "
            f"{_minor(it['version'])} interpreter exists", action)

    candidates.sort(key=lambda c: c["size_kb"], reverse=True)
    total_kb = sum(c["size_kb"] for c in candidates)
    return {
        "candidates": candidates,
        "total_reclaim_kb": total_kb,
        "total_reclaim_h": _h(total_kb),
        "protected": {"paths": sorted(protected["paths"]), "reasons": protected["reasons"]},
    }


def render_plan(plan: dict) -> str:
    L = ["################ Cleanup Plan ################",
         f"reclaimable: {plan['total_reclaim_h']}   candidates: {len(plan['candidates'])}",
         ""]
    if not plan["candidates"]:
        L.append("  No safe cleanup candidates found. Nothing to do. 🎉")
    for c in plan["candidates"]:
        L.append(f"  [#{c['id']}] {c['category']}  ({c['size_h']}, risk={c['risk']}, owner={c['owner']})")
        L.append(f"        {c['path'].replace(str(HOME), '~')}")
        L.append(f"        why-safe: {c['reason']}")
        if c.get("verify"):
            L.append(f"        verify:   {c['verify']}")
    L.append("")
    L.append(f"  Protected (never deleted): {len(plan['protected']['paths'])} path(s) "
             f"in use by automation / the OS.")
    L.append("  Run `pyhygiene clean` to preview removal (dry-run), "
             "`pyhygiene clean --apply` to execute (backup first).")
    return "\n".join(L)
