"""Small stdlib-only clients for common model API protocols."""

from __future__ import annotations

import json
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from ..settings import ModelSettings


class LLMRequestError(RuntimeError):
    pass


def _join_url(base: str, path: str) -> str:
    return f"{base.rstrip('/')}/{path.lstrip('/')}"


def _redact(value: str, secret: str | None) -> str:
    if secret:
        value = value.replace(secret, "***")
    return value[:1200]


def _request_json(
    url: str,
    *,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: int,
    secret: str | None,
) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=body, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as error:
        try:
            details = error.read(4096).decode("utf-8", errors="replace")
        except OSError:
            details = str(error)
        raise LLMRequestError(
            f"模型接口返回 HTTP {error.code}：{_redact(details, secret)}"
        ) from error
    except (URLError, TimeoutError, OSError) as error:
        raise LLMRequestError(f"无法连接模型接口：{_redact(str(error), secret)}") from error
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as error:
        raise LLMRequestError("模型接口没有返回有效 JSON。") from error
    if not isinstance(result, dict):
        raise LLMRequestError("模型接口响应结构无效。")
    return result


def _extract_openai_response(result: dict[str, Any]) -> str:
    output_text = result.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text
    for output in result.get("output", []):
        if not isinstance(output, dict):
            continue
        for content in output.get("content", []):
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                return text
    raise LLMRequestError("OpenAI 响应中没有可读取的文本。")


def _extract_chat_completion(result: dict[str, Any]) -> str:
    try:
        content = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise LLMRequestError("兼容接口响应中没有 choices[0].message.content。") from error
    if not isinstance(content, str) or not content.strip():
        raise LLMRequestError("兼容接口返回了空内容。")
    return content


def _extract_anthropic(result: dict[str, Any]) -> str:
    for item in result.get("content", []):
        if isinstance(item, dict) and item.get("type") == "text":
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                return text
    raise LLMRequestError("Anthropic 响应中没有文本内容。")


def _extract_gemini(result: dict[str, Any]) -> str:
    try:
        parts = result["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError) as error:
        raise LLMRequestError("Gemini 响应中没有候选文本。") from error
    texts = [part.get("text", "") for part in parts if isinstance(part, dict)]
    text = "".join(value for value in texts if isinstance(value, str)).strip()
    if not text:
        raise LLMRequestError("Gemini 返回了空内容。")
    return text


class LLMClient:
    """Protocol adapter for one saved model profile."""

    def __init__(self, settings: ModelSettings, api_key: str | None, audit_callback: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.settings = settings
        self.api_key = (api_key or "").strip()
        self.audit_callback = audit_callback
        if not settings.is_configured(self.api_key):
            raise LLMRequestError("模型配置不完整，请先填写 API 地址、模型名称和 API Key。")

    def request_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        json_mode: bool,
        max_output_tokens: int | None = None,
    ) -> str:
        protocol = self.settings.protocol
        maximum = max_output_tokens or self.settings.max_output_tokens

        def finish(text: str) -> str:
            if self.audit_callback is not None:
                self.audit_callback(
                    {
                        "system_prompt": system_prompt,
                        "user_prompt": user_prompt,
                        "response": text,
                        "json_mode": json_mode,
                        "max_output_tokens": maximum,
                    }
                )
            return text
        if protocol == "openai_responses":
            result = _request_json(
                _join_url(self.settings.api_base, "responses"),
                payload={
                    "model": self.settings.model,
                    "instructions": system_prompt,
                    "input": user_prompt,
                    "max_output_tokens": maximum,
                    "text": {"format": {"type": "json_object"}} if json_mode else {},
                },
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=self.settings.timeout_seconds,
                secret=self.api_key,
            )
            return finish(_extract_openai_response(result))

        if protocol == "anthropic":
            result = _request_json(
                _join_url(self.settings.api_base, "v1/messages"),
                payload={
                    "model": self.settings.model,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_prompt}],
                    "max_tokens": maximum,
                },
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                timeout=self.settings.timeout_seconds,
                secret=self.api_key,
            )
            return finish(_extract_anthropic(result))

        if protocol == "gemini":
            url = _join_url(
                self.settings.api_base,
                f"v1beta/models/{quote(self.settings.model, safe='')}:generateContent",
            )
            url = f"{url}?key={quote(self.api_key, safe='')}"
            generation_config: dict[str, Any] = {
                "maxOutputTokens": maximum,
                "temperature": self.settings.temperature,
            }
            if json_mode:
                generation_config["responseMimeType"] = "application/json"
            result = _request_json(
                url,
                payload={
                    "systemInstruction": {"parts": [{"text": system_prompt}]},
                    "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                    "generationConfig": generation_config,
                },
                headers={"Content-Type": "application/json"},
                timeout=self.settings.timeout_seconds,
                secret=self.api_key,
            )
            return finish(_extract_gemini(result))

        if protocol == "ollama":
            result = _request_json(
                _join_url(self.settings.api_base, "api/generate"),
                payload={
                    "model": self.settings.model,
                    "system": system_prompt,
                    "prompt": user_prompt,
                    "stream": False,
                    "format": "json" if json_mode else "",
                    "options": {"temperature": self.settings.temperature},
                },
                headers={"Content-Type": "application/json"},
                timeout=self.settings.timeout_seconds,
                secret=None,
            )
            text = result.get("response")
            if not isinstance(text, str) or not text.strip():
                raise LLMRequestError("Ollama 返回了空内容。")
            return finish(text)

        if protocol != "openai_compatible":
            raise LLMRequestError(f"不支持的模型接口协议：{protocol}")
        payload: dict[str, Any] = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.settings.temperature,
            "max_tokens": maximum,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        result = _request_json(
            _join_url(self.settings.api_base, "chat/completions"),
            payload=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=self.settings.timeout_seconds,
            secret=self.api_key,
        )
        return finish(_extract_chat_completion(result))

    def test_connection(self) -> str:
        text = self.request_text(
            system_prompt="You are a connection test. Return valid JSON only.",
            user_prompt='Return exactly {"ok": true}.',
            json_mode=True,
            max_output_tokens=64,
        )
        if "ok" not in text.lower():
            raise LLMRequestError("接口已响应，但没有返回预期的连接测试结果。")
        return f"连接成功：{self.settings.provider_name} / {self.settings.model}"
