# Fabprint - Claude Code Instructions

## Pre-PR Checklist (MANDATORY)
Before pushing any PR branch, always run locally:
1. `uv run ruff check src tests` — lint must pass with zero errors
2. `uv run ruff format --check src tests` — formatting must pass (run `uv run ruff format src tests` to auto-fix)
3. `uv run mypy src/fabprint` — type check must pass with zero errors
4. `uv run pytest` — all tests must pass

Do NOT push a PR until all four checks pass locally.

## Changelog (MANDATORY)
Every PR must include a CHANGELOG.md update:
1. Check the latest published version: `curl -s https://pypi.org/pypi/fabprint/json | python3 -c "import sys,json; print(json.load(sys.stdin)['info']['version'])"`
2. Add an entry at the top of CHANGELOG.md under a new version heading
3. Use the format: `## <next-version> — YYYY-MM-DD`
4. Bump the patch version from the latest on PyPI (e.g. 0.1.73 → 0.1.74)
5. Use today's date
6. List changes as bullet points — concise, user-facing descriptions

## Post-PR Checklist (MANDATORY)
After pushing a PR or merging to main:
1. Check GitHub Actions CI status with `gh run list --limit 3`
2. If any run fails, inspect with `gh run view <id> --log-failed`
3. Fix failures before moving on to other work
