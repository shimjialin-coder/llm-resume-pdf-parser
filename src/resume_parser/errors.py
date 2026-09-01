"""面向命令行用户的可恢复错误定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AppError(Exception):
    """表示可预期的业务错误，避免向终端输出堆栈。"""

    code: str
    message: str
    hint: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    exit_code: int = 2

    def as_dict(self) -> dict[str, Any]:
        """转换为可供脚本处理的错误格式。"""
        payload: dict[str, Any] = {"error": {"code": self.code, "message": self.message}}
        if self.hint:
            payload["error"]["hint"] = self.hint
        if self.details:
            payload["error"]["details"] = self.details
        return payload

