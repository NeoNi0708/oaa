---
commit_hash: dede6942e80750921cbdc0d18d0a327eb00380f0
analyzed_at: 2026-05-22T12:00:00+08:00
scope: oaa/agent/repair_loop.py, oaa/agent/idle_inspector.py, oaa/agent/proposal.py, oaa/gateway/management.py, oaa/agent/tools.py, tests/test_idle_inspector_integration.py
files_analyzed: 6
total_lines: 4383
---

## Functions & Classes

| File | Name | Kind | Lines | Calls / Contains |
|------|------|------|-------|-----------------|
| repair_loop.py | RepairPlan | dataclass | 22-28 | proposal_id, problem_context, attempt, max_retries, failure_history, rollback_manifest |
| repair_loop.py | RepairLoop | class | 30-215 | __init__, register_verifier, run, _build_feed_prompt, _feed, _verify, _rollback, _restore_backup, _classify_failure, _set_manifest_status, _load_manifest, _save_manifest |
| repair_loop.py | record_rollback_entry | function | 218-249 | Module-level helper for rollback manifest writes |
| idle_inspector.py | IdleInspector | class | ~50-800 | inspect, inspect_line_b, _check_tool_failures, _check_disk_usage, _check_channel_health, _check_memory_usage, _check_correction_patterns, _check_memory_health, _inspect_all_phases, _inspect_line_c, ignore_tool, is_tool_ignored |
| idle_inspector.py | _check_tool_failures | method | ~700-799 | Generates problem_context dict, creates Proposal with actions=None |
| proposal.py | Proposal | dataclass | 50-64 | type, title, problem, benefit, target, actions (optional), problem_context (new), status |
| proposal.py | ProposalStore | class | 67-186 | _load, _save, add, get, update_status, has_pending_for_target, list_pending, list_by_status, count_pending, has_pending, all_proposals, get_pending_proposal_text |
| proposal.py | ProposalExecutor | class | 189-296 | execute (runs action sequences), _summarize helper |
| management.py | ManagementHandler | class | 43-884 | handle, ~20 handler methods; _handle_proposal_approve, _inject_proposal_result, _handle_proposal_ignore, _handle_qr_login, _handle_poll_qr, etc. |
| management.py | _tool_failure_verifier | async function | 889-900 | Independent verifier for RepairLoop (returns True + message) |
| tools.py | AtomicTools | class | 109-1580 | 30+ tool implementations; _backup_file, _record_change, _record_rollback_entry (new), _clear_pycache, _restore_backup, do_self_improve, do_file_write, do_file_patch, do_rollback_change |
| tools.py | _record_rollback_entry | method | 207-231 | Writes file edits to rollback_manifest.json via repair_loop.record_rollback_entry |
| tests/test_idle_inspector_integration.py | TestToolFailureIntegration | class | 17-50 | Tests problem_context creation and dedup |
| tests/test_idle_inspector_integration.py | TestFullPipeline | class | 176-230 | Tests detect→store→RepairLoop flow with mock agent |
| tests/test_idle_inspector_integration.py | TestIgnoreList | class | 298-331 | Tests ignore persistence |

## Key Data Flows

### Self-healing flow (new)
```
IdleInspector._check_tool_failures()
  → builds problem_context dict {type, tool_name, failure_count, last_error, error_history, tool_source}
  → Proposal(type=tool_fix, actions=None, problem_context=ctx)
  → ProposalStore.add(prop) → proposals.json

User approves via GUI
  → ManagementHandler._handle_proposal_approve()
  → detects problem_context
  → RepairLoop.run(plan, agent)
    → _build_feed_prompt() → Chinese self-healing prompt
    → _feed(agent, prompt) → agent.process_message() via async generator
    → _verify(plan) → registered verifier (e.g. _tool_failure_verifier)
    → on fail: retry ≤3 times with failure history injected
    → all fail: _rollback() → restore from backup files
```

### Rollback manifest flow (new)
```
AtomicTools._record_rollback_entry(filepath, desc, backup)
  → repair_loop.record_rollback_entry(data_dir, "_tool_level", change)
  → writes rollback_manifest.json

RepairLoop._rollback(plan)
  → reads rollback_manifest for proposal_id
  → restores files from backup in reverse order
  → marks status "rolled_back"
```

### Traditional proposal flow (unchanged)
```
Proposal(actions=[...])
  → ProposalExecutor.execute(proposal, handler)
  → for each action: dispatch → verify → rollback on failure
```

## Risk Areas

| Risk | Location | Description |
|------|----------|-------------|
| Agent dependency | repair_loop.py:line 148 | _feed() requires agent.process_message() — null agent causes AttributeError |
| Verifier absence | repair_loop.py:line 160 | No verifier registered → assumed success with warning message |
| Rollback silo | repair_loop.py:line 230 | Proposals created at tool level use sentinel "_tool_level" — RepairLoop may not find them |
| Manifest race | repair_loop.py:lines 240-249 | No file locking on rollback_manifest.json writes |
| Single verifier | management.py:line 892 | _tool_failure_verifier always returns True — placeholder, not real verification |
| actions=None in legacy code | proposal.py:line 222 | ProposalExecutor.execute() was hardened with `or []` but any other action consumer may break |
| asdict serialization | proposal.py:line 108 | asdict(Proposal(actions=None)) → {"actions": None} — consumers must handle None |
