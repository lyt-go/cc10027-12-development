"""维表命中归属凭证验收脚本（可直接运行，也兼容 pytest）。

覆盖验收点：
1. 线上/离线各持私有部门维表，共享主档只读，跨源私有取数被拒绝；
2. 同一客户 CUST-1001 的两份日志各出一张行级凭证，每行只确认一个部门，
   且实际命中来自各自有权限的维表；
3. 两个合法维表为同一客户返回不同部门，部门立方体核对时该客户只入一个
   UNRESOLVED 组，不会被提前计入两个部门组；
4. 立方体每组保存维表来源与实际命中，可回查取数位置；
5. 用另一个源的同键凭证复查有效归属；
6. 维表更新后旧立方体保留原身份，重算产生新版本立方体，冲突解除。

运行：.venv/bin/python -I verify_dim_attestation.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.services.dimension_attestation import (  # noqa: E402
    MASTER_TABLE,
    OFFLINE_PRIVATE_TABLE,
    ONLINE_PRIVATE_TABLE,
    OFFLINE_SOURCE,
    ONLINE_SOURCE,
    UNRESOLVED_GROUP,
    DimensionAttestationService,
)


def test_attestation_workflow() -> None:
    svc = DimensionAttestationService()
    demo = svc.build_conflict_demo(reset=True)

    # -- 1. 维表与权限：三张表，私有/共享角色与只读读者正确 ----------------
    tables = demo["dim_tables"]
    assert set(tables) == {ONLINE_PRIVATE_TABLE, OFFLINE_PRIVATE_TABLE, MASTER_TABLE}
    assert tables[ONLINE_PRIVATE_TABLE]["scope"] == "private"
    assert tables[OFFLINE_PRIVATE_TABLE]["scope"] == "private"
    assert tables[MASTER_TABLE]["scope"] == "shared"
    assert set(tables[MASTER_TABLE]["readers"]) == {ONLINE_SOURCE, OFFLINE_SOURCE}

    # 线上凭证里离线私有维表必须出现在禁止清单，反之亦然（不能借用另一源私有记录）。
    online_cred = next(
        c for c in svc.list_credentials()
        if c["record_ref"]["source_key"] == ONLINE_SOURCE
        and c["customer_key"] == "CUST-1001"
    )
    forbidden_online = {f["table_name"] for f in online_cred["access_scope"]["forbidden_tables"]}
    assert OFFLINE_PRIVATE_TABLE in forbidden_online

    offline_cred = next(
        c for c in svc.list_credentials()
        if c["record_ref"]["source_key"] == OFFLINE_SOURCE
        and c["customer_key"] == "CUST-1001"
    )
    forbidden_offline = {f["table_name"] for f in offline_cred["access_scope"]["forbidden_tables"]}
    assert ONLINE_PRIVATE_TABLE in forbidden_offline

    # -- 2. 每行只确认一个可核对部门，且实际命中位置可说明 -----------------
    assert online_cred["status"] == "confirmed"
    assert offline_cred["status"] == "confirmed"
    assert online_cred["actual_hit"]["table_name"] == ONLINE_PRIVATE_TABLE
    assert online_cred["actual_hit"]["access_mode"] == "owner_private"
    assert online_cred["confirmed_department"] == "电商一部"
    assert offline_cred["actual_hit"]["table_name"] == OFFLINE_PRIVATE_TABLE
    assert offline_cred["confirmed_department"] == "渠道发展部"
    # 共享主档同键也命中时保留为备选，但实际命中仍是私有维表（私有优先）。
    candidates = {h["table_name"] for h in online_cred["candidate_hits"]}
    assert MASTER_TABLE in candidates

    # 共享主档兜底：线上 CUST-9001 私有维表无记录，只读共享命中成立。
    master_cred = next(
        c for c in svc.list_credentials()
        if c["record_ref"]["source_key"] == ONLINE_SOURCE
        and c["customer_key"] == "CUST-9001"
    )
    assert master_cred["actual_hit"]["table_name"] == MASTER_TABLE
    assert master_cred["actual_hit"]["access_mode"] == "shared_readonly"

    # -- 3. 冲突客户在立方体里只入一个组，绝不计入两个部门组 ----------------
    v1 = svc.get_cube(demo["cube_v1"]["cube_version"])
    groups_v1 = v1["groups"]
    assert UNRESOLVED_GROUP in groups_v1
    unresolved_members = {m["customer_key"] for m in groups_v1[UNRESOLVED_GROUP]["members"]}
    assert "CUST-1001" in unresolved_members

    # 冲突客户在整个立方体中只出现一次：汇总 UNRESOLVED 与各部门组。
    all_members: dict[str, int] = {}
    for group in groups_v1.values():
        for member in group["members"]:
            all_members[member["customer_key"]] = all_members.get(member["customer_key"], 0) + 1
    assert all_members["CUST-1001"] == 1, "冲突客户不能被提前计入两个组"
    assert v1["totals"]["counted"] == v1["totals"]["customer_keys"]

    # -- 4. 每组保存维表来源与实际命中，取数位置可回查 ---------------------
    decision = v1["customer_reconciliation"]["CUST-1001"]
    assert decision["conflict"] is True
    assert decision["provisional_owner"]["source_key"] == ONLINE_SOURCE
    evidence_sources = {e["dim_source"] for e in decision["row_evidence"]}
    assert evidence_sources == {ONLINE_PRIVATE_TABLE, OFFLINE_PRIVATE_TABLE}
    for evidence in decision["row_evidence"]:
        assert evidence["actual_hit"]["department"] == evidence["confirmed_department"]
    # 组级维表使用清单非空，标明版本与访问方式。
    assert groups_v1[UNRESOLVED_GROUP]["dim_sources_used"]

    # 普通部门组同样带来源与命中信息。
    assert "电商二部" in groups_v1
    assert groups_v1["电商二部"]["dim_sources_used"][0]["table_name"] == ONLINE_PRIVATE_TABLE

    # -- 5. 用另一个源同键复查有效归属 ------------------------------------
    # 维表更新前的同键复查（demo 已在更新前留存）：冲突在案且取数不越权。
    recheck = demo["cross_source_recheck"]
    assert recheck["conflict"] is True
    assert recheck["per_source"][ONLINE_SOURCE]["effective_department"] == "电商一部"
    assert recheck["per_source"][OFFLINE_SOURCE]["effective_department"] == "渠道发展部"
    # 复查时两边都拿不到对方的私有维表，只能用本源私有 + 只读共享。
    assert OFFLINE_PRIVATE_TABLE in {
        f["table_name"] for f in recheck["per_source"][ONLINE_SOURCE]["forbidden_tables"]
    }
    assert ONLINE_PRIVATE_TABLE in {
        f["table_name"] for f in recheck["per_source"][OFFLINE_SOURCE]["forbidden_tables"]
    }

    cred_recheck = demo["credential_recheck"]
    assert cred_recheck["drift"] is False  # 维表更新前复查，归属未漂移

    # -- 6. 维表更新：旧立方体身份保留，重算新版本，冲突解除 ---------------
    versions_before = {item["cube_version"] for item in svc.list_cube_versions()}
    assert demo["cube_v1"]["cube_version"] in versions_before

    v2 = svc.get_cube(demo["cube_v2"]["cube_version"])
    assert v2["cube_version"] != v1["cube_version"]
    # 旧版本文件与内部版本号保持不变。
    v1_reloaded = svc.get_cube(v1["cube_version"])
    assert v1_reloaded["resolution_no"] == v1["resolution_no"]
    assert UNRESOLVED_GROUP in v1_reloaded["groups"]

    # 重算后 CUST-1001 两源部门一致，落入唯一部门组且只计一次。
    assert UNRESOLVED_GROUP not in v2["groups"] or all(
        m["customer_key"] != "CUST-1001"
        for m in v2["groups"].get(UNRESOLVED_GROUP, {}).get("members", [])
    )
    assert "CUST-1001" in {m["customer_key"] for m in v2["groups"]["电商一部"]["members"]}
    assert v2["customer_reconciliation"]["CUST-1001"]["conflict"] is False
    # 新版本凭证引用了离线私有维表的第 2 版。
    new_offline_cred = next(
        c for c in reversed(svc.list_credentials())
        if c["record_ref"]["source_key"] == OFFLINE_SOURCE
        and c["customer_key"] == "CUST-1001"
    )
    assert new_offline_cred["actual_hit"]["table_version"] == 2
    # 旧凭证仍指向第 1 版，身份不变。
    old_offline = svc.get_credential(offline_cred["credential_id"])
    assert old_offline["actual_hit"]["table_version"] == 1
    # 维表更新后复查：跨源同键部门一致，冲突解除。
    assert demo["recheck_after_update"]["conflict"] is False
    assert svc.recheck_customer_attribution("CUST-1001")["conflict"] is False


def main() -> int:
    test_attestation_workflow()
    print("ALL DIMENSION ATTESTATION CHECKS PASSED")
    return 0


if __name__ == "__main__":
    try:
        import pytest
    except ImportError:
        sys.exit(main())
    else:
        sys.exit(pytest.main([__file__, "-v", "--tb=short", "-s"]))
