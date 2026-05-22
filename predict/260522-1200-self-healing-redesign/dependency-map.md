---
commit_hash: dede6942e80750921cbdc0d18d0a327eb00380f0
---

## Import Graph

| File | Imports From | Symbols |
|------|-------------|---------|
| repair_loop.py | ..logging_config | get_logger |
| repair_loop.py | dataclasses | dataclass, field |
| repair_loop.py | datetime | datetime, timezone |
| repair_loop.py | json, os, time | stdlib |
| idle_inspector.py | .proposal | Proposal, ProposalStore, TYPE_TOOL_FIX |
| idle_inspector.py | .memory_manager | MemoryManager |
| idle_inspector.py | ..async_io | async I/O helpers |
| proposal.py | ..async_io | async_write_json |
| proposal.py | dataclasses | dataclass, field, asdict |
| management.py | ..agent.repair_loop | RepairLoop, RepairPlan (new import) |
| management.py | ..agent.proposal | ProposalExecutor |
| management.py | ..agent.oaa_agent | OAAAgent (TYPE_CHECKING) |
| management.py | ..config | AppConfig |
| tools.py | .repair_loop | record_rollback_entry (new import, inside _record_rollback_entry) |
| tests/test_idle_inspector_integration.py | oaa.agent.repair_loop | RepairLoop, RepairPlan (new import) |
| tests/test_idle_inspector_integration.py | oaa.agent.proposal | ProposalStore, ProposalExecutor |

## Call Graph

| Caller | Callee | File:Line | Type |
|--------|--------|-----------|------|
| IdleInspector._check_tool_failures | ProposalStore.add | idle_inspector.py:777 | async call |
| IdleInspector._check_tool_failures | Proposal() with problem_context | idle_inspector.py:768-775 | constructor |
| ManagementHandler._handle_proposal_approve | RepairLoop.__init__ | management.py:592 | constructor |
| ManagementHandler._handle_proposal_approve | RepairLoop.register_verifier | management.py:595 | method call |
| ManagementHandler._handle_proposal_approve | RepairLoop.run | management.py:598 | async call |
| RepairLoop.run | RepairLoop._build_feed_prompt | repair_loop.py:75 | internal method |
| RepairLoop.run | RepairLoop._feed | repair_loop.py:78 | internal async method |
| RepairLoop.run | RepairLoop._verify | repair_loop.py:81 | internal async method |
| RepairLoop._feed | agent.process_message | repair_loop.py:141 | async generator iteration |
| RepairLoop._verify | registered verifier fn | repair_loop.py:161 | async call |
| RepairLoop._rollback | RepairLoop._restore_backup | repair_loop.py:153 | static method |
| AtomicTools._record_rollback_entry | repair_loop.record_rollback_entry | tools.py:223 | function call (inside try/except) |
| AtomicTools.do_self_improve | AtomicTools._record_rollback_entry | tools.py:733 | method call (success path) |
| AtomicTools.do_file_write | AtomicTools._record_rollback_entry | tools.py:436 | method call |
| AtomicTools.do_file_patch | AtomicTools._record_rollback_entry | tools.py:474 | method call |
| ProposalStore.add | asdict(proposal) | proposal.py:108 | serialization |

## Data Flows

| Source | Transform | Sink | Risk Areas |
|--------|-----------|------|------------|
| MemoryManager.get_tool_failures() | Counter aggregation | IdleInspector._check_tool_failures | Data source reliability |
| problem_context dict | Proposal.asdict → JSON | proposals.json | None → dict compatibility |
| proposals.json | ProposalStore.get → dict | management._handle_proposal_approve | Stale data between load and read |
| problem_context.type | verifier dispatch table | RepairLoop._verify | Unregistered types get skipped |
| agent.process_message() output | chunk collection → string | RepairLoop result | LLM hallucination → false success |
| rollback_manifest.json | json.load → manifest dict | RepairLoop._rollback | Concurrent writes from multiple proposals |
| RepairLoop result dict | store.update_status | proposals.json | Status consistency |
