# oaa/agent/complexity_evaluator.py
import re
from dataclasses import dataclass, field


@dataclass
class RouteDecision:
    route: str          # "local" | "cloud"
    score: float        # -1.0 ~ 1.0
    reasons: list[str] = field(default_factory=list)
    override: bool = False  # P0 强制路由


class ComplexityEvaluator:
    """优先级路由引擎 — 根据关键词/规则判断请求走本地还是云端。

    优先级链:
      P0: @local/@cloud → 强制路由
      P1: 本地关键词 → +0.6（单条封顶）
      P2: 云端分析类 → -0.5（可叠加）
      P3: 云端创作类 → -0.3（可叠加）
      P4: 外部知识 → -0.5 / 步骤模式 → -0.3
      Session黑名单 → 强制 cloud
      P6: score > +0.3 → local, else cloud
    """

    def __init__(self, config: dict):
        self._threshold = float(config.get("confidence_threshold", 0.3))
        self._local_kw = config.get("keywords_local", [])
        self._cloud_analysis = config.get("keywords_cloud_analysis", [])
        self._cloud_creation = config.get("keywords_cloud_creation", [])
        self._cloud_external = config.get("keywords_cloud_external", [])
        self._step_patterns = config.get("keywords_step", [])
        self._override_re = re.compile(r"@(local|cloud)\b")
        self._compiled_steps = [re.compile(p, re.IGNORECASE) for p in self._step_patterns]
        # Session 黑名单（不持久化）
        self._session_blacklist: list[str] = []

    def evaluate(self, text: str) -> RouteDecision:
        # P0: 检查显式指令
        override_m = self._override_re.search(text)
        if override_m:
            return RouteDecision(
                route=override_m.group(1),
                score=1.0 if override_m.group(1) == "local" else -1.0,
                override=True,
                reasons=[f"用户显式指定: @{override_m.group(1)}"],
            )

        # Session 黑名单
        clean = text.lower()
        for pattern in self._session_blacklist:
            if pattern in clean:
                return RouteDecision(
                    route="cloud", score=-1.0,
                    reasons=[f"命中 session 黑名单: {pattern}"],
                )

        score = 0.0
        reasons = []

        # P1: 本地关键词
        if any(kw in clean for kw in self._local_kw):
            score += 0.6
            reasons.append("本地关键词命中")

        # P2: 云端分析类
        analysis_hits = [kw for kw in self._cloud_analysis if kw in clean]
        if analysis_hits:
            score -= 0.5
            reasons.append(f"分析类关键词: {','.join(analysis_hits[:3])}")

        # P3: 云端创作类
        creation_hits = [kw for kw in self._cloud_creation if kw in clean]
        if creation_hits:
            score -= 0.3
            reasons.append(f"创作类关键词: {','.join(creation_hits[:3])}")

        # P4a: 外部知识
        external_hits = [kw for kw in self._cloud_external if kw in clean]
        if external_hits:
            score -= 0.5
            reasons.append(f"外部知识: {','.join(external_hits[:3])}")

        # P4b: 步骤模式
        step_matched = any(p.search(clean) for p in self._compiled_steps)
        if step_matched:
            score -= 0.3
            reasons.append("步骤模式匹配")

        route = "local" if score > self._threshold else "cloud"
        return RouteDecision(route=route, score=round(score, 2), reasons=reasons)

    def record_correction(self, text: str):
        """用户 @cloud 纠正时记录到 session 黑名单。取前 20 字作为模式。"""
        clean = text.lower().strip()
        if len(clean) > 20:
            clean = clean[:20]
        if clean not in self._session_blacklist:
            self._session_blacklist.append(clean)
