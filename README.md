<p align="center">
  <b>HSK-AI-Coach</b> · 面向 HSK 中文学习者的 AI 偏误纠错陪练
</p>
<p align="center">
  拿它练习写/说中文，而不是拿它问答案。
</p>
<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-green.svg?style=flat-square" alt="License: MIT"/></a>
  <img src="https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.11"/>
  <img src="https://img.shields.io/badge/deps-0%20third--party-4C9A2F?style=flat-square" alt="零第三方依赖"/>
  <img src="https://img.shields.io/badge/tests-633%20passed-brightgreen?style=flat-square" alt="633 tests"/>
  <img src="https://img.shields.io/badge/LLM-DeepSeek%20%E7%AD%89%20OpenAI%20%E5%85%BC%E5%AE%B9-FF6B35?style=flat-square" alt="LLM providers"/>
  <img src="https://img.shields.io/badge/OCR-RapidOCR-4F8EF7?style=flat-square" alt="OCR: RapidOCR"/>
  <img src="https://img.shields.io/badge/UI-0%20frontend%20deps-8E6BF3?style=flat-square" alt="UI: 原生单页"/>
</p>
<p align="center">
  <a href="#-快速开始">快速开始</a> · <a href="#-核心能力">核心能力</a> · <a href="#-效果核验">效果核验</a> · <a href="#-架构">架构</a>
</p>

---

## 📌 近期更新
- **2026-09 · P0.7 OCR+RAG** — 接入 RapidOCR（本地推理、零 API 费），图片/PDF 一键转文本进纠错链；OCR 校正语料沉淀进检索索引，`RAG 回答带来源`。首轮真实引擎实测采集 68 组汉字误读对。
- **2026-09 · 效果度量与数据闭环** — 新增 `engine/metrics.py` + 度量面板（续学率/通关率/满意度等 4 指标带口径文档），对话结束 1–5 星评分自动回写，指标随使用自动更新。
- **2026-09 · 免费访客闸门 + BYOK** — 未配 Key 的游客额度闸门（并发安全、跨天重置）；界面内直接加任意 OpenAI 兼容供应商并免重启切换。
- **2026-09 · 习得伙伴转向** — L1 母语迁移归因、生活场景对话、介入时机三档 + 个性化，从"纠错引擎"转向"让人愿意开口的陪练"。

## 📖 项目简介
**HSK-AI-Coach** 是一个开源的 AI 中文学习陪练：输入一句中文或一段对话，它告诉你**错在哪、为什么错、该怎么说**，并把每次纠正沉淀进一张偏误图谱，过段时间主动拉你复习。核心闭环一句话：**偏误检测 → 讲清原因 → 让学习者自己复述检验 → 沉淀进复习计划**。设计原则是"能交流就不打断，卡住才出手"。

全部用 Python 标准库实现，**clone 即跑、零第三方依赖**；每个能力都配可复跑的黄金评测集与真实模型实跑，不是"感觉效果不错"。

### ✨ 核心亮点
- **偏误识别做到"宁可漏判，不误报"** — 干净句误报确定性护栏压到 **0%**
- **费曼式复述检验** — 讲完让学习者用自己的话复述，而不是看一遍答案就"懂了"
- **偏误图谱是唯一权威** — 确定性数据层，AI 只能选路径、不能改数据，结果可复现
- **介入时机三档（none/light/block）** — 流利时静默记录，卡壳/求助才出手，打断上限服务端强制
- **教学语言分层** — 英文脚手架讲规则，中文载体装例句与词汇
- **图片/文档直接进闭环** — RapidOCR 把照片/PDF 转文本，交给同一纠错链
- **零配置跑通 + BYOK** — clone 即跑；未配 Key 有访客额度，界面内加任意 OpenAI 兼容供应商

## 🚀 快速开始
### 环境要求
- **Python** >= 3.11（无需任何第三方依赖，`pip install` 不是必须的）
- **一个 LLM API Key**（默认 DeepSeek；不配 Key 也能跑确定性纠错 / 冒烟测试）

### 1. 克隆 & 配置
```bash
git clone <repo-url> && cd HSK-AI-Coach
cp .env.example .env          # 填入 DEEPSEEK_API_KEY（https://platform.deepseek.com）
```

### 2. 冒烟测试（不需要 API Key，全 mock，不花钱）
```bash
python -m pytest tests -q        # 或 python -m unittest discover tests -v
```
**633 项测试全部 PASS**（识别/讲解/图谱/复习调度/降级兜底/前端服务层/技能化/度量…）。

### 3. 命令行跑两种用法
```bash
# 纠错闭环：输入一句中文 → 识别 → 讲解 → 图谱沉淀 → 出复习队列
python -m engine.demo_loop "我想买苹果很多。"

# 文件讲解：读一篇中文，选中片段 → 偏误走纠错链 / 正确句走泛讲解
python -m examples.demo_file
```

### 4. 启动前端壳（对话画布 + 认知地图）
```bash
python -m engine.serve          # 浏览器打开 http://127.0.0.1:8612
```
零依赖 HTTP 服务（仅标准库，原生 JS+SVG 单页）。左侧对话流呈现「偏误卡 → 讲解卡 → 学习成果卡」，侧栏进入「◈ 认知地图」看掌握度 × HSK 分布与复习队列。自由对话（`/api/dialog`）需配置 LLM Key，未配置时前端如实报错并给配置步骤（不静默降级）；仅用确定性纠错（`/api/process`）可不配 Key。

### 5.（可选）本地 OCR
图片/PDF 需要装例外依赖 `rapidocr_onnxruntime + pymupdf`；未安装时自动降级为"不解析图片"，不影响其它功能。

## 🧩 核心能力（场景 × 输出）
| 场景 | 你输入什么 | 得到什么 |
|---|---|---|
| 单句纠错 | "我想买苹果很多。" | 偏误定位、建议改法、错误类型 |
| 费曼讲解 | 有偏误的句子 | 四段式讲解（为什么错 → 正确说法 → 对比 → 验证） |
| 复述检验 | 用自己的话复述刚才的知识点 | 判定是否真的理解，不过则降难度重讲 |
| 文件/图片讲解 | 文档或照片 + 选中片段 | OCR 读出文本，偏误走纠错链，正确句讲背景（不污染图谱） |
| 对话练习 | 自由聊天，或选一个场景卡（咖啡厅/问路…） | 边聊边静默记录，卡壳/求助时才纠 |
| 复习提醒 | — | 按图谱待复习队列（到期/高优），过段时间主动提醒 |

## 📊 效果核验（可复现）
| 能力 | 关键指标 | 值 |
|---|---:|---:|
| 偏误识别（45 句黄金集） | F1 / 精确 / 召回 | **0.97 / 100% / 94.12%** |
| | 干净句误报 / 对抗过度纠正 | **0% / 0** |
| 文件讲解（41 条） | 偏误分流正确率 | **94.1%** |
| 复述验证（60 条） | 判定一致性 / 通过准确率 | **88.2% / 94.4%** |
| 图谱数据层 | 幂等/单调性/写接口 | **18/18 PASS** |
| 自由对话（两轮真实实跑） | 技能轨迹命中 / 评估合格率 | **80–86.7% / 93.3–100%** |

以上数字可由根目录评测脚本复跑（自由对话用 `python -m eval.run_eval_agent`，无 Key 时 `--mock --no-judge` 跑过程判据），逐轮"假设→改动→指标"记录见 [`datasets/eval/多轮实验记录_3.6.md`](datasets/eval/多轮实验记录_3.6.md)。

## 🏗️ 架构
```
学习者输入 ───→ planner 自由 ReAct（唯一对话入口 /api/dialog）
                   │  按情况自由选择技能
                   ▼
          统一 Skill 注册表（9 技能）
          identify_errors / explain_error / verify_retell /
          lookup_knowledge_point / retrieve_corpus / get_review_queue /
          generate_unit / web_search / parse_document
                   │
       ┌───────────┴────────────┐
       │ 三引擎(识别/讲解/验证)    │  工具(搜索/文档/生成单元 + OCR)
       └───────────┬────────────┘
                   ▼
      偏误图谱（确定性数据层，纯规则） KP 节点 / 混淆边 / 幂等 / aging
                   ▼
      serve.py：/api/dialog /api/process /api/graph /api/verify /
                /api/generate /api/profile /api/conversation /api/providers
                /api/metrics /api/feedback /api/ocr /api/quiz
```
几个支撑性设计：**自由在调度，约束在数据写**（planner 自由选技能，图谱写入两段式 + 幂等键）；**LLM 只做带宽，规则做守门**（置信度决策 + 确定性护栏防过度纠正）；**降级兜底**（识别不可用回退规则匹配，只产"待确认"候选，绝不污染图谱；某环节失败其余照常并记录 `degraded`）；**HSK 等级对照官方词表**防超纲幻觉。

## 📁 项目结构
```
HSK-AI-Coach/
├── planner/loop.py          planner 自由 ReAct + 技能注册/调用 + trace
├── skills/  registry.py + 9 个自由技能（薄壳包引擎）
├── engine/
│   ├── recognizer / explainer / verifier    三引擎
│   ├── transfer.py           L1 母语迁移归因（假设仅供讲解，不入图谱）
│   ├── scheduler/fsrs.py     自实现简化 FSRS 间隔重复
│   ├── ocr.py / rag.py       OCR 识字（RapidOCR，例外依赖）+ 检索增强
│   ├── metrics.py            效果度量（续学率/通关率/满意度）
│   ├── visitor_gate.py       游客额度闸门 + providers.py + serve.py
│   ├── persona.py + scenario.py + intervention.py + graph/error_graph.py
├── web/index.html / metrics.html   前端壳（对话画布 + 认知地图 + 度量面板）
├── datasets/  knowledge_points / lexicon / 考纲 / OCR 校正 / 设计稿与评测脚本
├── tests/                    633 个测试（免 Key 全 mock）
└── examples/                 命令行 demo
```

## 📚 数据与语料来源
- 黄金评测集（45 句 / 复述 60 条）为自建，偏误样本经二语教师仲裁；复述验证集取材自 [MuCGEC](https://github.com/HillZhang1999/MuCGEC)（NAACL 2022）与 CGED（HSK 动态作文语料库）
- 第三方原始语料不随仓库分发（见 `.gitignore`），版权归原作者；`HSK1-4_字表词表_GF0025-2021.xlsx` 为 HSK3.0 官方字表词表（《GF0025-2021》），引擎内置转换后的 `lexicon_hsk1_4.json`
- 考纲语法点取自开源 HSK-3.0 数据，经人工审核转正并标注依赖边；OCR 校正语料来自真实引擎实测（RapidOCR 逐字扫 HSK30 认读字表）

## 📄 设计文档与过程记录
每一步设计过程和取舍都留了档，可作评审证据：
- 0.22 习得伙伴转向设计稿（L1 迁移 / 场景对话 / 介入时机+个性化）→ [`datasets/docs/0.22-习得伙伴转向-设计稿.md`](datasets/docs/0.22-习得伙伴转向-设计稿.md)
- 对话契约总纲 / 纠错契约 v1（前后端 JSON 接缝）→ `datasets/docs/0.17-统一能力契约与架构总纲.md`、`JSON-接缝契约-v1.md`
- M1–M9 接入清单 → `datasets/docs/0.18-M1-M9-接入清单.md`
- 前端壳 v2 / 模型密钥 / 教学语言分层 → `datasets/docs/0.19-前端壳v2改造.md`、`0.20-模型密钥与模型选择.md`
- 逐轮"假设→改动→指标→结论"评测记录 → [`datasets/eval/多轮实验记录_3.6.md`](datasets/eval/多轮实验记录_3.6.md)

---

## ⚖️ 开源与许可
**[MIT License](LICENSE)**（2026），允许自由使用、修改、分发与商用，需保留版权声明。本项目为教育/研究用途，API Key 由使用者自行提供，本项目不承担 API 费用，纠错建议仅供学习参考。