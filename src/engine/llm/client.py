# ============================================================
# LLM 客户端
# 统一封装 LLM 调用，支持 DeepSeek / Qwen 切换（OpenAI 兼容格式）
# 所有 Agent 通过本模块调用模型，便于后续替换或加日志/成本统计。
# 传输层：openai SDK（B7 S0 由 requests 自写迁移，保行为）——对外调用
#   接口/错误语义不变，仅换传输；测试经 http_client 注入 httpx.MockTransport
# 替身（handler 按 httpx.Response 语义返回）。响应对象 .usage 原生透出
#   （self.last_usage / chat_with_usage），供 S2 能量计量读取。
# ============================================================

import time
from typing import List, Dict, Optional

from openai import OpenAI, OpenAIError, APIStatusError

from config import settings


class LLMHTTPError(RuntimeError):
    """4xx 业务性失败（Key 无效/参数被拒等），携带 HTTP code；不参与退避重试。"""

    def __init__(self, msg: str, code: int = 0):
        super().__init__(msg)
        self.code = code


class LLMClient:
    """轻量 LLM 客户端，OpenAI 兼容协议（/chat/completions）。
    支持供应商配置覆盖（config 字典，0.20 BYOK）与真实流式（chat_stream）。
    http_client: 可选注入 httpx.Client（测试传 MockTransport），None → openai 默认。
    响应 usage 在每次成功 chat()/chat_stream() 后落在 self.last_usage。"""

    def __init__(self, provider: Optional[str] = None,
                 http_client: Optional[object] = None,
                 timeout: float = 60):
        self.provider = provider or settings.LLM_PROVIDER
        self._http_client = http_client
        self._timeout = timeout
        self.last_usage: Optional[Dict] = None

    # ---------- 传输构造 ----------
    def _openai(self, config: Optional[Dict]):
        """按 config/全局配置解析 OpenAI 兼容三元组，构造一次调用用的 SDK client。
        max_retries=0：SDK 内建重试关闭——退避重试由本模块自定义循环负责（保行为）。"""
        if config:
            base_url = config.get("base_url") or ""
            api_key = config.get("api_key") or ""
            model = config.get("model") or ""
        else:
            base_url, api_key, model = settings.get_llm_config()
        if not api_key:
            raise RuntimeError(
                "未配置 API Key。请设置环境变量 DEEPSEEK_API_KEY （或 QWEN_API_KEY），"
                "或在 .env 文件中填写。本项目为开源项目，API Key 由用户自行提供。"
            )
        if self._http_client is None:
            from engine.providers import validate_provider_url
            base_url = validate_provider_url(
                base_url, allow_local=settings.ALLOW_LOCAL_PROVIDER_URLS)
        client = OpenAI(
            base_url=base_url.rstrip("/") or None,
            api_key=api_key,
            max_retries=0,
            timeout=self._timeout,
            http_client=self._http_client,
        )
        return model, client

    # ---------- 公共接口 ----------
    def chat(self, messages: List[Dict], temperature: float = 0.3,
             max_tokens: int = 2000, json_mode: bool = False,
             config: Optional[Dict] = None) -> str:
        """发送对话，返回纯文本回复（已剥离可能的 JSON 代码块标记）
        json_mode: True 时启用 API 级 JSON 模式（response_format=json_object）。
        config: 可选供应商覆盖（0.20 BYOK）：{base_url, api_key, model}。
        """
        model, client = self._openai(config)
        kwargs = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            # DeepSeek/Qwen 均支持 OpenAI 兼容的 response_format json_object
            kwargs["response_format"] = {"type": "json_object"}

        try:
            return self._create(client, kwargs)
        except LLMHTTPError as e:
            # json_mode 兼容性兜底（0.20）：部分供应商（Ollama 部分模型/GLM 某些版本）
            # 拒绝 response_format 参数返回 400 → 去掉该参数重试一次；
            # 仍失败则抛新错误（信息更真实），不静默吞掉。
            if json_mode and e.code == 400:
                fallback = dict(kwargs)
                fallback.pop("response_format", None)
                try:
                    return self._create(client, fallback)
                except Exception as e2:  # noqa: BLE001
                    raise RuntimeError(
                        f"LLM 调用失败（json_mode 降级重试仍失败）: {e2}") from e2
            raise RuntimeError(f"LLM 调用失败(HTTP {e.code}): {e}") from e

    def chat_with_usage(self, messages: List[Dict], temperature: float = 0.3,
                        max_tokens: int = 2000, json_mode: bool = False,
                        config: Optional[Dict] = None):
        """兼容 chat() 语义，额外返回 (text, usage)；
        usage = {prompt_tokens, completion_tokens}，失败/零产出走抛错路径不入账（S2）。
        """
        text = self.chat(messages, temperature=temperature, max_tokens=max_tokens,
                         json_mode=json_mode, config=config)
        return text, self.last_usage

    # ---------- 便捷方法 ----------
    def chat_stream(self, messages: List[Dict], temperature: float = 0.3,
                    max_tokens: int = 2000, config: Optional[Dict] = None):
        """流式对话：逐段产出增量文本（生成器），支持打字机节奏。
        错误语义与 chat 一致：4xx 抛 LLMHTTPError，网络/5xx/429 走退避重试。
        config: 可选供应商覆盖（同 chat）。"""
        model, client = self._openai(config)
        kwargs = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        last_err = None
        for attempt in range(3):
            try:
                stream = client.chat.completions.create(**kwargs)
                for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta \
                            and chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content
                return
            except LLMHTTPError:
                raise
            except APIStatusError as e:
                sc = e.status_code or 0
                if 400 <= sc < 500 and sc != 429:
                    raise LLMHTTPError(
                        f"LLM 调用失败(HTTP {sc})", code=sc)
                last_err = e
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
            except OpenAIError as e:
                last_err = e
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"LLM 调用失败(已重试): {last_err}")

    def chat_json(self, system: str, user: str, temperature: float = 0.2,
                  config: Optional[Dict] = None) -> dict:
        """请求模型返回 JSON，并自动解析（非强制，供宽松场景用）。"""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        raw = self.chat(messages, temperature=temperature, config=config)
        return parse_json(raw)

    def chat_json_strict(self, system: str, user: str, temperature: float = 0.2,
                         retries: int = 1, config: Optional[Dict] = None) -> dict:
        """结构化输出的调用层强约束（2.1-2.3 定稿要求）。

        - 启用 API 级 JSON 模式（response_format=json_object）；
        - 解析失败重试最多 retries 次（默认 1），仍失败抛 JSONStrictError（由调用方降级）；
        - 绝不静默返回空/坏结果。（P1-1：降级方向不能偏向"通过"）
        config: 可选供应商覆盖（默认 None → settings 全局配置）。
        """
        if retries < 0:
            raise ValueError("retries 必须 >= 0")
        attempt = 0
        while attempt <= retries:
            try:
                messages = [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ]
                raw = self.chat(messages, temperature=temperature,
                                json_mode=True, config=config)
                return parse_json(raw)
            except Exception as e:
                attempt += 1
                if attempt > retries:
                    raise JSONStrictError(
                        f"结构化输出解析失败，已重试 {retries} 次仍失败: {e}"
                    )
        raise JSONStrictError("unreachable")  # 不可达占位

    # ---------- 传输层 ----------
    def _create(self, client: OpenAI, kwargs: Dict) -> str:
        """单次完成调用（含瞬时故障退避重试，4xx 不重试直接抛）。
        网络/超时/5xx/429 重试最多 2 次，4xx 立即抛；成功回填 self.last_usage。"""
        self.last_usage = None
        last_err = None
        for attempt in range(3):
            try:
                resp = client.chat.completions.create(**kwargs)
                self.last_usage = _to_usage(getattr(resp, "usage", None))
                content = ""
                if resp.choices and resp.choices[0].message:
                    content = resp.choices[0].message.content or ""
                return strip_code_fence(content)
            except LLMHTTPError:
                raise   # 4xx 不重试
            except APIStatusError as e:
                sc = e.status_code or 0
                if 400 <= sc < 500 and sc != 429:
                    raise LLMHTTPError(
                        f"LLM 调用失败(HTTP {sc})", code=sc)
                last_err = e
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
            except OpenAIError as e:   # 断连/超时等传输层（含 APIConnectionError）
                last_err = e
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"LLM 调用失败(已重试): {last_err}")


# ---------- 工具函数 ----------
def _to_usage(usage) -> Optional[Dict]:
    """openai usage 对象 → 可序列化 {prompt_tokens, completion_tokens}；None 原样返回。"""
    if usage is None:
        return None
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
    }


def json_dumps(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


class JSONStrictError(Exception):
    """结构化输出解析连续失败后抛出，由调用方执行降级逻辑（不静默透传坏结果）"""


def json_loads(s):
    import json
    return json.loads(s)


def strip_code_fence(text: str) -> str:
    """剥离模型可能返回的 ```json ... ``` 代码块标记"""
    text = text.strip()
    if text.startswith("```"):
        # 去掉首行 ```json 或 ``` 和末尾 ```
        lines = text.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def parse_json(text: str) -> dict:
    """解析 JSON 文本，容忍代码块和前后杂讯"""
    import json
    text = strip_code_fence(text)
    # 尝试直接解析
    try:
        return json.loads(text)
    except Exception:
        pass
    # 尝试截取第一个 { 到最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            pass
    raise ValueError(f"无法解析为 JSON: {text[:200]}")
