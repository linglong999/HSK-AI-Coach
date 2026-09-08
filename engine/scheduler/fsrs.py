# -*- coding: utf-8 -*-
# ============================================================
# engine/scheduler/fsrs.py
# P0.17 · 真间隔重复（自实现简化 FSRS）——选型 B 已拍板。
#
# 用纯标准库(math+dataclasses)实现简化 FSRS，固定官方默认参数、无优化器、
# 零第三方依赖。公式结构取自 open-spaced-repetition/py-fsrs（MIT，仅借鉴公式
# 不 vendor 代码）；W 参数与 py-fsrs v5.1.3 官方源码（FSRS-5 记忆参考值）
# 逐值核对一致（19 项）。注意：py-fsrs 主干已是 FSRS-6 参数，落码以 FSRS-5
# v5.1.3 为准，勿对照主干。
#
# 职责边界（与 P0.4 priority 分离）：
#   priority 管"学什么"（节点紧迫度排序）；
#   本模块管"何时复习"（按遗忘曲线推算下次间隔），到期后仍按 priority 排。
#
# 复习评级 rating：1=忘记 / 2=困难 / 3=想起 / 4=轻松。
# ============================================================

import math
from dataclasses import dataclass

# ---- FSRS-5 全局常数（py-fsrs v5.1.3 官方默认，落码时逐值核对）----
DECAY = -0.5
FACTOR = 19.0 / 81.0

# FSRS-5 官方 21 参数（记忆模型参考值；末 2 项 W[19]/W[20] 常规置 0，未用于简化实现）。
# 对照 py-fsrs v5.1.3：w[0..18]=[0.40255,1.18385,3.173,15.69105,7.1949,
#   0.5345,1.4604,0.0046,1.54575,0.1192,1.01925,1.9395,0.11,0.29605,
#   2.2698,0.2315,2.9898,0.51655,0.6621]，与 FSRS-5 官方 README 记忆参考值一致。
W = [0.40255, 1.18385, 3.173, 15.69105, 7.1949, 0.5345, 1.4604, 0.0046,
     1.54575, 0.1192, 1.01925, 1.9395, 0.11, 0.29605, 2.2698, 0.2315,
     2.9898, 0.51655, 0.6621, 0.0, 0.0]

# 目标保持率 r（教学节奏常识，可调 0.85–0.95，当前取 0.9）
DESIRED_RETENTION = 0.9


@dataclass
class MemoryState:
    """FSRS 记忆状态。
    stability: S，记忆稳定性（天）——在此间隔尺度内仍能想起。
    difficulty: D，难度 [1,10]。"""
    stability: float = 0.0
    difficulty: float = 5.0


def retrievability(s: float, elapsed_days: float) -> float:
    """R(t,S)：经过 t 天后的可提取性（遗忘曲线）。
    R = (1 + FACTOR * t / S) ** DECAY，DECAY=-0.5 固定在 (0,1]。"""
    return (1.0 + FACTOR * elapsed_days / s) ** DECAY


def init_state(rating: int) -> MemoryState:
    """首次复习：S0 = W[G-1]；D0 = W[4] - e^(W[5]*(G-1)) + 1。
    rating 即首次评级 G；难度夹在 [1,10]。"""
    d = W[4] - math.exp(W[5] * (rating - 1)) + 1
    return MemoryState(stability=W[rating - 1],
                       difficulty=min(10.0, max(1.0, d)))


def next_state(st: MemoryState, rating: int, elapsed_days: float) -> MemoryState:
    """复习后状态更新（简化 FSRS-5 逐式落码，风险见注释）。
    rating: 1忘记 / 2困难 / 3想起 / 4轻松。
    elapsed_days 由"最近一次复习时间"算（P0.17 用 last_review_at，见 error_graph）。"""
    r = retrievability(st.stability, elapsed_days)
    # 难度更新（含向 D0(4) 的均值回归）：
    d1 = st.difficulty - W[6] * (rating - 3) * (10.0 - st.difficulty) / 9.0
    d0_easy = W[4] - math.exp(W[5] * 3.0) + 1            # D0(4)，忘了"轻松"往返的参考点
    d2 = W[7] * d0_easy + (1.0 - W[7]) * d1
    d2 = min(10.0, max(1.0, d2))
    if rating == 1:                                       # 遗忘
        s_new = (W[11] * d2 ** (-W[12])
                 * (st.stability + 1) ** W[13]
                 * math.exp((1.0 - r) * W[14]))
        s_new = min(s_new, st.stability)                  # 遗忘后 S 不高于旧 S
    else:                                                 # 想起（2/3/4）
        hard_penalty = W[15] if rating == 2 else 1.0
        easy_bonus = W[16] if rating == 4 else 1.0
        s_new = st.stability * (
            1.0 + math.exp(W[8]) * (11.0 - d2)
            * st.stability ** (-W[9])
            * (math.exp((1.0 - r) * W[10]) - 1.0)
            * hard_penalty * easy_bonus)
    return MemoryState(stability=max(0.1, s_new), difficulty=d2)


def interval(stability: float, r: float = DESIRED_RETENTION) -> float:
    """由目标保持率推下次复习间隔（天）：
    I = S / FACTOR * (r^(1/DECAY) - 1)。"""
    return stability / FACTOR * (r ** (1.0 / DECAY) - 1.0)