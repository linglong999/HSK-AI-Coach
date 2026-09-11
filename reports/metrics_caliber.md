# 效果度量 · 指标口径文档（P0.18）

> 模块：`engine/metrics.py`（纯标准库，零第三方）
> 数据源：`data/` 下 `ledger_*.json`（事件账本）、`graph_*.json`（偏误图谱）、
> `memory_*.json`（长期记忆/会话）、`feedback_*.json`（评分）。
> 渲染：`web/metrics.html` ← `GET /api/metrics`；评分入口 → `POST /api/feedback`。
> 更新日期：2026-09-10

本文件与代码一致：每个指标都返回 `{value, n, window, caveats}`；
**数据不足一律如实报缺（`value=null, n=0`），不编数**。

---

## 学习者发现

以 `ledger_*.json` 文件为学习者锚点（组件锚点），避免误匹配无 ledger 的图谱变体。
名称取 `ledger_` 与 `.json` 之间的部分，按名隔离。

---

## 指标 ①：知识点通过率变化（pass_rate_delta）

**口径**：对一个「已通过」的知识点 kp，取首次通过时刻 T（graph 节点的
`last_positive_at`；无该字段但有 `positive_count>0` 也视为通过），
对比两个时间窗的错误事件密度（错误条数/天数）：

- 通过前窗：`[T − W, T)`，W = 14 天
- 通过后窗：`[T, T + W)`
- `delta = 通过后密度 − 通过前密度`（**负值 = 通过后更少出错 = 进步**）

所有已通过 kp 的 delta 取均值即为该学习者指标值。

**数据源**：错误事件来自 ledger 的 `observation_error / repeated_error`；通过信号来自 graph 节点。

**局限/caveats**：
- 通过判定为横截面的正痕迹（positive_count/last_positive_at），非逐轮严谨判定；
- 通过前窗与通过后窗**都无**错误事件者剔除（单独计数，无法参与比较）；
- 数据量小 → 看方向不追统计显著。

---

## 指标 ②：达标交互数（interactions_to_pass）

**口径**：对一个已通过 kp，取首次错误事件时间到通过时刻 T 之间，该 kp 相关的
ledger 事件条数（`observation_error / repeated_error / concept_reviewed` 均计为一次
交互），即这个知识点的「纠错来回」轮数；各 kp 取均值。

**数据源**：ledger 事件 + graph 通过信号。

**局限/caveats**：
- 「交互轮数」以 ledger 事件条数为**代理**，非真实对话轮次；
- 首错晚于或等于首通过者（首次就答对、无纠错过程）无法测，剔除并计数。

---

## 指标 ③：续学率（continue_learning_rate）

**口径**：以记忆中的会话级 `created_at / updated_at` 估一次「学习活动」。
统计所有「当天有会话结束（updated_at）」的日期集合，对每个结束日 D，
若在该日结束后 `horizon = 24h` 内又有新会话开始，则 D 记为「续学」。
`续学率 = 续学日期数 / 有效结束日数`。

**数据源**：`memory_*.json` → sessions 映射（created_at / updated_at）。

**局限/caveats**：
- 以会话级时间戳估活动，非轮级粒度；
- 「续学」= 某结束日后 24h 内又重新开始新会话；
- 同日多次会话只计 1 个结束日。

---

## 指标 ④：答疑满意度（satisfaction）

**口径**：对话结束评分均值，1–5 星，可选提交。
`满意度 = 已提交评分之和 / 评分条数`。

**数据源**：`feedback_*.json` → scores[]（score 1–5）。

**局限/caveats**：
- 自愿提交偏差（不满意者可能不评）；
- 样本少时波动大，看方向不追显著。

---

## 反馈闭环与接口

- **前端评分入口**：`web/index.html` 每轮对话回复尾部渲染一行 1–5 星 + 跳过
  （可选），点星 → `POST /api/feedback`。
- **记录**：`engine/metrics.record_feedback()` 追加到 `data/feedback_<learner>.json`
  （tmp+replace 原子写；score 越界/非数拒绝并回 400）。字段：`{score, conversation_id, comment, ts}`。
- **聚合**：`GET /api/metrics` → `all_metrics()` 对全部学习者实时计算 4 指标（**随使用自动更新，不靠手工跑**）。
- **展示**：`web/metrics.html`（需通过 HTTP 访问，如 `http://localhost:8000/metrics.html`）。

## 验收核对

- [x] 4 指标有口径文档（本文件）且可复现计算（`tests/test_metrics.py` 用 synthetic 数据校验方向与数值）
- [x] 对现有 demo 数据算出真实数字（不改数据、不编数；缺数据如实报 null）
- [x] 评分入口可用（前端 1–5 星 → `/api/feedback` → 落盘）
- [x] 指标随使用自动更新（`/api/metrics` 实时聚合）
- [x] 隐私：汇总不含对话原文，数据纯本地不下发