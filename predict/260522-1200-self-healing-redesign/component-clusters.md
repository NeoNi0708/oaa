---
commit_hash: dede6942e80750921cbdc0d18d0a327eb00380f0
---

## Clusters

| Cluster | Files | Key Entities | External Deps | Risk Areas |
|---------|-------|-------------|---------------|------------|
| Self-Healing Core | repair_loop.py, idle_inspector.py | RepairLoop, RepairPlan, IdleInspector | agent.process_message | Agent dependency, verifier gaps, rollback correctness |
| Proposal Data Model | proposal.py | Proposal, ProposalStore, ProposalExecutor | async_io, JSON fs | None-actions compatibility, asdict serialization |
| API Routing | management.py | ManagementHandler, _tool_failure_verifier | RepairLoop, ProposalExecutor | Branch decision (problem_context vs actions), error handling |
| Tool Infrastructure | tools.py | AtomicTools, _backup_file, _record_rollback_entry | repair_loop.record_rollback_entry | Rollback manifest writes, self_improve safety |
| Integration Tests | test_idle_inspector_integration.py | TestToolFailureIntegration, TestFullPipeline | RepairLoop, mock agents | Test realism, mock completeness |

## Inter-Cluster Dependencies

```
Self-Healing Core ──proposal creation──→ Proposal Data Model
Self-Healing Core ──verifier registration──→ API Routing
API Routing ──dispatch──→ Self-Healing Core (RepairLoop)
API Routing ──fallback──→ Proposal Data Model (ProposalExecutor)
Tool Infrastructure ──rollback writes──→ Self-Healing Core (manifest)
Proposal Data Model ──consumed by──→ Self-Healing Core (problem_context)
```

## Cross-Cutting Concerns

| Concern | Affected Files | Risk |
|---------|---------------|------|
| Async correctness | repair_loop.py, management.py, tools.py, proposal.py | All calls correctly awaited — verified |
| Rollback safety | repair_loop.py, tools.py | Backup-restore cycle; manifest race conditions |
| Backward compatibility | proposal.py, management.py | Actions-based proposals still work via Path B |
| Error handling | repair_loop.py, management.py | _feed exceptions caught; verifier exceptions caught; ProposalExecutor exceptions caught |
| Testing coverage | test_idle_inspector_integration.py | Tests new problem_context path and RepairLoop with mock agent |
