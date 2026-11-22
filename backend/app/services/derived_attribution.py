"""derived_attribution 领域服务：跨域派生表的字段归属清单与复核结论。

血缘记录只说明“哪张表来自哪张表”，合规复核还需要回答“这个字段由谁负责、
凭什么是另一个域也能复核”。本模块把派生表的可核对字段写成归属清单：

    - 独有字段：只有归属域可以提交/确认，其他域不能代审；
    - 共享字段：归属域保留责任，另一个域凭 share_grant 授权复核；
    - 授权必须有依据（grant_id + basis），撤销后立即从可提交范围消失；
    - 同一字段同一时间只有一份结论生效，归属域结论优先；
    - 统计按“派生表 + 字段”去重，跨域共同负责不会放大全局分母。

跨模块数据流：
    lineage_graph → derived_attribution: 来源资产/来源字段的血缘依据
    derived_attribution → compliance_check: 各域可提交字段范围、复核结论
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "attribution")

ATTR_TABLE = "derived_attribution"
GRANT_TABLE = "_derived_share_grant"
REVIEW_TABLE = "_derived_attribution_review"
CONCLUSION_TABLE = "_derived_attribution_conclusion"


def default_round() -> str:
    """未显式指定轮次时，按自然月归轮。"""
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DerivedAttributionService(BaseModuleService):
    MODULE = ATTR_TABLE
    ENTITY = "派生归属"
    OPERATION_TERM = "核对"
    LIST_FIELDS = [
        "清单编号", "派生表", "来源资产", "来源字段", "目标字段",
        "归属域", "归属类型", "共享域", "授权依据",
    ]
    REQUIRED_FIELDS = ["清单编号", "派生表", "来源资产", "来源字段", "目标字段", "归属域"]
    STATUS_ORDER = ["草稿", "待核对", "已生效", "已失效"]
    ACTION_RULES = {
        "提交复核": "待核对",
        "确认结论": "已生效",
        "核对生效": "已生效",
        "撤销共享": "已生效",
        "标记失效": "已失效",
    }
    NEGATIVE_ACTIONS = ["标记失效"]

    def __init__(self):
        super().__init__()
        os.makedirs(DATA_DIR, exist_ok=True)

    # ---------- 清单读取 ----------
    def _attrs(self) -> list[dict[str, Any]]:
        return store.rows(ATTR_TABLE)

    def _grants(self) -> list[dict[str, Any]]:
        return store.rows(GRANT_TABLE)

    def _reviews(self) -> list[dict[str, Any]]:
        return store.rows(REVIEW_TABLE)

    def _conclusions(self) -> list[dict[str, Any]]:
        return store.rows(CONCLUSION_TABLE)

    def find_attribution(self, table: str | None = None,
                         field: str | None = None,
                         attribution_id: int | None = None) -> dict[str, Any] | None:
        for row in self._attrs():
            if attribution_id is not None and int(row.get("id", 0)) == int(attribution_id):
                return row
            if attribution_id is None and table and field:
                if row.get("派生表") == table and row.get("目标字段") == field:
                    return row
        return None

    def list_tables(self) -> list[str]:
        return sorted({str(row.get("派生表", "")) for row in self._attrs() if row.get("派生表")})

    def lineage_basis(self, attr: dict[str, Any]) -> dict[str, Any] | None:
        """返回归属清单引用的血缘记录，确认来源字段确实派生出目标字段。"""
        table = str(attr.get("派生表", ""))
        source = str(attr.get("来源资产", ""))
        declared = str(attr.get("授权依据", ""))
        candidates = []
        for row in store.rows("lineage_graph"):
            if row.get("status") not in ("已注册", "已生效"):
                continue
            if row.get("上游资产") == source and row.get("下游资产") == table:
                candidates.append(row)
        if not candidates:
            return None
        if declared.startswith("LINE-"):
            for row in candidates:
                if str(row.get("记录编号", "")) == declared:
                    return row
            return None
        return candidates[0]

    def active_grant(self, table: str, field: str, from_domain: str | None = None,
                     to_domain: str | None = None) -> dict[str, Any] | None:
        """只认“已生效 + 有依据 + 表字段与授权双方都对得上”的共享授权。"""
        for grant in self._grants():
            if str(grant.get("table", "")) != table:
                continue
            if str(grant.get("field", "")) != field:
                continue
            if str(grant.get("status", "")) != "已生效":
                continue
            if not str(grant.get("basis", "")).strip():
                continue
            if from_domain and str(grant.get("from_domain", "")) != from_domain:
                continue
            if to_domain and str(grant.get("to_domain", "")) != to_domain:
                continue
            return grant
        return None

    def _grant_for_attr(self, attr: dict[str, Any],
                        to_domain: str | None = None) -> dict[str, Any] | None:
        if str(attr.get("归属类型", "")) != "共享":
            return None
        declared = str(attr.get("授权依据", ""))
        if not declared.startswith("SHR-"):
            return None
        grant = self.active_grant(
            str(attr.get("派生表", "")),
            str(attr.get("目标字段", "")),
            str(attr.get("归属域", "")),
            to_domain or str(attr.get("共享域", "")),
        )
        if grant is None or str(grant.get("grant_id", "")) != declared:
            return None
        if str(grant.get("to_domain", "")) != str(attr.get("共享域", "")):
            return None
        return grant

    def permission(self, domain: str, table: str, field: str) -> dict[str, Any] | None:
        """某域对某字段的复核权限：实际归属，或当前有效的共享授权。"""
        attr = self.find_attribution(table=table, field=field)
        if attr is None or attr.get("status") != "已生效":
            return None
        lineage = self.lineage_basis(attr)
        if lineage is None:
            return None
        if domain == str(attr.get("归属域", "")):
            return {
                "kind": "归属",
                "attr": attr,
                "grant": None,
                "lineage": lineage,
                "basis": str(lineage.get("记录编号", "")),
                "basis_detail": str(lineage.get("加工逻辑", "")),
            }
        if str(attr.get("归属类型", "")) == "共享" and domain == str(attr.get("共享域", "")):
            grant = self._grant_for_attr(attr, to_domain=domain)
            if grant is None:
                return None
            return {
                "kind": "共享",
                "attr": attr,
                "grant": grant,
                "lineage": lineage,
                "basis": str(grant.get("grant_id", "")),
                "basis_detail": str(grant.get("basis", "")),
            }
        return None

    def submittable_fields(self, domain: str, table: str | None = None) -> list[dict[str, Any]]:
        """生成某域本轮可提交的字段范围，权限只覆盖归属或获准共享的字段。"""
        entries: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for attr in self._attrs():
            if attr.get("status") != "已生效":
                continue
            attr_table = str(attr.get("派生表", ""))
            if table and attr_table != table:
                continue
            field = str(attr.get("目标字段", ""))
            permission = self.permission(domain, attr_table, field)
            if permission is None:
                continue
            key = (attr_table, field)
            if key in seen:
                continue
            seen.add(key)
            grant = permission.get("grant") or {}
            entries.append({
                "清单编号": str(attr.get("清单编号", "")),
                "派生表": attr_table,
                "目标字段": field,
                "来源资产": str(attr.get("来源资产", "")),
                "来源字段": str(attr.get("来源字段", "")),
                "归属域": str(attr.get("归属域", "")),
                "归属类型": str(attr.get("归属类型", "")),
                "权限类型": permission["kind"],
                "授权编号": str(grant.get("grant_id", "")),
                "共享依据": str(grant.get("basis", "")),
                "血缘依据": str(permission["basis"]),
                "依据说明": str(permission.get("basis_detail", "")),
            })
        return entries

    def build_scope(self, domain: str, table: str | None = None) -> dict[str, Any]:
        """合规检查读取的“各域可提交字段范围”。"""
        entries = self.submittable_fields(domain, table)
        tables = sorted({entry["派生表"] for entry in entries})
        denominator = len({
            (str(row.get("派生表", "")), str(row.get("目标字段", "")))
            for row in self._attrs()
            if row.get("status") == "已生效" and (not table or row.get("派生表") == table)
        })
        return {
            "domain": domain,
            "table": table or (tables[0] if len(tables) == 1 else ""),
            "tables": tables,
            "fields": [entry["目标字段"] for entry in entries],
            "count": len(entries),
            "denominator_fields": denominator,
            "entries": entries,
            "note": "只包含实际归属字段与当前有生效依据的共享字段",
        }

    # ---------- 复核提交 ----------
    def _resolve_attr(self, attribution_id: int | None, table: str | None,
                      field: str | None) -> dict[str, Any] | None:
        if attribution_id is not None:
            return self.find_attribution(attribution_id=attribution_id)
        return self.find_attribution(table=table, field=field)

    def _pending_review(self, table: str, field: str, domain: str,
                        round_id: str) -> dict[str, Any] | None:
        for review in reversed(self._reviews()):
            if (review.get("派生表") == table and review.get("目标字段") == field
                    and review.get("提交域") == domain and review.get("轮次") == round_id
                    and review.get("status") == "待确认"):
                return review
        return None

    def _reject_reason(self, attr: dict[str, Any], domain: str) -> str:
        table = str(attr.get("派生表", ""))
        field = str(attr.get("目标字段", ""))
        owner = str(attr.get("归属域", ""))
        if attr.get("status") != "已生效":
            return f"归属清单 {attr.get('清单编号', '')} 尚未生效，不能提交「{field}」"
        if str(attr.get("归属类型", "")) == "独有":
            return f"独有字段「{field}」仅归{owner}，{domain}无权代审"
        if str(attr.get("归属类型", "")) == "共享":
            grant = self._grant_for_attr(attr, to_domain=domain)
            if grant is None:
                return f"共享字段「{field}」的授权{attr.get('授权依据', '')}已失效或缺少依据，{domain}不能提交"
        return f"{domain}对{table}.{field}没有复核权限"

    def submit_review(self, attribution_id: int | None = None, *,
                      domain: str, submitter: str, table: str | None = None,
                      field: str | None = None, fields: list[str] | None = None,
                      round_id: str | None = None,
                      remark: str = "") -> tuple[dict[str, Any] | None, str]:
        """提交某个字段的复核候选，记下所用字段、提交人和授权依据。"""
        attr = self._resolve_attr(attribution_id, table, field)
        if attr is None:
            return None, "派生归属清单记录不存在"
        table = str(attr.get("派生表", ""))
        field = str(attr.get("目标字段", ""))
        domain = (domain or "").strip()
        submitter = (submitter or "").strip()
        if not domain:
            return None, "提交域不能为空"
        if not submitter:
            return None, "提交人不能为空"

        permission = self.permission(domain, table, field)
        if permission is None:
            return None, self._reject_reason(attr, domain)

        used_fields = [str(item) for item in (fields or [field])]
        if field not in used_fields:
            return None, f"提交字段必须包含本清单字段「{field}」，不能借共享字段夹带其他字段"

        round_id = round_id or default_round()
        pending = self._pending_review(table, field, domain, round_id)
        if pending is not None:
            return pending, f"{domain}本轮已有待确认候选 {pending.get('复核编号', '')}，未重复登记"
        # 同一轮已有生效结论且仍是最高权限时，不再接受重复候选；新一轮可以重新复核。
        effective = self._effective_conclusion(table, field)
        if (effective is not None and str(effective.get("轮次", "")) == round_id
                and self._priority({"提交域": domain}, attr) >= self._priority(effective, attr)):
            return None, (
                f"{table}.{field} 本轮已有生效结论 {effective.get('结论编号', '')}"
                f"（{effective.get('提交域', '')}），{domain}无需重复复核"
            )

        # 同一轮另一域也提交同一字段时先标注冲突，确认阶段再决定谁的结论生效。
        other_pending = [
            review for review in self._reviews()
            if review.get("派生表") == table and review.get("目标字段") == field
            and review.get("轮次") == round_id and review.get("提交域") != domain
            and review.get("status") == "待确认"
        ]
        grant = permission.get("grant") or {}
        next_id = max((int(row.get("id", 0)) for row in self._reviews()), default=0) + 1
        review = {
            "id": next_id,
            "review_id": f"REVW-{next_id:04d}",
            "复核编号": f"REVW-{next_id:04d}",
            "status": "待确认",
            "pending": True,
            "abnormal": bool(other_pending),
            "派生表": table,
            "目标字段": field,
            "提交域": domain,
            "提交人": submitter,
            "所用字段": used_fields,
            "轮次": round_id,
            "权限类型": permission["kind"],
            "授权依据": str(grant.get("grant_id") or permission["basis"]),
            "依据说明": str(permission.get("basis_detail", "")),
            "冲突标记": bool(other_pending),
            "冲突域": sorted({str(item.get("提交域", "")) for item in other_pending}),
            "备注": remark,
            "提交时间": _now(),
            "确认时间": "",
            "确认人": "",
            "结论编号": "",
        }
        self._reviews().append(review)
        return review, (
            f"{domain}已提交{table}.{field}复核候选 {review['review_id']}，"
            f"权限类型={permission['kind']}，依据={review['授权依据']}"
        )

    # ---------- 结论确认 ----------
    def _latest_pending_review(self, attribution_id: int | None, table: str | None,
                               field: str | None, domain: str | None,
                               round_id: str | None) -> dict[str, Any] | None:
        attr = self._resolve_attr(attribution_id, table, field)
        if attr is None:
            return None
        table = str(attr.get("派生表", ""))
        field = str(attr.get("目标字段", ""))
        candidates = [
            review for review in self._reviews()
            if review.get("派生表") == table and review.get("目标字段") == field
            and review.get("status") == "待确认"
            and (not domain or review.get("提交域") == domain)
            and (not round_id or review.get("轮次") == round_id)
        ]
        return candidates[-1] if candidates else None

    def _effective_conclusion(self, table: str, field: str) -> dict[str, Any] | None:
        for conclusion in self._conclusions():
            if (conclusion.get("派生表") == table and conclusion.get("目标字段") == field
                    and conclusion.get("status") == "生效" and conclusion.get("effective")):
                return conclusion
        return None

    def _priority(self, conclusion_or_review: dict[str, Any], attr: dict[str, Any]) -> int:
        # 归属域结论优先于共享域结论；数字越小权限越高。
        return 0 if str(conclusion_or_review.get("提交域", "")) == str(attr.get("归属域", "")) else 1

    def confirm_conclusion(self, review_id: str | int | None = None, *,
                           attribution_id: int | None = None, table: str | None = None,
                           field: str | None = None, domain: str | None = None,
                           round_id: str | None = None,
                           confirmer: str = "") -> tuple[dict[str, Any] | None, str]:
        """确认复核候选：先按最新共享授权复核权限，再决定哪份结论生效。"""
        if review_id is not None:
            review = next(
                (row for row in self._reviews()
                 if str(row.get("review_id", "")) == str(review_id)
                 or (isinstance(review_id, int) and int(row.get("id", 0)) == review_id)),
                None,
            )
        else:
            review = self._latest_pending_review(attribution_id, table, field, domain, round_id)
        if review is None:
            return None, "没有找到可确认的复核候选"
        if review.get("status") == "已失效":
            return None, f"候选 {review.get('复核编号', '')} 已失效：{review.get('失效原因', '授权已撤销')}"

        attr = self.find_attribution(table=str(review.get("派生表", "")),
                                     field=str(review.get("目标字段", "")))
        if attr is None:
            return None, "派生归属清单记录不存在"

        table = str(review.get("派生表", ""))
        field = str(review.get("目标字段", ""))
        review_domain = str(review.get("提交域", ""))

        # 确认时重新读取最新授权，不能沿用提交时的旧结论。
        permission = self.permission(review_domain, table, field)
        if permission is None:
            if review.get("status") == "待确认":
                review["status"] = "已失效"
                review["pending"] = False
                review["abnormal"] = True
                review["失效原因"] = "共享授权已撤销或缺失"
                review["失效时间"] = _now()
            return None, self._reject_reason(attr, review_domain)

        existing = self._effective_conclusion(table, field)
        if review.get("status") == "已确认":
            conclusion = next(
                (row for row in self._conclusions()
                 if row.get("复核编号") == review.get("复核编号")),
                None,
            )
            if conclusion is not None and conclusion.get("effective"):
                return conclusion, f"候选 {review.get('复核编号', '')} 已确认，结论 {conclusion.get('结论编号', '')} 生效"
            if conclusion is not None:
                return conclusion, (
                    f"候选 {review.get('复核编号', '')} 已确认，但结论 {conclusion.get('结论编号', '')} "
                    f"未生效：{conclusion.get('冲突说明', '同字段已有更高权限结论')}"
                )
            return None, f"候选 {review.get('复核编号', '')} 已确认但没有结论记录"

        conflict_note = ""
        superseded = None
        if existing is not None:
            new_priority = self._priority({"提交域": review_domain}, attr)
            old_priority = self._priority(existing, attr)
            if new_priority < old_priority:
                # 归属域后来确认，覆盖共享域此前生效的结论。
                superseded = existing
                conflict_note = (
                    f"归属域{review_domain}结论优先，{existing.get('提交域', '')}"
                    f"的结论 {existing.get('结论编号', '')} 转为参考"
                )
            elif new_priority > old_priority:
                conflict_note = (
                    f"同字段已有归属域{existing.get('提交域', '')}的结论 "
                    f"{existing.get('结论编号', '')} 生效，本结论仅作参考"
                )
            else:
                # 同一域在新一轮重新确认，用最新结论替换旧结论。
                superseded = existing
                conflict_note = (
                    f"同域重新确认，旧结论 {existing.get('结论编号', '')} 被本轮结论替换"
                )

        effective = superseded is not None or existing is None
        if existing is not None and not effective:
            status = "参考"
        else:
            status = "生效"

        if superseded is not None:
            superseded["status"] = "参考"
            superseded["effective"] = False
            superseded["superseded_at"] = _now()
            superseded["superseded_reason"] = conflict_note

        review["status"] = "已确认"
        review["pending"] = False
        review["abnormal"] = bool(conflict_note)
        review["确认时间"] = _now()
        review["确认人"] = (confirmer or review.get("提交人", "")).strip()

        next_id = max((int(row.get("id", 0)) for row in self._conclusions()), default=0) + 1
        grant = permission.get("grant") or {}
        conclusion = {
            "id": next_id,
            "conclusion_id": f"CONC-{next_id:04d}",
            "结论编号": f"CONC-{next_id:04d}",
            "status": status,
            "effective": effective,
            "pending": False,
            "abnormal": bool(conflict_note),
            "复核编号": str(review.get("复核编号", "")),
            "派生表": table,
            "目标字段": field,
            "提交域": review_domain,
            "提交人": str(review.get("提交人", "")),
            "确认人": review["确认人"],
            "所用字段": list(review.get("所用字段", [field])),
            "轮次": str(review.get("轮次", "")),
            "权限类型": str(permission["kind"]),
            "授权依据": str(grant.get("grant_id") or permission["basis"]),
            "依据说明": str(permission.get("basis_detail", "")),
            "冲突": bool(conflict_note),
            "冲突说明": conflict_note,
            "确认时间": review["确认时间"],
        }
        self._conclusions().append(conclusion)
        review["结论编号"] = conclusion["conclusion_id"]
        self._write_conclusion(conclusion)

        if effective:
            message = (
                f"{review_domain}结论 {conclusion['conclusion_id']} 生效："
                f"{table}.{field}，所用字段={'、'.join(conclusion['所用字段'])}，"
                f"提交人={conclusion['提交人']}"
            )
        else:
            message = f"{review_domain}结论 {conclusion['conclusion_id']} 仅作参考：{conflict_note}"
        return conclusion, message

    # ---------- 共享授权撤销 ----------
    def revoke_share(self, grant_id: str, *, revoked_by: str = "数据治理委员会",
                     reason: str = "") -> tuple[dict[str, Any] | None, str]:
        """撤掉共享授权：立即失效待确认候选，已确认报告保留为历史记录。"""
        grant = next((row for row in self._grants()
                      if str(row.get("grant_id", "")) == str(grant_id)), None)
        if grant is None:
            return None, f"共享授权 {grant_id} 不存在"
        if grant.get("status") == "已撤销":
            return grant, f"共享授权 {grant_id} 已经撤销"

        grant["status"] = "已撤销"
        grant["revoked_at"] = _now()
        grant["revoked_by"] = revoked_by
        grant["revoke_reason"] = reason or "共享依据撤回"

        invalidated = 0
        for review in self._reviews():
            if (review.get("派生表") == grant.get("table")
                    and review.get("目标字段") == grant.get("field")
                    and review.get("提交域") == grant.get("to_domain")
                    and review.get("status") == "待确认"):
                review["status"] = "已失效"
                review["pending"] = False
                review["abnormal"] = True
                review["失效原因"] = f"共享授权 {grant_id} 已撤销"
                review["失效时间"] = _now()
                invalidated += 1

        self._write_grant(grant)
        return grant, (
            f"共享授权 {grant_id} 已撤销，{invalidated} 个待确认候选失效；"
            f"{grant.get('to_domain', '')} 本轮不再能提交「{grant.get('field', '')}」"
        )

    # ---------- 报告与追溯 ----------
    def trace_field(self, attribution_id: int | None = None, *,
                    table: str | None = None, field: str | None = None,
                    round_id: str | None = None) -> dict[str, Any]:
        """追查某字段实际生效的确认，以及两个域各自提交/确认的痕迹。"""
        attr = self._resolve_attr(attribution_id, table, field)
        if attr is None:
            return {"found": False, "message": "派生归属清单记录不存在"}
        table = str(attr.get("派生表", ""))
        field = str(attr.get("目标字段", ""))
        reviews = [
            review for review in self._reviews()
            if review.get("派生表") == table and review.get("目标字段") == field
            and (not round_id or review.get("轮次") == round_id)
        ]
        conclusions = [
            conclusion for conclusion in self._conclusions()
            if conclusion.get("派生表") == table and conclusion.get("目标字段") == field
            and (not round_id or conclusion.get("轮次") == round_id)
        ]
        effective = next(
            (row for row in conclusions if row.get("effective")),
            self._effective_conclusion(table, field),
        )
        return {
            "found": True,
            "table": table,
            "field": field,
            "attribution": {
                "清单编号": str(attr.get("清单编号", "")),
                "归属域": str(attr.get("归属域", "")),
                "归属类型": str(attr.get("归属类型", "")),
                "共享域": str(attr.get("共享域", "")),
                "授权依据": str(attr.get("授权依据", "")),
            },
            "scope": {
                domain: self.build_scope(domain, table)["fields"]
                for domain in sorted({
                    str(attr.get("归属域", "")), str(attr.get("共享域", "")),
                } - {""})
            },
            "effective": effective,
            "conclusions": conclusions,
            "reviews": reviews,
            "conflict": any(row.get("冲突") for row in conclusions),
            "covered_once": effective is not None,
        }

    def build_report(self, table: str | None = None,
                     round_id: str | None = None) -> dict[str, Any]:
        """按派生表+字段去重统计有效覆盖，跨域共同负责不重复计入分母。"""
        attrs = [
            row for row in self._attrs()
            if row.get("status") == "已生效" and (not table or row.get("派生表") == table)
        ]
        pairs = {(str(row.get("派生表", "")), str(row.get("目标字段", ""))) for row in attrs}
        tables = sorted({pair[0] for pair in pairs})
        domains = sorted({
            domain for row in attrs
            for domain in (str(row.get("归属域", "")), str(row.get("共享域", "")))
            if domain
        })

        effective_conclusions = [
            row for row in self._conclusions()
            if row.get("effective") and row.get("status") == "生效"
            and (not table or row.get("派生表") == table)
            and (not round_id or row.get("轮次") == round_id)
        ]
        # 只把“确认时授权仍成立”的结论计入有效覆盖；授权失效的保留在报告里另行标注。
        authoritative = [
            row for row in effective_conclusions
            if self.permission(str(row.get("提交域", "")),
                               str(row.get("派生表", "")),
                               str(row.get("目标字段", ""))) is not None
        ]
        covered = {
            (str(row.get("派生表", "")), str(row.get("目标字段", "")))
            for row in authoritative
        } & pairs
        lapsed = {
            (str(row.get("派生表", "")), str(row.get("目标字段", "")))
            for row in effective_conclusions
        } - covered

        confirmed_reviews = [
            row for row in self._reviews()
            if row.get("status") == "已确认"
            and (not table or row.get("派生表") == table)
            and (not round_id or row.get("轮次") == round_id)
        ]
        confirmed_by_field: dict[tuple[str, str], set[str]] = {}
        for review in confirmed_reviews:
            key = (str(review.get("派生表", "")), str(review.get("目标字段", "")))
            confirmed_by_field.setdefault(key, set()).add(str(review.get("提交域", "")))

        current_invalid = [
            {
                "结论编号": str(row.get("结论编号", "")),
                "派生表": str(row.get("派生表", "")),
                "目标字段": str(row.get("目标字段", "")),
                "提交域": str(row.get("提交域", "")),
                "提交人": str(row.get("提交人", "")),
            }
            for row in effective_conclusions
            if (str(row.get("派生表", "")), str(row.get("目标字段", ""))) in lapsed
        ]

        field_details = []
        for table_name, field_name in sorted(pairs):
            domain_reviews = sorted({
                str(row.get("提交域", "")) for row in confirmed_reviews
                if row.get("派生表") == table_name and row.get("目标字段") == field_name
            })
            effective_row = next(
                (row for row in authoritative
                 if row.get("派生表") == table_name and row.get("目标字段") == field_name),
                None,
            )
            field_details.append({
                "派生表": table_name,
                "目标字段": field_name,
                "covered": (table_name, field_name) in covered,
                "authorization_lapsed": (table_name, field_name) in lapsed,
                "confirmed_domains": domain_reviews,
                "confirmed_domain_count": len(domain_reviews),
                "cross_domain_confirmed": len(domain_reviews) > 1,
                # 一个字段无论几个域复核，进入分母和覆盖数都只算 1。
                "coverage_counted": 1 if (table_name, field_name) in covered else 0,
                "effective_conclusion": str(effective_row.get("结论编号", "")) if effective_row else "",
            })

        domain_scope_rows = sum(len(self.submittable_fields(domain, table)) for domain in domains)
        denominator = len(pairs)
        covered_count = len(covered)
        return {
            "table": table or (tables[0] if len(tables) == 1 else ""),
            "tables": tables,
            "table_count": len(tables),
            "attribution_rows": len(attrs),
            "denominator_fields": denominator,
            "covered_fields": covered_count,
            "coverage_rate": round(covered_count / denominator, 4) if denominator else 0.0,
            "domain_scope_rows": domain_scope_rows,
            "deduplicated": domain_scope_rows > denominator,
            "duplicate_field_reviews": sorted(
                f"{table_name}.{field_name}"
                for (table_name, field_name), review_domains in confirmed_by_field.items()
                if len(review_domains) > 1
            ),
            "domains": domains,
            "domain_scopes": {
                domain: [entry["目标字段"] for entry in self.submittable_fields(domain, table)]
                for domain in domains
            },
            "field_details": field_details,
            "effective_conclusions": effective_conclusions,
            "current_authorization_invalid": current_invalid,
            "status": "已确认" if denominator and covered_count == denominator else "待补充",
            "round": round_id or "",
            "generated_at": _now(),
            "note": "分母按派生表+字段去重；两个域复核同一字段只计一次有效覆盖",
        }

    def global_coverage(self) -> dict[str, Any]:
        """全局覆盖：同一派生表跨域共同负责也只计一张表、一份字段分母。"""
        report = self.build_report()
        return {
            "tables": report["tables"],
            "table_count": report["table_count"],
            "denominator_fields": report["denominator_fields"],
            "covered_fields": report["covered_fields"],
            "coverage_rate": report["coverage_rate"],
            "attribution_rows": report["attribution_rows"],
            "domain_scope_rows": report["domain_scope_rows"],
            "duplicate_field_reviews": report["duplicate_field_reviews"],
            "domains": report["domains"],
            "by_table": {
                table: {
                    key: value for key, value in self.build_report(table=table).items()
                    if key in ("denominator_fields", "covered_fields", "coverage_rate",
                               "domain_scope_rows", "status")
                }
                for table in report["tables"]
            },
            "note": "跨域共同负责的派生表只以其实际字段数进入全局分母",
        }

    def revalidate_confirmed(self, table: str | None = None) -> dict[str, Any]:
        """复查已确认报告与当前可提交范围；历史结论不回滚，只标注授权状态。"""
        conclusions = [
            row for row in self._conclusions()
            if row.get("status") in ("生效", "参考")
            and (not table or row.get("派生表") == table)
        ]
        still_valid = []
        lapsed = []
        for conclusion in conclusions:
            permission = self.permission(
                str(conclusion.get("提交域", "")),
                str(conclusion.get("派生表", "")),
                str(conclusion.get("目标字段", "")),
            )
            item = {
                "结论编号": str(conclusion.get("结论编号", "")),
                "派生表": str(conclusion.get("派生表", "")),
                "目标字段": str(conclusion.get("目标字段", "")),
                "提交域": str(conclusion.get("提交域", "")),
                "提交人": str(conclusion.get("提交人", "")),
                "结论状态": str(conclusion.get("status", "")),
                "授权仍有效": permission is not None,
            }
            (still_valid if permission is not None else lapsed).append(item)

        domains = sorted({
            str(row.get(field, ""))
            for row in self._attrs()
            if (not table or row.get("派生表") == table)
            for field in ("归属域", "共享域")
            if row.get(field)
        })
        return {
            "table": table or "",
            "confirmed_total": len(conclusions),
            "still_authorized": still_valid,
            "authorization_lapsed": lapsed,
            "current_scopes": {
                domain: self.build_scope(domain, table)["fields"] for domain in domains
            },
            "report_status": "已确认结论保留为历史记录，不因授权撤销回滚",
            "note": "本轮可提交范围按最新共享授权重新计算",
            "checked_at": _now(),
        }

    def recheck(self, table: str | None = None,
                round_id: str | None = None) -> dict[str, Any]:
        """撤权后的一站式复查：已确认报告 + 结论有效性 + 本轮可提交范围。"""
        return {
            "report": self.build_report(table=table, round_id=round_id),
            "revalidation": self.revalidate_confirmed(table=table),
            "global": self.global_coverage(),
            "round": round_id or "",
        }

    # ---------- 清单生效 ----------
    def activate_attribution(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        attr = store.find(ATTR_TABLE, entry_id)
        if attr is None:
            return None, f"{self.ENTITY} {entry_id} 不存在"
        lineage = self.lineage_basis(attr)
        if lineage is None:
            attr["status"] = "待核对"
            attr["pending"] = True
            return None, (
                f"归属清单 {attr.get('清单编号', '')} 缺少{attr.get('来源资产', '')}"
                f"→{attr.get('派生表', '')}的血缘依据"
            )
        if str(attr.get("归属类型", "")) == "共享":
            grant = self._grant_for_attr(attr)
            if grant is None:
                attr["status"] = "待核对"
                attr["pending"] = True
                return None, (
                    f"共享字段「{attr.get('目标字段', '')}」缺少生效的共享授权或授权依据，"
                    f"不能登记为可复核范围"
                )
        attr["status"] = "已生效"
        attr["pending"] = False
        attr["abnormal"] = False
        return attr, f"{self.ENTITY} {attr.get('清单编号', '')} 已生效"

    # ---------- 通用动作 ----------
    def run_action(self, entry_id: int, action: str,
                   payload: Any = None) -> tuple[dict[str, Any] | None, str]:
        attr = store.find(ATTR_TABLE, entry_id)
        if attr is None:
            return None, f"{self.ENTITY} {entry_id} 不存在"
        context = payload if isinstance(payload, dict) else {}
        if action == "核对生效":
            return self.activate_attribution(entry_id)
        if action == "提交复核":
            return self.submit_review(
                entry_id,
                domain=str(context.get("domain") or attr.get("归属域", "")),
                submitter=str(context.get("submitter") or "当前值班"),
                fields=context.get("fields"),
                round_id=context.get("round_id"),
                remark=str(context.get("remark") or ""),
            )
        if action == "确认结论":
            review = self._latest_pending_review(
                entry_id, None, None, None, context.get("round_id")
            )
            if review is None:
                return None, f"{attr.get('目标字段', '')} 本轮没有待确认候选"
            return self.confirm_conclusion(
                review.get("review_id", ""),
                confirmer=str(context.get("confirmer") or ""),
            )
        if action == "撤销共享":
            grant = self._grant_for_attr(attr)
            if grant is None:
                return None, f"{attr.get('目标字段', '')} 当前没有可撤销的共享授权"
            return self.revoke_share(
                str(context.get("grant_id") or grant.get("grant_id", "")),
                revoked_by=str(context.get("submitter") or "数据治理委员会"),
                reason=str(context.get("remark") or ""),
            )
        return super().run_action(entry_id, action)

    # ---------- 文件 I/O ----------
    def _write_conclusion(self, conclusion: dict[str, Any]) -> None:
        path = os.path.join(DATA_DIR, f"conclusion_{conclusion.get('conclusion_id', '')}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(conclusion, f, ensure_ascii=False, indent=2, default=str)

    def _write_grant(self, grant: dict[str, Any]) -> None:
        path = os.path.join(DATA_DIR, f"share_grant_{grant.get('grant_id', '')}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(grant, f, ensure_ascii=False, indent=2, default=str)

    def write_report(self, table: str | None = None,
                     round_id: str | None = None) -> dict[str, Any]:
        report = self.build_report(table=table, round_id=round_id)
        name = table or "all"
        path = os.path.join(DATA_DIR, f"attribution_report_{name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2, default=str)
        return report
