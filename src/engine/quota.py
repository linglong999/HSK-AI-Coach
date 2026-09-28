# ============================================================
# engine/quota.py · B7 S2 · token 线性计量 + 统一能量折算
# [B7-A] 聚焦 1/2：用户只见统一"分"，单条不跳 token 分；背后 input/output
#   按真实成本折算（token 线性）。实参三缺（①1 积分锚 ②一回合几分
#   ③输入长度上限）上线后按真实成本回填——留 QUOTA_PARAMS 常量位（已拍）。
# 本轮对象：TokenMeter（折算）+ RoundUsage（单回合 LLM 用量收集器）。
# 失败/零产出回合不入账不扣能量（产品规则非行业惯例）：意图在 DialogService
#   记账点按 trace ok 判定；此处收集器只如实累计，决策归记账方。
# 并发安全：收集器走 contextvars 随请求线程——每条 dialog 请求一回合一个
#   RoundUsage，RLock 串行化闭环下各请求互不串扰。
# ============================================================

import contextvars

# B7-A 拍板常量的哨兵位：真实 ①积分锚/一回合几分 上线后回填。
#   energy_per_1k_input / _output：每 1000 token 折算能量"分"。
#   哨兵值只保证确定性可测、口径线性，不代表真实定价（上线即替换）。
# 【2026-09-27 现状核对】DeepSeek 官方价（deepseek-v4-flash 为当前显式名，chat
#   兼容名已于 2026/07/24 弃用并映射 v4-flash 非思考模式）：输入 1元/M、输出 2元/M。
#   → 官方 per-1k = in 0.001 元 / out 0.002 元，in:out = 1:2 与下方哨兵比例一致，故数值保留；
#   ⚠ 真正待回填的是"1 能量分=多少成本"的积分锚（B7-A 三缺一 ①），属产品定价，
#   定锚后才能把 energy_per_1k 改为真实成本。
QUOTA_PARAMS = {
    "energy_per_1k_input": 1.0,
    "energy_per_1k_output": 2.0,
}

_usage_ctx: contextvars.ContextVar = contextvars.ContextVar(
    "hsk_round_usage", default=None)


class TokenMeter:
    """token → 统一能量分（线性折算）。

    energy(prompt_tokens, completion_tokens)：input×in 费率 + output×out 费率，
    按千 token 折算。None/负值视为 0；缺 token 的 usage 记为 0 分（不扣）。
    energy_from_usage(usage)：usage={prompt_tokens, completion_tokens} 便利口。
    """

    def __init__(self, params: dict | None = None):
        p = dict(params or QUOTA_PARAMS)
        try:
            self._in = float(p.get("energy_per_1k_input", 1.0))
            self._out = float(p.get("energy_per_1k_output", 2.0))
        except (TypeError, ValueError):
            self._in, self._out = 1.0, 2.0

    def energy(self, prompt_tokens, completion_tokens) -> float:
        def _n(v):
            try:
                v = float(v)
                return v if v > 0 else 0.0
            except (TypeError, ValueError):
                return 0.0
        return round(_n(prompt_tokens) / 1000.0 * self._in
                     + _n(completion_tokens) / 1000.0 * self._out, 3)

    def energy_from_usage(self, usage) -> float:
        if not isinstance(usage, dict):
            return 0.0
        return self.energy(usage.get("prompt_tokens"),
                           usage.get("completion_tokens"))


class RoundUsage:
    """单回合 across 所有 planner LLM 调用的 token 用量累计。"""

    def __init__(self):
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.calls = 0

    def add(self, usage) -> None:
        if not isinstance(usage, dict):
            return
        p = int(usage.get("prompt_tokens") or 0)
        c = int(usage.get("completion_tokens") or 0)
        if p or c:
            self.prompt_tokens += max(0, p)
            self.completion_tokens += max(0, c)
            self.calls += 1

    def totals(self) -> tuple:
        return self.prompt_tokens, self.completion_tokens


def begin_round() -> RoundUsage:
    """本回合用量收集器入坑（dialog 入口调用；planner 缓存的 llm_call 在
    调用时经 report_usage 写入当前 context 的收集器，不受缓存影响）。"""
    ru = RoundUsage()
    _usage_ctx.set(ru)
    return ru


def report_usage(usage) -> None:
    """LLM 客户端每成功一次回报 usage 至此（llm_call 包装内调用）。
    usage=None（失败/零产出）→ 收集器忽略，不入账。"""
    ru = _usage_ctx.get()
    if ru is not None:
        ru.add(usage)


def end_round() -> RoundUsage | None:
    """收尾：取走并清空本回合收集器。返回 RoundUsage（或 None 无收集器）。"""
    ru = _usage_ctx.get()
    _usage_ctx.set(None)
    return ru