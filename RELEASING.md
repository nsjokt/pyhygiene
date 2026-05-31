# Releasing pyhygiene

Publishing is **outward-facing and effectively irreversible** (a version number,
once uploaded to PyPI, can never be reused). So this is a manual checklist you
run yourself — never hand a PyPI token to an automated agent, and never paste it
into a chat.

## 0. Prerequisites (once)
- A PyPI account + a TestPyPI account.
- An API token for each (PyPI → Account settings → API tokens). Store them with
  `uv`/`keyring` or in `~/.pypirc`, not in the repo.

## 1. Pre-flight
```bash
uv run pytest -q          # all green
uv build                  # builds dist/*.whl and dist/*.tar.gz
uv run --with twine twine check dist/*   # metadata/render sanity
```
Bump `version` in `pyproject.toml` (and `pyhygiene/__init__.py`) if needed —
PyPI rejects re-uploading an existing version.

## 2. Dry-run on TestPyPI first
```bash
uv publish --publish-url https://test.pypi.org/legacy/ --token <TEST_PYPI_TOKEN>
# verify it installs cleanly from TestPyPI in a throwaway env:
uvx --index-url https://test.pypi.org/simple/ --from pyhygiene pyhygiene audit
```

## 3. Real release
```bash
uv publish --token <PYPI_TOKEN>
# smoke test the real thing:
uvx pyhygiene audit
uv tool install pyhygiene && pyhygiene guard status
```

## 4. Tag the release
```bash
git tag -a v$(grep '^version' pyproject.toml | head -1 | cut -d'"' -f2) -m "release"
git push --tags
```

## Distribution beyond PyPI (later)
- **Homebrew tap**: a formula that `pip install`s pyhygiene into its own venv, so
  `brew install <tap>/pyhygiene` works for non-Python users.
- **CI**: a GitHub Action running `uv run pytest` on push, and a release workflow
  that builds + publishes on tag (using a PyPI Trusted Publisher, so no token in
  CI secrets).
