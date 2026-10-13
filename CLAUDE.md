# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

HSK-AI-Coach：面向 HSK 中文学习者的 AI 偏误纠错陪练。核心闭环＝偏误检测 → 讲清原因 → 学习者复述检验 → 沉淀进复习计划。用户沟通语言为中文。

## 常用命令

依赖用 `uv` 管理（`pyproject.toml` + `uv.lock`；CI 使用 `uv sync --frozen`，Python 3.11）。

```bash
uv sync                                             # 运行时 + dev 依赖 + 可编辑安装（src/ 下的 engine/config/planner）
uv run pytest tests -q                              # 全量代码回归（全 mock，免 Key、不花钱）
uv run pytest tests/test_dialog_run.py -q           # 单个文件
uv run pytest tests/test_dialog_run.py -k 名称 -q   # 单个用例
uv run python -m datasets.eval.run_all --layer deterministic   # 教学确定性评测门禁（PR gate，强制离线、零 LLM）
uv run python -m datasets.eval.run_all --layer llm_judge       # 真实模型语义评测（花钱，nightly 层）
uv run python -m engine.serve                       # 启动服务 http://127.0.0.1:8612（等价 `hsk-coach`）
uv run python -m engine.demo_loop "我想买苹果很多。"  # 命令行纠错闭环 demo
```

前端 `web/`（React 18 + Vite + TS）：`npm run dev` / `npm run build`。`serve.py` 在 `/` 优先挂载 `web/dist`，未构建则回退 `web/` 目录本身；`/legacy/` 挂载 `web_legacy/` 旧工具壳。

CI（`.github/workflows/ci.yml`）有两道独立门禁：pytest 与 deterministic 评测，二者是不同类型的证据，“完成”须分别附上输出。最新验证数字记在 `EVALUATION.md`，不要把测试数量硬编码进 README。

配置：`.env`（从 `.env.example` 复制，默认 `DEEPSEEK_API_KEY`），由 `src/config/settings.py`（pydantic-settings）加载。路径统一走 `src/config/paths.py`。未配 Key 时 `/api/process` 的确定性纠错仍可用；`/api/dialog` 会如实报错，不静默降级。

## 架构（大图）

注意：README 里的结构图省略了 `src/` 前缀。实际可导入包位于 `src/engine`、`src/planner`、`src/config`；`skills/`、`datasets/`、`eval/`、`examples/` 在仓库根目录。

- **唯一对话入口** `/api/dialog`（SSE 四事件）→ `src/planner/loop.py` 自由 ReAct，从统一技能注册表 `skills/registry.py` 选技能（9 个，每个都是包装引擎的薄壳）。
- **编排层**：`engine/dialog_service.py`（DialogService）持有所有进程级实例缓存与一把 `RLock`；`engine/dialog_run.py`（DialogRun）按阶段编排 dialog 主链（AssembleDirectives → PreScanIntervene → CallPlanner → WritebackLedger → AssembleResponse）。不变式（缓存只归 DialogService、协作器内部不加锁、`DialogService._xxx` 静态助手是测试 patch 的稳定挂点）及领域术语见 **`CONTEXT.md`**，改编排层前必读。
- **三引擎**：`recognizer.py`（识别）/ `explainer.py`（讲解）/ `verifier.py`（复述验证）。辅以确定性策略模块 `intervention.py`（none/light/block 三档）、`persona.py`、`transfer.py`（L1 迁移归因，只供讲解、不入图谱）。这些策略模块刻意不统一成一个 Strategy 接口。
- **偏误图谱** `engine/graph/` 是唯一权威的确定性数据层：LLM 只选路径，不改数据；写入为两段式 + 幂等键。降级时只产出“待确认”候选，绝不污染图谱。
- **记忆/账本** `engine/memory/`：SQLite（`data/coach.db`），包括事件账本、学习者记忆、writeback。复习调度见 `engine/scheduler/fsrs_adapter.py`（fsrs 库）。
- **HTTP 层** `engine/serve.py`（FastAPI）：`/api/process` `/api/graph` `/api/verify` `/api/generate` `/api/providers` `/api/metrics` `/api/ocr` `/api/quiz` 等。供应商/BYOK 见 `providers.py` + `provider_secret.py`（Windows DPAPI），游客额度见 `visitor_gate.py` / `quota.py`。
- **词表真源**：`datasets/lexicon_hsk3_2025.json`（2025 HSK 大纲 3.0）。旧版 `lexicon_hsk1_4.json` 保留，仅供对照。
- OCR（`rapidocr_onnxruntime` + `pymupdf`）属例外依赖，未安装时相关测试自动 skip、功能自动降级。

## 必须遵守的规则（摘自 `HSK-AI-Coach-执行规范.md`）

- **R1 范围**：只改本批声明的文件；不做“顺手修改”。
- **R2 契约**：凡触及 schema、KINDS 常量、SSE 事件、阈值、排序粒度、命名，必须同步回写 `实施计划-v3.md` 对应批次并记账，禁止静默漂移。契约文档在 `datasets/docs/`。
- **R3 最小**：不重构；计划外的新依赖或技术选型必须先停下来问用户。
- **R4 证据**：声称“完成”须附命令输出或测试结果。
- **必须先问用户的情形**：跨批契约改动、砍需求或推翻已定拍板点、计划外依赖、不可逆操作（删库、改 git 历史、改共享配置、不可回滚的迁移）。
- 架构决策记在 `DECISIONS.md`，变更记在 `CHANGELOG.md`。
- 全仓库行尾为 LF（`.gitattributes` 中 `eol=lf`；本机 `core.autocrlf=true`）。`ci.yml` 必须保持 LF。

## 多智能体协作

本工作区由三方协作：用户拍板；**Claude 负责架构、规划、拆批次、写任务卡、审查**；**Codex 负责写全部代码**。Codex 的规则见 `AGENTS.md`。

- **Claude 不改代码**：不改 `src/`、`tests/`、`web/`、`web_legacy/`、`skills/`、`scripts/`、`tools/`、`eval/`，也不改 `datasets/` 下的 `.py` 脚本。产出是设计文档、任务卡和审查意见。
- **任务卡**放在 `tasks/`，模板为 `tasks/_template.md`。卡片中「目标」至「拍板点」各节由 Claude 写；文件边界、契约、验收断言要写得精确到可以逐字执行（Codex 执行得很死板）。拍板点全部确认后，才能把状态设为「待开工」。
- **规模**由 Claude 判定。小卡＝单文件或少量改动、无契约变更、无设计取舍，其余一律算大卡。大卡流程：待开工 → 方案待审 → 可执行 → 进行中 → 待审查 → 通过/打回。小卡跳过方案审查。
- 用户在 Codex 里用两个对话：**计划对话**写「实施方案」，**执行对话**写代码并记「执行日志」。
- **审查点 1（大卡，方案待审时）**：对照任务卡检查实施方案，看理解是否有偏差、是否超出文件边界、假设是否成立、是否漏了风险。把意见写进「方案审查 → Claude 意见」。Claude 认可时，可以直接在「用户决定」一栏代填「批准（Claude 代填）」，然后把状态改为「可执行」（用户 2026-10-01 授权）；不认可，或者涉及需要用户拍板的取舍时，必须先问用户。
- **审查点 2（待审查时）**：对照文件边界核对 `git diff`；对照「执行日志」检查偏离方案的地方是否合理；检查是否有静默契约漂移；复跑 pytest 和 deterministic 门禁；结论写进「审查意见 → Claude 结论」。
- **模型分工**（用户 2026-10-04 授权由 Claude 自行决定）：写卡、审方案、做取舍用 Opus；审查点 2 的代码审查、复跑和突变验证，派给 `model: "sonnet"` 的子代理，主对话抽查它给出的关键证据。
- **审查子代理的安全规则**（2026-10-09 事故后定）：工作区有多张卡未提交时，审查一律串行，不并行。派审查时，提示词里必须禁止 `git stash/checkout/restore/reset/clean` 这类会改写整个工作区的命令；突变的做法是先把单个文件备份到 %TEMP%，改完再还原，并核对 sha256。开审前和审完后都要核对其他卡关键文件的哈希，也要核对本卡全部改动文件的哈希（和完成报告记录比）。子代理报告说"已还原"，不能当成证据；主对话自己再跑一次全量测试。
- **省 token 规则**（用户 2026-10-05 采纳）：
  - 写卡时在「突变自检」表中列出本卡的每一道安全闸。突变验证由 Codex 交卡前完成，审查时只抽查 1–2 条，不再全部重做。
  - 审查分档：大卡首轮审查改动多，派 Sonnet 子代理；复审和小卡，主对话直接看几个关键点（diff 范围、新测试、复跑数字），不派子代理。
  - 多用小卡：改动小、没有契约变更、没有设计取舍的工作，一律开小卡，跳过方案审查。
  - 读输出时用 grep 和 tail 只看关键行，不要整段打印。
- **一卡一 commit**：提交信息以批次编号开头。提交前把卡片移到 `tasks/done/`，和代码一起提交。卡片对应哪个 commit，用 `git log -- tasks/done/<卡片>` 查询，不在卡片里回填。`tasks/` 根目录只放未完成的卡。
- **遗留问题**：审查中发现但不属于本卡范围的问题，记在卡片末尾的「遗留问题」里，作为下一张卡的来源。
- **契约和计划**的回写（`实施计划-v3.md`、`DECISIONS.md`、`datasets/docs/`）由 Claude 负责。
- 工作区里不是自己产生的未提交改动，一律不要修改、回退、暂存或提交；提交只在用户要求时进行，并且只 `git add` 具体文件。


## 创作素材记录

本人在做个人 IP。本项目接入了素材记录，规则见 `AGENTS.md` 末尾的「创作素材记录（.journal/）」一节，Claude 同样遵守。素材目录：`D:\HuaweiMoveData\Users\曹玲珑\Desktop\HSK\.journal\`（在仓库外层，不提交）。
