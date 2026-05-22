---
commit_hash: 9118574551d66f159dd20db77e9e61d6663cc4aa
analyzed_at: 2026-05-21T09:49:00
scope: oaa/agent/*.py, oaa/auth/*.py, oaa/gateway/*.py
files_analyzed: 36
---

## Core Classes

| File | Class | Kind | Key Properties | Methods |
|------|-------|------|----------------|---------|
| oaa/app.py | OAAApp | Application | config, permissions, session_mgr, evolution, scheduler, agent, worker, gateway, desktop, channel_adapters | start(), stop(), run(), _register_channels(), _notify_desktop(), _notify_channel(), _startup_check() |
| oaa/agent/oaa_agent.py | OAAAgent | Orchestrator | config, identity, memory, llm, skill_mgr, atomic, extended, browser, search, _idle_inspector, _proposal_store, archiver | process_message(), build_handler(), build_system_prompt(), set_channel_adapters() |
| oaa/agent/loop.py | AgentLoop | Core Loop | llm, handler, tools_schema, max_turns | run(), _compact_messages(), _summarize_with_llm(), _build_turn_messages() |
| oaa/agent/handler.py | BaseHandler | Abstract | _tool_registry, _dynamic_tools | dispatch(), register_dynamic(), unregister_dynamic() |
| oaa/agent/tools.py | AtomicTools | Tool Set | data_dir, permissions, working_memory | do_shell_run, do_code_exec, do_file_write, do_file_patch, do_self_improve, do_reload_module, dispatch() + 15 more |
| oaa/agent/extended_tools.py | ExtendedTools | Tool Set | data_dir, permissions, wechat_adapter | do_wechat_send_text, do_wechat_send_file, do_skill_load, do_excel_xlsx + more |
| oaa/agent/proposal.py | ProposalStore | Store | _path, _store | add(), get(), update_status(), list_pending(), has_pending() |
| oaa/agent/proposal.py | ProposalExecutor | Executor | - | execute(proposal, handler) |
| oaa/agent/idle_inspector.py | IdleInspector | Background | scheduler, memory_mgr, evolution, proposal_store, llm | inspect(), start_background(), stop_background(), ignore_tool() |
| oaa/agent/memory_manager.py | MemoryManager | Storage | base_dir | add_to_hot(), add_correction(), search(), build_memory_prompt() |
| oaa/agent/skill_manager.py | SkillManager | Manager | skills_dir, _skills, _current | discover(), get(), switch_to(), list_all() |
| oaa/agent/skill_manager.py | SkillInfo | Data | name, category, path, skill_md, sop_md | load(), build_system_prompt() |
| oaa/agent/conversation_archiver.py | ConversationArchiver | Storage | base_dir, llm | summarize_and_archive(), search(), load_recent_summaries() |
| oaa/gateway/gateway.py | Gateway | Router | agent, session_mgr, _adapters | register_adapter(), incoming_message(), send_to_channel() |
| oaa/gateway/gateway.py | Message | Data | source, user_id, content, metadata, session_id, images | - |
| oaa/gateway/management.py | ManagementHandler | API Handler | _config, _scheduler, _skill_mgr, _evolution, _channels, _agent | handle(), set_agent_state() + 20 _handle_* methods |
| oaa/gateway/adapters/desktop.py | DesktopAdapter | WebSocket | host, port, gateway, _clients, _server, _pending_confirms | start(), stop(), _handler(), _process_chat(), notify_all() |
| oaa/auth/permissions.py | PermissionsManager | Auth | config, _trust_data, _trust_threshold | confirm_operation(), check_path(), record_tool_success(), require_confirm() |
| oaa/evolution/engine.py | EvolutionEngine | Engine | data_dir, _llm, stats | record_skill_usage(), analyze_for_suggestions(), extract_and_crystallize(), get_auto_refinements() |

## Key Functions

| File | Function | Lines | Calls | Called By |
|------|----------|-------|-------|-----------|
| app.py | OAAApp.start() | 125-180 | desktop.start(), worker.start(), scheduler.start_loop(), inspector.start_background() | OAAApp.run() |
| app.py | OAAApp._register_channels() | 92-123 | Gateway.register_adapter() | OAAApp.__init__() |
| oaa_agent.py | OAAAgent.process_message() | 343-420 | AgentLoop.run(), IdleInspector.inspect(), evolution.record_trajectory() | Gateway.incoming_message() |
| loop.py | AgentLoop.run() | 119-330 | LLMClient.chat(), handler.dispatch(), _compact_messages() | OAAAgent.process_message() |
| tools.py | AtomicTools.dispatch() | 141-146 | super().dispatch(), permissions.record_tool_success() | AgentLoop.run() |
| management.py | ManagementHandler.handle() | 76-93 | getattr dispatch to _handle_* | DesktopAdapter._handle_management() |
| desktop.py | DesktopAdapter._handler() | 145-186 | _handle_management(), _process_chat(), _resolve_confirm() | WebSocket server |
| gateway.py | Gateway.incoming_message() | 40-78 | session_mgr.*, agent.process_message() | DesktopAdapter._process_chat() |

## Routes / Endpoints

| Method | Path | File | Handler | Purpose |
|--------|------|------|---------|---------|
| WebSocket | ws://127.0.0.1:9765 | desktop.py:62-63 | DesktopAdapter._handler() | GUI communication |
| Management | "get_config" | management.py:124 | _handle_get_config() | Read config |
| Management | "save_config" | management.py:129 | _handle_save_config() | Write config |
| Management | "get_status" | management.py:99 | _handle_get_status() | Runtime status |
| Management | "get_skills" | management.py:400 | _handle_get_skills() | List skills |
| Management | "list_proposals" | management.py:553 | _handle_list_proposals() | List proposals |
| Management | "proposal_approve" | management.py:567 | _handle_proposal_approve() | Execute proposal |
| Management | "proposal_ignore" | management.py:640 | _handle_proposal_ignore() | Ignore proposal |
| Management | "get_evolution_stats" | management.py:469 | _handle_get_evolution_stats() | Stats for GUI |

## Config Model

| Dataclass | File | Key Fields |
|-----------|------|------------|
| AppConfig | config.py:62 | data_dir, model, models, wechat, dingtalk, feishu, search, permissions |
| ModelConfig | config.py:15 | provider, plan, api_format, base_url, api_key, model_id, max_tokens, temperature |
| WeChatConfig | config.py:31 | enabled, iLink_token, iLink_bot_id, base_url, wechat_cli_path |
| DingTalkConfig | config.py:41 | enabled, client_id, client_secret |
| FeishuConfig | config.py:48 | enabled, app_id, app_secret |
