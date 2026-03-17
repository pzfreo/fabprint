# Contributing to fabprint

Thanks for your interest in contributing! Here's how to get started.

## Development setup

See [docs/developing.md](docs/developing.md) for full setup instructions. Quick start:

```bash
git clone https://github.com/pzfreo/fabprint.git
cd fabprint
uv sync --extra dev
```

## Before submitting a PR

1. **Lint**: `uv run ruff check src tests`
2. **Format**: `uv run ruff format src tests`
3. **Test**: `uv run pytest`
4. **Type check**: `uv run mypy src/fabprint`

All four must pass — CI enforces them.

## Code style

- Python 3.11+ with `from __future__ import annotations`
- Type hints on all public functions
- Use `FabprintError` for user-facing errors (not `ValueError`)
- Keep functions focused — prefer small functions over long ones
- No unnecessary comments or docstrings on obvious code

## Issues

- Bug reports: include Python version, OS, and the full error message
- Feature requests: describe the use case, not just the solution
- Security issues: see [SECURITY.md](SECURITY.md) — do not open a public issue

## Pull requests

- One logical change per PR
- Write a clear title and description
- Add tests for new functionality
- Update CHANGELOG.md for user-visible changes
