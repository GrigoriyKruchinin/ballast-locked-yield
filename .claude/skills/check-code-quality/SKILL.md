---
name: check-code-quality
description: Run ruff (lint + format check) and mypy --strict for Ballast and fix issues. Use before every commit.
---

1. Run `make lint` (ruff check + ruff format --check). If it fails, run `uv run ruff check --fix .` and `uv run ruff format .`, then re-run.
2. Run `make typecheck` (mypy --strict). Fix type errors properly; no blanket `# type: ignore` (a targeted ignore needs an error code and a reason comment).
3. Scan staged changes for secrets: `git diff --staged | grep -iE 'private_key|secret|api_key|token|BEGIN .*KEY'` must show only placeholders.
4. Report: pass/fail per step and files changed by auto-fixes. Do not paste full tool output; save long output to a file and give the path.
