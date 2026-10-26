from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse, urlencode
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from apps.agent.anthropic_client import AnthropicMessagesChatModel
from apps.agent.openai_client import OpenAICompatibleChatModel
from apps.agent.transport import request_json, JsonTransportError


router = APIRouter(prefix="/v1/model-config", tags=["model-config"])


class ModelEndpoint(BaseModel):
    provider_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    provider: str = Field(pattern=r"^(openai-compatible|anthropic)$")
    base_url: str = Field(min_length=8, max_length=2048)
    model: str = Field(min_length=1, max_length=200)
    api_key: str = Field(default="", max_length=4096)
    timeout_seconds: float = Field(default=90, gt=0, le=600)
    priority: int = 0
    tier: str = Field(default="primary", pattern=r"^(primary|secondary|emergency)$")
    supports_tools: bool = True
    supports_stream: bool = True


class ModelConfigBody(BaseModel):
    endpoints: list[ModelEndpoint] = Field(min_length=1, max_length=20)


class ModelTestBody(ModelEndpoint):
    pass


class ModelListBody(BaseModel):
    provider_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    provider: str = Field(pattern=r"^(openai-compatible|anthropic)$")
    base_url: str = Field(min_length=8, max_length=2048)
    api_key: str = Field(default="", max_length=4096)
    timeout_seconds: float = Field(default=30, gt=0, le=60)


def _validate_url(value: str):
    parsed = urlparse(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise HTTPException(status_code=422, detail="服务地址必须是无内嵌凭据的 HTTP/HTTPS URL")


def _local_key(base_url: str, key: str) -> str:
    return key or ("local" if urlparse(base_url).hostname in {"localhost", "127.0.0.1", "::1"} else "")


@router.post("/models")
def list_endpoint_models(body: ModelListBody, request: Request):
    if request.app.state.users.local_profile() is None:
        raise HTTPException(status_code=428, detail="请先创建本地用户档案")
    _validate_url(body.base_url)
    key = body.api_key
    if not key:
        previous = next((item for item in _read(_config_path(request)) if item.get("provider_id") == body.provider_id), {})
        if previous.get("provider") == body.provider and previous.get("base_url", "").rstrip("/") == body.base_url.rstrip("/"):
            key = previous.get("api_key", "")
    key = _local_key(body.base_url, key)
    if not key:
        raise HTTPException(status_code=422, detail="请填写 API 密钥；更换服务地址或协议后需重新填写")
    base = body.base_url.rstrip("/")
    for suffix in ("/chat/completions", "/messages", "/models"):
        if base.endswith(suffix):
            base = base[:-len(suffix)]
            break
    url = base + ("/models" if base.endswith("/v1") else "/v1/models")
    headers = ({"x-api-key": key, "anthropic-version": "2023-06-01"}
               if body.provider == "anthropic" else {"Authorization": f"Bearer {key}"})
    headers["Accept"] = "application/json"
    models, seen_cursors = {}, set()
    query = {"limit": 1000} if body.provider == "anthropic" else {}
    deadline = time.monotonic() + 60
    truncated = False
    try:
        for _ in range(100):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                truncated = True
                break
            page = request_json("GET", url + ("?" + urlencode(query) if query else ""), headers, None,
                                min(body.timeout_seconds, remaining))
            items = page.get("data")
            if not isinstance(items, list):
                raise ValueError("服务未返回标准模型列表")
            for item in items:
                if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip():
                    ident = item["id"].strip()
                    models[ident] = {"id": ident, "name": str(item.get("display_name") or item.get("name") or ident)}
            if not page.get("has_more"):
                break
            cursor = page.get("last_id") or (items[-1].get("id") if items and isinstance(items[-1], dict) else None)
            if not isinstance(cursor, str) or cursor in seen_cursors or len(models) >= 10000:
                truncated = True
                break
            seen_cursors.add(cursor)
            query["after_id" if body.provider == "anthropic" else "after"] = cursor
        else:
            truncated = True
    except JsonTransportError as exc:
        status = exc.status_code
        detail = ("服务不支持获取模型列表，请直接输入模型名称" if status in {404, 405, 501}
                  else "无法获取模型列表，请检查 API 密钥和访问权限" if status in {401, 403}
                  else "无法获取模型列表，请检查服务地址或稍后重试；也可直接输入模型名称")
        raise HTTPException(status_code=502, detail=detail) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="服务未返回有效模型列表，也可直接输入模型名称") from exc
    return {"models": sorted(models.values(), key=lambda item: item["id"].casefold()), "truncated": truncated}


def _config_path(request: Request) -> Path:
    return request.app.state.settings.database_path.parent / "models.toml"


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib
    return list(tomllib.loads(path.read_text(encoding="utf-8")).get("endpoints", []))


def _public(item: dict) -> dict:
    return {key: value for key, value in item.items() if key != "api_key"} | {"api_key_configured": bool(item.get("api_key"))}


@router.get("")
def get_model_config(request: Request):
    items = _read(_config_path(request))
    return {"endpoints": [_public(item) for item in items], "configured": bool(items), "profile_created": request.app.state.users.local_profile() is not None}


@router.put("")
def save_model_config(body: ModelConfigBody, request: Request):
    if request.app.state.users.local_profile() is None:
        raise HTTPException(status_code=428, detail="请先创建本地用户档案")
    path = _config_path(request)
    previous = {item.get("provider_id"): item for item in _read(path)}
    normalized = []
    seen = set()
    for model in body.endpoints:
        item = model.model_dump()
        parsed = urlparse(item["base_url"])
        if (parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise HTTPException(status_code=422, detail=f"模型端点 {item['provider_id']} 的地址必须是无内嵌凭据的 HTTP/HTTPS URL")
        if item["provider_id"] in seen:
            raise HTTPException(status_code=422, detail="模型端点编号不能重复")
        seen.add(item["provider_id"])
        if not item["api_key"]:
            old = previous.get(item["provider_id"], {})
            if old.get("provider") == item["provider"] and old.get("base_url", "").rstrip("/") == item["base_url"].rstrip("/"):
                item["api_key"] = old.get("api_key", "")
        item["api_key"] = _local_key(item["base_url"], item["api_key"])
        if not item["api_key"]:
            raise HTTPException(status_code=422, detail=f"请填写 {item['provider_id']} 的 API 密钥")
        item["weight"] = 1
        item["cost_level"] = "standard"
        item["max_concurrency"] = 8
        normalized.append(item)
    lines = []
    for item in normalized:
        lines.append("[[endpoints]]")
        for key in ("provider_id", "provider", "base_url", "model", "api_key", "tier", "priority", "weight", "max_concurrency", "timeout_seconds", "supports_tools", "supports_stream", "cost_level"):
            if key in item:
                value = json.dumps(item[key], ensure_ascii=False) if isinstance(item[key], str) else str(item[key]).lower() if isinstance(item[key], bool) else str(item[key])
                lines.append(f"{key} = {value}")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".toml.tmp")
    temporary.write_text("\n".join(lines), encoding="utf-8")
    temporary.replace(path)
    reload_agent = getattr(request.app.state, "reload_agent_service", None)
    if callable(reload_agent):
        try:
            reload_agent()
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"模型配置已保存但无法加载：{exc}") from exc
    return {"endpoints": [_public(item) for item in normalized], "configured": True}


@router.post("/test")
def test_model_config(body: ModelTestBody, request: Request):
    _validate_url(body.base_url)
    if request.app.state.users.local_profile() is None:
        raise HTTPException(status_code=428, detail="请先创建本地用户档案")
    api_key = body.api_key or next((item.get("api_key", "") for item in _read(_config_path(request)) if item.get("provider_id") == body.provider_id and item.get("provider") == body.provider and item.get("base_url", "").rstrip("/") == body.base_url.rstrip("/")), "")
    api_key = _local_key(body.base_url, api_key)
    if not api_key:
        raise HTTPException(status_code=422, detail="请先填写 API 密钥")
    provider = AnthropicMessagesChatModel if body.provider == "anthropic" else OpenAICompatibleChatModel
    model = provider(base_url=body.base_url, api_key=api_key, model=body.model, timeout_seconds=body.timeout_seconds)
    try:
        result = model.complete([{"role": "user", "content": "请只回复：连接成功"}], [])
    except Exception as exc:
        raise HTTPException(status_code=502, detail="连接模型服务失败，请检查服务地址、密钥与模型名称") from exc
    return {"ok": True, "message": str(result.get("content") or "已收到模型响应")[:200]}
