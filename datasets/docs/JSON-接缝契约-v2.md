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
| `construction_diagnostics` | object⁴ | **新增** | 规则诊断器 1 层扁平嵌套，**6 键实值** `{construction,instance,verdict,score,checks[],explanation}`（B1 产出；B0 恒 `None`） |

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

- **优先级（§2 ⑨）**：确定性规则诊断器 `construction_diagnostics` > 连续分 `score` > 离散 `verdict`。**B1 已接入**：命中规则条目 `verdict` 由诊断器 override（规则>连续）；条目 `score` 保留 LLM 连续分不覆盖（连续分=贯穿底分）；诊断器自身分留 `construction_diagnostics.trace` 内。
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
| 2 | `construction_diagnostics` | error 条目内 1 层嵌套，**B1 实值 6 键** `{construction,instance,verdict,score,checks[],explanation}`（早期草案 `{matched_rule,expected,actual,severity}` 已废弃） | B1 | ✔ 本批落地 |
| 3 | `meta_confidence` | 图谱 Node 最近快照 + message 完整事件；**不进识别契约本体** | B3 | ✔ 本批落地 |
| 4 | 回避（avoidance）独立类型 | 图谱层独立类型，**不入识别契约** | B3 | ✔ 本批落地 |
| 5 | `senses[]` 词义项 | Node 属性/子键（`sense_id/gloss/example/s_*` 四字段），主键仍 `kp_id` | B4 | ✔ 本批落地 |
| 6 | 账本 KINDS 扩展 | 同一 KINDS 常量四路：`feedback_presented`/`uptake_observed`(B2)、`avoidance_observed`(B3)、token 用量事件(B7)，与 `_writeback_ledger_events` 对齐 | B2/B3/B7 | ✔ B2 新增 `feedback_presented`/`uptake_observed`（实值见 §8）；✔ B3 新增 `avoidance_observed`（实值见 §9）；✔ B7 token 用量事件已落地（见 §10） |
| 7 | SSE 四事件形状 | message/done/error/intercept 的 JSON 传输层形状（payload = 契约 v1/v2 原样内嵌，只钉形状不新造 schema） | B7 S1 | ✔ B7 S1 落地（实值见 §10） |

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

## 7. B1 实现回填（双轨硬校诊断器）

以下由 B1 落地，为 **`construction_diagnostics` 挂载位（§2 表 #2）的实值实现**（§5 挂载位置为早期草案字段，本批以 6 键实值取代）：

- **确定性守门员**（`engine/construction_diagnostics.py`，纯确定性、零 LLM、零第三方分词；轻量关键成分定位）：把字句 6 查（C2 宾语有定 / C3 动词不光杆 / C4 否定能愿位次 / C5 结构判定链、C6 补语搭配位次；C1 构式义=汇总门）+ 补语 5 查。与旧护栏（`_is_legal_ba_disposal`/`_is_false_de_adverb`）**并存不混用**（方向相反：旧护栏撤 LLM 误报，诊断器复核/补漏）。
- **接入时序**（`recognizer.recognize` 主路径，旧护栏撤误报后、transfer_match 前）：补漏（LLM 漏报 error）→ **进 confirmed（方案 A）**；edge → 进 uncertain（触发复核）；复核已报 → 挂 `construction_diagnostics` + verdict override（规则>连续>离散）。`score` 字段保留 LLM 连续分不覆盖；诊断器分留 trace 内。降级路径 `_rule_fallback` 亦跑诊断器、error 照补 confirmed（构式守门员独立于 LLM 存活）。
- **6 键实值与判分**：`{construction, instance, verdict, score, checks[], explanation}`（+ 内部 `_main` 供主路径取主错 kp）。评分确定性直判三值——任一硬违 fail → `error/0.40`；仅次违 edge → `edge/0.70`；全 pass → `acceptable/0.90`（与 B0 VERDICT 阈值区间对齐，不走 `derive_verdict`）。单一主错：error 时 `kp_candidate` 取优先级链最高硬违（C4>C5>C3>C6），`checks[]` 全录 trace。可正常 `ordered_error(version=2)` 持久化（第 10 键自此有实值，B0 期恒 None）。

## 8. B2 实现回填（反馈策略选择器 + 深攻 1）

以下由 B2 落地，**非识别契约字段**（反馈策略属讲解/介入层注入段，识别契约顶/error 条目键集合保持 v2 冻结）：

- **策略选择器**（`engine/feedback_selector.py`，纯确定性、零 LLM）：Lyster & Ranta 六分类降为编码框架（`LR_TAGS`，每条反馈贴 1 标签供 B6 D-4 锚样例，不进决策）。规则 A 降为软偏好表 `TYPE_PREFERENCE`（error.type→倾向 direction/lr_tag，低权重不硬锁）；主决权=水平+ctx+诊断器强信号。水平调节（规则 B，Krashen+Swain）：HSK1-2→input 多示范、HSK4+→prompt 逼 self-repair。诊断器 bias（规则③）：`construction_diagnostics.verdict=="error"` → 语法类强制 metalinguistic/显式（学界 recast repair 最低）。
- **去向分层 × 深攻 1**：`{deep_dive, light_marks(≤2), silent_kps, preferences, deep_dive_count}`。深攻只在 block 档（显式精讲+引导复述），`deep_dive_count ∈ {0,1}`；none/light 只轻点不深攻；encourage 档全空。跨回合 `deep_dived_kps`（DialogService 按 conversation_id 会话缓存）保证同一错误点最多深攻 1 次（命中顺延次位）。
- **注入形态**（`dialog_run._pre_scan_intervene`，directive 编译后追加段，`intervention.py` 零改动）：`[FeedbackStrategy]` 段列深攻 1 点+轻标点+形态指令，文案过 tutor-style；silent 清单**不进 directive**（学习者不可见，后台记录进复习队列）。策略段仅 errors 非空且档位非 encourage 时产生。
- **三维观测**（`error_ledger` 账本，见 §5 表 #6 同源 KINDS 扩展）：`feedback_presented`（deep/light 呈现时记 kp+signature）、`uptake_observed`（账本推导：该 kp 曾 present 且其后有 concept_confirmed=复述通过 → 采纳）。repair 现成链不动（verify_retell pass→concept_confirmed，verdict=partial 亦可记 partial 供 B6 观测）；错误复发现成（同 kp+signature observation_error 自动改记 repeated_error）。两新 kind 走 `record()` 既有幂等链，不会被误改 repeated_error。
- **深攻计数**：`deep_dive_count` 由选择器产出（供 B6 D-6 断言消费），策略段附于 `intervention_directive` 进主链。

## 9. B3 实现回填（元认知自评 + 回避观测）

以下由 B3 落地，**不入识别契约本体**（识别契约顶层/error 条目键 v2 冻结；两者都落图谱 Node 层 + 独立事件流）：

- **元认知自评 `self_rating`**（D-8 契约，`meta_confidence` 消费批落地）：
  - 判定模块 `engine/meta_judgment.py`（纯确定性零 LLM）：`SELF_LEVELS=(certain/half/uncertain)` 三档 × 产出两列（correct/wrong）＝ **6 格 MATRIX** → `route_bias(self_level, outcome)` 只产路由偏置（`{route, scheduling}`；`half+wrong`＝unlearned 核心校准信号），不判对错、**永远不盖过 B1 确定性判据**（B3-G1 契约注释）；`should_ask_meta(target_kps_hit, outcome_has_error, already_asked)` 定两高危时机（目标构式正确产出一次后 / 疑似误用后）＋每任务≤1 防疲态。
  - **完整事件落 message.metadata_json**（`store.py` 现成列，零 DDL）：每条 `{"meta_event":"self_rating","self_level":"certain|half|uncertain","anchor":<kp_id 标准 id>,"outcome_at_time":"correct|wrong","construction_ref":<目标 kp>,"ts":<unix>}`；锚点禁自由文本（标准 kp_id/构式锚点）。`engine.memory.error_ledger.query_meta_events(learner_id)` 从 messages.metadata_json 聚合 `(self_level, outcome_at_time)` 成对序列——B6 ECE/Brier/Calibration-Gap 直算源。
  - **Node 最近一次快照**：`meta_confidence` + `meta_anchor` 两新字段（默认 None 向后兼容零迁移，`to_dict()` 同步输出；主键 `knowledge_point` 不变）。
  - **接入**（`dialog_run._b3_observe`，PreScanIntervene 后、WritebackLedger 前聚合一次）：学习者本轮 `meta_self_level`（payload 透传，合法三档）→ 落 message 完整事件 + Node 快照 + route_bias 偏置（喂 B4 调度/B2 反馈）。`meta_ask_state`（DialogService 按 conversation_id 缓存）保证每任务自评≤1。
- **回避（avoidance）"该用未用"独立类型**（区分于"错误"，不入识别契约）：
  - 图谱四态 `GRAPH_STATES=(avoided/unlearned/learned/undetermined)`；观测信号 `OBSERVED_SIGNALS` 五态（attempt_ok/attempt_err/avoided/unlearned/undetermined）与图谱态**两组并存、映射有测试锁定**。
  - 判定模块 `engine/avoidance_observer.py`：`observe_avoidance(task_targets, learner_output, identify_result, graph_nodes)`，证据链＝2 自动（语境必要=目标构式任务偏置 / 能力历史=`positive_count>0`）+1 观察（替代表达=人工抽查，不进自动判定）。avoided=没用+有历史能力（该复习）；unlearned=没用+无证据（该教）/用到但错；attempt_ok=用了且对（正向链，learned 迁移阈值归 B4）；undetermined=判不准保守态。
  - **完整事件进账本**（同一 KINDS 四路扩展）：`avoidance_observed` kind（`error_ledger.observe_avoidance(kp, state, evidence)`，走 record() 幂等链不误改 repeated）；不与"错误"混：错误=`error_count/error_types`，该用未用=`avoidance_state/avoidance_count` 独立可查。
  - **Node 快照**：`avoidance_state`/`avoidance_count`/`last_avoidance_at` 三新字段（默认值向后兼容）。
  - **静默处置**（总纲 §2）：avoided→"该复习"喂 B4 调度权重、unlearned→"该教"喂 B5 排序层——**绝不进 intervention/directive**（学习者不可见）。骨架期 `target_constructions` 为空 → 观测不激活零行为（B3 拍板3）；`attempt_ok/attempt_err` 走既有正向/偏误链，不重复写避险账本。

## 10. B7 实现回填（SSE 四事件 / quota 现态 / 邀请制）

以下由 B7 S1/S3/S5 落地，属**产品壳传输层与配额闸门**，识别契约顶/error 条目键集合保持 v2 冻结（SSE 只钉「传输形状」，payload 内嵌契约 v1/v2 原样，不新造 schema）。

- **`POST /api/dialog`：200 成功改 SSE**（S1）。事件序 = `intercept? → message×N → done`：
  - `intercept`（可选，S3 配额三档拦截才注入）：`out["intercept"]` 原样，含 `{msg, energy_left, tier}`（low/exhaust/round_cap 各自触发，round_cap 不截断已产出回合）。
  - `message`：单回合卡片 `{trace_i, kind, ok, payload}`——`payload` = 契约回合卡片原样内嵌（契约 v1/v2 字段全集，SSE 不转录不新造）。
  - `done`：完整对话响应 dict（含 `trace/conversation_id/…`），另加 `rounds`（=len(trace)）与 `energy_used`（S2/S3 计量后回填，缺省 0）。
  - `error`：非 200 路径（空文本/无 Key/配额硬错）**不流式**，仍一次性 JSON，与 M0 纪律同构。
- **`GET /api/quota` 现态端点**（S1，QuotaBar 首屏数据源）：返回 `{energy_left, daily_total, est_cost, round_count, reset_at}`。只读（不消耗、不落盘），读 VisitorGate 现态；gate=None（测试默认豁免）或 BYOK 非 env 供应商 → 额度不限（`energy_left=-1/daily_total=0/reset_at="额度不限"`）。intercept 事件只管会话中增量更新，首屏必须靠此 REST 拉取源。
- **能量折算**（S2 TokenMeter，见 §5 表 #6 挂载位）：`energy = prompt_tokens/1000×in_rate + completion_tokens/1000×out_rate`（线性折算，哨兵费率 `QUOTA_PARAMS={input:1.0, output:2.0}`，上线按真实成本回填）。失败回合（零产出）不入账不扣能量；`RoundUsage` 随请求线程 contextvars 收集，RLock 串行化下互不串扰。
- **12 回合硬限**（S3）：单会话最多 12 回合，round_cap 拦截不截断已产出；三档文案与触发由 VisitorGate 锁定。
- **邀请制 allowlist**（S5）：`config.settings.INVITE_ALLOWLIST`（逗号分隔 open_id；空=闸门关闭完全旁路，契约零变化）。闸门开启时——页面请求只出邀请页（不公开 speak/图谱入口）；`/api/*` 未授权 → `403 {"error":"invite_required",...}`（防绕过直取数据）。open_id 来源：query `open_id=` 或 Cookie `hsk_openid`。
- **双壳静态托管**（S4）：`/` → React 壳构建产物 `index_dir/dist`（未构建回退 `index_dir` 目录本身，便于开发）；`/legacy/` → legacy 工具壳（`web_legacy/`）。两者/API 均为 GET 静态/home，POST 等未知路径由 `api_unknown_method` 统一 404——13 条既有路由契约零变化。