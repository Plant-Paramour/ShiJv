from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request
from apps.http_transport import model_urlopen as urlopen
from collections.abc import Iterator


class JsonTransportError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


def request_json(
    method: str,
    url: str,
    headers: Mapping[str, str],
    payload: Mapping[str, Any] | None,
    timeout_seconds: float,
) -> dict[str, Any]:
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=data, headers=dict(headers), method=method)
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        exc.close()
        retry_after = exc.headers.get("Retry-After")
        try:
            retry_after = float(retry_after) if retry_after else None
        except ValueError:
            retry_after = None
        raise JsonTransportError(f"模型服务返回 HTTP {exc.code}", status_code=exc.code, retry_after=retry_after) from exc
    except URLError as exc:
        raise JsonTransportError(str(exc.reason)) from exc
    try:
        result = json.loads(body)
    except json.JSONDecodeError as exc:
        raise JsonTransportError("响应不是有效 JSON") from exc
    if not isinstance(result, dict):
        raise JsonTransportError("响应 JSON 根节点不是对象")
    return result


def request_sse(method: str, url: str, headers: Mapping[str, str], payload: Mapping[str, Any], timeout_seconds: float) -> Iterator[str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=data, headers={**dict(headers), "Accept": "text/event-stream"}, method=method)
    try:
        response = urlopen(request, timeout=timeout_seconds)
    except (HTTPError, URLError) as exc:
        code = exc.code if isinstance(exc, HTTPError) else None
        if isinstance(exc, HTTPError):
            exc.close()
        raise JsonTransportError(f"模型流式连接失败（HTTP {code}）" if code else "模型流式连接失败，请检查网络与服务地址", status_code=code) from exc
    def lines():
        try:
            for line in response:
                yield line.decode("utf-8", errors="replace")
        finally:
            response.close()
    return lines()
