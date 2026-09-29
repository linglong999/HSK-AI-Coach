# HSK-AI-Coach 架构事实说明

> 更新时间：2026-09-29。本文是当前实现的事实说明，不等同于公网部署拓扑。架构图属于逻辑视图；部署边界、认证、TLS 和数据隔离以 `SECURITY.md`、`DEPLOYMENT.md` 为准。

## 请求与数据流

1. `web/` 提供 React 18 + Vite + TypeScript 前端；生产构建产物为 `web/dist`。
2. `src/engine/serve.py` 创建 FastAPI 应用并由 uvicorn 运行。主要入口包括 `/api/process`、`/api/dialog`、`/api/verify`、`/api/generate`、`/api/graph`、`/api/profile`、`/api/conversation`、`/api/providers`、`/api/ocr`、`/api/metrics`、`/api/feedback` 和 `/api/quiz`。
3. `DialogService` 负责请求上下文、会话记忆、供应商解析、配额和写回；`planner/` 通过 Skill 注册表调度能力。
4. `engine/recognizer`、`engine/explainer`、`engine/verifier` 组成识别/讲解/复述验证链。LLM 只提供候选内容和自然语言生成，确定性规则负责置信度、数据写入和降级边界。
5. 偏误图谱和事件账本分别由图谱存储层与 SQLite 记忆层持久化；`data/` 是当前单机默认数据根目录。
6. OCR 和 RAG 属于可选能力；缺少 OCR 例外依赖时，接口应返回结构化降级结果。
7. 当前单机 HTTP 运行诊断由 `engine/ops_metrics.py` 在进程内聚合，仅保留固定路由、状态码和响应头耗时；不是跨进程监控或账单系统。

## 评测边界

- `pytest tests -q` 是代码回归证据。
- `python -m datasets.eval.run_all --layer deterministic` 是零 LLM、零真实用户图谱读写的教学确定性门禁。
- `llm_judge`/nightly 是真实模型语义质量评测，不得与 deterministic 结果混报。
- 当前黄金识别集只有 36 条（偏误 17 + clean 19），作者 self-review，尚未经二语教师仲裁；不能据此宣称学习增益或外部泛化。

## 当前非目标

仓库尚未提供账户体系、租户隔离、跨设备同步、生产级可观测性、真实成本硬预算、可替换存储、TLS 终止和完整隐私删除流程。把服务监听到公网前必须完成 `SECURITY.md` 和 `DEPLOYMENT.md` 中的前置条件。
