# CLAUDE.md: Ballast (ballast-locked-yield)

DeFi locked-yield analytics toolkit. Collects fixed-rate stablecoin markets (Pendle PT), lending rates, peg prices, TVL, gas and
futures basis; computes Net Locked Yield (NLY) after gas, fees and slippage; scores asset and protocol risk against a sourced
incident catalog; checks executability on an Anvil fork; backtests and simulates a paper portfolio on live data.
It holds no keys and sends no transactions outside a local fork.

**Goals, in order:** correct numbers > honest, reproducible analysis > code quality > documentation > features.
Results are reported as estimates, with assumptions and data gaps stated next to them.

## Session start

1. Read this file and `todo.md`; continue with the first open item unless the user asks for something else.
2. Project state lives in files (`todo.md`, `docs/decisions.md`, `docs/reports/`, `CHANGELOG.md`, git history), not in chat memory.

## Language policy

- Code, comments, docstrings, `README.md`, `todo.md`, `CHANGELOG.md`, commits: **English**.
- Chat with the user, the user guide (`docs/guides/user-guide.md`) and Telegram message text: **Russian**.
- The user is not a DeFi/quant specialist: in chat, explain terms on first use in plain words.

## Commands

```bash
uv sync                      # install deps
make lint                    # ruff check + ruff format --check
make typecheck               # mypy --strict
make test                    # pytest (unit; Postgres tests via TEST_DATABASE_URL)
make check                   # lint + typecheck + test
make config                  # validate docker-compose.local.yml
make up / make down / make ps / make logs
make kick | backfill | backtest | simulate | report | verify-whitelist | notify-test | digest
uv run ballast --help        # same commands without Docker (DB_URL_OVERRIDE for the database)
```

## Code style

- Python 3.12, `uv`, `pyproject.toml` + `uv.lock`; `mypy --strict` and `ruff` clean. No `Any` without a reason in a comment.
- Money: `Decimal` only, explicit token `decimals`, conservative rounding. Floats only inside analytics summaries.
- SOLID: narrow interfaces (`ILockedYieldSource`, `IChainClient`, `IPriceOracle`, `IRiskRule`, `ICostModel`, `IAllocator`,
  `ITxExecutor`), composition in `ballast.pipeline.context`. A new source, chain or rule is a new class, not a core edit.
- Live scans, the backtest and the fork simulation share the same decision code (Analyzer, allocator, paper engine).
- Modular `pydantic-settings` groups; each container gets only the variables it needs.
- Unknown assets/contracts are rejected by default; simulated transactions pass `TxGuard` (whitelisted target, exact approval,
  `minOut`, own recipient, size limit).
- Never log keys, tokens, RPC URLs with keys or other secrets (`ballast.logging` redacts; keep it that way).
- Tests for every number that drives a result; hypothesis property tests for NLY, money math, allocator and portfolio invariants.

## Reproducibility

- Every report states its data window, sources, assumptions and gaps. Thresholds tuned on one window are evaluated on another.
- Changing a parameter means a dated entry in `docs/decisions.md` and a `CHANGELOG.md` line.
- Large outputs (logs, tables) go to files; chat gets a summary and the path. Heavy research goes to a subagent.

## Workflow

For each task (or coherent group) in `todo.md`:
1. Implement.
2. Verify: `make lint`, `make typecheck`, `make test`; for infra changes also `make config`.
3. Mark `[x]` in `todo.md` only for verified work.
4. Check `git diff --staged` for secrets, commit (Conventional Commits, English) with `todo.md` in the same commit.
5. `git push`. On failure: try once (`git pull --rebase`, check remote), then tell the user in Russian.

## Git

- `main` is stable; work happens on short-lived feature branches merged after checks pass.
- Small atomic commits: `feat(scope): ...`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`.
- Never commit `.env`, keys, `*.pem`, database dumps or large data exports.
- Forbidden without explicit user permission: force-push to `main`, history rewrites, branch deletion, deleting Docker volumes.
- Releases: update `CHANGELOG.md` and the README status badge, tag `vX.Y.Z`, push tags.

## Security

- Secrets only in `.env` (gitignored) and `.env.example` with placeholders. No wallet keys or exchange keys exist in this project.
- RPC keys reach only chain-facing containers; the Telegram token only the notifier.
- Before a release: `make check`, the `audit-quality` skill.

## Compaction rule

When compacting context, always preserve: the current task from `todo.md`, the list of changed files, verification commands
(`make lint`, `make typecheck`, `make test`), decisions made (`docs/decisions.md`), the asset whitelist and open questions to the user.
