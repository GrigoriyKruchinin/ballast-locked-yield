# CLAUDE.md: Ballast (ballast-locked-yield)

Locked-Yield Router for stablecoin capital: scans sources whose payoff is known at entry
(Pendle PT, dated futures vs spot, tokens below redemption value), computes Net Locked Yield (NLY)
after gas, fees and slippage, enters only above a risk-adjusted threshold, parks idle capital
in a low-risk lending market, exits at maturity.

**Priority order:** safety > correctness of the numbers > yield. Skipping 10 good trades beats entering one bad one.
A high fixed rate usually means the market sees elevated asset risk: risk scoring and hard limits matter more than entry mechanics.

## Session start (always)

1. Read this file and `todo.md`.
2. Find the first unchecked task in the current stage and continue from it.
3. Project state lives in files (`todo.md`, `CLAUDE.md`, git history, `docs/reports/`, `docs/decisions.md`), never in chat memory.

## Language policy

- Code, comments, docstrings, prompts, architecture docs, `README.md`, `todo.md`, commits: **English**.
- All chat with the user, user guides (`docs/guides/`), and the "Простыми словами" block of reports: **Russian**.
- The user is not a DeFi/quant specialist: in chat, explain terms on first use in plain words. Do not dumb down, do not drown in jargon.

## Commands

```bash
uv sync                      # install deps
make lint                    # ruff check + ruff format --check
make typecheck               # mypy --strict
make test                    # pytest
make check                   # lint + typecheck + test
docker compose -f docker-compose.local.yml config   # validate local stack
docker compose -f docker-compose.local.yml up -d    # start local stack (Stage 1)
```

(Targets are created in Stage 1; until then they may not exist.)

## Code style

- Python 3.12, `uv`, `pyproject.toml` + `uv.lock`.
- `mypy --strict` clean, `ruff` clean. No `Any` without a comment explaining why.
- Money: `Decimal` only, always respect token `decimals`. Floats only in the analytics layer (pandas/numpy), never on the execution path.
- SOLID: narrow interfaces (`ILockedYieldSource`, `IChainClient`, `IPriceOracle`, `IRiskRule`, `ICostModel`, `IAllocator`, `ITxExecutor`), DI via factories/containers. New source/chain/rule = new implementation, no core edits.
- Same selection/allocation logic runs on three `ITxExecutor` implementations: historical simulator, Anvil fork, live chain.
- Pydantic v2 + modular `pydantic-settings` groups. Each container role gets only the env vars it needs; the hot-wallet key is visible only to the executor container.
- Async I/O (`asyncio`, async SQLAlchemy, async web3). Property-based tests (hypothesis) for NLY and money math.
- Unknown asset/contract = rejected by default. Exact approvals only, `minOut` always, fork simulation before every tx.
- Never log keys, secrets or private data.

## Workflow (autonomous after "Start Implementation")

For each task (or coherent group) in `todo.md`:
1. Implement.
2. Verify: `make lint`, `make typecheck`, `make test`; for infra changes also `docker compose config` and healthchecks. Fix failures, never commit broken code.
3. Mark `[x]` in `todo.md` only for verified work; partial work stays `[ ]` with a status note.
4. Check `git diff --staged` for secrets, then commit (Conventional Commits, English), `todo.md` in the same commit.
5. `git push`. On failure: try once (`git pull --rebase`, check remote), then tell the user in Russian.
6. Next task.

Large dumps (logs, tables, candles) go to files; chat gets a summary and the path.
Heavy research (API docs, big exports, wide code search) goes to a subagent that returns a short conclusion.

After a large task or a stage is committed and pushed, tell the user (Russian):
«Задача закоммичена, `todo.md` актуален. Если контекст разросся, можно сделать `/clear` и написать "продолжай": я продолжу по `todo.md`».
After each stage: recommend `/clear` so the next stage starts with a clean context.

## Git

- `main` is stable. Work branches: `stage-1-mvp`, `stage-2-prod`, `stage-3-dashboard`; merge into `main` only after all stage checks pass.
- Small atomic commits: one commit = one finished task. Format: `feat(scope): ...`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`.
- Never commit `.env`, keys, `*.pem`, DB dumps with real data, large historical dumps.
- Forbidden without explicit user permission: `git push --force` to `main`, history rewrites, branch deletion.
- Stage end: all stage items in `todo.md` checked → `chore: complete stage N` → tag (`v0.1.0` / `v0.2.0` / `v0.3.0`) → push commits and tags → update README status badge → short Russian summary.
- **Autonomy ends at stage transitions.** Moving to the next stage (especially Stage 2, real money) requires explicit user confirmation.

## User guides protocol

- Guides are written **only to files**, in Russian: `docs/guides/stage-N-user-guide.md`, created right before stage N.
- Numbered steps, checkboxes, ⚠️ warnings, a "what NOT to do" list, plain language, placeholders only (no real keys).
- In chat, only: «Гайд для Стадии N создан: `docs/guides/stage-N-user-guide.md`. Выполни шаги из него и напиши, когда готово».
- The next stage does not start until the user confirms the guide steps are done.

## Fork after Stage 1: Go / Rework / Research-toolkit

After Stage 1 do **not** proceed automatically. Open `docs/reports/stage-1-go-no-go.md`, discuss results with the user honestly
in plain Russian (metrics, risks, data weaknesses, no fitting conclusions to wishes) and propose one outcome:

1. **Go:** criteria met. Write `docs/guides/stage-2-user-guide.md`, wait for confirmation, then Stage 2, then Stage 3.
2. **Rework:** borderline. At most 1–2 iterations (change hypothesis, sources, risk/entry thresholds, asset list, collection period).
   Iteration goals and new criteria go into `todo.md`. Changed thresholds are re-validated **on new data** (markets matured after the
   first check + continued live collection), never on the data they were tuned on.
3. **No-Go → research toolkit:** only after the user **explicitly** confirms ("we won't develop it as an income source").
   Without explicit confirmation nothing is deleted or rewritten. Conversion steps:
   - `README.md`: reposition as "DeFi Locked-Yield Analytics Toolkit" (monitoring, NLY math, risk-adjusted source comparison,
     incident catalog, live-data portfolio simulation). Report Stage 1 research results neutrally and exactly. Keep header style,
     update tagline and status badge ("research toolkit"). Remove passive-income / profit / live-mode wording.
   - `CLAUDE.md` and `.claude/`: new mission; "Security & Trading Risk Critic" becomes "Security & Code Quality Critic"; remove
     execution, kill-switch, later-stage skills and instructions; add code quality, reproducibility, documentation goals.
     No mentions of later stages may remain.
   - `todo.md`: remove Stages 2 and 3, add a short "Polish & docs" section; keep completed items checked.
   - Code: do not implement live execution; delete code needed only for it. `Settings` / `.env.example` keep analytics vars only.
     Telegram may stay for analytics notifications.
   - Remove all Docker infrastructure without asking: compose files, Dockerfiles, `.dockerignore`, deploy/image workflows,
     Docker Make targets and mentions. `ci.yml` keeps only ruff/mypy/pytest. Drop Redis/Celery/workers; storage becomes SQLite
     or Parquet/CSV; run via `uv run ...` and `Makefile`. Verify clean install + tests without Docker. No "Deployment" section.
   - Git: atomic commits (`docs: reposition project as research toolkit`, `chore: remove execution plans`,
     `chore: remove docker infrastructure`), neutral `CHANGELOG.md` entry, tag `v1.0.0`, push.
   - The word "pet" is used only in chat with the user, never in repo files, commits or tags.
   - Final plain-Russian summary: what was rewritten/removed, what remains useful, how to use it.

## Security (always)

- Secrets only in `.env` (gitignored) + `.env.example` with placeholders. Keys never in repo, images or logs.
- Main capital outside the system (hardware wallet / Safe); hot wallet holds only the working amount; withdrawals only to owner address.
- Exact approvals, whitelisted contracts only, fork simulation before every tx, per-tx and daily limits.
- CEX keys (if ever used): no withdrawal permission, IP-bound.
- Before every release: `make lint`, `make typecheck`, `make test`, `audit-risks` skill; for contracts `forge test` + Slither.
- No financial guarantees: "locked" yield holds only to maturity and only if asset and protocol stay solvent and pegged.
  All reports label results as estimated/historical.

## Compaction rule

When compacting context, always preserve: current stage and current task from `todo.md`, list of changed files,
verification commands (`make lint`, `make typecheck`, `make test`), decisions made (`docs/decisions.md`),
entry thresholds, asset whitelist, and open questions to the user.
