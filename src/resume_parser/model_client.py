"""OpenAI-compatible 模型客户端与受限 JSON 修复。"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 使用兼容包
    import tomli as tomllib
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .errors import AppError

LOGGER = logging.getLogger("resume_parser.model")


@dataclass(frozen=True)
class ModelConfig:
    """模型服务配置。"""

    base_url: str
    model: str
    api_key: str | None
    timeout_seconds: int = 20
    fallback_enabled: bool = True
    provider: str = "openai"
    response_path: str | None = None
    content_type: str = "text"


@dataclass(frozen=True)
class AppSettings:
    """应用级模型和降级设置。"""

    model: ModelConfig | None
    fallback_enabled: bool = True
    local_model: LocalModelConfig | None = None
    ocr_enabled: bool = True


@dataclass(frozen=True)
class LocalModelConfig:
    """本地语义模型配置。"""

    embedding_model: str


def _as_bool(value: Any, default: bool) -> bool:
    """解析 TOML 或环境变量中的布尔值。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _read_config_file(config_path: str | Path) -> dict[str, Any]:
    """读取可选 TOML 配置文件。"""
    path = Path(config_path)
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            parsed = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        details = {"path": str(path)}
        if isinstance(exc, tomllib.TOMLDecodeError):
            if getattr(exc, "lineno", None) is not None:
                details["line"] = exc.lineno
            if getattr(exc, "colno", None) is not None:
                details["column"] = exc.colno
        raise AppError(
            "config_invalid",
            "配置文件无法读取或不是合法 TOML。",
            "请检查 config.toml 的语法。",
            details,
        ) from exc
    if not isinstance(parsed, dict):
        raise AppError("config_invalid", "配置文件根节点必须是对象。", "请检查 config.toml 的结构。")
    return parsed


def load_settings(
    config_path: str | Path = "config.toml",
    base_url: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    fallback_enabled: bool | None = None,
) -> AppSettings:
    """按 CLI 参数、环境变量、TOML、默认值的顺序加载配置。"""
    config = _read_config_file(config_path)
    llm = config.get("llm", {}) if isinstance(config.get("llm", {}), dict) else {}
    runtime = config.get("runtime", {}) if isinstance(config.get("runtime", {}), dict) else {}
    local = config.get("local_model", {}) if isinstance(config.get("local_model", {}), dict) else {}
    ocr = config.get("ocr", {}) if isinstance(config.get("ocr", {}), dict) else {}
    env_enabled = os.getenv("RESUME_AI_ENABLED")
    explicit_model_config = bool(base_url or model or os.getenv("RESUME_AI_BASE_URL") or os.getenv("RESUME_AI_MODEL"))
    llm_enabled = (
        _as_bool(env_enabled, True)
        if env_enabled is not None or explicit_model_config
        else _as_bool(llm.get("enabled"), True)
    )
    resolved_fallback = (
        fallback_enabled
        if fallback_enabled is not None
        else _as_bool(os.getenv("RESUME_AI_FALLBACK_ENABLED"), _as_bool(runtime.get("fallback_enabled"), True))
    )
    resolved_url = base_url or os.getenv("RESUME_AI_BASE_URL") or llm.get("base_url")
    resolved_model = model or os.getenv("RESUME_AI_MODEL") or llm.get("model")
    resolved_key = api_key or os.getenv("RESUME_AI_API_KEY") or llm.get("api_key")
    provider = str(os.getenv("RESUME_AI_PROVIDER") or llm.get("provider") or "openai").lower()
    response_path = os.getenv("RESUME_AI_RESPONSE_PATH") or llm.get("response_path")
    content_type = str(os.getenv("RESUME_AI_CONTENT_TYPE") or llm.get("content_type") or "text").lower()
    local_enabled = _as_bool(os.getenv("RESUME_LOCAL_MODEL_ENABLED"), _as_bool(local.get("enabled"), False))
    embedding_model = str(os.getenv("RESUME_LOCAL_EMBEDDING_MODEL") or local.get("embedding_model") or "BAAI/bge-small-zh-v1.5")
    local_model = LocalModelConfig(embedding_model=embedding_model) if local_enabled else None
    ocr_enabled = _as_bool(os.getenv("RESUME_OCR_ENABLED"), _as_bool(ocr.get("enabled"), True))
    if not llm_enabled or not resolved_url or not resolved_model:
        return AppSettings(model=None, fallback_enabled=resolved_fallback, local_model=local_model, ocr_enabled=ocr_enabled)
    try:
        timeout_seconds = int(os.getenv("RESUME_AI_TIMEOUT_SECONDS", str(llm.get("timeout_seconds", 20))))
    except (TypeError, ValueError):
        timeout_seconds = 20
    model_config = ModelConfig(
        base_url=str(resolved_url).rstrip("/"),
        model=str(resolved_model),
        api_key=str(resolved_key) if resolved_key else None,
        timeout_seconds=max(1, min(timeout_seconds, 120)),
        fallback_enabled=resolved_fallback,
        provider=provider,
        response_path=str(response_path) if response_path else None,
        content_type=content_type,
    )
    return AppSettings(model=model_config, fallback_enabled=resolved_fallback, local_model=local_model, ocr_enabled=ocr_enabled)


def load_model_config(base_url: str | None = None, model: str | None = None, api_key: str | None = None) -> ModelConfig | None:
    """从参数和环境变量加载模型配置；未配置时返回空。"""
    return load_settings(base_url=base_url, model=model, api_key=api_key).model


def repair_json_object(raw: str) -> dict[str, Any]:
    """仅修复代码围栏和尾随逗号等无语义歧义的格式问题。"""
    content = raw.strip()
    content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.IGNORECASE)
    content = re.sub(r"\s*```$", "", content).strip()
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end < start:
        raise AppError("invalid_model_json", "模型没有返回 JSON 对象。", "请调整模型提示词或切换模型服务。")
    candidate = re.sub(r",\s*([}\]])", r"\1", content[start : end + 1])
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise AppError(
            "invalid_model_json",
            "模型返回的 JSON 无法解析。",
            "已尝试修复代码围栏和尾随逗号；请检查模型输出。",
            {"line": exc.lineno, "column": exc.colno},
        ) from exc
    if not isinstance(parsed, dict):
        raise AppError("invalid_model_json", "模型返回的根节点不是 JSON 对象。")
    return parsed


class OpenAICompatibleClient:
    """通过标准库调用本地或线上 OpenAI-compatible 服务。"""

    def __init__(self, config: ModelConfig):
        self.config = config
        self.last_status = "not_called"
        self.last_error: str | None = None

    def complete_json(self, system_prompt: str, user_content: str) -> dict[str, Any]:
        """请求结构化 JSON，并转换为字典。"""
        self.last_status = "calling"
        self.last_error = None
        payload = {
            "model": self.config.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        }
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        request = Request(
            f"{self.config.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            response = self._request_response(request)
            content = extract_response_content(response, self.config)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise AppError(
                "invalid_model_response",
                "模型服务返回了无法识别的响应。",
                "请确认服务兼容 OpenAI Chat Completions 接口。",
                {"reason": type(exc).__name__},
            ) from exc
        except HTTPError as exc:
            raise AppError(
                "model_http_error",
                "模型服务返回请求错误。",
                "请检查服务地址、模型名称和密钥。",
                {"status": exc.code},
            ) from exc
        except (URLError, TimeoutError) as exc:
            raise AppError(
                "model_unavailable",
                "模型服务暂时不可用。",
                "已自动使用本地规则和轻量模型降级结果。",
                {"reason": type(exc).__name__},
            ) from exc
        result = repair_json_object(content)
        self.last_status = "success"
        return result

    def _request_response(self, request: Request) -> dict[str, Any]:
        """请求两次以内，短暂网络抖动时不立即触发降级。"""
        last_error: Exception | None = None
        for attempt in range(2):
            started = time.monotonic()
            LOGGER.info("模型请求开始（第 %d/2 次）", attempt + 1)
            try:
                with urlopen(request, timeout=self.config.timeout_seconds) as response:
                    body = json.loads(response.read().decode("utf-8"))
                if not isinstance(body, dict):
                    raise TypeError("响应根节点不是对象")
                LOGGER.info("模型响应已收到，第 %d 次请求耗时 %.1f 秒", attempt + 1, time.monotonic() - started)
                return body
            except (URLError, TimeoutError) as exc:
                last_error = exc
                LOGGER.warning("模型请求失败（第 %d/2 次，耗时 %.1f 秒）：%s", attempt + 1, time.monotonic() - started, type(exc).__name__)
                if attempt == 0:
                    continue
                raise
        raise last_error or RuntimeError("模型请求失败")


def _get_json_path(payload: dict[str, Any], path: str) -> Any:
    """按点号路径读取嵌套 JSON，支持数组下标。"""
    current: Any = payload
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            raise AppError(
                "invalid_model_response",
                f"模型响应中找不到配置路径：{path}。",
                "请检查 response_path 配置和服务商返回格式。",
            )
    return current


def extract_response_content(response: dict[str, Any], config: ModelConfig) -> str:
    """依据 provider 和 response_path 将服务响应转换为 JSON 文本。"""
    provider = config.provider.lower()
    if provider in {"openai", "deepseek", "ollama", "openai-compatible"}:
        value = _get_json_path(response, config.response_path or "choices.0.message.content")
    elif provider == "custom":
        if not config.response_path:
            raise AppError(
                "config_invalid",
                "custom provider 必须配置 response_path。",
                "例如 response_path = \"data.output.content\"。",
            )
        value = _get_json_path(response, config.response_path)
    else:
        raise AppError(
            "config_invalid",
            f"不支持的模型 provider：{config.provider}。",
            "请使用 openai、deepseek、ollama、openai-compatible 或 custom。",
        )

    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, str) and value.strip():
        if config.content_type == "json":
            parsed = json.loads(value)
            return json.dumps(parsed, ensure_ascii=False) if isinstance(parsed, (dict, list)) else value
        return value
    raise AppError(
        "invalid_model_response",
        "模型响应路径对应的内容为空或类型不支持。",
        "请检查 response_path 和 content_type 配置。",
    )
