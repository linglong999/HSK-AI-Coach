# HSK-AI-Coach

> 一个基于**费曼教学法**、面向 HSK 3.0 中文学习的**偏误纠错引擎**。
> "planner 自由 ReAct 编排 + 统一 Skill 注册表（识别/讲解/验证/图谱…）+ 偏误图谱数据层"架构，完成"识别偏误 → 费曼式讲解 → 复述验证 → 图谱沉淀 → 间隔复习"的完整闭环。

**一句话定位：领域层费曼偏误纠错引擎，让 AI 真正理解"中文学习者错在哪、为什么错、怎么讲才懂"。**

- **零第三方依赖**：运行时只用 Python 标准库，clone 即跑
- **API Key 自备（BYOK）**：默认 DeepSeek（已评测校准）；也可在界面「设置 → 模型密钥」直接添加任意 OpenAI 兼容供应商（Qwen/GLM/Kimi/Ollama…），即时生效免重启
- **全程可评测**：黄金句集 + 评测脚本 + 多轮实验记录，每个指标都可复跑核验

---

## 快速开始（最短路径）

### 1. 克隆并配置

```bash
git clone <repo-url> && cd HSK-AI-Coach
cp .env.example .env
# 编辑 .env，填入你的 DEEPSEEK_API_KEY（https://platform.deepseek.com）
```

无需 `pip install`——运行时零第三方依赖（.env 由 `config/settings.py` 内置解析器加载）。

### 2. 冒烟测试（不需要 API Key）

```
python -m unittest discover tests -v
```

**32 个测试全部 PASS** 即说明链路完整（识别/讲解/图谱/降级兜底/前端服务层，全部 mock，不调 LLM 不花钱）。当前主支已扩至 **260 个测试**。

### 3. 跑起来

```bash
# 模式一 · 纠错闭环：输入一句中文 → 识别 → 四段式讲解 → 图谱沉淀 → 复习队列
python -m engine.demo_loop "我想买苹果很多。"

# 模式二 · 文件讲解：打开一篇中文 → 选中片段 → 偏误走纠错链 / 正确句走泛讲解
python -m examples.demo_file

# 固定样本演示（识别 → 讲解 → 复述验证 → 图谱全流程）
python -m examples.demo
```

### 4. 启动前端壳（认知地图 · 免配置）

```bash
python -m engine.serve          # 浏览器打开 http://127.0.0.1:8612
```

零依赖 HTTP 服务（仅标准库），原生 JS+SVG 单页，**无需安装前端依赖**。**v2（Explore 化改造）**：
- **左侧栏**：常驻 AI 助教 + 会话列表（首条消息自动命名，独立上下文，URL 深链 `?conversation=` 可收藏回访）+ 学习动线（开始复习 / AI 学习报告 / 阅读材料）
- **中央对话画布**（全宽，内容居中限宽）：偏误卡 → 费曼讲解卡 → 学习成果卡；卡片悬停出现分支动作 **↗ 深挖 / → 相邻 / ↓ 待复习**——全部**锚定图谱权威边**（无权威边的方向置灰，守住"图谱唯一权威、无自由发散"）
- **认知地图独立视图**：仅经侧栏「◈ 认知地图」进入，全屏呈现（横轴 HSK 等级 × 纵轴掌握度 + 复习队列）；对话流内点知识点（下划线名词/报告行/复习项）自动跳入地图并定位节点
- **Smart Annotation**：AI 回复中你图谱里已有的知识点带下划线，点击在地图定位
- **两套主题**（孟菲斯 / 暖橙）：单强调色 + 灰阶，语义色（KP 类型四色 / 偏误红绿）不随主题降级
- **BYOK 模型密钥**（0.20）：「设置 → 模型密钥」添加任意 OpenAI 兼容供应商（测试连通/设默认/删除，即时生效）；输入框左上角按会话切换模型（DeepSeek 已按 M6 评测校准，其他模型未评测）
- **隐藏内部工具轨迹**，只显示转译后的学习成果卡

**自由对话（`/api/dialog`，planner 唯一入口）需 LLM Key**：未配置任何供应商时前端直接报错并给出配置指引（fail-loud，不静默降级）——两种配置方式：① `.env` 填 `DEEPSEEK_API_KEY` 后重启；② 界面「设置 → 模型密钥」添加任意 OpenAI 兼容供应商（即时生效）。仅用确定性纠错闭环（`/api/process`）时可不配 Key，识别自动降级为规则回退、一行不崩（见 "降级策略"）。API 采用**契约化 JSON**（对话契约见 `datasets/docs/0.17-统一能力契约与架构总纲.md`，纠错契约见 `datasets/docs/JSON-接缝契约-v1.md`，前端 v2 见 `datasets/docs/0.19-前端v2-Explore化改造.md`）。

## 两种使用模式

| 模式 | 场景 | 链路 |
|------|------|------|
| **模式一 · 纠错闭环** | 学习者主动输出中文（对话/写作） | 识别引擎 → 讲解引擎 →（复述验证）→ 图谱写入 → 复习队列 |
| **模式二 · 文件讲解** | 学习者阅读中文材料，选中不懂的片段 | 判定分流：含偏误 → 纠错链 + 图谱沉淀；正确片段 → 泛讲解（不污染图谱） |

## 架构

```
                     ┌──────────────┐
   学习者输入 ──────→ │  planner 自由 ReAct  │──────→ 自然语回复 + 技能轨迹（trace）
                     │  （唯一对话入口）      │
                     └──────┬───────┘
                            │ 自由选择
                            ▼
              ┌─────────────────────────┐
              │   统一 Skill 注册表（9 技能）    │
              │  identify_errors          │
              │  explain_error            │
              │  verify_retell            │
              │  lookup_knowledge_point   │
              │  retrieve_corpus          │
              │  get_review_queue         │
              │  generate_unit            │
              │  web_search               │
              │  parse_document           │
              └────────┬────────┬─────────┘
                       │        │
              ┌────────▼──┐  ┌─▼──────────┐
              │ 三引擎     │  │ 工具 / 生成  │
              │(识别/讲解/  │  │(搜索/文档/   │
              │  验证)     │  │ 费曼单元)    │
              └────┬──────┘  └──────┬──────┘
                   │                │
                   ▼                ▼
              ┌──────────────────────────────┐
              │  偏误图谱（确定性数据层，纯规则）    │
              │  KP 节点 / 混淆边 / 幂等 / aging │
              └───────────────┬──────────────┘
                              │
              ┌───────────────▼──────────────┐
              │  serve.py /api/dialog /api/process  │←── web/index.html
               │  /api/graph /api/verify /api/generate│
               │  /api/profile /api/conversation     │
               │  /api/providers (BYOK 模型密钥)     │
              └──────────────────────────────┘
```

**设计要点**
- **planner 自由 ReAct 为唯一对话入口**（`/api/dialog`），router 保留为兼容遗留 runner（`/api/process` 不变）
- **自由在调度，约束在数据写**：planner 自由选技能，但图谱写入经 writeback 两段式 + 幂等键，防漂移
- **LLM 只做带宽，规则做守门**：识别结果经置信度决策序列 + 确定性护栏（把字句白名单、"地"可省等）双重过滤，防过度纠正
- **降级兜底**：LLM 网络瞬时故障自动退避重试；识别不可用时回退规则匹配（只产"待确认"候选，**绝不污染图谱**）；讲解/验证/图谱写任一环节失败，其余链路照常完成并记录 `degraded`
- **连续验证不过自动升级**：同一知识点复述连续 2 次不过 → 降难度重讲；连续 4 次 → 建议找老师
- **HSK 等级约束防幻觉**：对照 HSK1-4 官方词表判定超纲，知识点清单外不编造
- **前端壳只是壳，不碰数据规则**：认知地图只渲染图谱"已确认"节点/关联边，无自由发散——发散只能沿权威边（知识点→关联→混淆边→待复习）生长

## 评测指标（终态）

| 能力 | 指标 | 值 |
|------|------|-----|
| 偏误识别（3.1，45 句黄金集） | F1 / 精确 / 召回 | **0.97** / 100% / 94.12% |
| | 干净句误报 / 对抗过度纠正 | **0%** / 0 |
| 文件讲解（3.5，41 条） | 偏误分流正确率 | **94.1%** |
| | span 精确命中 / 包含率 | 76.5% / 94.1% |
| | 正确句误报 / 泛讲解合规 | **0%** / 100% |
| 复述验证（3.3，60 条） | 判定一致性 / 通过准确率 | **88.2%** / 94.4% |
| | 流利空洞拦截率 | 93.75% |
| 图谱数据层（3.4） | 幂等/单调性/写接口验证 | **18/18 PASS** |
| 自由对话（M6，15 条黄金集，两轮真实实跑） | 技能轨迹命中 / 终止率 | **80%~86.7%** / **100%** |
| | LLM-judge 达标率 / 平均步数 | **93.3%~100%** / ≤1.94 |

完整"假设→改动→指标→结论"逐轮记录见 [`datasets/eval/多轮实验记录_3.6.md`](datasets/eval/多轮实验记录_3.6.md)（含 6 条方法论教训与已知残留，可作技术评审证据）。所有数字可由 `datasets/eval/*.json` 与根目录评测脚本复跑核验；自由对话指标可由 `python -m eval.run_eval_agent` 复跑（需 `.env` 配 Key；无 Key 用 `--mock --no-judge` 跑过程判据），实跑记录见 `eval/m6_real_run_20260901*.txt`。

## 项目结构

```
HSK-AI-Coach/
├── config/settings.py          # 配置 + 零依赖 .env 加载器
├── planner/
│   └── loop.py                 # planner 自由 ReAct（唯一对话入口）+ skill 注册/调用 + trace
├── skills/
│   ├── registry.py             # Skill 注册表（统一协议：metadata + input/output_schema + run）
│   └── identify_errors / explain_error / verify_retell /
│       lookup_knowledge_point / retrieve_corpus / get_review_queue /
│       generate_unit / web_search / parse_document   # 9 个自由技能（薄壳包引擎）
├── engine/
│   ├── router.py               # 兼容遗留 runner（/api/process 固定序，不进新叙事）
│   ├── recognizer.py           # 识别引擎（LLM + 置信度决策 + 确定性护栏）
│   ├── explainer.py            # 讲解引擎（费曼四段式）
│   ├── verifier.py             # 验证引擎（逐点判定 + 规则聚合）
│   ├── file_mode.py            # 模式二：文件讲解两路分流
│   ├── serve.py                # 零依赖 HTTP 服务层（/api/dialog /api/process /api/graph…）
│   ├── demo_loop.py            # 模式一单命令入口
│   ├── graph/error_graph.py    # 偏误图谱（确定性数据层）
│   └── llm/client.py           # LLM 客户端（DeepSeek/Qwen + 退避重试）
├── web/index.html              # 前端壳（对话画布全宽 + 认知地图独立视图[侧栏入口]）
├── eval/                       # M6 自由对话评测（黄金集 15 例 / judge / 实跑记录）
├── datasets/
│   ├── knowledge_points_v1_4.json   # HSK1-4 知识点清单（骨架版）
│   ├── lexicon_hsk1_4.json          # HSK1-4 权威词表（超纲判定真源）
│   ├── HSK1-4_字表词表_GF0025-2021.xlsx  # 官方词表原始文件（来源：国家标准 GF0025-2021）
│   └── eval/                    # 黄金集 / 评测脚本 / 结果 / 实验记录
├── examples/                    # demo / demo_file
├── tests/                       # 240 个测试（免 Key，全 mock）
└── run_eval*.py                 # 3.1/3.2/3.3 评测入口
```

## 数据与语料来源

- 黄金评测集（`golden_v1_4.json` 45 句 / `retell_verification.json` 60 条）为**自建**，偏误样本经二语教师仲裁定稿
- 复述验证集方向二取材自 [MuCGEC](https://github.com/HillZhang1999/MuCGEC)（NAACL 2022）与 CGED（HSK 动态作文语料库）等真实学习者语料
- 第三方原始语料**不随仓库分发**（见 `.gitignore`），版权归原作者/机构，请按原来源自行获取
- `HSK1-4_字表词表_GF0025-2021.xlsx` 为 HSK3.0 官方字表词表原始文件，来源：国家标准《GF0025-2021》/ 教育部中外语言交流合作中心发布。作为引擎词集的原始数据随源码分发，便于他人复现；如对再分发有疑虑可移除该 xlsx（引擎已内置转换后的 `lexicon_hsk1_4.json`）

## Roadmap

- [x] M1: 架构定稿（三引擎+图谱+轻路由）+ 2.1-2.4 落地设计
- [x] M2: 前置数据资产（HSK 知识点清单 + 官方词表）
- [x] M3: 识别 baseline 评测 + overcorrection 专项（误报 10.5%→0%）
- [x] M4: 讲解引擎评测（四段式模板）
- [x] M5: 复述验证黄金集（MuCGEC/CGED 数据底座）
- [x] M6: 复述验证评测（判定一致性 88.2%）
- [x] M7: 模式二文件讲解（两路分流 + 3.5 评测）
- [x] M8: 图谱验证（18/18）+ 多轮实验记录
- [x] M9: 最小可运行闭环 + 降级兜底
- [x] M10: 前端壳（认知地图 + 费曼对话的交互界面）
- [x] 0.17: 架构反转转正 —— planner 自由 ReAct 为唯一对话入口（`/api/dialog`），router 拆散为自由技能，全部能力收敛到统一 Skill 协议；前端壳切左右布局 + 学习成果卡
- [x] 0.20: BYOK 模型密钥 —— 界面内添加任意 OpenAI 兼容供应商（即时生效免重启），输入框按会话切换模型；json_mode 400 自动降级兜底（详见 `datasets/docs/0.20-BYOK模型密钥与模型选择.md`）
- [ ] M11: 参数标定（aging/mastery 等间隔复习参数）+ 评测集扩集（汉字/词汇/语用）

## License

**[MIT License](LICENSE)**（2026）。允许自由使用、修改、分发与商用，需保留版权声明。

---

**说明**：本项目为教育/研究用途的开源项目。API Key 由使用者自行提供，本项目不承担任何 API 费用；纠错建议仅供学习参考。
