## Hypothesis Queue

| Rank | ID | Hypothesis | Severity | Confidence | Location | Consensus |
|------|----|-----------|----------|-----------|----------|-----------|
| 1 | H-01 | Rollback manifest entries are keyed under "_tool_level" sentinel instead of the real proposal ID — rollback is completely non-functional | CRITICAL | HIGH | tools.py:223, repair_loop.py:230 | 5/5 |
| 2 | H-02 | Agent judgment quality — may choose wrong fix strategy or give up too early. Corrected: this is not a capability boundary, see findings.md for correction. | INFO | N/A | design | corrected |
| 3 | H-03 | _tool_failure_verifier() always returns True — failed repairs reported as success | MEDIUM | HIGH | management.py:889-900 | 5/5 |
| 4 | H-04 | RepairLoop._verify returns True when verifier is missing — silently passes verification | MEDIUM | HIGH | repair_loop.py:155-163 | 4/5 |
| 5 | H-05 | _handle_proposal_approve blocks the management handler for 30-120s during repair | MEDIUM | HIGH | management.py:598 | 4/5 |
| 6 | H-06 | agent.process_message() has no timeout in RepairLoop._feed — hang = permanent block | MEDIUM | HIGH | repair_loop.py:141 | 4/5 |
| 7 | H-07 | Proposal dataclass lacks __post_init__ validation for mutually exclusive fields | MEDIUM | HIGH | proposal.py:50-64 | 3/5 |
| 8 | H-08 | Independent verification is still missing — the verifier stub masks the original problem | MEDIUM | MEDIUM | management.py:889 | 3/5 |
