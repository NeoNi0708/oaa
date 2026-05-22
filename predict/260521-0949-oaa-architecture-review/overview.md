# Predict Analysis — OAA Architecture Review

**Date:** 2026-05-21 09:49–10:20
**Scope:** `oaa/agent/*.py`, `oaa/auth/*.py`, `oaa/gateway/*.py`, `oaa/app.py`, `oaa/config.py`
**Personas:** 5 (Architecture Reviewer, Security Analyst, Performance Engineer, Reliability Engineer, Devil's Advocate)
**Debate Rounds:** 2 completed
**Commit Hash:** `9118574551d66f159dd20db77e9e61d6663cc4aa`
**Anti-Herd Status:** ✅ PASSED — no groupthink detected (flip_rate=0.032, convergence is evidence-based)

## Summary

- **Total Findings:** 25
  - Confirmed: 25 | Probable: 0 | Minority: 0
- **Severity Breakdown:** Critical: 3 | High: 10 | Medium: 8 | Low: 4
- **Composite Score:** **431** (see metric below)

## Top 5 Findings

1. [Plaintext credentials in config.json — entire credential store exposed](./findings.md#finding-1) — CRITICAL | 5/5 consensus
2. [No authentication on WebSocket management interface](./findings.md#finding-2) — CRITICAL | 4/5 consensus
3. [Arbitrary shell command execution with auto-approval by default](./findings.md#finding-3) — CRITICAL | 4/5 consensus
4. [Dynamic tools dispatch path fully broken](./findings.md#finding-4) — HIGH | 5/5 consensus
5. [ManagementHandler 20+ handlers with fragile getattr dispatch](./findings.md#finding-5) — HIGH | 5/5 consensus

## Key Systemic Themes

1. **Missing trust boundary** — No WebSocket auth + plaintext credentials + unredacted management API = any local process can exfiltrate all cloud credentials (Findings 1, 2, 7)
2. **Auto-execution privilege escalation** — Default "auto" permission + autonomous proposal generation + broken trust tracking = LLM can execute arbitrary shell commands without confirmation (Findings 3, 9, 10)
3. **Dead/broken features** — Dynamic tools dispatch never works (DA-2 → Finding 4), trust tracking is dead code (DA-1 → Finding 10)
4. **Synchronous I/O on async hot path** — 6+ independent JSON persistence systems block the event loop on every tool execution (Finding 6)
5. **Architectural coupling** — ManagementHandler (835 lines, 6 domains) exceeds OAAAgent's coupling issues (Finding 5, 13)

## Files in This Report

- [Findings](./findings.md) — 25 findings ranked by priority score
- [Hypothesis Queue](./hypothesis-queue.md) — for chain handoff
- [Persona Debates](./persona-debates.md) — full 2-round debate transcript
- [Iteration Log](./predict-results.tsv) — per-persona per-round data

## Composite Score Calculation

```
predict_score = 25 confirmed × 15       = 375
              + 0 probable × 8          =   0
              + 7 minority opinions × 3 =  21
              + (5/5 personas) × 20     =  20
              + (2/2 rounds) × 10       =  10
              + anti_herd_passed × 5    =   5
              = 431
```
