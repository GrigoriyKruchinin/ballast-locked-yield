---
name: audit-risks
description: Run a security and trading-risk audit of Ballast using the security-risk-critic agent. Use before merges into main, releases, and the Stage 1 Go/No-Go report.
---

1. Collect scope: `git diff main...HEAD --stat` (or the whole repo for releases), `docs/decisions.md`, current whitelist and risk config.
2. Launch the `security-risk-critic` agent with that scope.
3. Verify each finding against the code; drop false positives with a one-line reason.
4. Write the result to `docs/reports/audit-<YYYY-MM-DD>.md` (severity, file:line, scenario, fix, status).
5. CRITICAL/HIGH findings block the merge/release until fixed. Report a short summary to the user in Russian, plain language.
