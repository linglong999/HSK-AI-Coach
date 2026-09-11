# tutor_quality 评测报告（2026-09-11）

- 用例数：19 · 确定通过：19 · 通过率：1.0
- 门禁（≥90% 且无确定红线）：**PASS**

## 确定红线触发（FAIL）

- 无

## 待复核（PENDING，不计 fail）
- TQ-HSK1ERR001, TQ-HSK1ERR002, TQ-HSK1ERR003, TQ-HSK2ERR005, TQ-HSK2ERR006, TQ-HSK2ERR008, TQ-HSK3ERR012, TQ-RT-001, TQ-RT-002, TQ-RT-003, TQ-RT-004, TQ-RT-005, TQ-HSK3ERR012-E1

## 逐 case 详情
| id | type | L1命中 | redlines | L2 | elapsed_ms |
|---|---|---|---|---|---|
| TQ-HSK1CLN004 | clean_guard | 0 | - | pass | 955 |
| TQ-HSK2CLN007 | clean_guard | 0 | - | pass | 691 |
| TQ-HSK3CLN010 | clean_guard | 0 | - | pass | 668 |
| TQ-HSK3CLN011 | clean_guard | 0 | - | pass | 1358 |
| TQ-HSK4CLN013 | clean_guard | 0 | - | pass | 841 |
| TQ-HSK4CLN014 | clean_guard | 0 | - | pass | 818 |
| TQ-HSK1ERR001 | explain | 1 | - | pass | 3043 |
| TQ-HSK1ERR002 | explain | 1 | - | pass | 3166 |
| TQ-HSK1ERR003 | explain | 0 | - | pass | 864 |
| TQ-HSK2ERR005 | explain | 1 | - | pass | 3320 |
| TQ-HSK2ERR006 | explain | 1 | - | pass | 3390 |
| TQ-HSK2ERR008 | explain | 1 | - | pass | 3795 |
| TQ-HSK3ERR012 | explain | 1 | - | pass | 3103 |
| TQ-RT-001 | retell | 0 | - | pass | 945 |
| TQ-RT-002 | retell | 0 | - | pass | 3418 |
| TQ-RT-003 | retell | 0 | - | pass | 3207 |
| TQ-RT-004 | retell | 0 | - | pass | 2913 |
| TQ-RT-005 | retell | 0 | - | pass | 1285 |
| TQ-HSK3ERR012-E1 | intervention | 0 | - | pass | 3104 |

## 版本

- 模型：
- case 版本：v0.1
- LLM-judge：MOCK（--mock-llm 规则化占位，非真实 LLM-judge）
- 日期：2026-09-11 16:27:54
- 说明：红线=确定判分；L2 接入真实 LLM-judge 后需按 rubric.md §四 人工校准