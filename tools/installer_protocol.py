"""可靠的安装器请求、事件和结果协议。"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import time
from typing import Any


@dataclass
class InstallRequest:
    runtime_source: str
    runtime_path: str
    model_source: str
    model_path: str
    model_id: str
    profile: str = "auto"
    action: str = "install"

    def validate(self) -> None:
        if self.runtime_source not in {"new", "local"}:
            raise ValueError("运行环境来源必须是 new 或 local")
        if self.model_source not in {"official", "local"}:
            raise ValueError("模型来源必须是 official 或 local")
        if self.model_id not in {"Qwen/Qwen3.5-0.8B", "Qwen/Qwen3.5-2B", "Qwen/Qwen3.5-4B"}:
            raise ValueError("当前版本仅支持 Qwen3.5 0.8B、2B、4B")
        if not self.runtime_path or not self.model_path:
            raise ValueError("环境目录和模型目录不能为空")


@dataclass
class InstallEvent:
    stage: str
    state: str
    message: str
    progress: int | None = None
    error_code: str | None = None
    details: str | None = None
    can_retry: bool = False
    can_fallback_to_cpu: bool = False
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = time.time()

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(asdict(self), ensure_ascii=False) + "\n")
            stream.flush()


def write_request(path: Path, request: InstallRequest) -> None:
    request.validate()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(request), ensure_ascii=False, indent=2), encoding="utf-8")


def read_request(path: Path) -> InstallRequest:
    request = InstallRequest(**json.loads(path.read_text(encoding="utf-8")))
    request.validate()
    return request


def write_result(path: Path, *, ok: bool, message: str, **extra: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    result = {"ok": ok, "message": message, "timestamp": time.time(), **extra}
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
