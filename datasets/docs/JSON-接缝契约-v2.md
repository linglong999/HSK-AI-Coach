# JSON 接缝契约 · v2（识别契约升级）

> **文档地位**：`router.process()` 输出契约的权威定义。B0（实施计划 D4）引入。
> **v1 已被 v2 取代**：`contract_version` 由 `v1` 升级为语义化 `v2`，主路径只产 v2；v1 是历史契约，不再维护，其 13 键/7 键形状见测试静态 fixture（`tests/test_contract_v1.py::test_v1_contract_locked_by_fixture`、`test_soft_score.py`）。
> 旧引用说明：`datasets/docs/JSON-接缝契约-v1.md` 从未落盘（历史文档债），router 引用已改指本文档。

面向会话即 v2 的主契约是**加法兼容**、**语义化版本递增**：顶层与 error 条目的新增字段全部**可选 + 默认值/null**，缺省不破坏既有调用方（前端只读它认识的键即可）。

---

## 1. 顶层结构（14→15 键）

`router.process()` 返回纯可序列化 dict，顶层键集合严格白名单：

| 键 | 类型 | v1→v2 | 说明 |
|---|---|---|---|
| `contract_version` | string | 值 `v1`→`v2` | 语义化版本 |
| `learner_id` | string | 不变 | 会话学习者 |
| `user_level` | string | 不变 | 如 `HSK3` |
| `native_lang` | string | 不变 | 母语 |
| `input_text` | string | 不变 | 本轮输入 |
| `errors` | array | 不变 | 已确认偏误（条目见 §2） |
| `uncertain` | array | 不变 | 待确认偏误（条目同构） |
| `hypotheses` | array | 不变 | L1 迁移假设（0.22） |
| `has_error` | bool | 不变 | `bool(errors)` |
| `graph_size` | int | 不变 | 图谱节点数 |
| `review_queue` | array | 不变 | 可复习 KP |
| `verdict` | string³ | **新增** | 句子级软评分三值；识别主失败/降级/mock 不给 → `None` |
| `score` | number³ | **新增** | 句子级连续分（误置 confirmed 取 min 聚合）；同缺省 → `None` |
| `degraded` | array | 不变 | 结构化降级（stage/reason/fatal） |
| `meta` | object | 不变 | `{start_ts,end_ts,elapsed_ms}` |

> 顶层共 15 键。³`verdict` 值域 `error|edge|acceptable`；`score` 0-1。识别主失败（fatal 早退）与 mock 缺省时两键为 `None`——见 §3「缺省口径」。

---

## 2. error 条目结构（7→10 键）

每个 error 条目为嵌套结构 `{error:{…}, explanation, graph_write, verification}`，其中 **`construction_diagnostics` 挂内层 `error` dict**（不混入外层嵌套、不碰 explanation）：

| `error` 内键 | 类型 | v1→v2 | 说明 |
|---|---|---|---|
| `fragment` | string | 不变 | 最小且机制完整的偏误片段 |
| `correction` | string | 不变 | 修正建议 |
| `type` | string | 不变 | 词汇/语法/语用/汉字 |
| `type_confident` | bool | 不变 | 类型是否为高置信 |
| `confidence` | number | 不变 | 初值 0-1 |
| `knowledge_point_id` | string | 不变 | 命中校验后的 kp id |
| `uncertain` | bool | 不变 | 待确认标记 |
| `score` | number³ | **新增** | 该偏误的连续分（0-1）；无 → `None` |
| `verdict` | string³ | **新增** | 由 `score` 确定性派生；无 → `None` |
| `construction_diagnostics` | object⁴ | **新增** | 1 层扁平可选嵌套 `{matched_rule,expected,actual,severity}`；B1 产出，B0 恒 `None` |

`ordered_error(e, version=2)` 负责白名单与恒序；`version=1` 时只返回 7 旧键。

---

## 3. verdict 派生规则与阈值

- `score` 是**源真值**；`verdict` 由 `score` 经阈值**确定性派生**（`engine/recognizer.derive_verdict`），**不经 LLM 独立再判**（避免"高分却盖正常章"的自相矛盾）。
- 阈值（调参起点，总纲 §2 ④，高置信 → 判为 error）：

| 条件 | verdict |
|---|---|
| `score >= 0.85` | `error`（确凿偏误） |
| `0.60 <= score < 0.85` | `edge`（母语者可两可） |
| `score < 0.60` | `acceptable`（疑似误报） |
| 非数值 / `None` / bool / str | `None`（不破坏缺省） |

- **优先级（§2 ⑨）**：确定性规则诊断器 `construction_diagnostics` > 连续分 `score` > 离散 `verdict`。B1 接入诊断器后，命中规则条目可 override verdict；B0 无诊断器，verdict 纯由 score 派生。
- **句子级聚合**：识别层顶层取已确认（confirmed）偏误 `score` 的 **min**（最差值）；无 confirmed → `acceptable`/`1.0`。uncertain（低置信）不进句子级聚合（宁漏勿错）。识别主失败/降级路径（`_rule_fallback`）**不产软评分**：顶层与条目均缺省 `None`。

---

## 4. 版本演进规则（v2）

- **加法兼容**：后续版本只能**新增可选字段（默认值/null）**，不得删除或改序既有键；白名单是"键集合 + 键顺序"双断言（仅对当前 v2）。
- **语义化递增**：契约结构性变化必须升 `contract_version`（`v1`→`v2`→…），不得原地改写。
- **v1 冻结**：v1 为历史契约，只读不产出（其形状以测试 fixture 兜底，不再维护）。

---

## 5. schema 挂载总表（本轮 7 处挂载点，标注消费批）

上线即 v2 的主契约之外，本轮共 6+1=7 处字段挂载方式在此钉死，避免每加一个功能改一次 schema：

| # | 字段 | 挂载方式 | 消费批 | 状态 |
|---|---|---|---|---|
| 1 | `verdict` / `score` | 识别输出：顶层 + error 条目 | **B0**（已实现） | ✔ 本批落地 |
| 2 | `construction_diagnostics` | error 条目内 1 层嵌套 `{matched_rule,expected,actual,severity}` | B1 | 挂载位已钉，恒 `None` |
| 3 | `meta_confidence` | 图谱 Node 最近快照 + message 完整事件；**不进识别契约本体** | B3 | 仅钉挂载位 |
| 4 | 回避（avoidance）独立类型 | 图谱层独立类型，**不入识别契约** | B3 | 仅钉挂载位 |
| 5 | `senses[]` 词义项 | Node 属性/子键（`sense_id/gloss/example/s_*` 四字段），主键仍 `kp_id` | B4 | ✔ 本批落地 |
| 6 | 账本 KINDS 扩展 | 同一 KINDS 常量四路：`feedback_presented`/`uptake_observed`(B2)、`avoidance_observed`(B3)、token 用量事件(B7)，与 `_writeback_ledger_events` 对齐 | B2/B3/B7 | 仅钉挂载位 |
| 7 | SSE 四事件形状 | message/done/error/intercept 的 JSON 传输层形状（payload = 契约 v1/v2 原样内嵌，只钉形状不新造 schema） | B7 S1 | 仅钉挂载位 |

> 顺手锁定：`priority`（L3）与候选质量（L2）**不进契约 schema**（派生量归主路径现算）；`meta_confidence`/回避类型由 B3（图谱层）消费，B0 只钉挂载位不实现。

## 5. B4 实现回填（2026-09-23）

以下三处由 B4 落地，**不入识别契约本体**（都在图谱 Node 层 / 现算派生量）：

- **词义项子键 `senses[]`**（Node dataclass 子键，默认空 list，向后兼容零迁移）：
  每项 `{"sense_id", "gloss", "example", "s_stability", "s_difficulty", "s_last_review_at", "s_next_review_at"}`。
  白名单源 `datasets/sense_whitelist_v1.json`（义项数≥5 × HSK1-2，243 词，`seed/level_filter/min_senses` 落产物 metadata 可复现）。
  `review_feedback(sense_id=...)` 命中非空 `senses[]` → 子卡独立 FSRS 更新 + 词级四字段同步；未命中/非白名单 → 词级单卡零变化。
- **字子层**（图谱独立 char Node，id=`char:璃`、knowledge_point=字、level=`char-HSK{n}`）：事件驱动增量建点（不预建全量字表），字卡自有 FSRS/mastery，防词频绑架；字级超纲判定仍走 recognizer 读 lexicon `char_level`，与图谱字节点无关。
- **构式晋升 / learned 迁移**（现算派生量，**不落盘**，`to_dict()` 均不输出）：
  - promoted：`positive_count≥3 且 unfixed_streak==0`（PROMOTE_RULE，priority × PROMOTE_BOOST，与 FOSSIL_BOOST 互斥）
  - learned：承接 B3 拍板2，阈值 `attempt_ok_reps≥3` 且无未纠正 attempt_err（LEARNED_RULE）；B3 未落地前缺字段容错为 0。

## 6. B5 实现回填（脚手架 / 超纲确定性 / 排序层）

以下由 B5 落地，**不入识别契约本体**（识别契约顶层/error 条目键集合保持 v2 冻结；脚手架注入与排序属讲解层/独立函数库，非识别 schema）：

- **脚手架渐褪**（`engine/scaffold.py`，纯函数零 LLM）：两层定档——第一层等级定起点 `LEVEL_TIERS{(1,2):full,(3,4):mid,(5,9):minimal}`；第二层单点内表现渐褪 `PERF_LADDER=(full_scaffold→no_pos→keyword_hint→minimal_hint)`。`pick_tier/pick_stage/scaffold_directive` 确定性选档（总纲 §1 决策2：档位由调用层评估，LLM 只做档位内自然讲解）。四域=英文·拼音·中文·词性；**英文最先撤、中文解包恒锚**。表述纪律注释：四域四档=ZPD 原理落地的产品自有实现、非学界既定分级。
- **讲解事前约束**（explainer.explain 注入段追加，主链零改动）：`SYSTEM_PROMPT/SYSTEM_PROMPT_EN` 各追加 `{b5_scaffold}` 占位符，内容=目标词（等级）+ 已掌握词集硬约束 + 脚手架档位指令。已掌握词集来自新接口 `error_graph.mastered_words()`（mastery≥threshold、等级过滤、priority 降序、limit 截断防 prompt 膨胀；排除 `char: `字子层节点，防污染 allowed_vocab）。graph 缺省/等级未知 → `{b5_scaffold}="（无）"`，不误约束（旧调用方零破坏）。
- **超纲事事拦截环**（`engine/unpack_guard.py`，复用 `recognizer.detect_beyond_level` 零改动）：`check_unpack` 纯判定（tokenize→查当前生效词表→违规清单）；`guard_unpack` 编排——违规 → 带违规清单重试 1 次（违规词经 `_b5_guard_violations` 注入 error 回灌 prompt）→ 再漏 → `degraded_overscope=True + overscope_violations` 降级标注（非无限重试）。挂载点 = `router.process` 讲解出口（`item.explanation` 内新增 `degraded_overscope/overscope_violations` 可选键，纯降级标注、不改变正常 explanation 形状）；B2 explain_error 深攻讲解落地时同接守门。查表源现行 `lexicon_hsk1_4.json`，B8 完成后随 recognizer 换源 `lexicon_hsk3_2025.json`。（当前 HSK1_4 词表命中上限 4 级，对 HSK4 学习者因宽容相邻仅对 >5 判超纲——数据真源口径由 B8 词表补充高等级后缓解。）
- **排序层**（`engine/sort/rank.py` 函数库，未落盘）：`rank_syllabus(lexicon, scene_weights)` → `[{item, level, comm_score, rank}]`。主序 level 升序（硬主序，禁越级抬/压级）；同级 comm_score 降序，`scene_weights` 未回填 → `comm≡0.5` → 退化 lexicon 原序（稳定不随机）。排序单元跟随 §4a：白名单词（B4 `sense_whitelist_v1.json`）→ 义项粒度 `item=sense_id`；其余词 → 词形；白名单文件缺席 → 自动退化全词形（软依赖）。语法/构式不进静态序（靠 §4 螺旋）；第二弹性序（复现/掌握度）不进——归 §4 动态调度。`comm_score` 打分管线（方案 A 相关性+对数衰减+封顶）实值待教材线回填，本批只留接口位。