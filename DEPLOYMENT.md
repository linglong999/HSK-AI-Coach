# HSK-AI-Coach 部署边界

## 本地内测

```bash
uv sync --frozen
python -m engine.serve --host 127.0.0.1 --port 8612
```

本地内测可使用确定性 `/api/process`，不配置 LLM Key 也能运行；`/api/dialog` 需要有效供应商。数据默认写入 `data/`，其中 SQLite、图谱和供应商文件都属于用户数据/秘密，不能提交到 Git。

当前 CLI 会拒绝非 loopback `--host`。这只是防止误操作的本机监听护栏，不是身份认证；不可通过反向代理或直接调用 `create_app` 绕过后宣称生产可用。

近期单机使用也应注意：任何本机进程都能访问 loopback 端口；浏览器跨站写入护栏只针对带 `Origin` / `Sec-Fetch-Site` 的浏览器请求，不能隔离本机恶意程序。不要将 `data/` 或 `.env` 放入同步盘/公开共享目录，且不要将仓库目录整体打包分享。

如需连接本机 Ollama 等 HTTP 服务，先确认服务仅绑定本机，再在 `.env` 设置 `ALLOW_LOCAL_PROVIDER_URLS=true` 并重启；默认不允许任何回环 HTTP 供应商。外部模型仍须使用 HTTPS。

### 本机数据备份与恢复

先停止服务，再选择**不存在**的备份目录执行：

```bash
python -m engine.local_backup inspect data
python -m engine.local_backup create data backup-copy
python -m engine.local_backup verify backup-copy
python -m engine.local_backup restore backup-copy restored-data
```

备份只含 `data/` 顶层普通文件；`coach.db` 通过 SQLite backup API 生成一致副本，`-wal`/`-shm` 临时文件不复制，子目录和 `.env` 不包含在内。`inspect` 只统计纳入/排除数量，不打印文件名或内容；清单中的 `excluded_paths` 和 `complete_for_source` 记录实际遗漏。**若 `complete_for_source=false`，此备份不是完整目录迁移包**；先单独判断子目录/排除项是否为需保留的数据，不能直接删除源目录。清单记录 SHA-256 哈希；恢复只允许写入不存在的新目录，复制到暂存目录后再次校验哈希和 SQLite，不覆盖当前 `data/`。恢复后先核对内容，再由操作者决定是否切换数据目录。备份包含学习记录，**不是加密归档**，请放在受保护位置。Windows DPAPI Key 在同一 Windows 用户/系统环境可解密；跨设备须重新填写供应商 Key。

### 本机运行诊断与费用边界

每个 `/api/*` 响应带 `X-Request-ID`。`GET /api/ops/summary` 提供当前进程按固定路由模板聚合的请求量、HTTP 5xx 失败率和最近最多 256 次响应耗时 p95；不记录查询参数、正文或 API Key。CLI 默认关闭 uvicorn 原始访问日志，以免查询串进入日志，改输出含请求 ID、路由模板、状态码和耗时的结构化记录。指标重启归零；SSE 耗时只计到响应头，不含完整对话流，不能当作端到端延迟。日志仍应只保存在本机受控目录，且第三方代理的访问日志须单独审查。

现有 `VisitorGate` 只对使用项目方 Key 的访客提供能量/回合限制；token 用量记账仅覆盖成功且供应商回报 usage 的对话。能量分是产品哨兵比例，**不是人民币成本或真实预算**；BYOK 路径无项目方日费用硬上限。公开部署前须按实际供应商价格、模型和结算口径建立预算及超额拒绝策略，不得把 `/api/quota` 的能量值当财务成本。

删除本机数据前应停服务、先做可验证备份，再逐项确认目标路径；当前不提供“一键清空”命令，以免误删真实用户数据。密钥轮换流程为：先在供应商处生成新 Key → 在 UI 新增并测试新供应商 → 设为默认 → 删除旧供应商 → 在供应商侧撤销旧 Key；如果使用 `.env`，在环境中更换并重启服务。删除本地记录不等于撤销供应商侧 Key，也不保证历史备份被清除。

## 公网部署前置条件

- 使用反向代理终止 TLS，不直接把 uvicorn 开发进程作为公网边界。
- 明确认证、用户/租户隔离、CORS allowlist、速率限制、请求体上限和日志脱敏策略。
- 将 BYOK Key 迁移到系统密钥库、加密文件或等价 secrets 管理，并验证轮换/撤销。
- 对 `/api/providers/test` 实施 SSRF 防护，限制协议、DNS/IP、重定向和超时。
- 规划 SQLite/文件数据的加密、备份、恢复、保留期限和用户删除流程。
- 先在隔离环境执行完整 pytest、deterministic gate 和安全负向测试，再发布镜像/进程。

## 发布闸门

```bash
python -m pytest tests -q
python -m datasets.eval.run_all --layer deterministic
```

nightly 的真实模型评测不能替代上述 deterministic gate；其供应商、模型、成本和失败原因必须单独记录。当前仓库没有统一容器镜像、编排文件或生产环境演练记录，部署者必须把目标环境信息补入发布记录。
