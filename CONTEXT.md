# HSK-AI-Coach · 领域词表（CONTEXT）

> 本文件是架构审阅/拆分的领域词汇锚定。术语取代码里的既有叫法；深化后命名的新协作器在此登记，避免后续审阅再换词重造。
> 建立：2026-09-15（improve-codebase-architecture · 01 DialogService 深化拷问结晶）。

## 编排层

- **DialogService**：对话运营服务层（Controller-Service 里的 Service）。职责=进程级实例缓存（planner/memory/writeback/tracker/identify）+ 一把 `RLock`（事务边界）+ 6 个入口方法（process/verify/generate/dialog/session_update/session_delete）+ 若干静态助手（serve.py/测试借用的稳定挂点）。对外公开面冻结。
- **DialogRun**：dialog() 的编排协作器（0.33 深化批次D 已落地，`engine/dialog_run.py`）。纯接收、不持有缓存引用；每次 run 由 DialogService 解析好所需实例再传入。负责把 dialog 主链按阶段编排（AssembleDirectives → PreScanIntervene → CallPlanner → WritebackLedger → AssembleResponse；LoadSessionContext/游客闸门/ResolveProvider 三个入口阶段留在 DialogService）。纯静态助手以构造器注入（`DialogService._normalize_user_level`/`_writeback_ledger_events`/`_assistant_payload` 仍为稳定挂点，patch 对主链生效）。锁不在 DialogRun 内部（归 DialogService 入口统一持有，`with self._lock:` 包住入口阶段+DialogRun.run）。
- **阶段协作器**：dialog 主链的阶段对象——`LoadSessionContext`（learner/conversation/lang/l1 归一）、`ResolveProvider`（LLM 供应商解析 + llm_call 工厂；实现类名 `ProviderResolver`，深化批次C 已落地：默认=生产实现，测试可注入替身，legacy `dialog_llm` 参数包装为注入式 resolver 统一两条注入路径）、`PreScanIntervene`（预扫 + 介入分档）、`AssembleDirectives`（画像/persona/风格/场景收集成 prompt 参数）、`CallPlanner`、`WritebackLedger`（M8 账本事件 + 画像 facts + 记忆追加 + 会话标题 + 成果卡 meta）、`AssembleResponse`。

## 不变式

- **缓存即权威**：planner/memory/writeback/tracker/identify 的实例缓存只归 DialogService 持有，协作器不得另起一份，避免"重复实例"破坏多轮一致性。
- **锁 = 跨资源事务边界**：dialog 同时写记忆/账本/画像/干预状态，是跨资源事务；`RLock` 由 DialogService 入口统一持有，协作器内部不加锁。
- **接口 = 测试面**：dialog 主链（现状测不到）深化后每个阶段可注入 fake 单测；静态助手保留 `DialogService._xxx` 稳定挂点，serve.py/既有测试零改动。
- **策略模块保持独立**：intervention/persona/transfer 是形态各异的确定性决策模块，不强求统一成一个大 Strategy 接口；由编排层各自收集、统一组装进 prompt 参数（反过早抽象，2026-09-15 调研结论）。