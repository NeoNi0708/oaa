"""Model provider presets — covers major Chinese LLM providers with plan modes.

Each preset maps to an OpenAI-compatible endpoint (except Anthropic).
Plan modes:
  - api:   普通按量计费 API
  - token: 预付费 Token 包（折扣更大，部分厂商有独立 base_url）
  - coding: Coding Plan（代码场景专用，部分厂商提供独立 base_url）
"""

PROVIDER_PRESETS = [
    # ========================
    # 深度求索
    # ========================
    {
        "name": "DeepSeek · 普通API",
        "provider": "deepseek",
        "plan": "api",
        "base_url": "https://api.deepseek.com",
        "model_placeholder": "deepseek-chat",
        "api_format": "openai",
        "note": "性价比最高，无 Plan 模式，按量计费统一价",
    },
    # ========================
    # 火山引擎 / 豆包
    # ========================
    {
        "name": "豆包 (火山引擎) · 普通API",
        "provider": "volcengine",
        "plan": "api",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model_placeholder": "ep-2024xxxxx",
        "api_format": "openai",
        "note": "按量计费，有免费额度，适合国内用户",
    },
    {
        "name": "豆包 (火山引擎) · Token Plan",
        "provider": "volcengine",
        "plan": "token",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model_placeholder": "ep-2024xxxxx",
        "api_format": "openai",
        "note": "预付费 Token 包，折扣大；在火山控制台购买后使用同一 base_url",
    },
    {
        "name": "豆包 (火山引擎) · Coding Plan",
        "provider": "volcengine",
        "plan": "coding",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model_placeholder": "ep-2024xxxxx",
        "api_format": "openai",
        "note": "代码场景专用 Plan，按 Token 量阶梯计价；需先在火山控制台开通",
    },
    # ========================
    # 通义千问 / 百炼
    # ========================
    {
        "name": "通义千问 (百炼) · 普通API",
        "provider": "tongyi",
        "plan": "api",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model_placeholder": "qwen-plus",
        "api_format": "openai",
        "note": "阿里云百炼平台，按量计费，国内稳定",
    },
    {
        "name": "通义千问 (百炼) · Token Plan",
        "provider": "tongyi",
        "plan": "token",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model_placeholder": "qwen-plus",
        "api_format": "openai",
        "note": "百炼预付费资源包，折扣更大；购买后同一 base_url 自动抵扣",
    },
    {
        "name": "通义千问 (百炼) · Coding Plan",
        "provider": "tongyi",
        "plan": "coding",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model_placeholder": "qwen-coder-plus",
        "api_format": "openai",
        "note": "代码专用 Plan；推荐搭配 qwen-coder-plus 模型使用",
    },
    # ========================
    # 硅基流动
    # ========================
    {
        "name": "硅基流动 (SiliconFlow) · 普通API",
        "provider": "siliconflow",
        "plan": "api",
        "base_url": "https://api.siliconflow.cn/v1",
        "model_placeholder": "Qwen/Qwen3-235B-A22B",
        "api_format": "openai",
        "note": "多模型聚合平台，按量计费，有免费额度",
    },
    {
        "name": "硅基流动 (SiliconFlow) · Pro Plan",
        "provider": "siliconflow",
        "plan": "token",
        "base_url": "https://api.siliconflow.cn/v1",
        "model_placeholder": "Qwen/Qwen3-235B-A22B",
        "api_format": "openai",
        "note": "Pro 会员订阅，不限量调用部分模型；同一 base_url",
    },
    # ========================
    # 智谱
    # ========================
    {
        "name": "智谱 GLM · 普通API",
        "provider": "zhipu",
        "plan": "api",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model_placeholder": "glm-4-plus",
        "api_format": "openai",
        "note": "按量计费，GLM-4 系列模型",
    },
    {
        "name": "智谱 GLM · Token Plan",
        "provider": "zhipu",
        "plan": "token",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model_placeholder": "glm-4-plus",
        "api_format": "openai",
        "note": "预付费资源包，折扣更大；同一 base_url",
    },
    {
        "name": "智谱 GLM · Coding Plan",
        "provider": "zhipu",
        "plan": "coding",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model_placeholder": "glm-4-plus",
        "api_format": "openai",
        "note": "代码场景专用 Plan；需在智谱控制台开通",
    },
    # ========================
    # Moonshot / Kimi
    # ========================
    {
        "name": "Kimi (Moonshot) · 普通API",
        "provider": "moonshot",
        "plan": "api",
        "base_url": "https://api.moonshot.cn/v1",
        "model_placeholder": "moonshot-v1-8k",
        "api_format": "openai",
        "note": "长上下文见长，按量计费",
    },
    {
        "name": "Kimi (Moonshot) · Token Plan",
        "provider": "moonshot",
        "plan": "token",
        "base_url": "https://api.moonshot.cn/v1",
        "model_placeholder": "moonshot-v1-8k",
        "api_format": "openai",
        "note": "预付费包，折扣价；同一 base_url",
    },
    {
        "name": "Kimi (Moonshot) · Coding Plan",
        "provider": "moonshot",
        "plan": "coding",
        "base_url": "https://api.moonshot.cn/v1",
        "model_placeholder": "moonshot-v1-8k",
        "api_format": "openai",
        "note": "代码场景专用 Plan；需在 Moonshot 控制台开通",
    },
    # ========================
    # 百川
    # ========================
    {
        "name": "百川 (Baichuan) · 普通API",
        "provider": "baichuan",
        "plan": "api",
        "base_url": "https://api.baichuan-ai.com/v1",
        "model_placeholder": "Baichuan4-Air",
        "api_format": "openai",
        "note": "按量计费",
    },
    {
        "name": "百川 (Baichuan) · Token Plan",
        "provider": "baichuan",
        "plan": "token",
        "base_url": "https://api.baichuan-ai.com/v1",
        "model_placeholder": "Baichuan4-Air",
        "api_format": "openai",
        "note": "预付费包；同一 base_url",
    },
    # ========================
    # 阶跃星辰
    # ========================
    {
        "name": "阶跃星辰 (StepFun) · 普通API",
        "provider": "stepfun",
        "plan": "api",
        "base_url": "https://api.stepfun.com/v1",
        "model_placeholder": "step-2-16k",
        "api_format": "openai",
        "note": "按量计费",
    },
    # ========================
    # MiniMax
    # ========================
    {
        "name": "MiniMax · 普通API",
        "provider": "minimax",
        "plan": "api",
        "base_url": "https://api.minimax.chat/v1",
        "model_placeholder": "abab7-chat",
        "api_format": "openai",
        "note": "按量计费，长上下文",
    },
    # ========================
    # 零一万物
    # ========================
    {
        "name": "零一万物 (01.AI) · 普通API",
        "provider": "lingyi",
        "plan": "api",
        "base_url": "https://api.lingyiwanwu.com/v1",
        "model_placeholder": "yi-large",
        "api_format": "openai",
        "note": "按量计费",
    },
    # ========================
    # 海外
    # ========================
    {
        "name": "OpenAI · 普通API",
        "provider": "openai",
        "plan": "api",
        "base_url": "https://api.openai.com/v1",
        "model_placeholder": "gpt-4o",
        "api_format": "openai",
        "note": "需海外网络，按量计费",
    },
    {
        "name": "Anthropic Claude · 普通API",
        "provider": "anthropic",
        "plan": "api",
        "base_url": "https://api.anthropic.com",
        "model_placeholder": "claude-sonnet-4-20250514",
        "api_format": "anthropic",
        "note": "需海外网络，长上下文 + 代码能力强",
    },
    # ========================
    # 自定义
    # ========================
    {
        "name": "自定义 · OpenAI 兼容",
        "provider": "custom-openai",
        "plan": "api",
        "base_url": "",
        "model_placeholder": "",
        "api_format": "openai",
        "note": "任意 OpenAI 兼容 API，自行填写 base_url",
    },
    {
        "name": "自定义 · Anthropic 兼容",
        "provider": "custom-anthropic",
        "plan": "api",
        "base_url": "",
        "model_placeholder": "",
        "api_format": "anthropic",
        "note": "任意 Anthropic 兼容 API，自行填写 base_url",
    },
]
