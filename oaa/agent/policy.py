"""Policy Engine — lightweight runtime rule enforcement for tool calls (P4).

Rules are loaded from a skill's ``rules.json`` on activation and checked
before each tool dispatch.  Three policy types are supported:

- ``deny`` — block the tool call entirely
- ``require_confirm`` — yield a confirm_request to the frontend
- ``require_param`` — auto-add a required parameter if missing
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Policy:
    """A single enforceable policy rule."""

    type: str  # "deny" | "require_confirm" | "require_param"
    tool: str | None = None  # None = applies to all tools
    param: str | None = None  # for require_param
    default: Any = None  # default value for require_param
    reason: str = ""

    def matches(self, tool_name: str) -> bool:
        """Check if this policy applies to *tool_name*."""
        if self.tool is None:
            return True
        # Support glob patterns (e.g. "wechat_*", "dingtalk_*")
        if "*" in self.tool:
            pat = re.escape(self.tool).replace(r"\*", ".*")
            return bool(re.match(pat, tool_name))
        return self.tool == tool_name


@dataclass
class CheckResult:
    """Outcome of a policy check."""

    action: str  # "allow" | "block" | "confirm" | "modify"
    reason: str = ""
    modifications: dict[str, Any] = field(default_factory=dict)


class PolicyEngine:
    """Holds active policies and checks tool calls against them."""

    def __init__(self):
        self._policies: list[Policy] = []

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_rules(self, rules_data: dict):
        """Parse policies from a ``rules.json`` dict (supports both
        legacy string-list format and the structured ``policies`` array).

        Old format (prompt-only, skipped by engine)::

            {"rules": ["text rule"]}

        New structured format::

            {
              "rules": ["提示性规则"],
              "policies": [
                {"type": "deny", "tool": "wechat_send_file", "reason": "..."},
                {"type": "require_confirm", "tool": "email_send", ...},
                {"type": "require_param", "tool": "email_send", "param": "cc", "default": "boss@x.com"}
              ]
            }
        """
        policies_raw = rules_data.get("policies", [])
        if not policies_raw:
            # If no structured policies exist, don't try to parse string rules
            # as policies — they're just prompt text.
            return

        for entry in policies_raw:
            if not isinstance(entry, dict):
                continue
            self._policies.append(Policy(
                type=entry.get("type", ""),
                tool=entry.get("tool"),
                param=entry.get("param"),
                default=entry.get("default"),
                reason=entry.get("reason", ""),
            ))

    def clear(self):
        """Remove all active policies."""
        self._policies.clear()

    # ------------------------------------------------------------------
    # Checking
    # ------------------------------------------------------------------

    def check(self, tool_name: str, args: dict) -> CheckResult:
        """Run *tool_name* and *args* through all active policies.

        Priority order: ``deny`` > ``require_confirm`` > ``require_param``.
        - ``deny`` rules are scanned first — if any deny matches, the call is blocked
          regardless of other rules.
        - Then ``require_confirm`` and ``require_param`` are evaluated (first match wins).

        Returns a ``CheckResult`` with action ``"allow"``, ``"block"``,
        ``"confirm"``, or ``"modify"`` (modifications are merged into args).
        """
        # Round 1: deny rules have highest priority
        for p in self._policies:
            if p.type == "deny" and p.matches(tool_name):
                return CheckResult(
                    action="block",
                    reason=p.reason or f"策略禁止使用 {tool_name}",
                )

        # Round 2: require_confirm / require_param — first match wins
        for p in self._policies:
            if p.type != "deny" and p.matches(tool_name):
                if p.type == "require_confirm":
                    return CheckResult(
                        action="confirm",
                        reason=p.reason or f"使用 {tool_name} 前需要确认",
                    )

                if p.type == "require_param" and p.param:
                    if p.param not in args or not args.get(p.param):
                        mods = {p.param: p.default} if p.default is not None else {}
                        if mods:
                            return CheckResult(
                                action="modify",
                                reason=p.reason or f"自动补充参数 {p.param}={p.default}",
                                modifications=mods,
                            )
                        return CheckResult(
                            action="block",
                            reason=p.reason or f"缺少必要参数 {p.param}",
                        )

        return CheckResult(action="allow")
