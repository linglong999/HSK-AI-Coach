# HSK-AI-Coach 评测口径

## 证据分层

| 层级 | 命令/来源 | 能证明什么 | 不能证明什么 |
|---|---|---|---|
| 代码回归 | `python -m pytest tests -q` | 当前代码在测试覆盖范围内未回归 | 不能证明教学效果或生产安全 |
| 确定性教学门禁 | `python -m datasets.eval.run_all --layer deterministic` | 规则链、语料契约和 D-6 约束可复现；不调用 LLM | 不能证明真实模型语义质量 |
| 真实模型评测 | `--layer llm_judge` 或 nightly | 在给定供应商、提示词和样本上的语义质量 | 不能外推到所有模型、学习者或长期习得 |
| 人工教学评估 | 待建立的教师仲裁协议 | 标签和解释是否符合二语教学判断 | 当前仓库尚无完成的教师仲裁证据 |
| 学习增益 | 待执行前测/后测/延迟后测 | 学习者是否真正习得并迁移 | 当前仓库没有此类实验结果 |

## 当前可复现数字

- 2026-09-29：P0 验收为 `1067 passed, 7 skipped, 32 warnings`；P1 单机收口后为 `1113 passed, 7 skipped, 32 warnings`；P2 仓库内准备后为 `1119 passed, 7 skipped, 32 warnings`；P3 单机诊断和备份范围收口后最新回归为 `1125 passed, 7 skipped, 32 warnings in 35.47s`，命令均为 `python -m pytest tests -q`。
- deterministic 套件：`tutor_l1_l3` 为 27/27，其余 `golden_corpus`、`retell_corpus`、`cged_corpus`、`skill_corpus`、`d6_assertions` 均 PASS。
- 识别黄金集：36 条，偏误 17、clean 19；指标透明度和置信区间见 `datasets/eval/eval_transparency.md`。
- 历史识别指标来自旧版 `datasets/eval/eval_results.json` 的离线重算，不是 2026-09-29 的真实模型重新运行结果。当前 README 只将其标为历史记录。
- 识别标签为作者 self-review，非二语教师仲裁；小样本指标不代表行业基准或学习增益。
- P2 教师仲裁目前仅有[协议草案](datasets/eval/teacher_arbitration_protocol.md)和[待审队列](datasets/eval/teacher_arbitration_queue.md)；尚无教师独立标注结果，不能升级为“经教师仲裁”。
- P2 [分层盘点](datasets/eval/strata_baseline_2026-09-29.md)显示现有识别语料缺全部母语字段，复述语料缺显式等级/母语/偏误类型；旧版预测结果与当前金标版本不匹配，不能发布当前分层准确率。历史比例区间已在 [`eval_transparency.md`](datasets/eval/eval_transparency.md) 更正为 Wilson score，避免 0 次误报被写成零不确定性。

## 发布规则

任何新指标必须同时写明：数据版本、样本数、分层方式、标注者/仲裁状态、运行命令、运行日期、置信区间（适用时）以及已知限制。工程正确率、真实模型质量和学习效果必须分栏发布。

正式对外发布还须遵守[指标发布规范](datasets/eval/metric_publication_policy.md)；学习增益研究目前只有[方案草案](datasets/eval/learning_gain_study_protocol.md)，没有真实受试者结果。
