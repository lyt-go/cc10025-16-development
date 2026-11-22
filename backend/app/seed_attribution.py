"""跨域派生表的字段归属清单示例数据。

派生表 DWD_TRADE_SETTLE 同时依赖交易域 ODS_TRADE 和结算域 ODS_SETTLE：
    - 交易金额：交易域独有，结算域不能代审
    - 结算码：结算域独有，交易域不能代审
    - 对账金额：交易域归属，依据 SHR-0001 共享给结算域
    - 对账状态：结算域归属，依据 SHR-0002 共享给交易域

一条清单记录表示“某派生表的一个目标字段由哪个域负责”。共享字段保留
归属域，另外用授权依据指向 share_grant；没有生效授权的共享范围不成立。
"""
from __future__ import annotations

from typing import Any

DERIVED_TABLE = "DWD_TRADE_SETTLE"
TRADE_DOMAIN = "交易域"
SETTLE_DOMAIN = "结算域"
SOURCE_TRADE = "ODS_TRADE"
SOURCE_SETTLE = "ODS_SETTLE"

# 血缘记录：派生表的来源和去向，归属清单的授权依据引用这两条记录。
LINEAGE_ROWS: list[dict[str, Any]] = [
    {
        "id": 4,
        "status": "已生效",
        "pending": False,
        "abnormal": False,
        "记录编号": "LINE-0004",
        "上游资产": SOURCE_TRADE,
        "下游资产": DERIVED_TABLE,
        "依赖类型": "字段派生",
        "加工逻辑": "交易明细按交易号聚合出交易金额与对账金额",
        "生效时间": "2026-09-10",
        "变更SHA": "line-ods-trade-dwd",
        "血缘状态": "已生效",
    },
    {
        "id": 5,
        "status": "已生效",
        "pending": False,
        "abnormal": False,
        "记录编号": "LINE-0005",
        "上游资产": SOURCE_SETTLE,
        "下游资产": DERIVED_TABLE,
        "依赖类型": "字段派生",
        "加工逻辑": "结算流水按交易号补齐结算码与对账状态",
        "生效时间": "2026-09-10",
        "变更SHA": "line-ods-settle-dwd",
        "血缘状态": "已生效",
    },
]

# 共享授权本身也是可撤销的：归属域把字段复核范围授权给另一个域。
SHARE_GRANT_ROWS: list[dict[str, Any]] = [
    {
        "id": 1,
        "grant_id": "SHR-0001",
        "table": DERIVED_TABLE,
        "field": "对账金额",
        "from_domain": TRADE_DOMAIN,
        "to_domain": SETTLE_DOMAIN,
        "status": "已生效",
        "basis": "跨域对账共享协议 RECON-2026-01",
        "granted_by": "数据治理委员会",
        "granted_at": "2026-10-01",
        "revoked_at": "",
        "revoked_by": "",
        "revoke_reason": "",
    },
    {
        "id": 2,
        "grant_id": "SHR-0002",
        "table": DERIVED_TABLE,
        "field": "对账状态",
        "from_domain": SETTLE_DOMAIN,
        "to_domain": TRADE_DOMAIN,
        "status": "已生效",
        "basis": "跨域对账共享协议 RECON-2026-02",
        "granted_by": "数据治理委员会",
        "granted_at": "2026-10-01",
        "revoked_at": "",
        "revoked_by": "",
        "revoke_reason": "",
    },
]

ATTRIBUTION_ROWS: list[dict[str, Any]] = [
    {
        "id": 1,
        "status": "已生效",
        "pending": False,
        "abnormal": False,
        "清单编号": "ATTR-0001",
        "派生表": DERIVED_TABLE,
        "来源资产": SOURCE_TRADE,
        "来源字段": "TRADE_AMT",
        "目标字段": "交易金额",
        "归属域": TRADE_DOMAIN,
        "归属类型": "独有",
        "共享域": "",
        "授权依据": "LINE-0004",
        "字段说明": "交易金额仅由交易域负责，其他域不得代审",
    },
    {
        "id": 2,
        "status": "已生效",
        "pending": False,
        "abnormal": False,
        "清单编号": "ATTR-0002",
        "派生表": DERIVED_TABLE,
        "来源资产": SOURCE_SETTLE,
        "来源字段": "SETTLE_CODE",
        "目标字段": "结算码",
        "归属域": SETTLE_DOMAIN,
        "归属类型": "独有",
        "共享域": "",
        "授权依据": "LINE-0005",
        "字段说明": "结算码仅由结算域负责，其他域不得代审",
    },
    {
        "id": 3,
        "status": "已生效",
        "pending": False,
        "abnormal": False,
        "清单编号": "ATTR-0003",
        "派生表": DERIVED_TABLE,
        "来源资产": SOURCE_TRADE,
        "来源字段": "RECON_AMT",
        "目标字段": "对账金额",
        "归属域": TRADE_DOMAIN,
        "归属类型": "共享",
        "共享域": SETTLE_DOMAIN,
        "授权依据": "SHR-0001",
        "字段说明": "交易域归属，依据 SHR-0001 共享给结算域复核",
    },
    {
        "id": 4,
        "status": "已生效",
        "pending": False,
        "abnormal": False,
        "清单编号": "ATTR-0004",
        "派生表": DERIVED_TABLE,
        "来源资产": SOURCE_SETTLE,
        "来源字段": "RECON_STATUS",
        "目标字段": "对账状态",
        "归属域": SETTLE_DOMAIN,
        "归属类型": "共享",
        "共享域": TRADE_DOMAIN,
        "授权依据": "SHR-0002",
        "字段说明": "结算域归属，依据 SHR-0002 共享给交易域复核",
    },
]

ATTRIBUTION_SEED_ROWS: dict[str, list[dict[str, Any]]] = {
    "derived_attribution": [dict(row) for row in ATTRIBUTION_ROWS],
    "_derived_share_grant": [dict(row) for row in SHARE_GRANT_ROWS],
    "_derived_attribution_review": [],
    "_derived_attribution_conclusion": [],
}
