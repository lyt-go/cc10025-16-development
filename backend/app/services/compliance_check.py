"""compliance_check 领域服务：合规检查管理，检查数据资产合规性并生成审计日志和质量报告。

跨模块数据流：
    asset_catalog → compliance_check: 资产变更触发合规扫描
    derived_attribution → compliance_check: 各域可提交字段范围与复核结论
    compliance_check → audit_log: 检查结果写入审计日志
    compliance_check → quality_report: 合规结果纳入质量报告
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "quality")


class ComplianceCheckService(BaseModuleService):
    MODULE = "compliance_check"
    ENTITY = "合规规则"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["规则编号", "检查维度", "合规标准", "扫描范围", "阻断阈值", "修复建议", "检查频率", "规则版本"]
    REQUIRED_FIELDS = ["规则编号", "检查维度", "合规标准"]
    STATUS_ORDER = ["草稿", "待校验", "已生效", "已停用"]
    ACTION_RULES = {"提交校验": "待校验", "标记生效": "已生效", "停用规则": "已停用"}
    NEGATIVE_ACTIONS = ["停用规则"]

    def __init__(self):
        super().__init__()
        os.makedirs(DATA_DIR, exist_ok=True)

    def run_check(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """执行合规检查：扫描关联资产并生成检查结果。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在"

        dimension = str(entry.get("检查维度", ""))
        standard = str(entry.get("合规标准", ""))
        scope = str(entry.get("扫描范围", ""))

        # 模拟合规扫描：每行只统计一次，避免跨域共享的派生表被重复计入。
        checked_count = 0
        violations = 0
        attributed_domains: set[str] = set()
        if scope:
            for attr in store.rows("derived_attribution"):
                if scope in str(attr.get("归属域", "")) or scope in str(attr.get("派生表", "")):
                    attributed_domains.add(str(attr.get("归属域", "")))
        for module_name in store.module_names():
            for row in store.rows(module_name):
                if scope and attributed_domains:
                    # 指定了扫描范围时，只统计落在该范围派生域内的记录。
                    domain = str(row.get("所属域", "")) or str(row.get("归属域", ""))
                    if domain and domain not in attributed_domains:
                        continue
                checked_count += 1
                if row.get("abnormal"):
                    violations += 1

        passed = violations == 0

        # 读取派生归属清单：字段级分母按派生表+字段去重，跨域共同负责不重复计入。
        attribution_report = self.attribution_report(round_id=None)

        # 写入合规检查结果
        result = {
            "rule_id": str(entry.get("规则编号", "")),
            "dimension": dimension,
            "standard": standard,
            "scope": scope,
            "checked_count": checked_count,
            "violations": violations,
            "passed": passed,
            "attribution_fields": attribution_report["denominator_fields"],
            "attribution_covered": attribution_report["covered_fields"],
            "attribution_rate": attribution_report["coverage_rate"],
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        self._write_check_result(str(entry.get("规则编号", "")), result)

        # 写入审计日志
        self._write_audit_entry(
            entity="合规检查",
            action="执行扫描",
            target_id=str(entry.get("规则编号", "")),
            detail=f"合规检查 {dimension}: {violations} / {checked_count} 违规",
        )

        # 更新质量报告
        self._update_quality_report(dimension, checked_count, violations)

        message = f"合规检查完成：{checked_count}项，{violations}违规{'，通过' if passed else '，不通过'}"
        return entry, message

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        if action == "标记生效":
            return self.run_check(entry_id)
        return super().run_action(entry_id, action)

    def field_scope(self, domain: str, table: str | None = None) -> dict[str, Any]:
        """读取派生归属清单，生成某域可提交的字段范围。"""
        return self._attribution().build_scope(domain, table)

    def attribution_report(self, table: str | None = None,
                           round_id: str | None = None) -> dict[str, Any]:
        """确认覆盖报告：同字段两域复核只计一次，派生表不重复进入全局分母。"""
        return self._attribution().build_report(table=table, round_id=round_id)

    def _attribution(self):
        from app.services import service_for
        service = service_for("derived_attribution")
        if service is None:  # pragma: no cover - 模块注册表固定包含该服务
            raise RuntimeError("派生归属服务未注册")
        return service

    def _write_check_result(self, rule_id: str, result: dict[str, Any]) -> None:
        filename = f"compliance_check_{rule_id}.json"
        path = os.path.join(DATA_DIR, filename)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)

    def _write_audit_entry(self, entity: str, action: str, target_id: str,
                           detail: str) -> None:
        audit_rows = store.rows("audit_log")
        next_id = max((int(r.get("id", 0)) for r in audit_rows), default=0) + 1
        entry = {
            "id": next_id,
            "status": "已记录",
            "pending": False,
            "abnormal": False,
            "记录编号": f"AUDI-{next_id:04d}",
            "操作主体": entity,
            "操作类型": action,
            "操作对象": target_id,
            "操作时间": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "影响范围": detail,
            "审计摘要": detail,
            "归档状态": "已记录",
        }
        audit_rows.append(entry)

    def _update_quality_report(self, dimension: str, total: int, violations: int) -> None:
        quality_rows = store.rows("quality_report")
        next_id = max((int(r.get("id", 0)) for r in quality_rows), default=0) + 1
        completeness = "高" if total > 0 and violations / total < 0.1 else "中" if violations > 0 else "高"
        report = {
            "id": next_id,
            "status": "已生成",
            "pending": False,
            "abnormal": violations > 0,
            "报告编号": f"QUAL-{next_id:04d}",
            "关联资产": "全量资产",
            "检核时间": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "完整性": completeness,
            "准确性": f"合规率:{1 - violations/max(total,1):.1%}",
            "及时性": "实时",
            "异常数": str(violations),
            "趋势方向": "恶化" if violations > 0 else "稳定",
        }
        quality_rows.append(report)