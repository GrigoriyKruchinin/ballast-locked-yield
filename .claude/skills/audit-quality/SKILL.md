---
name: audit-quality
description: Run a security, correctness and research-honesty audit of Ballast using the security-code-quality-critic agent. Use before merges into main and releases.
---

1. Collect scope: `git diff main...HEAD --stat` (or the whole repo for releases), `docs/decisions.md`, `config/whitelist.yaml`,
   the latest reports in `docs/reports/`.
2. Launch the `security-code-quality-critic` agent with that scope.
3. Verify each finding against the code; drop false positives with a one-line reason.
4. Write the result to `docs/reports/audit-<YYYY-MM-DD>.md` (severity, file:line, scenario, fix, status).
5. CRITICAL/HIGH findings block the merge or release until fixed. Summarize for the user in Russian, plain language.
