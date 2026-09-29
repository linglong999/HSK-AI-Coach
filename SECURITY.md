# HSK-AI-Coach 安全边界

## 当前默认边界

- `python -m engine.serve` 默认监听 `127.0.0.1`，且 CLI 拒绝非 loopback `--host`；当前只支持本机内测。反向代理或自定义 ASGI 启动仍可绕过 CLI 限制，不能将此限制当作完整认证。
- CORS 默认不允许跨域；跨域前端需通过 `CORS_ALLOW_ORIGINS` 显式配置精确 Origin。游客 Cookie 属于本地开发兼容逻辑；公网部署前仍必须配置身份认证和 HTTPS，不能把“有邀请白名单”当作完整账户体系。
- 所有请求会校验 `Host` 必须是 loopback，以阻断恶意域名 DNS 重绑定后的本机 GET 读取。浏览器向 `/api/*` 发出的写请求还会校验 `Origin` / `Sec-Fetch-Site`；未在来源列表且非本机同源的写请求返回 `403 cross_origin_write_denied`。无 `Origin` 的本机 CLI 请求仍可用。该护栏不等同于身份认证，非浏览器客户端仍可直接访问本机端口。
- BYOK 供应商以 `data/llm_providers.json` 持久化。Windows 下 UI 添加的 Key 使用当前用户 DPAPI 加密，首次读取旧版明文文件时原子迁移；非 Windows 下仅使用当前用户文件权限保护。`.env` 始终是明文，备份、同步盘与共享目录仍需按高敏感数据处理。DPAPI 不防同一 Windows 用户账户下的恶意进程，换设备/重装系统需重新填写 Key。
- 供应商 Base URL 目前先做协议、IP/DNS 解析预检查；外部地址要求 HTTPS，私网/元数据地址拒绝，`/api/providers/test` 不跟随重定向且不回传底层异常。`http://localhost`/loopback 仅在显式设置 `ALLOW_LOCAL_PROVIDER_URLS=true` 后可用，适用于本机 Ollama。DNS 重绑定、直接 ASGI 暴露及普通对话请求的连接层限制仍未达到公网级 SSRF 防护要求。
- 响应不得返回完整 API Key 或内部堆栈；日志也不得记录完整 Key、Authorization 头或请求正文中的密钥。

## P1 安全验收清单

1. 默认 `DEBUG=False`，错误响应只含稳定错误码/用户可读消息。
2. 公网启动必须显式开启认证；未配置认证时只允许 loopback 监听。
3. CORS 使用显式 allowlist；不得在带凭据请求中使用 `*`。
4. provider Base URL 仅允许外部 `https`（本地开发可显式允许 loopback `http`），阻断 RFC1918、链路本地、云元数据和 DNS 解析后的私网地址；公网前还需连接层 DNS 固定和普通对话请求重定向策略。
5. API Key 只在创建/更新请求中接收，列表和错误响应均掩码；存储文件使用最小权限并有轮换、撤销和删除流程。
6. 反向代理负责 TLS、请求体大小、速率限制和安全响应头；应用层记录 trace、状态码、延迟和供应商，不记录秘密。

## 已知未完成项

当前仓库尚未完成账户/租户隔离、生产密钥库适配和完整 SSRF 防护。单机备份恢复仅在隔离目录演练；真实用户数据与跨设备恢复尚未演练。公网模式保持关闭；完成前不得宣称“生产安全”或“多租户安全”。
