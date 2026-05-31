"""pyhygiene — keep your Python environment clean and your cleanups safe.

MVP surface:
  pyhygiene audit         read-only diagnosis (interpreters, venvs, automation)
  pyhygiene guard status  check whether the prevention guardrails are installed
  pyhygiene plan          (coming soon) risk-ranked cleanup plan from an audit
  pyhygiene clean         (coming soon) backup-first, never auto-sudo
  pyhygiene guard install (coming soon) install the prevention guardrails

Design rule: the only subcommands that exist today are read-only. Anything that
deletes or modifies the system ships only once it carries the safety guarantees
(backup-first, automation-aware, no auto-sudo).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import audit as audit_mod
from . import plan as plan_mod
from . import clean as clean_mod
from . import guard as guard_mod

HOME = Path.home()


def _roots(args: argparse.Namespace):
    return [Path(r) for r in args.roots] if args.roots else None


def cmd_audit(args: argparse.Namespace) -> int:
    report = audit_mod.audit(_roots(args))
    if args.json:
        json.dump(report, sys.stdout, indent=2)
        print()
    else:
        print(audit_mod.render_text(report))
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    plan = plan_mod.build_plan(audit_mod.audit(_roots(args)))
    if args.json:
        json.dump(plan, sys.stdout, indent=2)
        print()
    else:
        print(plan_mod.render_plan(plan))
    return 0


def cmd_clean(args: argparse.Namespace) -> int:
    report = audit_mod.audit(_roots(args))
    plan = plan_mod.build_plan(report)
    ids = set(args.id or [])
    if args.only:
        ids |= {c["id"] for c in plan["candidates"] if c["category"] in args.only}
    if not ids and not args.only:
        # Blanket default = low-risk, non-surprising categories only. Redundant
        # interpreters and expensive model caches require explicit selection
        # (--include-interpreters / --only cache / --id) so a routine cleanup
        # never silently nukes a 13 GB model cache or an interpreter.
        ids = {c["id"] for c in plan["candidates"]
               if c["category"] in ("user_packages", "broken_venv")
               or (c["category"] == "cache" and c["risk"] == "low")}
    result = clean_mod.execute(plan, ids, report, apply=args.apply,
                               include_interpreters=args.include_interpreters)
    print(clean_mod.render_result(result))
    return 0


def cmd_guard_status(_args: argparse.Namespace) -> int:
    print(guard_mod.render_status(guard_mod.status()))
    return 0


def cmd_guard_install(args: argparse.Namespace) -> int:
    result = guard_mod.install(apply=not args.dry_run)
    print(guard_mod.render_install(result))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pyhygiene",
                                description="Diagnose and safely clean up Python environments.")
    sub = p.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("audit", help="read-only diagnosis")
    pa.add_argument("roots", nargs="*", help="project roots to scan (default: common dirs)")
    pa.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    pa.set_defaults(func=cmd_audit)

    pg = sub.add_parser("guard", help="prevention guardrails")
    gsub = pg.add_subparsers(dest="guard_cmd", required=True)
    gs = gsub.add_parser("status", help="check whether guardrails are installed")
    gs.set_defaults(func=cmd_guard_status)
    gi = gsub.add_parser("install", help="install prevention guardrails (idempotent)")
    gi.add_argument("--dry-run", action="store_true", help="preview without changing files")
    gi.set_defaults(func=cmd_guard_install)

    pp = sub.add_parser("plan", help="risk-ranked cleanup plan (read-only)")
    pp.add_argument("roots", nargs="*", help="project roots to scan")
    pp.add_argument("--json", action="store_true")
    pp.set_defaults(func=cmd_plan)

    pc = sub.add_parser("clean", help="backup-first cleanup (dry-run unless --apply)")
    pc.add_argument("roots", nargs="*", help="project roots to scan")
    pc.add_argument("--apply", action="store_true",
                    help="actually execute (default is a safe dry-run)")
    pc.add_argument("--id", type=int, action="append",
                    help="limit to candidate #(s) from `plan` (repeatable)")
    pc.add_argument("--only", action="append",
                    choices=["user_packages", "broken_venv", "redundant_interpreter", "cache"],
                    help="limit to a category (repeatable)")
    pc.add_argument("--include-interpreters", action="store_true",
                    help="allow removing redundant interpreters (off by default)")
    pc.set_defaults(func=cmd_clean)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
