"""通用接口契约；模块字段由领域服务持有，不再为每个模块复制一份模型类。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20


class ActionResult(BaseModel):
    ok: bool
    message: str
    entry: dict[str, Any] | None = None


class EntryPayload(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None


class ActionPayload(BaseModel):
    action: str
    remark: str | None = None
    # 派生归属复核用到的上下文，其他模块忽略即可。
    domain: str | None = None
    submitter: str | None = None
    fields: list[str] | None = None
    round_id: str | None = None
    table: str | None = None
    field: str | None = None
    grant_id: str | None = None
    review_id: str | None = None
    confirmer: str | None = None


class ReviewSubmitPayload(BaseModel):
    """复核提交：没有通用动作名，字段都可以单独指定。"""

    domain: str
    submitter: str
    table: str | None = None
    field: str | None = None
    fields: list[str] | None = None
    round_id: str | None = None
    remark: str | None = None


class ReviewConfirmPayload(BaseModel):
    confirmer: str | None = None
    remark: str | None = None


class GrantRevokePayload(BaseModel):
    submitter: str | None = None
    remark: str | None = None
