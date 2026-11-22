"""派生归属专用接口：字段范围、复核提交/确认、共享撤销与复查报告。

通用资源路由仍然可用（列表/登记/动作），这里补的是需要携带复核上下文、
以及按域生成可提交范围与跨域报告的读取入口。
"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.schemas import (
    ActionResult,
    GrantRevokePayload,
    ReviewConfirmPayload,
    ReviewSubmitPayload,
)
from app.services import service_for

router = APIRouter(prefix="/api/attribution", tags=["派生归属"])

MODULE = "derived_attribution"


def _service():
    service = service_for(MODULE)
    if service is None:
        raise HTTPException(status_code=404, detail="派生归属服务未注册")
    return service


def _result(entry: dict[str, Any] | None, message: str) -> ActionResult:
    return ActionResult(ok=entry is not None, message=message, entry=entry)


@router.get("/compliance/scope", response_model=dict)
def compliance_scope(domain: str = Query(...),
                     table: str | None = Query(default=None)) -> dict[str, Any]:
    """合规检查侧读取：与 /fields 同源，走合规检查服务的消费入口。"""
    service = service_for("compliance_check")
    return service.field_scope(domain, table)


@router.get("/compliance/report", response_model=dict)
def compliance_report(table: str | None = Query(default=None),
                      round_id: str | None = Query(default=None)) -> dict[str, Any]:
    """合规复核报告：分母按派生表+字段去重。"""
    service = service_for("compliance_check")
    return service.attribution_report(table=table, round_id=round_id)


@router.get("/fields", response_model=dict)
def field_scope(domain: str = Query(...), table: str | None = Query(default=None)) -> dict[str, Any]:
    """合规检查读取：某域本轮可提交的字段范围。"""
    return _service().build_scope(domain, table)


@router.get("/permission", response_model=dict)
def field_permission(domain: str = Query(...), table: str = Query(...),
                     field: str = Query(...)) -> dict[str, Any]:
    """某域对某字段是否有复核权限，以及权限依据。"""
    service = _service()
    permission = service.permission(domain, table, field)
    if permission is None:
        attr = service.find_attribution(table=table, field=field)
        reason = service._reject_reason(attr, domain) if attr else "字段不在归属清单中"
        return {"granted": False, "message": reason}
    attr = permission["attr"]
    grant = permission.get("grant") or {}
    return {
        "granted": True,
        "kind": permission["kind"],
        "attribution": {
            "清单编号": str(attr.get("清单编号", "")),
            "归属域": str(attr.get("归属域", "")),
            "归属类型": str(attr.get("归属类型", "")),
            "共享域": str(attr.get("共享域", "")),
        },
        "basis": str(permission["basis"]),
        "basis_detail": str(permission.get("basis_detail", "")),
        "grant": {
            "grant_id": str(grant.get("grant_id", "")),
            "status": str(grant.get("status", "")),
            "from_domain": str(grant.get("from_domain", "")),
            "to_domain": str(grant.get("to_domain", "")),
        } if grant else None,
        "message": (
            f"{domain}可复核{table}.{field}（{permission['kind']}，依据 {permission['basis']}）"
        ),
    }


@router.get("/report", response_model=dict)
def report(table: str | None = Query(default=None),
           round_id: str | None = Query(default=None),
           persist: bool = Query(default=False)) -> dict[str, Any]:
    """确认覆盖报告：按派生表+字段去重统计，跨域共同负责不重复计入分母。"""
    service = _service()
    if persist:
        return service.write_report(table=table, round_id=round_id)
    return service.build_report(table=table, round_id=round_id)


@router.get("/global", response_model=dict)
def global_coverage() -> dict[str, Any]:
    """全局覆盖：同一派生表只计一次。"""
    return _service().global_coverage()


@router.get("/trace/{attribution_id}", response_model=dict)
def trace(attribution_id: int, round_id: str | None = Query(default=None)) -> dict[str, Any]:
    """追查某字段实际生效的确认与两个域的提交痕迹。"""
    result = _service().trace_field(attribution_id, round_id=round_id)
    if not result.get("found"):
        raise HTTPException(status_code=404, detail=str(result.get("message", "未找到字段")))
    return result


@router.get("/revalidate", response_model=dict)
def revalidate(table: str | None = Query(default=None),
               round_id: str | None = Query(default=None)) -> dict[str, Any]:
    """撤权后复查：已确认报告保留情况与本轮可提交范围。"""
    return _service().recheck(table=table, round_id=round_id)


@router.get("/export")
def export_report(table: str | None = Query(default=None)) -> StreamingResponse:
    """导出字段覆盖清单 CSV。"""
    service = _service()
    report = service.build_report(table=table)
    output = io.StringIO()
    output.write("﻿")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["派生表", "目标字段", "已覆盖", "确认域", "确认域数", "覆盖计数", "生效结论"])
    for row in report["field_details"]:
        writer.writerow([
            row["派生表"], row["目标字段"], "是" if row["covered"] else "否",
            "、".join(row["confirmed_domains"]), row["confirmed_domain_count"],
            row["coverage_counted"], row["effective_conclusion"],
        ])
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="attribution_report.csv"'},
    )


@router.post("/reviews", response_model=ActionResult)
def submit_review(payload: ReviewSubmitPayload) -> ActionResult:
    """提交某字段的复核候选：记录提交域、提交人、所用字段与授权依据。"""
    service = _service()
    entry, message = service.submit_review(
        None,
        domain=str(payload.domain or ""),
        submitter=str(payload.submitter or ""),
        table=payload.table,
        field=payload.field,
        fields=payload.fields,
        round_id=payload.round_id,
        remark=str(payload.remark or ""),
    )
    return _result(entry, message)


@router.post("/reviews/{review_id}/confirm", response_model=ActionResult)
def confirm_review(review_id: str, payload: ReviewConfirmPayload | None = None) -> ActionResult:
    """确认候选：按最新共享授权校验后，决定哪份结论生效。"""
    service = _service()
    entry, message = service.confirm_conclusion(
        review_id,
        confirmer=str((payload.confirmer if payload else "") or ""),
    )
    return _result(entry, message)


@router.post("/grants/{grant_id}/revoke", response_model=ActionResult)
def revoke_grant(grant_id: str, payload: GrantRevokePayload | None = None) -> ActionResult:
    """撤销共享授权，待确认候选立即失效。"""
    service = _service()
    entry, message = service.revoke_share(
        grant_id,
        revoked_by=str((payload.submitter if payload else "") or "数据治理委员会"),
        reason=str((payload.remark if payload else "") or ""),
    )
    return _result(entry, message)
