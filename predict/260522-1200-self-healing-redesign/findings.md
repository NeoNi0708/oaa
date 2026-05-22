# Findings — self-healing-redesign

## Finding 1: Rollback manifest sentinel _tool_level prevents any rollback (CRITICAL)

**Confidence:** HIGH
**Location:** `tools.py:223`, `repair_loop.py:230`
**Consensus:** 5/5 personas

**Evidence:**
`AtomicTools._record_rollback_entry()` calls `record_rollback_entry(self.data_dir, "_tool_level", change)` — using the literal string `"_tool_level"` as the proposal_id. When RepairLoop._rollback() reads the manifest, it looks up `manifest[plan.proposal_id]` (e.g., `"prop_1779422062_1"`). The entry is under a different key — no changes are found, no rollback occurs. All self-healing file modifications become permanent.

**Recommendation:**
Thread a contextvar or explicit proposal_id from management.py → RepairLoop → AtomicTools. Set it before the repair loop runs and clear it after.

---

## Finding 2: Agent's judgment quality and effort level — not capability boundaries (INFO)

**Confidence:** N/A — corrected after review

**Location:** design architecture
**Consensus:** 3/5 personas originally; **corrected after user review**

**Original finding (incorrect):**
The architecture assumes feeding a rich prompt to agent.process_message() will result in a successful fix. But the previous system demonstrated that agents struggle with non-code failures (missing `wechat-cli` binary on Windows). The feed prompt is well-designed but doesn't change the agent's fundamental capability boundaries.

**Correction:**
This is NOT a capability boundary issue — it is an issue of **judgment quality and effort level**:

- `wechat_contacts` failing is because the agent chose the wrong approach (should have used iLink as alternative), not because it couldn't install `wechat-cli`
- All CLI tools in the OAA ecosystem are designed for AI consumption — the agent can install them via `shell_run`
- Cross-platform compatibility is solvable once the agent correctly identifies its own platform
- Services that require user registration/providing keys should be handled as: agent explains what's needed → user provides → agent completes the rest

**Action taken:**
`repair_loop._build_feed_prompt` constraints section rewritten with anti-laziness guardrails:
1. Use existing tools first before attempting anything new
2. When existing tools don't suffice, try installing/searching before giving up
3. Do everything possible on your own before asking the user
4. When asking the user, clearly state: what was tried, what is blocked, what the user needs to do

---

## Finding 3: _tool_failure_verifier is a stub — always returns True (MEDIUM)

**Confidence:** HIGH
**Location:** `management.py:889-900`
**Consensus:** 5/5 personas

**Evidence:**
```python
async def _tool_failure_verifier(context):
    return True, f"已确认 {tool_name} 无新失败记录"
```
The function unconditionally returns True. A failed repair is reported as successful.

**Recommendation:**
Check MemoryManager for tool failures recorded after the repair attempt's start time. Return False if new failures exist.

---

## Finding 4: Missing verifier silently passes in RepairLoop (MEDIUM)

**Confidence:** HIGH
**Location:** `repair_loop.py:155-163`
**Consensus:** 4/5 personas

**Evidence:**
When no verifier is registered for a problem_type, `_verify()` logs a warning and returns `(True, "已跳过验证")`. This makes verification opt-in with a silent-pass default.

**Recommendation:**
Return `(False, "No verifier registered")` and let the retry logic handle it. This forces verifiers to be explicitly registered.

---

## Finding 5: Management handler blocks during repair execution (MEDIUM)

**Confidence:** HIGH
**Location:** `management.py:598`
**Consensus:** 4/5 personas

**Evidence:**
`_handle_proposal_approve` calls `await repair_loop.run()`, which blocks the handler for the full duration of agent processing (30-120s). The GUI receives no status updates during this time.

**Recommendation:**
Use `asyncio.create_task()` and return immediately with `{"ok": True, "status": "running"}`. Push completion/failure via `notify_all`.

---

## Finding 6: No timeout for agent.process_message() in _feed (MEDIUM)

**Confidence:** HIGH
**Location:** `repair_loop.py:141`
**Consensus:** 4/5 personas

**Evidence:**
The async for loop over process_message has no timeout. An agent hang (LLM timeout, stuck tool) blocks indefinitely.

**Recommendation:**
Wrap in `asyncio.wait_for()` with a configurable timeout (default 300s).

---

## Finding 7: Proposal dual-mode without __post_init__ validation (MEDIUM)

**Confidence:** HIGH
**Location:** `proposal.py:50-64`
**Consensus:** 3/5 personas

**Evidence:**
Both `actions` and `problem_context` can be set simultaneously or neither. No runtime validation.

**Recommendation:**
Add `__post_init__` that validates exactly one of {actions, problem_context} is set.

---

## Finding 8: Independent verification gap still exists (MEDIUM)

**Confidence:** MEDIUM
**Location:** design → `management.py:889`
**Consensus:** 3/5 personas

**Evidence:**
The design calls for "independent verification — re-check original inspection conditions." The implementation has a verifier stub that always passes. The verification gap that motivated this redesign still exists in the code, just in a different place.

**Recommendation:**
Implement real verification functions for each problem type (tool_failure → re-call the tool; memory_health → re-check HOT.md; correction → re-check correction list).

---

## Minority Findings

None discarded — all findings reached at least "Probable" consensus.
