"""派生归属复核专项测试：字段范围、共享依据、冲突结论与撤权复查。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from app.seed import SEED_ROWS
from app.seed_attribution import ATTRIBUTION_SEED_ROWS, LINEAGE_ROWS
from app.services import service_for
from app.store import store

RED = "\033[91m"
GREEN = "\033[92m"
RESET = "\033[0m"

TABLE = "DWD_TRADE_SETTLE"
TRADE = "交易域"
SETTLE = "结算域"
ROUND = "2026-11"

passed = 0
failed = 0


def check(desc, condition, detail=""):
    global passed, failed
    if condition:
        print(f"  {GREEN}PASS{RESET} {desc}")
        passed += 1
        return True
    print(f"  {RED}FAIL{RESET} {desc} {detail}")
    failed += 1
    return False


def reset_store():
    """恢复种子数据，避免与其他集成测试共享内存时互相影响。"""
    store._tables = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}
    for name, rows in ATTRIBUTION_SEED_ROWS.items():
        store._tables[name] = [dict(row) for row in rows]
    store._tables["lineage_graph"].extend(dict(row) for row in LINEAGE_ROWS)


def test_unique_fields_are_owner_only():
    print("\n=== 独有字段：其他域无权代审 ===")
    svc = service_for("derived_attribution")

    review, message = svc.submit_review(
        None, domain=SETTLE, submitter="结算-张三",
        table=TABLE, field="交易金额", round_id=ROUND,
    )
    check("结算域提交交易金额被拒", review is None, message)
    check("拒绝原因说明无权代审", "无权代审" in message, message)

    owner, message = svc.submit_review(
        None, domain=TRADE, submitter="交易-李四",
        table=TABLE, field="交易金额", round_id=ROUND,
    )
    check("交易域可提交自己的独有字段", owner is not None, message)
    if owner:
        check("候选记录所用字段", owner["所用字段"] == ["交易金额"], str(owner["所用字段"]))
        check("候选记录提交人", owner["提交人"] == "交易-李四")
        check("独有字段权限类型为归属", owner["权限类型"] == "归属")

    settled, message = svc.submit_review(
        None, domain=SETTLE, submitter="结算-张三",
        table=TABLE, field="结算码", round_id=ROUND,
    )
    check("结算域可提交结算码", settled is not None, message)
    denied, message = svc.submit_review(
        None, domain=TRADE, submitter="交易-李四",
        table=TABLE, field="结算码", round_id=ROUND,
    )
    check("交易域不能代审结算码", denied is None, message)

    scope_trade = svc.build_scope(TRADE, TABLE)
    scope_settle = svc.build_scope(SETTLE, TABLE)
    check("交易域范围不含结算码", "结算码" not in scope_trade["fields"])
    check("结算域范围不含交易金额", "交易金额" not in scope_settle["fields"])
    return owner, settled


def test_shared_field_conflict(owner_review):
    print("\n=== 共享字段：两域同轮提交与结论生效顺序 ===")
    svc = service_for("derived_attribution")

    # 两个域在同一轮各自提交：先标记冲突，确认顺序决定哪份结论生效。
    shared, message = svc.submit_review(
        None, domain=SETTLE, submitter="结算-张三",
        table=TABLE, field="对账金额", round_id=ROUND,
    )
    check("结算域凭 SHR-0001 提交对账金额", shared is not None, message)
    if shared:
        check("共享候选带授权编号", shared["授权依据"] == "SHR-0001", shared["授权依据"])
        check("共享候选权限类型为共享", shared["权限类型"] == "共享")

    owner_side, message = svc.submit_review(
        None, domain=TRADE, submitter="交易-李四",
        table=TABLE, field="对账金额", round_id=ROUND,
    )
    check("交易域对同一字段也提交", owner_side is not None, message)
    if owner_side:
        check("冲突被标记", owner_side["冲突标记"] is True)
        check("冲突域记录结算域", "结算域" in owner_side["冲突域"])

    if not (shared and owner_side):
        return None, None

    settle_conclusion, message = svc.confirm_conclusion(shared["复核编号"])
    check("结算域结论先生效", settle_conclusion is not None, message)
    if settle_conclusion:
        check("第一份结论为生效", settle_conclusion["effective"] is True, settle_conclusion["status"])
        check("结论写回所用字段", settle_conclusion["所用字段"] == ["对账金额"])
        check("结论写回提交人", settle_conclusion["提交人"] == "结算-张三")
        check("结论写回确认人", settle_conclusion["确认人"] != "", settle_conclusion["确认人"])

    owner_conclusion, message = svc.confirm_conclusion(owner_side["复核编号"])
    check("归属域确认成功", owner_conclusion is not None, message)
    if owner_conclusion:
        check("归属域结论取代共享域结论", owner_conclusion["effective"] is True)
        check("冲突说明指明归属域优先", "归属域" in owner_conclusion["冲突说明"],
              owner_conclusion["冲突说明"])

    trace = svc.trace_field(None, table=TABLE, field="对账金额", round_id=ROUND)
    check("可追查实际生效确认", trace["found"] and trace["effective"] is not None)
    check("生效确认属于归属域", trace["effective"] and trace["effective"]["提交域"] == TRADE)
    check("两个域的提交都留痕", len(trace["reviews"]) == 2, str(len(trace["reviews"])))
    check("同字段只计一次覆盖", trace["covered_once"] is True)

    duplicate, message = svc.submit_review(
        None, domain=SETTLE, submitter="结算-张三",
        table=TABLE, field="对账金额", round_id=ROUND,
    )
    check("已生效后再提交被拒", duplicate is None, message)
    return shared, owner_side


def test_report_dedup(settle_review, trade_review):
    print("\n=== 覆盖报告：派生表与字段去重 ===")
    svc = service_for("derived_attribution")
    svc.confirm_conclusion(settle_review["复核编号"])
    svc.confirm_conclusion(trade_review["复核编号"])

    report = svc.build_report(TABLE, ROUND)
    check("分母按派生表+字段计为 4", report["denominator_fields"] == 4,
          str(report["denominator_fields"]))
    check("两域范围行数多于分母", report["domain_scope_rows"] > report["denominator_fields"],
          f"{report['domain_scope_rows']} vs {report['denominator_fields']}")
    check("去重标记为真", report["deduplicated"] is True)
    check("重复复核字段被识别", "DWD_TRADE_SETTLE.对账金额" in report["duplicate_field_reviews"],
          str(report["duplicate_field_reviews"]))
    check("对账金额只形成一份生效结论",
          sum(1 for row in report["effective_conclusions"] if row["目标字段"] == "对账金额") == 1,
          str(len(report["effective_conclusions"])))
    shared_detail = next(
        row for row in report["field_details"] if row["目标字段"] == "对账金额"
    )
    check("同字段两域确认但覆盖只计 1", shared_detail["coverage_counted"] == 1,
          str(shared_detail))
    check("同字段两域确认被标记为跨域", shared_detail["cross_domain_confirmed"] is True,
          str(shared_detail))

    global_view = svc.global_coverage()
    check("全局只包含一张派生表", global_view["table_count"] == 1)
    check("全局分母仍为 4", global_view["denominator_fields"] == 4)
    check("全局按表拆分", TABLE in global_view["by_table"])


def test_revoke_and_retry():
    print("\n=== 撤权：旧候选重试与范围复查 ===")
    svc = service_for("derived_attribution")

    # 撤权前先留一个待确认候选，验证撤销会把它打回。
    pending, message = svc.submit_review(
        None, domain=SETTLE, submitter="结算-张三",
        table=TABLE, field="对账金额", round_id="2026-12",
    )
    check("撤权前结算域可提交对账金额", pending is not None, message)

    grant, message = svc.revoke_share("SHR-0001", revoked_by="治理-王五", reason="协议到期")
    check("共享授权撤销成功", grant is not None and grant["status"] == "已撤销", message)
    check("撤销后提示候选失效", "失效" in message, message)

    if pending:
        check("待确认候选转为失效", pending["status"] == "已失效", pending["status"])

    retried, message = svc.submit_review(
        None, domain=SETTLE, submitter="结算-张三",
        table=TABLE, field="对账金额", round_id="2026-12",
    )
    check("撤权后重试旧字段被拒", retried is None, message)
    check("拒绝原因指向授权失效", "授权" in message and "失效" in message, message)

    if pending:
        confirmed, message = svc.confirm_conclusion(pending["复核编号"])
        check("已失效候选不能确认", confirmed is None, message)

    scope = svc.build_scope(SETTLE, TABLE)
    check("结算域范围移除对账金额", "对账金额" not in scope["fields"], str(scope["fields"]))
    check("结算域仍保留自己的结算码", "结算码" in scope["fields"])
    check("交易域归属字段不受影响",
          "对账金额" in svc.build_scope(TRADE, TABLE)["fields"])

    # 已确认报告保留，但标注授权失效。
    report_after = svc.build_report(TABLE, ROUND)
    still_covered = [
        row for row in report_after["effective_conclusions"]
        if row["目标字段"] == "对账金额"
    ]
    check("撤权不回滚已生效的归属域结论",
          any(row["提交域"] == TRADE for row in still_covered), str(still_covered))

    recheck = svc.recheck(TABLE, ROUND)
    lapsed = recheck["revalidation"]["authorization_lapsed"]
    check("复查列出失效授权结论",
          any(item["提交域"] == SETTLE and item["目标字段"] == "对账金额" for item in lapsed),
          str(lapsed))
    check("复查保留报告状态", recheck["revalidation"]["report_status"] == "已确认结论保留为历史记录，不因授权撤销回滚",
          recheck["revalidation"]["report_status"])
    check("复查重算本轮可提交范围",
          "对账金额" not in recheck["revalidation"]["current_scopes"][SETTLE],
          str(recheck["revalidation"]["current_scopes"]))


def test_scope_basis_required():
    print("\n=== 共享范围必须有依据 ===")
    svc = service_for("derived_attribution")
    attr = svc.find_attribution(table=TABLE, field="对账金额")
    check("共享字段清单存在", attr is not None)

    # 审批依据被清空后，共享授权不应再成立。
    grant = svc.active_grant(TABLE, "对账金额", TRADE, SETTLE)
    check("撤权前共享授权有效", grant is not None)
    original = grant["basis"] if grant else ""
    if grant:
        grant["basis"] = ""
        check("无依据的共享授权不被认可",
              svc.permission(SETTLE, TABLE, "对账金额") is None)
        grant["basis"] = original


def test_compliance_consumes_scope():
    print("\n=== 合规检查读取派生归属清单 ===")
    compliance = service_for("compliance_check")
    settle_scope = compliance.field_scope(SETTLE, TABLE)
    trade_scope = compliance.field_scope(TRADE, TABLE)
    check("合规侧可读结算域范围", settle_scope["domain"] == SETTLE)
    check("合规侧范围与归属清单一致",
          settle_scope["fields"] == ["结算码", "对账金额", "对账状态"], str(settle_scope["fields"]))
    check("合规侧范围不含独有字段", "交易金额" not in settle_scope["fields"])

    report = compliance.attribution_report(TABLE, ROUND)
    check("合规报告分母按字段去重", report["denominator_fields"] == 4)
    check("合规报告识别重复复核字段",
          "DWD_TRADE_SETTLE.对账金额" in report["duplicate_field_reviews"])

    entry, message = compliance.run_check(3)
    check("合规扫描读取归属清单后执行成功", entry is not None, message)


def test_lapsed_coverage_excluded():
    print("\n=== 撤权后覆盖不再计入，但结论留痕 ===")
    reset_store()
    svc = service_for("derived_attribution")

    review, message = svc.submit_review(
        None, domain=SETTLE, submitter="结算-张三",
        table=TABLE, field="对账金额", round_id=ROUND,
    )
    check("结算域提交共享字段", review is not None, message)
    if review is None:
        return
    conclusion, message = svc.confirm_conclusion(review["复核编号"])
    check("共享域结论生效", conclusion is not None and conclusion["effective"], message)

    before = svc.build_report(TABLE, ROUND)
    check("撤权前覆盖数为 1", before["covered_fields"] == 1, str(before["covered_fields"]))

    svc.revoke_share("SHR-0001", reason="协议到期")
    after = svc.build_report(TABLE, ROUND)
    check("撤权后覆盖数归零", after["covered_fields"] == 0, str(after["covered_fields"]))
    check("分母不因撤权改变", after["denominator_fields"] == 4)
    detail = next(row for row in after["field_details"] if row["目标字段"] == "对账金额")
    check("字段标记授权失效", detail["authorization_lapsed"] is True, str(detail))
    check("失效结论仍保留在报告里",
          any(row["结论编号"] == conclusion["结论编号"] for row in after["effective_conclusions"]),
          str(after["effective_conclusions"]))
    check("撤销列表列出该结论",
          any(item["结论编号"] == conclusion["结论编号"]
              for item in after["current_authorization_invalid"]),
          str(after["current_authorization_invalid"]))


if __name__ == "__main__":
    print("=" * 60)
    print("派生归属复核专项测试")
    print("=" * 60)

    reset_store()
    test_unique_fields_are_owner_only()
    shared_review, owner_review = test_shared_field_conflict(None)
    if shared_review and owner_review:
        test_report_dedup(shared_review, owner_review)
    test_scope_basis_required()
    test_compliance_consumes_scope()
    test_revoke_and_retry()
    test_lapsed_coverage_excluded()

    print(f"\n{'=' * 60}")
    print(f"结果: {GREEN}{passed} 通过{RESET} / {RED}{failed} 失败{RESET} / "
          f"{passed + failed} 总计")
    print("=" * 60)
    if failed > 0:
        sys.exit(1)
