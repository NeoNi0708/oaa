---
commit_hash: 9118574551d66f159dd20db77e9e61d6663cc4aa
---

## Clusters

| Cluster | Files | Key Entities | External Deps | Risk Areas |
|---------|-------|-------------|---------------|------------|
| Agent Orchestration | oaa/agent/oaa_agent.py, oaa/agent/loop.py, oaa/agent/handler.py | OAAAgent, AgentLoop, BaseHandler, _tool_registry | anthropic SDK, openai SDK | God object (OAAAgent ~400 lines, 15+ dependencies), LLM retry/fallback logic, message compaction truncation |
| Tool Execution | oaa/agent/tools.py, oaa/agent/extended_tools.py | AtomicTools (20+ tools), ExtendedTools | aiohttp, openpyxl, psutil | Tool dispatch bypasses permission checks for dynamic tools (handler bypass), shell_run has no arg sanitization, self_improve writes to live module files |
| Gateway & Routing | oaa/gateway/gateway.py, oaa/gateway/management.py, oaa/gateway/__init__.py | Gateway, ManagementHandler (20+ handlers), Message | websockets | Dynamic dispatch via getattr (management.py:81), no input schema validation before dispatch, 6-constructor-dependency ManagementHandler |
| Channel Adapters | oaa/gateway/adapters/desktop.py, dingtalk.py, feishu.py, wechat_ilink.py | DesktopAdapter (WebSocket), DingTalkAdapter, FeishuAdapter, WeChatILinkAdapter | websockets, dingtalk SDK, feishu SDK, httpx | WebSocket raw JSON → no validation before dispatch, confirm callback via contextvars (fragile), adapters stored in two places (app._channels + gateway._adapters) |
| Authentication & Authorization | oaa/auth/permissions.py, oaa/auth/__init__.py | PermissionsManager, _trust_data, _trust_threshold | — (in-memory + JSON) | Trust count can wrap/overflow, dangerous_tools set maintained manually, no expiration on trust entries, confirm_operation returns bool not exception (no details) |
| Self-Evolution | oaa/evolution/engine.py, oaa/agent/proposal.py | EvolutionEngine (3 levels), ProposalStore, ProposalExecutor, Proposal | LLMClient (optional) | Auto-executed proposals no user re-confirmation, autonomous proposal generation by IdleInspector, file backup versioning limited |
| Autonomy & Monitoring | oaa/agent/idle_inspector.py, oaa/agent/proposal.py | IdleInspector, ProposalStore, ProposalExecutor | asyncio scheduler | Background auto-analysis generates proposals autonomously, no user opt-in for idle inspection, cooldown starts at launch (not first activity) |
| Memory & Persistence | oaa/agent/memory_manager.py, oaa/agent/conversation_archiver.py | MemoryManager, ConversationArchiver | LLMClient (for summarization) | In-memory working memory (hot/correction), JSON file persistence, conversation archive search is LLM-dependent (fallback to keyword), no vector index |
| Skills System | oaa/agent/skill_manager.py | SkillManager, SkillInfo | yaml (PyYAML) | No sandboxing between skills, skill switching clears in-progress state, no validation on skill SOP content |
| Application Bootstrap | oaa/app.py, oaa/config.py | OAAApp, AppConfig, ModelConfig, WeChatConfig, DingTalkConfig, FeishuConfig | pydantic, pyyaml | API keys in plaintext JSON config (config.json), no config encryption, startup sequence order-sensitive (evolution needs scheduler running) |

## Inter-Cluster Dependencies

| Source Cluster | Target Cluster | Dependency Type | Risk |
|----------------|---------------|-----------------|------|
| Application Bootstrap | All others | Constructor injection (DI container) | High — bootstrap must initialize in correct order; failure causes partial init |
| Gateway & Routing | Agent Orchestration | Runtime call (Gateway → OAAAgent.process_message) | Medium — agent crash drops all channels simultaneously |
| Autonomy & Monitoring | Self-Evolution | IdleInspector → EvolutionEngine.analyze_for_suggestions | Medium — background loop drives autonomous change |
| Autonomy & Monitoring | Tool Execution | IdleInspector → ProposalExecutor → handler.dispatch | High — autonomous proposal execution via production tools |
| Gateway & Routing | Channel Adapters | Dynamic dispatch by msg_type | Medium — reflection-based dispatch bypasses type checking |
| Agent Orchestration | Tool Execution | AgentLoop.run → handler.dispatch → tool | Low — well-defined call chain |
| Agent Orchestration | Memory & Persistence | OAAAgent ↔ MemoryManager, ConversationArchiver | Low — well-separated storage layer |

## Shared State

| State | Location | Readers | Writers | Risk |
|-------|----------|---------|---------|------|
| Config (AppConfig) | oaa/config.py → config.json | All components (via OAAApp._config) | ManagementHandler._handle_save_config | Concurrent read/write during hot-reload |
| Trust data (.tool_trust.json) | oaa/auth/permissions.py | PermissionsManager | PermissionsManager (record_tool_success/failure) | JSON file race on concurrent tool completion |
| Proposals (proposals.json) | oaa/agent/proposal.py | ManagementHandler, IdleInspector | IdleInspector (add), ManagementHandler (update_status) | Concurrent proposal status updates |
| Backups directory | tools.data_dir/backups/ | AtomicTools (rollback), EvolutionEngine | AtomicTools (self_improve) | Disk space exhaustion from unlimited backups |
