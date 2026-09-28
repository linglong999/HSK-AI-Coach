# tutor_quality · 教学 Judge 评测

> 配套设计：[《datasets/docs/0.25-教学judge-rubric-评测设计.md》](../../docs/0.25-教学judge-rubric-评测设计.md)
> 评审标准：[`rubric.md`](rubric.md)

评测 HSK-AI-Coach **每次纠错 / 讲解 / 介入的教学质量**。用法例：

```bash
# 全量跑（无 Key 也能跑：L1 程序断言 + 红线确定项如此现在就是确定性，L2 占位待接入）
python -m datasets.eval.tutor_quality.run_tutor_judge

# 单 case
python -m datasets.eval.tutor_quality.run_tutor_judge --case TQ-HSK1ERR001

#（接入 L2 后用 --model 记录被测模型）
```

## 目录与角色

| 文件 | 角色 |
|---|---|
| `rubric.md` | 6 维度 + 5 红线（人读评审标准） |
| `cases.json` | 统一 schema 评测用例（复用 golden/retell，**不改写原数据集**） |
| `build_cases.py` | 从 golden_v1_4 + retell_golden 抽 case 的生成脚本（复现用） |
| `judge.py` | 三层 judge：L1 程序断言 / L3 红线硬判（L2 LLM-judge 占位） |
| `run_tutor_judge.py` | 驱动 + 汇总 + 版本化报告（落 `reports/eval_tutor_quality_*.md`） |

## 运行约定

- **判分口径**：L1 对照 `expected_behavior.detect`（span/type/correction）断言；红线任一定 fail；pending（R3/R4/R5 及 case 可疑）**不计 fail**，单独列出待人工/L2 复核。
- **回归门槛**：确定 pass 率 **≥ 90%** 且无确定红线 → PASS；否则 FAIL。
- **无 Key 行为**：L1+L3 确定项全跑（真实引擎识别回退规则匹配）；L2 待人工校准后接入并 `--model` 记录。

## 当前状态（2026-09-11 · 首版）

- 用例 19 条：`explain` × 7 · `clean_guard` × 6 · `retell` × 5 · `intervention` × 1
- 跑通 **19/19 确定通过，门禁 PASS**（全确定性，未接 LLM-judge）
- 已如实标出 **1 条可疑 case** `TQ-HSK1ERR003`（"你喝水吧？"被黄金标成 `吧→吗` 偏误，实测引擎不报偏误 = 句子本对）→ 判 `PENDING_CASE_SUSPECT`，待人工核数据

### 下一步（L2 接入 + 校准）
1. L2 LLM-judge 依 `rubric.md` §四 落地：对讲解四段 / 语言分层 / 介入 / 归因逐维打分
2. 人工核 **20 个 case** 的 L2 判定，报告 judge 与人工一致率并回填 `gold_judge_hint`
3. badcase 回流：线上失败经归因 SOP → 入 `cases.json` regression 子集（版本升级、记录日期）

## B6 校准（2026-09-27 · 达标启用 L2 门）

- **三元组 pin**：`judge_model_id=deepseek-chat`（settings 默认）· `rubric_version=v2` · `prompt_template_hash=2ce0f212`
- **对拍集**：`../calibration/calibration_cases.json` v2（100 条 focus 维），人工标注 `annotation_user.csv`
- **一致性**：二次加权 kappa **0.8622** / 二元 kappa **0.9081**（≥0.70 门槛）→ **judge_qualified=true**
- **样本修正**：dim6 错放 CAL-057/058/082 mid→low、CAL-096/097/098 mid→high
- **锚定**：每维 1/3/5 描述性档（rubric.md §七），生产 `_build_judge_prompt` 整篇注入，fix 双峰化
- **基线**：`../baseline.json` naturalness_mean=2.769、band=0.3 已回写，delta 统计门控生效
- **残留**：8 条命令式口吻 mid（人工 3/judge 1）系统偏严 + 3 条 dim5 英文占比（人工 5/judge 3-4），已接受不调锚（防过拟合）

## 版本与日期记录

| 版本 | 日期 | 说明 |
|---|---|---|
| v0.1 | 2026-09-11 | 首版：L1+L3 确定性，19 case，门禁 PASS，1 条 case 标记待核 |
| v2 | 2026-09-27 | B6 校准达标：kappa≈0.86，L2 LLM-judge 启用，rubric 增 §七 每维 1/3/5 锚，baseline 统计门控生效 |