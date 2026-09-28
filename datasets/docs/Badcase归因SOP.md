# Badcase 归因 SOP

> 配套：`datasets/eval/tutor_quality/`（`cases.json` → `judge.py:load_cases` → `run_all.py`）· 评审标准 `rubric.md`
> 用途：线上失败 / 回归失败 / 人工抽检打回的 badcase，经统一归因后**回流进 `cases.json`**，成为随时间升值的回归资产（rubric 第六·"不可丢"）。
> 状态：**v1（2026-09-27 首立）**——SOP 先立、管道先行，线上流量到位即启用。

## 一、触发源（三路 → S1）

| 路 | 来源 | 典型信号 |
|---|---|---|
| ① | 线上运行失败 | 真实 /api/dialog 回合被自动网关或人工抓成可疑；图谱异常；teacher/correction 被学习者或 QA 驳回 |
| ② | 回归 / 夜间 gate fail | `run_all.py` 某层失败点（deterministic / llm_judge / d6_assertions）；rubric 双门槛未过 |
| ③ | 人工抽检打回 | QA / 教师抽检对话输出，判定"这里不对" |

## 二、归因五步

**S1 记录**：捕获原样 `input + context + learner_level + native_lang + 被测输出`（贴 trace），附触发路与时间戳。缺料的不追溯。

**S2 复现**：用同一 `input/context/learner_level` 走 `Router`，确认**非偶发**。environment 抖动 / key 瞬时故障 / 偶发超时 → 记异常、**不入回流**（见 §六）。

**S3 归属**：badcase 命中哪一层——
- 识别：偏误定位（`construction_diagnostics` 把字句/补语类，over/under/mis）
- 讲解/介入：讲解四段不全 · 复述验证不严 · 介入时机不当 · 语言分层 / 归因不谨
- 红线：R1 教错 · R2 误报 · R3 漏复述 · R4 流利过度打断 · R5 归因污染图谱

**S4 判真伪**（三选一）：
- **真 badcase**：系统缺陷 → 继续 S5
- **数据错**：case/golden 标错（如句子本对被标偏误）→ **改 case，不当作系统缺陷修引擎**
- **口径差**：有限但可接受（与 rubric 锚仍有分歧，如命令式口吻偏严、dim5 英文占比）→ 记录，**不回流不调锚（防过拟合）**

**S5 定位根因**：可确定性修（守门员 / 排序层 / 词表 / 三层 judge 阈值）→ 修引擎并补单测；否则交由计划立项（refer @ D 层欠项），**不注水洗白**。

**S6 落回流**：写成符合既有 schema 的 case 追加入 `cases.json`（见 §三）。

## 三、cases.json 回流 schema

`judge.py:load_cases` 全量消费 `["cases"]` 数组——**新增 case 即自动进 run_all，无需改任何代码**。回流 case 沿用同一 schema：

```json
{
  "id": "<RC-seq或来源ID>",
  "source": "badcase-回流",
  "source_id": "<原始对话/输出定位>",
  "case_type": "clean_guard | explain | retell | intervention | naturalness_anchor",
  "input": "<原样输入>",
  "context": "<上下文，无则 null>",
  "expected_behavior": {
    "detect": {"span": "...", "type": "...", "correction": "..."} | null,
    "must_explain": true|false,
    "must_verify": true|false
  },
  "redlines": ["R1"],
  "gold_judge_hint": null,
  "metadata": {"learner_level": 1, "native_lang": "英语"}
}
```

- Bump `meta.version`（`0.2` → `0.3` 起逐次 `+0.1`）、更新 `meta.date` 与 `meta.note` 记本次回流。
- 单一回归点可拆多条时用序号后缀（`-01/-02`）；一条只锁一个断言点，**不塞多断言进同 case**（易掩盖根因）。

## 四、验证（S7）

```bash
# deterministic 层必须零 LLM 全绿（PR 红线护）
python -m datasets.eval.tutor_quality.run_tutor_judge
python -m datasets.eval.run_all --layer deterministic

# llm_judge 层带 key 跑，统计门控对齐 baseline
python -m datasets.eval.run_all --layer llm_judge --judge-model <跨家族模型>
```

加入新 case 后：既有关键维度**不退化**（run_all delta 门控 / baseline 对照）；新 case 的 judge 判定符合归因结论（可用校准一致率口径复核）。

## 五、记录

- 每次回流提交：git 信息注明 RC case id + 归因结论（真 / 数据 / 口径 + 根因 + 是否已修）。
- 归因结论沉淀进项目 memory（B6 评测区），供后续批量回顾。
- 版本口径与 `calibration` 对齐：judge 改 rubric / prompt 后按 `JUDGE_UPGRADE.md` 重跑校准，不得裸换。

## 六、死在入口的（不进回流）

- **不可复现**（环境抖动 / key 瞬时 / 偶发超时）→ 记异常，不落 case。
- **需新能力才过**（超当前 scope / 属 D 层欠项如教材线实值）→ 归待立项，不注水进回归集。
- **口径差可接受** → 记录即可，不回流不调锚。

## 版本

| 版本 | 日期 | 说明 |
|---|---|---|
| v1 | 2026-09-27 | 首立：触发源三路 · 归因五步 · cases.json 回流 schema · 验证与记录口径 |