"""Stable, user-reportable error identifiers for Agent failures."""

from __future__ import annotations

import uuid
import logging

logger = logging.getLogger(__name__)


ERROR_MESSAGES = {
    "AGT-TOOL-ARGUMENTS": "工具参数格式无效",
    "AGT-TOOL-NOT-FOUND": "请求的操作暂不可用",
    "AGT-TOOL-VALIDATION": "请求内容未通过校验",
    "AGT-TOOL-EXECUTION": "操作执行失败",
    "AGT-TOOL-OUTPUT": "操作返回了无法识别的结果",
    "AGT-MODEL-UNAVAILABLE": "智能助手暂时无法完成请求",
    "AGT-TURN-EMPTY": "智能助手没有生成有效回复",
    "AGT-TURN-LIMIT": "请求步骤过多，未能完成",
    "AGT-TURN-TIMEOUT": "请求处理超时",
    "AGT-TURN-FAILED": "请求处理失败",
    "AGT-REQUEST-INVALID": "请求参数无效",
    "AGT-SERVICE-UNAVAILABLE": "智能助手服务暂不可用",
}


def new_error_id() -> str:
    return uuid.uuid4().hex[:12].upper()


def public_error(code: str, error_id: str | None = None) -> dict[str, str]:
    return {
        "error_code": code,
        "error_id": error_id or new_error_id(),
        "message": ERROR_MESSAGES.get(code, ERROR_MESSAGES["AGT-TURN-FAILED"]),
    }


def user_error_text(code: str, error_id: str | None = None) -> str:
    error = public_error(code, error_id)
    logger.error("Agent request failed [%s] error_id=%s", error["error_code"], error["error_id"])
    return f"{error['message']}（错误码：{error['error_code']}，追踪号：{error['error_id']}）"
