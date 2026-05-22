## Hypothesis Queue

Ranked hypotheses for downstream chain consumption (scenario → debug → fix).

| Rank | ID | Hypothesis | Confidence | Location | Source Persona |
|------|----|-----------|-----------|----------|----------------|
| 1 | H-01 | Any local process can exfiltrate all cloud API keys by reading `~/OAA/config.json` or connecting to `ws://127.0.0.1:9765/get_config` | HIGH | oaa/config.py:77-81, oaa/gateway/management.py:124-127 | Security Analyst / Devil's Advocate (confirmed 5/5) |
| 2 | H-02 | LLM can execute arbitrary shell commands without user confirmation in default "auto" permission mode via `do_shell_run` or `self_improve` verify parameter | HIGH | oaa/agent/tools.py:845-846 | Security Analyst (confirmed 4/5) |
| 3 | H-03 | Runtime-created dynamic tools (`register_dynamic()`) are completely undispatchable due to unguarded `backend._tool_registry` access on `ExtendedTools` | HIGH | oaa/agent/oaa_agent.py:59-63 | Devil's Advocate (confirmed 5/5) |
| 4 | H-04 | Synchronous `json.dump()` + `open().write()` calls on the async event loop thread cause measurable blocking during multi-tool agent turns | HIGH | oaa/auth/permissions.py:178-187 | Performance Engineer (confirmed 5/5) |
| 5 | H-05 | `AtomicTools.dispatch()` override for trust tracking (`record_tool_success`) is never called — the entire trust-threshold mechanism is dead code | HIGH | oaa/agent/tools.py:141-146 | Devil's Advocate (confirmed 5/5) |
| 6 | H-06 | Threading.Lock in SessionManager + brute-force `all_tasks().cancel()` in signal handler causes deadlock or data corruption on shutdown | MEDIUM | oaa/session/manager.py:36, oaa/app.py:311-312 | Reliability Engineer (confirmed 4/5) |
| 7 | H-07 | `idle_inspector.py` generates autonomous `self_improve` proposals every 600 seconds, which execute without user confirmation in "auto" mode | MEDIUM | oaa/agent/idle_inspector.py:174-235 | Architecture Reviewer (confirmed 4/5) |
| 8 | H-08 | Corrupt `evolution_stats.json` (from crash mid-write) prevents entire application startup due to missing error handling in `EvolutionEngine._load_stats` | MEDIUM | oaa/evolution/engine.py:33-37 | Reliability Engineer (confirmed 5/5) |
| 9 | H-09 | Port 9765 conflict (zombie process, Electron dev server) causes entire app startup failure with no diagnostic message | MEDIUM | oaa/gateway/adapters/desktop.py:125-128 | Reliability Engineer (confirmed 5/5) |
| 10 | H-10 | WSL2 or Docker port forwarding exposes unauthenticated WebSocket to network-accessible attackers (non-code hypothesis) | MEDIUM | oaa/gateway/adapters/desktop.py:62-63 | Devil's Advocate (confirmed 3/5) |
| 11 | H-11 | ConversationArchiver.search() performance degrades linearly with number of archived conversations — no index or FTS | LOW | oaa/agent/conversation_archiver.py:150-173 | Performance Engineer (confirmed 4/5) |
| 12 | H-12 | Backups from `self_improve` accumulate unbounded — no rotation, leading to predictable disk exhaustion | LOW | oaa/agent/tools.py:618-619 | Devil's Advocate (confirmed 5/5) |
