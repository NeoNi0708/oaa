# Findings — OAA Architecture Review

Ranked by priority score (severity × 0.4 + confidence × 0.2 + consensus × 0.4).

---

## Finding 1: Plaintext credentials in config.json — entire credential store exposed

**Severity:** CRITICAL
**Confidence:** HIGH
**Location:** `oaa/config.py:20-21, 77-81`, `oaa/gateway/management.py:124-127`
**Consensus:** 5/5 personas

**Evidence:**
7+ credential fields (LLM API keys, DingTalk client_secret, Feishu app_secret, WeChat iLink_token, Tavily/Exa/AnySearch API keys) stored in `~/OAA/config.json` via `json.dump(asdict(self))` with default OS file permissions. `_handle_get_config` returns all credentials unredacted to any WebSocket client. Combined with no WebSocket authentication (Finding 2), any local process can exfiltrate all cloud service credentials.

**Recommendation:**
- Encrypt credentials at rest (OS keychain or encrypted JSON envelope)
- Redact credential fields in management API responses (return `"********"`)
- Restrict config.json permissions to user-only (`os.chmod(0o600)`)
- Add per-session WebSocket authentication token

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm | Upgraded from HIGH after SA debate on attack chain |
| Security Analyst | confirm | Vector amplified by SA-1 (no WebSocket auth) |
| Performance Engineer | confirm | Storage redundancy affects config write performance |
| Reliability Engineer | confirm | File corruption risk means total credential loss |
| Devil's Advocate | confirm | Concede with conditions: personal workstation risk lower than shared env |

**Debate Log:** [Round 1, SA challenge to AR-5](./persona-debates.md#round-1)

---

## Finding 2: No authentication on WebSocket management interface

**Severity:** CRITICAL
**Confidence:** HIGH
**Location:** `oaa/gateway/adapters/desktop.py:62-63, 127-128`
**Consensus:** 5/5 personas (DA: conditional downgrade)

**Evidence:**
`DesktopAdapter` binds to `127.0.0.1:9765` and accepts any WebSocket connection without authentication, origin check, or TLS. Any local process can invoke management operations (`save_config`, `switch_model`, `proposal_approve`, `reconnect_channel`). In WSL2/Docker configurations, localhost can be exposed to network-accessible attackers.

**Recommendation:**
- Add origin header validation
- Implement per-session shared-secret token handshake
- In WSL2/Docker: bind to Unix socket or add mandatory authentication

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm | No input validation before dispatch (AR-6) |
| Security Analyst | confirm | Primary attack vector for all other exploits |
| Performance Engineer | confirm | Low overhead to add token check |
| Reliability Engineer | confirm | Port conflict handling also missing (RE-6) |
| Devil's Advocate | dispute | Standard localhost-only is sufficient for single-user desktop; upgrade for WSL2/Docker |

**Debate Log:** [Round 2, DA challenge to consensus](./persona-debates.md#round-2)

---

## Finding 3: Arbitrary shell command execution with auto-approval by default

**Severity:** CRITICAL
**Confidence:** HIGH
**Location:** `oaa/agent/tools.py:845-846`, `oaa/auth/permissions.py:94-98`
**Consensus:** 4/5 personas

**Evidence:**
`do_shell_run` passes LLM-generated `command` to `asyncio.create_subprocess_shell()` with zero sanitization. Default permission level is `"auto"` which always returns True for dangerous operations. `self_improve` `verify` parameter (tools.py:648-652) provides a secondary shell execution path bypassing `shell_run` permission check. The recovery hints in `loop.py:44-49` ("完成任务是第一优先级") incentivize autonomous tool use.

**Recommendation:**
- Change default permission level to `"restrict"`
- Add command allowlist (block `rm -rf`, `curl|bash`, `sudo`)
- Remove or sandbox `verify` parameter from `self_improve`
- Use `create_subprocess_exec` instead of `create_subprocess_shell`

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm | Permission bypass in proposal execution (AR-3) |
| Security Analyst | confirm | Direct path to arbitrary OS execution |
| Performance Engineer | confirm | Subprocess overhead negligible |
| Reliability Engineer | confirm | No error handling on failed shell commands |
| Devil's Advocate | abstain | Technical risk clear; disputing severity would be contrived |

---

## Finding 4: Dynamic tools dispatch path fully broken

**Severity:** HIGH
**Confidence:** HIGH
**Location:** `oaa/agent/oaa_agent.py:59-63`, `oaa/agent/extended_tools.py:18`
**Consensus:** 5/5 personas

**Evidence:**
`_MergedHandler.__getattr__` accesses `backend._tool_registry` on `ExtendedTools` which is a plain class without `_tool_registry` attribute. This raises `AttributeError` that propagates out of `__getattr__`, causing `hasattr()` in `BaseHandler.dispatch()` to skip method resolution entirely. The `_dynamic_tools` fallback loop at line 65-66 is never reached. All runtime-created dynamic tools (`register_dynamic()`) are completely undispatchable. Additionally, `AtomicTools.dispatch()` override (tools.py:141-146) is never called because `BaseHandler.dispatch()` → `getattr()` → `method(args)` bypasses it — making trust tracking (DA-1) dead code.

**Recommendation:**
- Replace `backend._tool_registry` with `getattr(backend, '_tool_registry', {})`
- Add instance-level `self._dynamic_tools = {}` to `_MergedHandler.__init__`
- Move `record_tool_success()` into individual `do_*` methods or fix dispatch chain

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm | More severe than AR-2 getattr fragility — this is fully broken |
| Security Analyst | confirm | Eliminates entire dynamic tool feature |
| Performance Engineer | confirm | Dead code imposes no performance cost |
| Reliability Engineer | confirm | Feature advertised as working but never tested |
| Devil's Advocate | confirm | Original finding; confirmed by all paths traced |

**Debate Log:** [Round 1, AR challenge to DA-2](./persona-debates.md#round-1)

---

## Finding 5: ManagementHandler 20+ handlers with fragile getattr dispatch

**Severity:** HIGH
**Confidence:** HIGH
**Location:** `oaa/gateway/management.py:76-93`, `oaa/gateway/management.py:23-39`
**Consensus:** 5/5 personas

**Evidence:**
`ManagementHandler` (835 lines) uses `getattr(self, f"_handle_{msg_type}")` for dynamic dispatch. No compile-time or import-time verification that handlers exist for declared types. File spans config, tasks, skills, evolution, proposals, QR login, channel reconnect — violating SRP across 6 domains.

**Recommendation:**
- Replace `getattr` with explicit registry dict
- Split into domain-specific handler classes (`ConfigHandler`, `EvolutionHandler`, etc.)

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm | Primary architectural debt, more urgent than OAAAgent |
| Security Analyst | confirm | getattr allows calling unintended handlers via crafted msg_type |
| Performance Engineer | confirm | Reflection overhead per dispatch (related to PE-7) |
| Reliability Engineer | confirm | Missing handler produces silent "No handler" error |
| Devil's Advocate | confirm | Agreed this is higher priority than OAAAgent refactoring |

**Debate Log:** [Round 2, DA challenge to AR-1](./persona-debates.md#round-2)

---

## Finding 6: Synchronous file I/O blocks async event loop on hot paths

**Severity:** HIGH
**Confidence:** HIGH
**Location:** `oaa/auth/permissions.py:178-187`, `oaa/evolution/engine.py:49-52`, `oaa/agent/memory_manager.py:57-61`, `oaa/agent/proposal.py:90-96`
**Consensus:** 5/5 personas

**Evidence:**
4 independent JSON persistence systems (`_save_trust()`, `_save_stats()`, `add_to_hot()`, `_save()`) perform synchronous `json.dump()` + `open().write()` on every mutation. Tool dispatch chain triggers `record_tool_success()` → `_save_trust()` on every successful tool execution. Evolution engine writes full stats on every skill usage. Memory manager reads-modifies-writes entire files on every hot memory append (3x file I/O per call). All on the main async event loop thread.

**Recommendation:**
- Batch all JSON writes into a periodic flush timer (e.g., 30s interval)
- Use dirty-flag pattern: write only when state changed, not on every call
- Convert `threading.Lock` + sync SQLite to `asyncio.Lock` + `aiosqlite`

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm | Compounding factor in agent loop blocking |
| Security Analyst | confirm | File I/O errors can corrupt trust data mid-write |
| Performance Engineer | confirm | Primary finding; 6 separate instances identified |
| Reliability Engineer | confirm | No concurrency locks compound corruption risk |
| Devil's Advocate | confirm | Non-controversial: deferred writes are safe for non-critical state |

**Debate Log:** [Round 1, PE challenge to RE-4](./persona-debates.md#round-1)

---

## Finding 7: Credentials returned unredacted to GUI and WebSocket clients

**Severity:** HIGH
**Confidence:** HIGH
**Location:** `oaa/gateway/management.py:124-127, 264-270`
**Consensus:** 5/5 personas

**Evidence:**
`_handle_get_config` returns full `AppConfig` including all API keys as unredacted fields via `asdict(self._config)`. `_handle_get_models` explicitly includes `api_key` for every model entry. Both responses are sent over unauthenticated WebSocket (Finding 2).

**Recommendation:**
- Implement redacted serialization that masks credential fields to `"********"` in management responses
- Accept only masked updates from GUI; require separate full-credential entry flow

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm | Part of AR-5 credential exposure chain |
| Security Analyst | confirm | Combined with SA-1, any local process reads all keys |
| Performance Engineer | abstain | Not a performance concern |
| Reliability Engineer | confirm | Accidental credential exposure via logging/replay |
| Devil's Advocate | confirm | Simple fix with high impact |

---

## Finding 8: Agent pipeline lacks outer timeout — can block channels for 105 minutes

**Severity:** HIGH
**Confidence:** HIGH
**Location:** `oaa/gateway/gateway.py:64`, `oaa/agent/loop.py:119`
**Consensus:** 4/5 personas

**Evidence:**
No `asyncio.wait_for` around the `async for chunk in self.agent.process_message(...)` loop. With `max_turns=70` and per-turn LLM timeout of 90s, worst-case is 105 minutes blocking all channels. Desktop management requests remain responsive (same event loop), but WeChat/DingTalk/Feishu chat is queued.

**Recommendation:**
- Wrap `process_message` await with `asyncio.wait_for(..., timeout=300)`
- Add configurable hard-limit to cumulative agent loop duration

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm | Standard resilience pattern |
| Security Analyst | confirm | Long-running LLM loop = DoS vector for other channels |
| Performance Engineer | abstain | Real blocking is sync I/O between turns (PE-1), not LLM turns |
| Reliability Engineer | confirm | Primary finding; demonstrable worst-case |
| Devil's Advocate | confirm | Tangible user-facing impact |

---

## Finding 9: Autonomous proposal generation + execution bypasses permission system

**Severity:** HIGH
**Confidence:** MEDIUM
**Location:** `oaa/agent/idle_inspector.py:174-235`, `oaa/agent/proposal.py:194-283`, `oaa/agent/oaa_agent.py:314-315`
**Consensus:** 4/5 personas

**Evidence:**
IdleInspector generates proposals with executable tool actions (`shell_run`, `self_improve`). ProposalExecutor dispatches via `handler.dispatch()` (bypassing `AtomicTools.dispatch()` trust tracking). System prompt instructs agent: "看到待处理提案时：用 proposal_list 查看 → proposal_approve 执行 → 不需要先问用户是否执行". In `"auto"` permission mode, all tool confirmations return True.

**Recommendation:**
- Add `proposal_origin` flag that forces confirmation even in "auto" mode for dangerous tools
- Require explicit user opt-in before starting IdleInspector background loop
- Add rate limit for proposal generation

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm | Autonomous permission bypass (AR-3) |
| Security Analyst | confirm | Self-modification pipeline triggered without consent (SA-8) |
| Performance Engineer | confirm | Background I/O cascade compounds event loop blocking |
| Reliability Engineer | confirm | Combined with RE-7 (task cancel), mid-write corruption risk |
| Devil's Advocate | dispute | In practice, proposals still need agent approval step before execution |

---

## Finding 10: Trust tracking is dead code — counter never increments

**Severity:** HIGH
**Confidence:** HIGH
**Location:** `oaa/agent/tools.py:141-146`, `oaa/agent/oaa_agent.py:47-67`
**Consensus:** 4/5 personas

**Evidence:**
`AtomicTools.dispatch()` (tools.py:141-146) overrides `dispatch()` to call `record_tool_success()`, but it is never invoked. `BaseHandler.dispatch()` → `getattr(self, method_name)` → `__getattr__` calls the method directly without routing through `AtomicTools.dispatch()`. `PermissionsManager._trust_data` is never incremented. Trust-threshold mechanism permanently returns False — `"confirm"` level never skips any confirmation.

**Recommendation:**
Inject `record_tool_success()` into `_MergedHandler.__init__` as a post-dispatch hook, or call it from each `do_*` method individually.

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm | Explains why AR-8 (trust expiry) is currently moot |
| Security Analyst | confirm | Finding partially invalidates SA-7 (trust accumulation risk) |
| Performance Engineer | confirm | Dead code has no performance impact |
| Reliability Engineer | confirm | Feature advertised but non-functional |
| Devil's Advocate | confirm | Original finding |

**Debate Log:** [Round 1, DA challenge to SA-7](./persona-debates.md#round-1)

---

## Finding 11: SessionManager sync SQLite + threading.Lock blocks event loop

**Severity:** HIGH
**Confidence:** HIGH
**Location:** `oaa/session/manager.py:36, 146-162`, callers at `oaa/gateway/gateway.py:46-58`
**Consensus:** 4/5 personas

**Evidence:**
`threading.Lock` (not `asyncio.Lock`) protects synchronous SQLite operations. Gateway.incoming_message() calls 3 session operations sequentially before any async processing, each blocking the event loop. Multi-channel scenario serializes all messages on `threading.Lock`. Combined with RE-7 (`_signal_stop()` brute-force task cancel), a cancellation mid-write can leave the lock held in a cancelled coroutine.

**Recommendation:**
- Replace `threading.Lock` with `asyncio.Lock`
- Use `aiosqlite` for native async SQLite operations
- Implement graceful shutdown that waits for in-flight session writes

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm | Architectural coupling between sync and async layers |
| Security Analyst | abstain | Not a security concern |
| Performance Engineer | confirm | Primary finding; measurable event loop blocking |
| Reliability Engineer | confirm | Combined with RE-7: cancellation deadlock risk |
| Devil's Advocate | confirm | Non-controversial technical debt |

---

## Finding 12: self_improve only validates syntax, not semantics, of LLM-generated patches

**Severity:** HIGH
**Confidence:** MEDIUM
**Location:** `oaa/agent/tools.py:674-696`
**Consensus:** 4/5 personas

**Evidence:**
`do_self_improve` applies LLM-generated code patches with only `ast.parse()` syntax validation. No unit tests, type checks, or integration tests before `importlib.reload()`. A patch with undefined names, type mismatches, or infinite loops corrupts the running process. Backup exists but no automatic fallback on runtime failure — only on syntax/verify command failure.

**Recommendation:**
- Stage patches in a copy, import in subprocess, run diagnostics before live swap
- Add circuit breaker: auto-rollback on first runtime error in patched module

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm | Self-modification should have guard gates beyond syntax |
| Security Analyst | confirm | LLM-controlled code injection path |
| Performance Engineer | abstain | Not performance-related |
| Reliability Engineer | confirm | Process corruption requires manual recovery |
| Devil's Advocate | confirm | Original finding |

---

## Finding 13: OAAAgent god object — SRP violation with 15+ dependencies

**Severity:** HIGH
**Confidence:** HIGH
**Location:** `oaa/agent/oaa_agent.py:73-146`
**Consensus:** 4/5 personas (DA: downgraded)

**Evidence:**
OAAAgent injects 15+ dependencies in `__init__()` (77 lines), acting as DI container, system-prompt builder, message pipeline orchestrator, and channel-status provider simultaneously. A change to any responsibility risks breaking others.

**Recommendation:**
Split into `SystemPromptBuilder`, `MessagePipeline`, and `ChannelStatusReporter` classes. OAAAgent becomes a thin facade. Note: ManagementHandler (835 lines) is a more urgent refactoring target.

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm | Primary finding; 77-line constructor is a SRP violation |
| Security Analyst | abstain | Not security-critical |
| Performance Engineer | confirm | Large __init__ delays agent construction |
| Reliability Engineer | confirm | Many dependencies = many failure points |
| Devil's Advocate | dispute | Pragmatic in single-agent architecture; ManagementHandler is worse |

**Debate Log:** [Round 2, DA challenge to AR-1](./persona-debates.md#round-2)

---

## Finding 14: No input validation on WebSocket payloads before dispatch

**Severity:** MEDIUM
**Confidence:** HIGH
**Location:** `oaa/gateway/adapters/desktop.py:153-170`, `oaa/gateway/management.py:76-93`
**Consensus:** 5/5 personas

**Evidence:**
Only `json.loads()` validation — no schema validation for any message type. Malformed payloads propagate to `ManagementHandler.handle()` (may raise `AttributeError`) or `Gateway.incoming_message()`. `_handle_save_config` blindly merges every field from `payload.get("config", {})` without type checking.

**Recommendation:**
Add Pydantic model or JSON Schema validation per `msg_type` at the WebSocket boundary.

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm | Defense in depth; first line of defense missing |
| Security Analyst | confirm | Type confusion attacks via crafted payloads |
| Performance Engineer | abstain | Validation overhead negligible |
| Reliability Engineer | confirm | Malformed payloads cause confusing errors |
| Devil's Advocate | confirm | Basic input validation is standard practice |

---

## Finding 15: GUI confirmation for unknown request_id silently discarded

**Severity:** MEDIUM
**Confidence:** MEDIUM
**Location:** `oaa/gateway/adapters/desktop.py:192-198`
**Consensus:** 5/5 personas

**Evidence:**
`_resolve_confirm()` does `self._pending_confirms.get(request_id)` — if unknown, returns silently with no log. GUI believes confirmation was processed; agent permissions system hangs until 60s timeout. Invisible in production.

**Recommendation:**
Log `logger.warning` for unknown request_id. Return error response to WebSocket.

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm | Poor error reporting (AR not directly covering this) |
| Security Analyst | confirm | Silent failure hides potential attack attempts |
| Performance Engineer | abstain | Not performance-related |
| Reliability Engineer | confirm | Primary finding; invisible failure |
| Devil's Advocate | confirm | Simple fix, high debuggability impact |

---

## Finding 16: JSON persistence has no concurrency locks — corruption risk

**Severity:** MEDIUM
**Confidence:** HIGH
**Location:** `oaa/agent/proposal.py:90-96`, `oaa/auth/permissions.py:178-187`
**Consensus:** 4/5 personas

**Evidence:**
ProposalStore._save() and PermissionsManager._save_trust() write JSON files without any file-level or process-level lock. Simultaneous writes interleave (IdleInspector writing proposals while ManagementHandler updates status). No atomic write pattern (write to temp + rename).

**Recommendation:**
Add `asyncio.Lock` to both stores. Use atomic write pattern. SessionManager already uses `threading.Lock` — follow that pattern but with async lock.

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm | Standard file-level protection missing |
| Security Analyst | confirm | Corrupted trust data could enable unauthorized tool access |
| Performance Engineer | confirm | Lock contention adds latency (but necessary) |
| Reliability Engineer | confirm | Primary finding; demonstrated crash scenarios |
| Devil's Advocate | dispute | In practice, sequential dispatch + 600s interval makes race window tiny |

---

## Finding 17: Signal handler brute-force cancels all tasks, risking data loss

**Severity:** MEDIUM
**Confidence:** HIGH
**Location:** `oaa/app.py:311-312`
**Consensus:** 4/5 personas

**Evidence:**
`_signal_stop()` calls `task.cancel()` on `asyncio.all_tasks()` indiscriminately. Mid-write JSON/ProposalStore operations receive `CancelledError` inside `json.dump()`, leaving truncated files. After restart, corrupted JSON crashes components (RE-3).

**Recommendation:**
Replace with graceful shutdown: cancel chat tasks first, set "shutting down" flag, give in-flight writes grace window (2s), then cancel remaining.

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm | Graceful shutdown is standard practice |
| Security Analyst | confirm | Truncated files on crash = partial state leak |
| Performance Engineer | confirm | Cancel storm adds latency at shutdown |
| Reliability Engineer | confirm | Primary finding; compounds all JSON corruption risks |
| Devil's Advocate | dispute | On SIGTERM, process needs to exit ASAP — graceful window is acceptable |

---

## Finding 18: EvolutionEngine._load_stats unhandled exception on corrupt startup

**Severity:** MEDIUM
**Confidence:** HIGH
**Location:** `oaa/evolution/engine.py:33-37`
**Consensus:** 5/5 personas (DA: downgraded)

**Evidence:**
Unlike `ProposalStore._load()` which handles `json.JSONDecodeError`, `EvolutionEngine._load_stats()` has zero error handling. Corrupt `evolution_stats.json` raises unhandled exception from `EvolutionEngine.__init__()`, called from `OAAApp.__init__()`, preventing entire application startup.

**Recommendation:**
Add try/except `(json.JSONDecodeError, OSError)` matching ProposalStore._load() pattern (3-line fix).

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm | Startup crash is highest availability impact |
| Security Analyst | abstain | Not security-relevant |
| Performance Engineer | confirm | Startup crash = zero availability |
| Reliability Engineer | confirm | Primary finding; propagate corrupt file handling |
| Devil's Advocate | dispute | Downgraded from HIGH: 3-line fix, existing pattern to follow |

---

## Finding 19: WebSocket server start has no retry on port conflict

**Severity:** MEDIUM
**Confidence:** HIGH
**Location:** `oaa/gateway/adapters/desktop.py:125-128`
**Consensus:** 5/5 personas

**Evidence:**
`await websockets.serve(self._handler, self.host, self.port)` with hardcoded port 9765 and no retry loop. Port conflict (zombie process, Electron dev server) propagates unhandled through `OAAApp.start()`, preventing app launch.

**Recommendation:**
Add retry loop (2-3 attempts, 1s delay). Log clear ERROR identifying port conflict with `netstat -ano | findstr :9765`.

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm | Basic resilience pattern |
| Security Analyst | confirm | Port conflict = DoS by another process binding the port |
| Performance Engineer | confirm | Retry overhead is negligible |
| Reliability Engineer | confirm | Primary finding; demonstrated failure scenario |
| Devil's Advocate | confirm | Non-controversial |

---

## Finding 20: Tool trust accumulation has no expiry or decay mechanism

**Severity:** MEDIUM
**Confidence:** HIGH
**Location:** `oaa/auth/permissions.py:140-143, 136-138`
**Consensus:** 3/5 personas

**Evidence:**
Integer counter per tool_name with no timestamp, TTL, cap, or decay. Once threshold crossed, confirmation permanently skipped. Trust data persists across restarts. **Note:** Currently moot because trust tracking is dead code (Finding 10). Valid as design concern if dispatch chain is fixed.

**Recommendation:**
Add per-entry timestamps with configurable TTL (e.g., 24h). Cap maximum trust count. Add decay mechanism.

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm | Design concern even if currently moot (AR-8) |
| Security Analyst | confirm | Permanent privilege escalation if fix dispatch (SA-7) |
| Performance Engineer | abstain | Not performance-related |
| Reliability Engineer | abstain | Not reliability-related |
| Devil's Advocate | dispute | Currently dead code (DA-1); design concern only |

---

## Finding 21: Shared `_dynamic_tools` class variable violates instance isolation

**Severity:** MEDIUM
**Confidence:** HIGH
**Location:** `oaa/agent/handler.py:18`, `oaa/agent/oaa_agent.py:40-46`
**Consensus:** 4/5 personas

**Evidence:**
`BaseHandler._dynamic_tools = {}` is a mutable class-level dict never shadowed at instance level. All `_MergedHandler` instances share the same dict. A dynamic tool registered on one instance is visible to all. Currently masked by single-agent architecture.

**Recommendation:**
Add `self._dynamic_tools = {}` to `_MergedHandler.__init__`.

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm | Isolation violation even if currently latent |
| Security Analyst | confirm | Cross-instance tool visibility = security boundary failure |
| Performance Engineer | confirm | Dict lookup not affected |
| Reliability Engineer | confirm | Latent bug surfaces in multi-agent scenario |
| Devil's Advocate | confirm | Original finding |

---

## Finding 22: `__getattr__` double-traverses 4 backends per dispatch — no caching

**Severity:** LOW
**Confidence:** HIGH
**Location:** `oaa/agent/oaa_agent.py:47-67`, `oaa/agent/handler.py:38-39`
**Consensus:** 5/5 personas

**Evidence:**
`BaseHandler.dispatch()` calls `hasattr()` then `getattr()`, each triggering `_MergedHandler.__getattr__` which traverses 4 backends + 4 `_tool_registry` dicts + 1 `_dynamic_tools`. 2× traversal per tool call. No caching layer — same tool name re-resolved every call.

**Recommendation:**
Add `functools.lru_cache(maxsize=128)` or `_method_cache` dict on `__getattr__`.

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm | Unnecessary overhead in hot path |
| Security Analyst | abstain | Not security-relevant |
| Performance Engineer | confirm | Primary finding; measurable overhead per dispatch |
| Reliability Engineer | confirm | N minor inefficiencies compound |
| Devil's Advocate | confirm | Low severity but easy to fix |

---

## Finding 23: No backup rotation — disk space exhaustion risk

**Severity:** LOW
**Confidence:** HIGH
**Location:** `oaa/agent/tools.py:618-619`, `oaa/agent/tools.py:80-84`
**Consensus:** 5/5 personas

**Evidence:**
Backups created on every `self_improve` with no retention policy. Unbounded growth (`_check_disk_usage` monitors but doesn't clean backups).

**Recommendation:**
Keep last N backups per file, delete backups older than T days, add disk-pressure trigger to prune oldest first.

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm | Basic resource management |
| Security Analyst | abstain | Not security-relevant |
| Performance Engineer | confirm | Disk I/O grows unbounded |
| Reliability Engineer | confirm | Disk exhaustion = complete failure |
| Devil's Advocate | confirm | Original finding |

---

## Finding 24: ConversationArchiver.search() O(n) sequential file reads — no index

**Severity:** LOW
**Confidence:** HIGH
**Location:** `oaa/agent/conversation_archiver.py:150-173`
**Consensus:** 4/5 personas

**Evidence:**
`search()` iterates all `.md` files, reads each entirely, does substring match. No inverted index or FTS. Degrades linearly with conversation growth.

**Recommendation:**
Maintain lightweight filename→title index JSON. Use FTS5 in session SQLite for full-text search.

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm | Scales poorly but not critical at current size |
| Security Analyst | abstain | Not security-relevant |
| Performance Engineer | confirm | Primary finding; measurable degradation |
| Reliability Engineer | confirm | Degrades gracefully (no crash) |
| Devil's Advocate | confirm | Premature optimization at current conversation volume |

---

## Finding 25: Channel adapters registered in two separate locations

**Severity:** LOW
**Confidence:** HIGH
**Location:** `oaa/app.py:92-123`
**Consensus:** 4/5 personas

**Evidence:**
`_register_channels()` stores each adapter in `Gateway._adapters` AND `OAAApp.channel_adapters`. No sync enforcement between the two references.

**Recommendation:**
Eliminate one of the two storage locations. Delegate app `channel_adapters` to gateway's registry.

**Persona Votes:**
| Persona | Vote | Note |
|---------|------|------|
| Architecture Reviewer | confirm | DRY violation; latent divergence risk (AR-7) |
| Security Analyst | abstain | Not security-relevant |
| Performance Engineer | abstain | Dual references → double memory + sync cost |
| Reliability Engineer | confirm | Desynchronization causes confusing routing failures |
| Devil's Advocate | dispute | In practice, references are set once at startup, never updated independently |
