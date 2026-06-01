# Scenario catalog — "other people's messy machines"

Each scenario in `test_scenarios.py` simulates a real situation that shows up on
*someone else's* computer, builds it as synthetic fixtures under a temp dir, and
drives the real `audit → plan → clean` pipeline. Nothing touches the host: every
deletion is on a throwaway fixture, and destructive system commands
(`brew`/`pyenv uninstall`) are asserted to be **gated**, never run.

They double as **regression guards** — every bug found in development is pinned
here so a future feature can't silently bring it back.

| # | Scenario | Simulates | Guards (what must stay true) |
|---|----------|-----------|------------------------------|
| 1 | **ML researcher** | 40GB HuggingFace + torch + uv/pip caches, a cron-driven training venv | Caches surfaced & ranked; model caches = `medium` (gated), wheel caches = `low`; the training venv + its interpreter are never candidates; a blanket clean takes only the cheap caches |
| 2 | **Accidental `--user` installs** | 1GB+ in `~/.local/lib/python3.x`, system Python, no automation | `--user` dir is a candidate; the OS interpreter never is; on apply it's removed **and the backup manifest is non-empty** (regression: nested `site-packages` once produced an empty manifest) |
| 3 | **Duplicate 3.11** | pyenv 3.11 + Homebrew 3.11; only pyenv has venvs | Homebrew copy flagged redundant; pyenv copy (in use) is not; a blanket clean won't remove an interpreter; even if selected, execute **refuses** without `--include-interpreters` |
| 4 | **launchd-driven bot** | a venv run by a launchd agent | The venv **and its base interpreter** are protected; neither is a candidate |
| 5 | **uv-run wrapper** | launchd → `run_morning.sh` → `uv run` in a WorkingDirectory | Detection follows the WorkingDirectory + wrapper script to resolve the venv; it ends up protected (regression: `uv run` was missed when only literal venv paths were scanned) |
| 6 | **Broken venv** | a venv whose base interpreter is gone | It's a candidate and is removed on apply |
| 7 | **Root-owned python.org framework + pasted password** | a `/Library` framework; user pastes a password | The candidate is `owner=root`; apply writes a **sudo handoff script** and never deletes the root path; the CLI has no password parameter to misuse at all |
| 8 | **Clean machine** | one interpreter, one healthy venv | Zero candidates, 0 reclaimable |
| 9 | **Stale plan vs live machine** | a plan says delete venv X; by clean-time automation uses X | Execute re-validates from a fresh report and **refuses** — the venv survives |
| 10 | **Blanket clean on a mixed machine** | `--user` pkgs + cheap cache + 13GB model cache + redundant interpreter | A no-flag `clean --apply` removes only the `--user` pkgs + cheap cache; the **model cache and interpreter survive** |

## Running

```bash
uv run pytest tests/test_scenarios.py -v     # scenarios only
uv run pytest -q                             # full suite (25 tests)
```

## Adding a scenario

When you add a feature (or fix a bug), add a scenario that would have caught the
problem. Build the synthetic machine with the helpers (`_venv`, `_interp`,
`_site`, `_cache`, `_machine`), then assert on `build_plan(report)` and/or
`clean.execute(...)`. Keep deletions on tmp fixtures and never let a scenario run
a real `brew`/`pyenv`/`sudo` command.
