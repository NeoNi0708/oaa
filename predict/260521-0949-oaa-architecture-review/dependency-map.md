---
commit_hash: 9118574551d66f159dd20db77e9e61d6663cc4aa
---

## Import Graph (Key Dependencies)

| File | Imports From | Symbols |
|------|-------------|---------|
| oaa/app.py | oaa.agent.oaa_agent | OAAAgent |
| oaa/app.py | oaa.agent.worker | WorkerAgent |
| oaa/app.py | oaa.auth.permissions | PermissionsManager |
| oaa/app.py | oaa.evolution.engine | EvolutionEngine |
| oaa/app.py | oaa.gateway.gateway | Gateway |
| oaa/app.py | oaa.gateway.management | ManagementHandler |
| oaa/app.py | oaa.gateway.adapters.desktop | DesktopAdapter |
| oaa/app.py | oaa.gateway.adapters.dingtalk | DingTalkAdapter |
| oaa/app.py | oaa.gateway.adapters.feishu | FeishuAdapter |
| oaa/app.py | oaa.gateway.adapters.wechat_ilink | WeChatILinkAdapter |
| oaa/app.py | oaa.config | AppConfig |
| oaa/app.py | oaa.scheduler | TaskScheduler |
| oaa/app.py | oaa.session.manager | SessionManager |
| oaa/agent/oaa_agent.py | oaa.agent.loop | AgentLoop |
| oaa/agent/oaa_agent.py | oaa.agent.tools | AtomicTools |
| oaa/agent/oaa_agent.py | oaa.agent.extended_tools | ExtendedTools |
| oaa/agent/oaa_agent.py | oaa.agent.browser_tools | BrowserTools |
| oaa/agent/oaa_agent.py | oaa.agent.handler | BaseHandler |
| oaa/agent/oaa_agent.py | oaa.agent.skill_manager | SkillManager |
| oaa/agent/oaa_agent.py | oaa.agent.memory_manager | MemoryManager |
| oaa/agent/oaa_agent.py | oaa.agent.conversation_archiver | ConversationArchiver |
| oaa/agent/oaa_agent.py | oaa.agent.idle_inspector | IdleInspector |
| oaa/agent/oaa_agent.py | oaa.llm | LLMClient |
| oaa/agent/oaa_agent.py | oaa.evolution.engine | EvolutionEngine |
| oaa/agent/oaa_agent.py | oaa.auth.permissions | PermissionsManager |
| oaa/agent/loop.py | oaa.llm | LLMClient, LLMResponse |
| oaa/gateway/gateway.py | oaa.agent.oaa_agent | OAAAgent |
| oaa/gateway/gateway.py | oaa.session.manager | SessionManager |
| oaa/gateway/management.py | oaa.agent.proposal | ProposalExecutor |
| oaa/agent/tools.py | oaa.auth.permissions | PermissionsManager |
| oaa/agent/tools.py | oaa.agent.handler | BaseHandler |
| oaa/agent/extended_tools.py | oaa.auth.permissions | PermissionsManager |
| oaa/agent/idle_inspector.py | oaa.agent.proposal | Proposal, ProposalStore |
| oaa/agent/idle_inspector.py | oaa.evolution.engine | EvolutionEngine |
| oaa/evolution/engine.py | (optional) oaa.llm | LLMClient |

## Circular Dependencies (POTENTIAL ISSUES)

| Cycle | Path | Risk |
|-------|------|------|
| app.py ↔ oaa_agent.py ↔ gateway.py ↔ management.py | app→OAAAgent→Gateway→app (via DesktopAdapter) | Moderate — not a direct module cycle, but tight bidirectional references at runtime |
| tools.py ↔ memory_manager.py | tools imports MemoryManager at runtime in read_own_source | Low — runtime lazy import |
| idle_inspector.py ↔ proposal.py | inspector creates proposals, proposal references handler | Low — one directional |

## Call Graph (Architecture-Level)

| Caller | Callee | File:Line | Pattern |
|--------|--------|-----------|---------|
| OAAApp.__init__ | PermissionsManager() | app.py:43 | Constructor injection |
| OAAApp.__init__ | OAAAgent() | app.py:47 | Constructor injection |
| OAAApp.__init__ | Gateway() | app.py:52 | Constructor injection |
| OAAApp.__init__ | DesktopAdapter() | app.py:55 | Constructor injection |
| OAAApp.__init__ | ManagementHandler() | app.py:78-87 | Constructor injection |
| OAAApp.start() | desktop.start(), worker.start() | app.py:127-142 | Lifecycle management |
| DesktopAdapter._handler() | ManagementHandler.handle() | desktop.py:169 | Dynamic dispatch via msg_type |
| DesktopAdapter._handler() | Gateway.incoming_message() | desktop.py:179 | Via _process_chat |
| Gateway.incoming_message() | OAAAgent.process_message() | gateway.py:64 | Agent pipeline |
| OAAAgent.process_message() | AgentLoop.run() | oaa_agent.py:381 | Core loop |
| AgentLoop.run() | handler.dispatch() | loop.py:249 | Tool execution |
| ManagementHandler.handle() | _handle_* methods | management.py:81-83 | Dynamic dispatch via getattr |
| ManagementHandler._handle_proposal_approve() | ProposalExecutor.execute() | management.py:587 | Executes proposal |

## Data Flows

| Source | Transform | Sink | Risk Areas |
|--------|-----------|------|------------|
| WebSocket raw JSON | json.loads | DesktopAdapter._handler() | No input validation before dispatch |
| User chat input | AgentLoop.run() → LLM → tool dispatch | Tools (shell_run, file_write, etc.) | No content sanitization on chat input |
| GUI config save | _handle_save_config() → config merge | config.json on disk | API keys in plaintext JSON |
| Proposal approval | ProposalExecutor → handler.dispatch() | self_improve, shell_run, code_exec | No user re-confirmation on auto-executed proposals |
| IdleInspector inspection | inspect() → proposal_store.add() | Proposal system | Proposals generated autonomously |
| Management responses | _send_response → json.dumps → websocket.send | GUI client | No PII scrubbing on management responses |
