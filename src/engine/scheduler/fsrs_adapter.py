# -*- coding: utf-8 -*-
# engine/scheduler/fsrs_adapter.py
# B4 · py-fsrs 适配器（H1，拍板 ①）。
# 复用开源 py-fsrs（FSRS-6），不自己实现 FSRS——只做接入 + 教学口径封装。
# 对外保自实现 fsrs.py 的四 API 语义（MemoryState/init_state/next_state/interval），
# 内部持 Scheduler → error_graph 调用点几乎不改（见 error_graph.review_feedback）。
# 评级口径 1:1：自实现 1忘记/2困难/3想起/4轻松 ↔ py-fsrs Rating.Again/Hard/Good/Easy（同为 1-4）。

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from fsrs import Card, Rating, Scheduler, State


@dataclass
class MemoryState:
    """FSRS 记忆状态（迁自旧自实现 fsrs.py，字段不变）。
    stability: S，记忆稳定性（天）；difficulty: D，难度[1,10]。"""
    stability: float = 0.0
    difficulty: float = 5.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


class FsrsSchedulerAdapter:
    """py-fsrs 适配器：对外保 MemoryState/init_state/next_state/interval 语义。
    内部持 Scheduler（块级无状态可共享）。两点强制约束（相对拍板稿 H1 的修正，非行为漂移）：
    - enable_fuzzing=False：fuzz 只随机化 due——对"后天复习"是面向学习的噪音，
      且会让 interval()（确定性公式）与库内实际 due（fuzz 后）失一致，故关闭保确定。
    - interval() 镜像 py-fsrs 自己的 _DECAY/_FACTOR 常数（_next_interval 同式），
      不重复实现公式、不沿用旧自实现的 19/81·-0.5（那是 FSRS-5，已换库）。"""

    def __init__(self, desired_retention: float = 0.9):   # 沿自实现常量 DESIRED_RETENTION
        self._retention = desired_retention
        self._sched = Scheduler(desired_retention=desired_retention,
                                enable_fuzzing=False)
        # 镜像库内常数（版本已钉 fsrs==6.3.2；升级需复核此处）
        self._DECAY = self._sched._DECAY
        self._FACTOR = self._sched._FACTOR

    def init_state(self, rating: int) -> MemoryState:
        # 新卡首评：Learning 起点，py-fsrs 自动给初始 S/D（不自己算）
        card, _ = self._sched.review_card(Card(), Rating(rating))
        return MemoryState(stability=card.stability, difficulty=card.difficulty)

    def next_state(self, st: MemoryState, rating: int, elapsed_days: float) -> MemoryState:
        # 成型卡复习须置 State.Review：Learning 态会被学习步骤（60s/600s）困住，不产真实间隔
        card = Card(stability=st.stability, difficulty=st.difficulty,
                    last_review=_now() - timedelta(days=elapsed_days),
                    state=State.Review)
        card, _ = self._sched.review_card(card, Rating(rating))
        return MemoryState(stability=card.stability, difficulty=card.difficulty)

    def interval(self, stability: float, r: Optional[float] = None) -> float:
        # I = S/FACTOR * (r^(1/DECAY) - 1)，与库内 _next_interval 严格一致
        r = self._retention if r is None else r
        return stability / self._FACTOR * (r ** (1.0 / self._DECAY) - 1.0)