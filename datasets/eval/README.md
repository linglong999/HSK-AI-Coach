# HSK 识别引擎评测集 · 黄金句集

> 配套：`golden_v1_4.json`
> 用途：3.1 偏误识别引擎 baseline 评测
> 状态：**MVP 小规模验证版 v0.3**，已按二语教师仲裁 + 用户终裁（2026-08-23）定案，统计已收敛一致

## 一、构建依据

吸收近期（2025–2026）中文偏误/纠错评测最佳实践，要点如下：

| 实践 | 本研究采纳 |
|------|-----------|
| 识别任务用 span-level F1 + type-level recall（非整句改写） | ✅ 主指标定位到 span + 分类 |
| 只报 accuracy 不行，必须有 recall | ✅ 单独报告 recall / 类型级 recall |
| 多标注 + 高级仲裁 | ✅ 结构预留 `golden_status`，待仲裁 |
| 对抗样本测 overcorrection | ✅ 含干净/混用/冗余对抗 |
| LLM-as-judge 只能辅助 + 须校准 | ✅ MVP 以人工标注为核心，LLM 只作初筛（后续） |
| 避免只用一个标准答案句（纠错多解） | ✅ correction 为参考解，非唯一答案 |

## 二、数据结构（对齐 2.1 v0.3 schema + 评测字段）

每条含：
- `item_id` / `learner_level`（1–4 级）
- `original` / `corrected` / `error_span` / `error_type` / `sub_type` / `correction`
- `error_type` 枚举 = **词汇 | 语法 | 语用 | 汉字**（与 2.1 schema 严格一致）
- `severity`(0–5) / `impact`(low/medium/high/none)
- `explanation` / `golden_status`(reviewed/pending_review/disputed)
- `source`（self-built / self-built-adversarial）

## 三、当前规模与分布（v0.3，终裁后）

| 维度 | 数据 |
|------|------|
| 种子集 | 32 句（偏误 15 + 干净对照 17） |
| 对抗样本 | 4 句（11%，偏低端区间） |
| 偏误类型分布 | 语法 14 / 词汇 1 / 语用 1 / 汉字 1 |
| clean 对照 | 19 句（含种子 17 + 对抗 2 句干净） |
| 类型分布(含 clean) | 语法 14 / 词汇 1 / 语用 1 / 汉字 1 / clean 19 |
| 等级分布 | 1级8 / 2级11 / 3级9 / 4级4 |
| golden_status | reviewed 35 / disputed 1 |

> 说明：**偏误 17 + 干净 19 = 36**。4 句 A 类（`CLN-014/016/019/032`）经终裁改判干净作负样本（测 over-flagging），`HSK3-CLN-009`（"因为…所以"）由对抗集归位种子 clean 对照作误报测试。对抗样本 11% 在 10–20% 区间下沿，扩充时优先补。
> 仅 1 条 disputed 遗留=`HSK1-ADV-004`（删除型冗余对抗，口径保留：只判 span 命中、不判替换正确性）。

## 四、已知局限（诚实声明，不隐藏）

1. **偏误标注部分依赖教学判断**：剩余 1 条 `disputed`（`HSK1-ADV-004` 删除型冗余对抗，口径已定：只判 span 命中、不判替换正确性）。其余 8 条初判 disputed 均经终裁定案（A 类 4 条改判 clean、B 类 3 条翻 reviewed、009 归位），已标注仲裁字段（`arbitrator`/`arbitration_date`）。
2. **语法类占比极高（14/17）**：语法 14 vs 汉字/词汇/语用各 1，少数类覆盖严重不足，class-level 指标不可靠——扩充优先序：汉字 > 词汇 > 语用。
3. **样本量为小规模**：32+4 句仅够验证评测**流程**与指标口径，不足以得出可靠能力结论；结论性评测需扩到数百句。
4. **restate/复述验证黄金集尚缺**（属 3.3，非本文件范围）。
5. **clean 语义**：干净对照样本 `error_type=null`、`sub_type=clean`（对照点在 `note`），用于测误报/overcorrection。

## 五、审查记录（2026-08-23 二语教师仲裁 + 用户终裁）

- **P0 · 假阳性改判（终裁 Group1，改判 clean 负样本）**：#014（话题链承前省略）、#016（把字句副词）、#019（把+NP+V+了 合法把字句）、#032（对…很关心 合法介宾状语）→ 改判 clean、`golden_status=reviewed`、ID 前缀改为 `CLN-`（`CLN-014/016/019/032`）。作负样本测 `over-flagging`，不保留为"可判错"。
- **B 类真偏误保留（终裁 Group3，翻 reviewed）**：#003（吧/吗→语法 question_particle）、#031（几岁→多大了，语用 age_register）、#023（是…的→是…了，加新例句）→ 正式纳入正向偏误集。
- **009 归位（终裁解释）**：`HSK3-ERR-009`（"因为…所以"）由对抗集移回种子 clean 对照（`CLN-009`），保留误报测试 note。
- **P1 · 语用加语境**：#003（吧/吗→语法，加 context）、#031（几岁→语用，加 context 降 severity）。
- **P1 · 术语修正**：#008 `compliment_structure`→`complement_structure`（拼写）、#023 换典型"是…的/是…了"例。
- **P2 · 分类/校准**：#030 词汇→语法、#006/#008 severity 校准、#012 并为 `temporal_aspect_error`、#013/20 sub_type 统一为受控英文 snake_case + clean 统一。
- **P2 · 数据清理**：#002"很多"改数量短语、#ADV-004 标删除型对抗（口径：只判 span 命中、不判替换正确性）。
- **流程固化**：新增 `context`（语用必填）、`arbitrator`/`arbitration_date`（disputed 必填）；arbitrator 归一为 `l2-teacher-arbitration`；启用 golden_status 三态。

## 六、后续扩充路径

- 等权威 HSK 大纲/词表到位 → 补词汇/汉字/语用平衡样本（优先汉字）
- 接入公开学习者语料（CGED/MuCGEC/HSK 语料）做来源多元
- 扩充对抗样本到 15–20%
- 每个类型补至 5–10 条以保证 class-level 指标可算
- 把数据集校验固化为 CI 脚本（span 子串、correction 还原、README 统计核对），提交前自动跑

## 七、产出

- `golden_v1_4.json` —— 评测集本体（v0.3）
- `check_dataset.py` —— 数据校验脚本（CI 雏形）
- 本 README —— 构建口径、局限与审查记录