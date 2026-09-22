# 调研报告 · B 教学 LLM 对话产品工程规范（全貌总览）

> **属性**：第 3 步「补充代码设计」前一层的**全面系统调研**（用户定的 B 层）。产出"地图总纲"。
> **用法**：本报告给全貌；**逐批次写码前的「A 聚焦」调研结论**将按批追加到"批注"小节（见文末），每次追加都需用户决策确认后才可在《实施计划-v3.md》写该批代码设计。
> **调研日期**：2026-09-21（两个 Explore 代理并行，网络检索）。
> **状态**：✅ 全貌已成稿；现状兼容性待逐批 A 聚焦扫完真实代码后回填。

---

## 一、域一 · 偏误识别与纠正（CGEC/CGED）

- **主流方向已从规则模型转向「LLM + 微调」**；中文纠错（CGEC）当前前沿为微调大模型。
- **可参考的开源模型**：
  - `CSRP / ChineseErrorCorrector4-4B`：**Apache-2.0**，基于 Qwen3-4B 微调，论文 ACL 2026 Oral；LLM 方案，可作"弱检测器/解释"。
  - `MuCGEC / FCGEC / NaSGEC`：**评测数据集/代码**（Apache-2.0 等），非完整纠错模型，可作评测语料。
- **分工共识（关键）**：**确定性规则诊断器管明确语法槽位**（把/被结构、补语位置）、**LLM 管语义/搭配偏误 + 解释生成**；两者轻量融合。**→ 与本项目"双轨诊断器 + LLM 识别"的既有设计一致，无需推翻。**

## 二、域二 · 学习者模型 / 知识图谱 / 学习分析

- 语言教学产品更常落"**语法点/生词/结构点 + 状态表**"轻知识图谱，而非复杂图数据库。
- **掌握度结构共识**：`learner_id × knowledge_point → {mastery_prob, error_count, last_correct, last_review, status}`。→ **轻量 SQLite/JSON 表即可承载，无需自造图数据库。**
- "错误账本 + 复现率 + 石化/晋升阈值"**没有标准库**，属产品自身学习者建模，可参考 SRS 的稳定性/难度/可提取性参数思路。
- 参考：`EduKTM / KTbench / StanBKT`（知识追踪思路，非开箱 HSK 方案）。

## 三、域三 · 记忆调度 / 间隔重复

- **`fsrs`**：PyPI 当前 **6.3.2**，`pip install fsrs`，提供 Scheduler/Card/Rating/ReviewLog。**可用作自主 SRS 调度。**（FSRS 为 Open Spaced Repetition 开源，Anki 23.10 起内置，已升级 FSRS-6。）
- **`FSRS-Optimizer 6.5.0`**：用个人复习日志优化 FSRS 参数，可参考参数更新，非教学调度器。
- **卡片设计先例**：Anki 生态有 cloze/多字段/选择题/复习状态；"生词独立成卡 + 例句语境 + 多选题 + 多义词分义项 + 语法构式作复合卡"**方案合理，属产品设计问题，非现成工具**。→ 与本项目 §4"词义项卡 + 例句语境 + 构式上层复合卡"一致。

## 四、域四 · 教学效果评测 / 回归门禁

- **LLM-as-a-judge + rubric** 是语言教学 AI 评测当前路线。
- `L2-Bench`（2026 ACL）：开源 rubric-based benchmark，1000+ 样例、12 能力/31 子能力 taxonomy——**借鉴评测框架**（非库）。
- `Langfuse`（核心 MIT）：**LLM regression testing / golden dataset / experiment / threshold / CI gate**——适合作评测门禁基础设施。`LangSmith`：商业平台，非开源。
- **一致性**：`scikit-learn.metrics.cohen_kappa_score` / `statsmodels` 的 Cohen/Fleiss kappa，算标注者一致性。

## 五、域五 · Python 工程规范（2026）

- **包管理**：`pyproject.toml` 为事实标准；`uv`（当前 ~0.12.5，MIT OR Apache-2.0）为新项目/重构活跃推荐。`poetry`/`hatchling` 仍可用但不作默认首选。
- **结构**：`src/<package>/` 布局 + 根级 `tests/`（按功能域拆子目录）。
- **类型与校验**：`pydantic`（2.x）+ `pydantic-settings`（配置）；模型定义/路由入参/LLM 结构化输出统一走 Pydantic model。
- **测试**：`pytest` 为事实标准，配置入 `pyproject.toml` 的 `tool.pytest`。
- ⚠️ **现状兼容性待评估**：此推荐假设"重构成 pyproject+uv+src"。**是否采纳取决于现有项目是 requirements.txt+pip 还是已在 pyproject** —— 待扫真实代码后回填，避免为换而换推翻现状。

## 六、域六 · LLM 集成与 AI 应用层（2026）

- **SDK**：`OpenAI Python SDK`（~3.14.1，MIT）、`Anthropic Python SDK`（~1.6.0）。**多供应商抽象**：`LiteLLM`（~1.97.0，MIT）——重试/回退/成本/API-key 集中管理；`Pydantic AI`（~2.32.1，MIT，~20k stars）——应用层框架，内置工具/结构化输出/多模型适配。
- **判断**：只接少数供应商用官方 SDK 更清晰；需兼容多供应商/集中治理用 LiteLLM。**Pydantic AI 偏应用框架，非轻量网关。**
- **流式**：**SSE 为默认方向**。FastAPI `EventSourceResponse`（官方推荐）或 `sse-starlette`。**不要手写 yield "data:..."**。
- **结构化输出**：LLM 侧用 JSON/strict tool output，业务侧 Pydantic 校验，**保留规则兜底**。
- **Prompt 组织**：模板目录/skill 目录，**不单一大 prompt 文件**——按能力拆 `identify/explain/give_feedback/`。

## 七、域七 · 产品呈现层：React 免装网页端 + Python + SSE（2026）

- **前端**：React + Vite + TypeScript；**CRA 已于 2025-02-14 被官方废弃，勿用**。React 19 / Vite 6 / TS 5.8 为参考（非强事实标准）。
- **行 token 流式**：**首选 SSE**（EventSource 原生自动重连、文本流更轻）。WebSocket 用于双向实时；fetch streaming 作备选。→ **与项目"路线 B（响应式免装网页端 + SSE）"一致。**
- **后端**：**FastAPI**（~0.141.1）——基于类型注解、与 Pydantic 深度集成、生成 OpenAPI。Flask/Django 未到"过时"但流式/类型化匹配度 FastAPI 更高。
- **部署**：FastAPI（Uvicorn/Gunicorn）+ React 构建产物 + **Nginx 反向代理** + HTTPS。
- **替代**：Next.js full-stack 可作备选，但会改变"Python 后端 + SSE"分离式架构；若坚持分离，保持 FastAPI + React/Vite + SSE。

## 八、过时技术回避清单（勿踩老车）

| 已过时/不推荐 | 原因 |
|---|---|
| Create React App | React 官方 2025-02 废弃 |
| 手写 SSE 逐 token | 用 FastAPI `EventSourceResponse` / `sse-starlette` |
| `requirements.txt` 单文件 | 中型项目迁 `pyproject.toml` |
| 每供应商各写一套 LLM 调用 | 多供应商用 LiteLLM/统一 SDK |
| 纯 prompt 约束 | 配合 JSON/strict tool output + Pydantic 校验 |
| 单一大 prompt 文件 | 模板/技能目录 |
| 自建鉴权轮子 | 用标准 OAuth2/OIDC/API Key |

---

## 九、可复用成熟方案清单（无需自造）

1. `fsrs` 6.3.2 —— 记忆调度
2. `sse-starlette` / FastAPI `EventSourceResponse` —— SSE 流式
3. `pydantic` + `pydantic-settings` —— 校验/配置
4. `LiteLLM` / 官方 SDK —— LLM 接入（现为 requests 自写，见现状）
5. `Langfuse` / LLM-as-judge + rubric —— 评测门禁（对齐 D-4/D-6）
6. `cohen_kappa`（sklearn/statsmodels）—— 标注一致性
7. `scikit-learn` 相关 —— 众测指标

---

## 十、批注栏（逐批 A 聚焦 → 追加，先过用户闸门）

> 每次给某个批次跑「A 聚焦」调研后，结论**追加**到此处并标注批次编号；**拿到用户决策确认后**才在《实施计划-v3.md》写该批代码设计。

### 批注 M0 · 工程现代化基建（2026-09-21 · 用户拍板 = 决策点2 A 全量现代化）

> **全量现代化为前置地基改造**，横切 B0–B8 全部功能迭代（非独立排 B8 旁）。

**用户拍板记录**：
- 决策点1 fsrs：**替换**为开源 fsrs 6.3.2（弃现状自实现，API 迁移 + 同步改测试）。
- 决策点2 工程布局：**A 全量现代化**——迁 `src/` 布局 + 换 `uv`(uv.lock) + 引 pydantic v2/pydantic-settings + 引 FastAPI + LLM 客户端换官方 SDK（回落用 LiteLLM）。**现状兼容性待回填 → 已回填 = A**（总纲 §7 无工程结构内容，产品功能§7已在 B7，两者不冲突）。
- 迁移方式：**a 保行为迁移**（采用，用户未另选）——先建 src+uv+引 pydantic/FastAPI，把现有 27 个 engine 模块+全部测试整体迁绿，**迁移期间不改任何教学逻辑**；纯搬结构+换工具链，现有测试全绿后才在其上开 B0。fsrs 替换单独在 B4 处理。

**由此：新增前置批次 M0（工程现代化）** = src 布局/uv/pydantic/FastAPI/SDK 全部落地 + 现有测试迁移通过。M0 完成后 B0–B8 全程跑在新地基上。"接得上"基于搬迁后结构。

### 批注 M0-A · 专项聚焦调研结论（2026-09-21 · 用户拍板 A）

> 现状核实：`config/settings.py` 仅有 **deepseek/qwen 两个 provider、均为 OpenAI 兼容格式**，因此 LLM 迁移无需 LiteLLM/Anthropic SDK，`openai` 官方 SDK 传 `base_url` 一套覆盖两端点。

**四块迁移裁定（带来源）**：
1. **setuptools→uv**：`uv sync` 管理，保留 setuptools 作 build backend，`uv.lock` 锁定；平铺→src 后 import 改包级绝对导入（pytest 官方 "Good Integration Practices"）。
2. **平铺→src**：源码迁 `src/`，`packages.find` 指向 src，import 统一 `from engine...import`；**测试同步改 import**（迁移失败多因测试旧路径）。
3. **常量→pydantic-settings**：**保留 `config.settings` 模块级对象作兼容层**、内部换 `BaseSettings`，守住现有 `mock.patch("config.settings.X")` 兼容（settings.py 头注释本就有"勿重构成 dataclass"红线）。
4. **serve→FastAPI**：`sse-starlette`(3.4.8, Apache-2.0) 的 `EventSourceResponse`，不用手写 `yield "data:"`；断连/STOP 规范做法（CancelledError 清理）。

**LLM 客户端 = 保留 requests 自写（用户拍板 A 渐进拆分）**：M0 不动 `engine/llm/client.py`（requests 版现能跑、改动面小）；"requests→openai SDK"（`_session`→`http_client`、LLMHTTPError/4xx/json_mode400 语义迁移）拆到后续 B 批次补做。M0 验收不要求 SDK 冒烟。
> 由用户拍板 A 后，M0 聚焦定稿。

### 批注 B0-A · 识别契约扩展 · 专项聚焦（2026-09-21 · 用户拍板"方向 A 契约升级 v2"）

> 现状核对：`test_contract_v1.py` 有严格 10 键顶层白名单（"不多不少"）+ error 字段顺序白名单，向契约加任何字段都会破坏白名单测试 = 业界"反模式"（JSON Schema 铁律：严格 `additionalProperties:false` + 顺序白名单破坏向后兼容）。

**聚焦结论（带来源）**：
1. **连续分 + 离散标签共存**是主流 schema（verdict 供下游分流/gating、score 供排序/阈值微调）；但须一致性约束：`score` 为源真值、`verdict` 由 score 确定性派生，避免"离散整数"与"连续性"自相矛盾（TrustJudge：冲突率 11→低；NLI SNLI 同暴露连续分布+argmax 硬标签）。
2. **LLM 直接输出离散评分可靠性最差**（信息损失），连续更可靠；确定性规则诊断器拥有 **override 权**（规则 > 连续 > 离散）。
3. **契约扩展 = 加法兼容 + contract_version 语义化递增**：新字段标 optional+默认值；白名单改"键集合"（非顺序/非不多不少）；破坏性变更才 bump 版本（Apicurio BACKWARD 兼容模式）。
4. **嵌套诊断 = 独立通道**：`construction_diagnostics` 作 error 条目内 1 层扁平可选嵌套（`matched_rule/expected/actual/severity`），不混入主 errors（保审计追溯 LLM 判 vs 规则核）；嵌套限 2-3 层、4 层以上 LLM 生成失败率飙升 3-5 倍。

**用户拍板：方向 A**（契约升级 v2 + 白名单按版本分支重构 + contract_version 语义化），非保守打补丁。已写入《实施计划-v3.md》B0 代码设计版。

### 批注 B1-A · 双轨硬校诊断器 · 专项聚焦（2026-09-21 · 用户拍板 3 点）

> 现状核对：golden_v1_4.json 把字句句例=ERR-005(偏误)/CLN-010·016·019/ADV-003(clean)；recognizer 现有旧护栏 `_is_legal_ba_disposal`/`_is_false_de_adverb` 与新建诊断器方向相反须并存不混用。

**聚焦结论（带来源）**：
1. **业界无专治把字句/补语的开源规则诊断器可抄**（pycorrector/ChineseErrorCorrector/HanLP 均通用多类或模型驱动，不适用于场景特化教学规则）→ **自建价值所在，非重复造轮子**；公认走"轻量规则层 + 关键成分定位"，不必配完整句法树（HanLP 可作为通用基线参考，不引入）。
2. **规则 vs LLM 融合**：未触发=不产单错；触发且有冲突=规则为强信号/标注规则修正；edge 仅供补充不覆盖 LLM 主判断（与 B0 "规则>连续>离散"一致）。
3. **软评三值无业界统一**：关键是**保护 clean 正例**（把字句干净句绝不误判 error）；edge 保守化。
4. **分词坑**：把/了/得/的/地是结构标记，按"词级成分定位 + 字符级标记"混合，不依赖完整句法树。
5. **评估**：span-level F1 + type recall + 误报率 + 一致性(不重复 LLM 已报，CGED 口径)。

**用户拍板 3 点**：
1. **不引分词依赖**（B1 纯字符串/关键成分定位，不加 HanLP/Jieba/spaCy；M0 依赖瘦身哲学延续）。
2. **CLN-016「请你把这个问题认真考虑一下」= 无语法偏误**（仅"某语境可能不礼貌"→ 语用层，B2 管 if 需要，B1 绝不报 error）。
3. **"一下"划入有效尾成分 R 白名单**（连同 了/完/掉/走/成/干净/清楚/好 + 处置义构成 + 口语尾成分）——CLN-016 类"V+一下"合法变体保护。

已写入《实施计划-v3.md》B1 代码设计版。

### 批注 B2-A · 反馈策略选择器 + 深攻 1 · 专项聚焦（2026-09-21 · 用户拍板"方向 A 软偏好"）

> 现状核对：intervention.py 现有 `decide_intervention`(四档介入 none/light/block/encourage)+`RECAST_DIRECTIVE_Z H/EN`(recast 单一常量+深攻1已部分落地)；缺§5最新定"反馈策略选择器"（错误类型→反馈方向），当前所有错误默认 recast。

**聚焦结论（带来源）**：
1. **Lyster & Ranta 六分类=描述性非处方性**——只用于给反馈贴分类标签，学界（Lyster 2001；Bao&Wang 2023；Zhang et al 2025）无"错误↔反馈"硬规则 → 不宜做僵化映射。
2. **recast 用最多但 repair 最低**，prompt 类（elicitation/元语言/明确纠正）repair 更高（Zhang et al 2025 classroom Discourse）→ 单设 recast 为默认可能偏弱，尤其语法类。
3. **LLM 教学产品（Duolingo Max/Speak）未公开"错误→反馈"规则表**，按类型分流只是辅助非核心 → 自建可行但应是软偏好。
4. **input/output 导向有 Krashen+Swain 依据**：低水平多 input/示范、高水平逼 output/self-repair → 水平调力度保留。
5. **单句轻点 1 有强证据**（Bitchener 2008/Sheen 2009/Ellis 2009 防认知过载）→ 深攻 1 合理。
6. **uptake/repair 可操作**：full repair/partial repair/错误复发（Zhang 2025/Lyster 2001）可作日志回归指标。

**用户拍板：方向 A（软偏好）**——"错误→反馈"降级为软偏好表（error.type 给倾向方向、权重低，主决权给水平+情境+诊断器强信号）；语法类+诊断器强信号→显式/元语言 bias 非默认 recast；L&R 降级作编码框架；观测升级三维(uptake/full·partial repair/错误复发)。已写入《实施计划-v3.md》B2 代码设计版。

### 批注 B3-A · 元认知自评 + 回避观测 · 专项聚焦（2026-09-21 · 用户拍板"分叉1=A 改 undetermined、分叉2=A 半确定降校准中间态"）

> 现状核对：graph/model.py Node 以 KP 为主键、含 error_types/mastery/nature/unfixed_streak；**现无 meta_confidence 字段、无回避独立图谱类型** → B3 补。

**聚焦结论（带来源）**：
1. **自评 validity**：自评×表现=**校准信号**（Dunning-Kruger 1999 低技能高估；Phakiti 2005/2016 local confidence calibration；Glenberg illusion-of-knowing 流畅感≠能产出）；能力错觉由"确定但错"识别。
2. **"半确定"无独立理论**：归 **partial knowledge / calibration error**（非稳定量表，非第三能力态）→ 判定矩阵保留（校准 gap 路由），但不作独立习得状态。
3. **回避判据（Schachter 1974/Ellis 1985）**：目标结构**用得多但错率高=未习得**、**用得少+绕行=回避**；**现编句基线查无理论支持** → 弃，四态第4态改 **undetermined**（凡判不准保守）。
4. **自评频率无"≤1"硬依据**：仅原则性（高反馈意义节点=任务/剧情回放/目标构式产出后，嵌剧情非脱离）；后备低侵入信号=语速/停顿/纠错次数。
5. **建模**：自评/回避宜**独立事件流** + 节点留学习者快照（estimated_accuracy/calibration_error/avoidance_count），与 D-8 契约"事件落 message+Node 快照"一致。
6. **指标**：ECE/Brier/Calibration Gap/Confidence-Accuracy 一致性/Avoidance-Rate/Avoidance-Error Profile 可供 B6 回归。

**用户拍板 2 分叉均 A**：①回避四态=avoided/unlearned/learned/**undetermined**（弃现编句基线）；②"半确定"=partial knowledge/校准 gap 中间态（矩阵路由保留、不作文独立习得状态）。已写入《实施计划-v3.md》B3 代码设计版。

### 批注 B4-A · 记忆调度内核 fsrs 接入 · 专项聚焦（2026-09-21 · 用户拍板"三点确认"）

> 现状核对：`scheduler/fsrs.py` 自实现 FSRS-5(v5.1.3)纯标准库；被 `error_graph.py:386-391` 调用 `fsrs.next_state`/`fsrs.interval`；`FOSSIL_RULE={"unfixed_streak":3,"days_idle":180}` 在 error_graph.py:62。（早前已拍板替换自实现→开源 fsrs，见决策记录首批）

**聚焦结论（带来源）**：
1. **库选型**：`fsrs`(py-fsrs，兴盛 Ye/OSR) v6.3.2、**FSRS-6**、纯 Python(仅 typing-extensions, Python 3.10+)。(PyPI/GitHub/awesome-fsrs/OSR)
2. **API**：`Scheduler().review_card(card, rating)`→`(Card, ReviewLog)`；`get_card_retrievability()`；间隔=`card.due - last_review`；状态=Card.state/Step(New/Learning/Review/Relearning)。与现有 next_state/interval 分离语义**不同→需适配层**。
3. **序列化**：`Card.to_json()/from_json()` 含 stability/difficulty/due/last_review → **可保持 Node 4 字段 schema 不变**。
4. **首评**：新建 `Card()` 默认 S/D=None、首评自动给初始(不用自己算)；旧自实现 D0(4)/S=W[G-1] 兜底新卡源码即弃，旧卡 S/D=None 按首评重算。
5. **优化器**：`fsrs[optimizer]` 带 PyTorch 重依赖；初期数据量小**建议默认参数**、不装 optimizer，攒历史后再优化。
6. **卡粒度**：库模型单元=单 Card、无内置复合卡；词义项卡=单 Card、构式复合卡=单体复现达标升独立卡；**同原子卡不可双调度**。

**用户拍板三点确认**：①加 `FsrsSchedulerAdapter`(内部持 Scheduler、对外保 next_state/interval/MemoryState 语义，error_graph 调用点几乎不改)；②落盘 schema 不变(Node 4 字段只做 Card 字段映射、graph store 测试不动)；③**只装基础版 `fsrs`、不装 `[optimizer]`**(避免 PyTorch，优化器后置有历史后)。已写入《实施计划-v3.md》B4 代码设计版。

### 批注 B5-A · 脚手架渐褪 + 超纲确定性 + 排序层 · 专项聚焦（2026-09-21 · 用户拍板"①要事前兜底(方案3)、②对、③行"）

> 现状核对：`beyond_level`/解包均在 explainer.py，`levels.py` 管等级；`sort/rank.py` **待新建**。

**聚焦结论（带来源）**：
1. **脚手架**：ZPD 有据(van de Pol 2010 contingency+fading+transfer of responsibility)；**"四域四档"是产品自有实现、非学界既定分级**——表述注明"ZPD 原理落地"、勿引文献背书。
2. **等级受限释义**：业界成熟载体=CEFR 分级词汇(CEFRLex)+learner dictionary(定义不比被查词难, Cambridge)；**中文无同等成熟公开实现→中文解包属原创**。
3. **超纲检测**：**查表最稳**(非 LLM)；`lexicon_hsk1_4.json` source=GF0025-2021、word_count=3208 即权威真源；L2 readability 回归模型(ACM 2024)属"全文难度评估"另一题、勿与超纲检测混。**（2026-09-22 补注：目标真源已立项迁 2025 大纲 3.0=B8、优先在 B 前完成；2021 表仅为迁移完成前现行真源，B8 完成后查 `lexicon_hsk3_2025.json`——实施计划-v3 B5 已按此修正）**
4. **排序**："硬主序+同级破平"是课程排序常见模式(CEFR 分级+词频/覆盖)；**"排序单元=词义项粒度"查无行业标准→作产品规格非文献**；无现成 HSK 难度排序库→自建轻量 sort/rank.py。

**用户拍板 3 点**：①**超纲兜底 = 方案 3「事前约束+事后拦截」结合**（事前=生成前喂「目标词等级+该学习者已掌握词集(复用图谱 mastery 不另造、只喂等级段高频防 prompt 膨胀)」硬约束；事后=输出仍走超纲硬查兜漏网；两者互补非互斥）；②**rank 粒度跟随 §4a**（白名单词→义项粒度、其余词→词形，与调度主键一致不产生新口径）；③comm 实值仍等教材线、不卡本轮。已写入《实施计划-v3.md》B5 代码设计版。

### 批注 B6-A · 评测与回归闸门 · 专项聚焦（2026-09-21 · 用户拍板"①接受校验前置、②接受 CI 分层、③计数断言走确定性脚本 + 逐条落全部调研结论"）

> 现状核对：`datasets/eval/tutor_quality/` 链路**已存在**（run_tutor_judge.py/judge.py/rubric.md/cases.json/build_cases.py/README）→ B6 是对接+扩展非从零建；另有 golden_v1_4/retell_golden/cged_recognition 语料齐备。

**聚焦结论（带来源，逐条对应 B6 落点）**：
1. **judge 可靠化**(LLM-as-a-Judge 2026 主流、不可裸上线；Zheng et al. 2306.05685 / Wang et al. 2305.17926 / arXiv 2606.19544)：①**先 judge-human 校准**——100-200 条代表对话人工标注 vs judge 评分，**Cohen's kappa ≥0.7 才启用**(业界达标线：生产≥0.6、强≥0.8，保守取≥0.7)；②**score anchor+固定 rubric**、**跨家族 judge**(避自我偏好 10-25%)、pairwise A/B+B/A 双跑取均值(控位置偏好 / verbosity)；③**别用简单一致率**(chance-corrected 高估，cohort mean 高估 38.6pp)；3+ 标注者才切 Krippendorff's alpha。
2. **计数断言接法**：D-6 八个计数**走确定性文本统计脚本+pytest assert**，LLM judge 只管 D-4 语义质量、不承担计数(FutureAGI LLM testing pyramid 最底层=deterministic/schema/regex/scanner)。
3. **CI 稳定性**：全 pin `judge_model_id+rubric_version+prompt_template_hash` 三元组、temperature 0、pin dataset/prompt/model 版本；阈值用**统计门控**(pass rate/p95/delta gate 与 baseline 比，防慢漂移)非逐字 exact-match。
4. **回归闸门分层**：五层金字塔(deterministic→regression eval→integration→red-team→canary)；`run_all.py` 单入口、PR 只跑影响路径轻量 subset(cents 级)、全量放 nightly(防 CI 过慢 flaky)；红 line 放 deterministic 层、judge 只管 D-4。
5. **judge 漂移**：judge 是需版本管理的生产组件；升级/改 rubric 按迁移处理(重跑校准集重算 kappa，升级 mean shift 3-8pt 不可裸换)；高 stakes 门禁可多票。

**用户拍板 3 点 + 逐条落点**：①接受 judge-human 校验前置(100-200条/kappa≥0.7)；②接受 CI 分层(PR subset+nightly 全量)；③计数断言走确定性脚本。**全部调研结论逐条已写入《实施计划-v3.md》B6 代码设计版（内容 5 块 + 验收 6 条）**。

### 批注 B7-A · 产品外壳 · 专项聚焦（2026-09-21 · 用户拍板"Vite+React Router、token 统一积分、出海/落地页暂缓"）

> 现状核对：`engine/serve.py`=手写 `http.server`(ThreadingHTTPServer)+ 一把锁串行、**非流式**(`/api/process` 一次性返回契约 v1 JSON)；`web/index.html`=单文件原生 JS 188KB(非 React)；`engine/visitor_gate.py`=游客**按次**(每条对话计1)日额度(cookie vid 跨天重置)、`uses_owner_key`(未带 provider 或 ==env)→计配额、**BYOK 无限**；**全项目无 token usage/total_tokens 记录**(LLM client 不返回用量)→token 线性计量是**全新落点**；`GET /api/graph` 返回 nodes/edges/queue 已有图谱数据面。

**聚焦结论（带来源）**：
1. **token 计费主流 = input/output 分离单价**，用量取自 API `usage` 字段，LiteLLM 可做 key/user/team 粒度 spend tracking；DeepSeek/Qwen 均 input/output 分别计价（[LiteLLM](https://docs.litellm.ai/docs/proxy/cost_tracking)、[DeepSeek](https://www.alibabacloud.com/blog/603181)）。
2. **"失败零产出不扣"查无业界统一规范**（公开资料只确认"按实际使用 token 计费"）→ 是我们的**产品规则非行业惯例**，无需行业背书。
3. **"按日刷新不可攒"查无明确主流依据**；但 ChatGPT/Claude/Gemini 免费额度普遍按 message/会话/小时/日做**周期限制**，"按日周期而非攒余额"有先例，方向成立。
4. **限流算法**：token bucket 适合"平均速率+短时突发"；"当日额度固定、用完即止"更贴 **fixed-window/日档位**，不是简单 token bucket（[API Rate Limits](https://stochasticsandbox.com/posts/api-rate-limits-compared-2026-06-06/)、[Azure OpenAI Quotas](https://learn.microsoft.com/fr-ca/azure/foundry/openai/quotas-limits?view=azureml-api-2)）→ B7 走"日额度档位"。
5. **前端形态**：免装响应式纯展示壳推荐 **Vite SPA + 反向代理**（非 Next.js）；静态图谱用**表格/卡片/进度条**即可，不必引复杂图表库（业界查无强制标准）。Vite=构建工具、产物纯静态；Next.js=全栈框架自带 SSR/SEO/API routes，本项目交互 App 全用不上、白背 Node 常驻（对比经用户演示确认）。
6. **SSE 契约**：`sse-starlette` 的 `EventSourceResponse` + 前端 `EventSource` 监听 `message/done/error` 事件（[sse-starlette](https://pypi.org/project/sse-starlette/3.1.1/)）；**断连重连 OpenAI 原生不支持、需后端管**。
7. **12 回合成本锚**：查无"一节课/一轮教学对话 token"公开统计 → 按 DeepSeek/Qwen 单价自算；印证总纲"实参留待真实成本回填"。

**用户拍板（2026-09-21）**：
- **①React 框架 = Vite + React Router（SPA），配反向代理**（经对比 Vite vs Next.js 利弊后确认）。
- **②token 计量 = 统一"能量/分"、单条不跳 token 分、背后按 input/output 真实成本折算**（用户核过国内厂商也是这版，维持总纲）。
- **③出海、落地页/SEO 暂缓，不做进 B7**（用户拍板"先不考虑"）；B7 范围收敛回总纲路线 B"React 纯展示壳 + Python + SSE"，前后端分离部署、跨域可配暂按本地部署假设。
- 已锁（总纲§7，本调研仅印证/明定性）：token 线性(输入+输出)/按日刷新不可攒/单会话≤12回合/防嫖(token 线性+单次输入长度上限)/可见口径(日能量+任务级余额+预计消耗、单条不跳 token 分、换算公式帮助页可查)/静默拦截三档文案已定。

### 批注 B8-A · lexicon 迁 2025 大纲 3.0 · 专项聚焦（2026-09-21 · 用户拍板三项+换源已代码核证）

> 现状核对：`syllabus_hsk30_2025.json`=**3.0 语法**大纲(593点,源自 krmanik/HSK-3.0, CC BY-SA 4.0,本源 GF0025-2021 语法等级大纲)——3.0 语法表**已入库**；`lexicon_hsk1_4.json`=**GF0025-2021 旧版字词表**(词3208/字1200,扁平 `{词:级}`/`{字:级}`,认读书写不分轨)——**真 3.0 字词表未入库=B8 核心**；`convert_lexicon.py` 2021 专用需新增 3.0 版；消费方=`recognizer.detect_beyond_level`(读 word_level 最长匹配,`>learner+1` 判超纲)+`rag.ingest_lexicon`(读 word_level)+义项 tools+超纲约束 eval/tests+golden 重标。

**聚焦结论（带来源）**：
1. **HSK 3.0 = 2025.11 发布 / 2026.7 实施的最新版**(汉考国际/孔子学院)，非 2021 版。
2. **3.0 官方 1-4 级字词量 = 词汇 2000 / 认读字 1096 / 书写字 400**(累计)（[learn-chinese.online 官方数据表](https://learn-chinese.online/mod/page/view.php?id=1836)）——与立项"约 2000 词"吻合；1-6 级全量=5400 词/1940 认读字/700 书写字。
3. **GF0025-2021=四维基准**(音节/汉字/词汇/语法)，**无认读/书写双轨**（[教育部说明](http://www.moe.gov.cn/jyb_xwfb/s271/202104/t20210402_524194.html)）→ 现有 lexicon 不分轨是对的。
4. **最佳现成源=`krmanik/HSK-3.0`(2025 版)**：含 `HSK Words`(7 txt)+`HSK Hanzi`(认读)+`HSK Handwritten`(书写)分轨目录、1-9 级全、CC BY-SA 4.0、来源含官方 PDF（[仓库](https://github.com/krmanik/HSK-3.0)、[License](https://github.com/krmanik/HSK-3.0/blob/main/License.md)）；`elkmovie/hsk30`(MIT, wordlist+charlist, 官方 PDF OCR)可作交叉核对（[elkmovie/hsk30](https://github.com/elkmovie/hsk30)）。
5. **无公开 2.0↔3.0 逐词等级映射表**（查无）→ 迁移只能语义对齐，不靠官方对照表。
6. **分轨 schema 惯例**：业界常用 `char→recognize_level∥write_level` 或拆文件，无统一字段名，自定即可。
7. **口径差待标注**：Explore 见 GF0025-2021 标准内"汉字900/词汇2245"，与我们 2021 1-4 级 xlsx 去重 3208 词/1200 字不冲突(前者标准内基础表、后者 1-4 累计表)，B8 迁移后以官方 3.0 数字(2000/1096/400)为锚。

**用户拍板（2026-09-21）**：
- **①数据源 = 接受**：`krmanik/HSK-3.0`(2025 版)作真源 + `elkmovie/hsk30`(MIT)交叉核对；接受 **CC BY-SA 4.0 披露**（产物 lexicon JSON 保留署名/相同许可，与教材版权护盾同类考量）。
- **②分轨范围 = 认同**：**词表纯 3.0 词(~2000)不分轨；字表认读/书写分轨**（schema `char→level→track`，认读1096/书写400，即 999 只认不写 + 400 认且写）。语法表只补对齐不重组；golden 重标非双轨（沿用已锁口径）。
- **③超纲判定 = 只换源（已代码核证，算法零改动）**：全库消费 `char_level` 的仅 convert(生成)+recognizer.load_lexicon 取键+detect_beyond_level 解构，**无任何行真用 char_level 值判超纲**；超纲扫描全程只用 `word_level`(recognizer.py:113-137)；`rag.ingest_lexicon` 也只读 word_level(rag.py:85)。3.0 词不分轨→`word_level` 键结构不变(仅值随源换)→ recognizer/rag **零改动**。
- **顺带影响（立项口径预期非新决策）**：3.0 只迁 1-4 级词(2000)<旧 3208 → 超纲判定覆盖率略降(部分旧表词不在新表→不报超纲，更"宽容")，符合已锁"1-4 级纯 3.0 词表"口径。