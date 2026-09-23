# JUDGE_UPGRADE.md · LLM-judge 升级迁移 Runbook

> B6 J6（B6-A⑤：judge 是需版本管理的生产组件）。凡改动 **judge 模型 / rubric / prompt 模板**
> 三者任一，均视为 judge 升级，必须按本 runbook 走迁移——**禁止裸换**（升级会使 mean shift 3–8pt，
> 直接替换等于无声改门禁，慢漂移会绕过当夜回归）。

相关组件：`datasets/eval/tutor_quality/`（judge.py / rubric.md / build_cases.py / cases.json）、
`datasets/eval/calibrate_judge.py`（校准管线）、`datasets/eval/run_all.py`（分层回归门控）、
`datasets/eval/baseline.json`（统计门控基准）。

## 三元组 pin

每次 LLM judge 运行产物头记录（`run_all.py::_pin_meta`，已实现）：

- `judge_model_id`：judge 模型 id（跨家族，避与生成模型同族）
- `rubric_version`：rubric.md 文首 `rubric_version=v2`（改 rubric 必须 bump 版本号）
- `prompt_template_hash`：rubric 全文 sha256 前 8 位

报告与校准集均携带三元组；diff 可查——**diff 里 rubric_version / prompt_template_hash 变了 = 一次升级**。

## 升级迁移五步（改动 judge_model / rubric / prompt 任一）

1. **冻结旧基线**：记录当前 `baseline.json` 的 `naturalness_mean`、`pass_rate`（旧 judge 的最后真实值）。
2. **bump 版本**：改 rubric → `rubric_version` + 1（如 v2→v3）；改 prompt 模板 → 记新 `prompt_template_hash`。
3. **重跑校准集**：用新 judge 对既有校准集（`datasets/eval/calibration/`）重新判分——
   `calibrate_judge.py --judge <新评分json> --human <人工标注>`。
4. **重算 kappa**：`judge_qualified = kappa_binary ≥ 0.70`。
   - **达标**（≥0.70）→ 可切：更新 `baseline.json`（写入新 judge 在该校准集上的 naturalness_mean，
     接 round-trip 一次 nightly 确认无红线回归）。
   - **不达标**（<0.70）→ **不得切换**。回退旧 judge 或据 diff 定位 rubric/prompt 问题后重试。
5. **迁移回填**：切新 judge 后，当轮 nightly 用新三元组记录，diff 旧→新报告留档（mean shift 预警：
   若 naturalness_mean 相对旧基线上移 > band 或下移 > band，人工确认是真提升还是判分器漂移）。

## 触发清单

| 触发 | 原因 | 动作 |
|---|---|---|
| 换 judge 模型（同族/跨族） | mean shift 3–8pt | 全五步 |
| 改 rubric.md（维度/判据/自然度锚） | 打开放质量口径变了 | bump version + 全五步；**审查新锚 case 是否需重生成** |
| 改 prompt 模板（表头/注入结构） | 注入内容变了 | 记新 hash + 全五步 |
| baseline.json 慢漂移触发门禁 | 当夜 LLM judge 平均自然度超带宽 | **不覆盖**——先查是 drift 还是真回退，降级前先校准复核 |

## 一致性口径红线

- 一致性**只报 kappa**（二值 + ordinal 双口径），**禁用"简单一致率"**（chance-corrected 会高估 ~38.6pp）。
- 3+ 人工标注者时才切 Krippendorff's alpha（当前单标注者用 Cohen's kappa）。

## 阻断点提醒

- 人工校准执行（B6 验收①）：到 kappa≥0.70 卡点，需人工标注 100–200 条（真实或合成对话）——
  在**校准达标前，L2 语义门控不启用**（baseline naturalness_mean 保持 null，门控退化不拦），
  deterministic 红线/d6 门控不受影响。