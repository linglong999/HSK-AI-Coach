# ============================================================
# LLM 客户端
# 统一封装 LLM 调用，支持 DeepSeek / Qwen 切换（OpenAI 兼容格式）
# 所有 Agent 通过本模块调用模型，便于后续替换或加日志/成本统计。
# 传输层：requests.Session（主流 HTTP 客户端，原生支持流式）。测试经
#   self._session 注入 duck-typed fake（post() 须按 requests.Response 语义返回）。
# ============================================================

import time
from typing import List, Dict, Optional

import requests

from config import settings


class LLMHTTPError(RuntimeError):
    """4xx 业务性失败（Key 无效/参数被拒等），携带 HTTP code；不参与退避重试。"""

    def __init__(self, msg: str, code: int = 0):
        super().__init__(msg)
        self.code = code


class LLMClient:
    """轻量 LLM 客户端，OpenAI 兼容协议（/chat/completions）。
    支持供应商配置覆盖（config 字典，0.20 BYOK）与真实流式（chat_stream）。"""

    def __init__(self, provider: Optional[str] = None, session: Optional["requests.Session"] = None):
        self.provider = provider or settings.LLM_PROVIDER
        self._session = session if session is not None else requests.Session()

    # ---------- 公共接口 ----------
    def chat(self, messages: List[Dict], temperature: float = 0.3,
             max_tokens: int = 2000, json_mode: bool = False,
             config: Optional[Dict] = None) -> str:
        """发送对话，返回纯文本回复（已剥离可能的 JSON 代码块标记）
        json_mode: True 时启用 API 级 JSON 模式（response_format=json_object），
                  提示词不必再靠"严格 JSON"愿望堆叠（对齐 2.1-2.3 调用层强约束）。
        config: 可选供应商覆盖（0.20 BYOK）：{base_url, api_key, model}，
                None → settings 全局配置（.env）。OpenAI 兼容协议统一格式。
        """
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

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if json_mode:
            # DeepSeek/Qwen 均支持 OpenAI 兼容的 response_format json_object
            payload["response_format"] = {"type": "json_object"}

        # 注意：此处按 provider 切换 base_url，但统一使用 OpenAI 兼容 /chat/completions
        url = base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        try:
            return self._post_chat(url, headers, payload)
        except LLMHTTPError as e:
            # json_mode 兼容性兜底（0.20）：部分供应商（Ollama 部分模型/GLM 某些版本）
            # 拒绝 response_format 参数返回 400 → 去掉该参数重试一次；
            # 仍失败则抛新错误（信息更真实），不静默吞掉。
            if json_mode and e.code == 400:
                fallback = dict(payload)
                fallback.pop("response_format", None)
                try:
                    return self._post_chat(url, headers, fallback)
                except Exception as e2:  # noqa: BLE001
                    raise RuntimeError(
                        f"LLM 调用失败（json_mode 降级重试仍失败）: {e2}") from e2
            raise RuntimeError(f"LLM 调用失败(HTTP {e.code}): {e}") from e

    # ---------- 便捷方法 ----------
    def chat_stream(self, messages: List[Dict], temperature: float = 0.3,
                    max_tokens: int = 2000, config: Optional[Dict] = None):
        """流式对话：逐段产出增量文本（生成器），支持打字机节奏。
        请求 stream=True；每次 yield 一个文本增量（SSE `\data:` 已剥外层）。
        错误语义与 chat 一致：4xx 抛 LLMHTTPError，网络/5xx/429 走退避重试。
        config: 可选供应商覆盖（同 chat）。"""
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
        url = base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        for chunk in self._post_chat_stream(url, headers, payload):
            yield chunk

    def chat_json(self, system: str, user: str, temperature: float = 0.2,
                  config: Optional[Dict] = None) -> dict:
        """请求模型返回 JSON，并自动解析（非强制，供宽松场景用）。
        config: 可选供应商覆盖（0.26 why 复用），透传给 chat()。"""
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
        config: 可选供应商覆盖（默认 None → settings 全局配置；0.26 why 需按请求供应商）。
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
    def _post_chat(self, url: str, headers: Dict, payload: Dict) -> str:
        """单次 HTTP POST（含瞬时故障退避重试，4xx 不重试直接抛）。
        经 self._session.post() 发送；网络/超时/5xx/429 重试最多 2 次，4xx 立即抛。"""
        last_err = None
        for attempt in range(3):
            try:
                resp = self._session.post(
                    url, json=payload, headers=headers, timeout=60)
                if resp.status_code >= 400:
                    if 400 <= resp.status_code < 500 and resp.status_code != 429:
                        raise LLMHTTPError(
                            f"LLM 调用失败(HTTP {resp.status_code})", code=resp.status_code)
                    raise requests.HTTPError(f"HTTP {resp.status_code}: {resp.text!r}")
                content = resp.json()["choices"][0]["message"]["content"]
                return strip_code_fence(content)
            except LLMHTTPError:
                raise   # 4xx 不重试
            except requests.RequestException as e:
                last_err = e
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"LLM 调用失败(已重试): {last_err}")

    def _post_chat_stream(self, url: str, headers: Dict, payload: Dict):
        """流式传输：解析 OpenAI 兼容 SSE（`data: {...}`，末尾 `data: [DONE]`）。
        逐 chunk 产出 content 增量；4xx → LLMHTTPError，网络/5xx/429 退避重试。"""
        for attempt in range(3):
            try:
                resp = self._session.post(
                    url, json=payload, headers=headers, timeout=60, stream=True)
                if resp.status_code >= 400:
                    if 400 <= resp.status_code < 500 and resp.status_code != 429:
                        raise LLMHTTPError(
                            f"LLM 调用失败(HTTP {resp.status_code})", code=resp.status_code)
                    raise requests.HTTPError(f"HTTP {resp.status_code}: {resp.text!r}")
                for line in resp.iter_lines(decode_unicode=True):
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        return
                    try:
                        chunk_obj = json_loads(data)
                    except Exception:  # noqa: BLE001 非 JSON 行（心跳等）跳过
                        continue
                    choices = chunk_obj.get("choices") or []
                    if choices and choices[0].get("delta", {}).get("content"):
                        yield choices[0]["delta"]["content"]
                return
            except LLMHTTPError:
                raise
            except requests.RequestException as e:
                last_err = e
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
            except (ValueError, KeyError, TypeError) as e:
                # 响应格式异常：非网络错误，重试无益，直接抛
                raise RuntimeError(f"LLM 流式响应解析失败: {e}") from e
        raise RuntimeError(f"LLM 调用失败(已重试): {last_err}")


# ---------- 工具函数 ----------
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