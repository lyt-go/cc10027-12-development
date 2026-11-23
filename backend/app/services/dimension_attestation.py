"""维表命中归属凭证：为日志记录中的客户键确认可核对的部门归属。

业务约束（对应富化/聚合取数依据可回查的要求）：

1. 同一客户的两份日志记录分别来自线上、离线两个日志源；每个源只能使用
   自己的私有部门维表，或以只读方式取共享主档，不能借用另一源的私有记录。
2. 每行日志只能确认一个可核对部门：私有维表优先，命中不到再取共享主档；
   所有候选命中（含只读共享命中）都写进凭证，实际命中只有一条。
3. 两个合法维表为同一客户返回不同部门时，行级凭证各自成立，客户级核对
   判定为部门冲突：该客户在部门立方体里只占一个位置（UNRESOLVED），
   不能提前计入两个部门组，另一个源的同键凭证作为复查依据保留。
4. 维表更新追加新版本，既有立方体与旧凭证保留原身份不变；重算产生新的
   立方体版本和新一轮凭证，未决归属在重算前不会被提前落组。
"""
from __future__ import annotations

import json
import time
from typing import Any

from app.services.base import BaseModuleService

# 线上 / 离线两个日志源，以及共享客户主档的固定标识。
ONLINE_SOURCE = "LOG-ONLINE-01"
OFFLINE_SOURCE = "LOG-OFFLINE-01"
KNOWN_SOURCES = [ONLINE_SOURCE, OFFLINE_SOURCE]

MASTER_TABLE = "dim_customer_master"
ONLINE_PRIVATE_TABLE = "dim_online_private"
OFFLINE_PRIVATE_TABLE = "dim_offline_private"

# 未决桶本身也是一个且仅一个组：冲突/缺失的客户键落在这里，绝不进两个部门组。
UNRESOLVED_GROUP = "UNRESOLVED"

# 源优先级仅用于给出确定性的“暂定归属”，不改变未决判定。
SOURCE_PRIORITY = {ONLINE_SOURCE: 0, OFFLINE_SOURCE: 1}


class DimensionAttestationService(BaseModuleService):
    """部门维表命中归属凭证与部门立方体核对服务。"""

    MODULE = "dimension_attestation"
    ENTITY = "维表命中归属凭证"

    def __init__(self) -> None:
        self._dir = self._data_dir("dim_attestation")
        self._registry_file = self._dir / "dim_tables.json"
        self._credential_file = self._dir / "attestations.jsonl"
        self._cube_dir = self._dir / "cubes"
        self._log_dir = self._dir / "logs"
        self._cube_dir.mkdir(parents=True, exist_ok=True)
        self._log_dir.mkdir(parents=True, exist_ok=True)

        self._registry: dict[str, Any] = self._load_registry()
        self._credentials: list[dict[str, Any]] = self._read_jsonl(self._credential_file)
        self._credential_seq = max(
            (int(c.get("credential_id", "CRED-00000").split("-")[-1]) for c in self._credentials),
            default=0,
        )
        self._resolution_seq = int(self._registry.get("resolution_seq", 0))

    def reset_workspace(self) -> dict[str, Any]:
        """清空归属凭证工作区（维表、凭证、立方体），用于可重复的核对演示。"""
        import shutil

        shutil.rmtree(self._dir, ignore_errors=True)
        self._cube_dir.mkdir(parents=True, exist_ok=True)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._registry = {"tables": {}, "resolution_seq": 0}
        self._credentials = []
        self._credential_seq = 0
        self._resolution_seq = 0
        self._save_registry()
        with open(self._credential_file, "w", encoding="utf-8"):
            pass
        return {"ok": True, "reset": str(self._dir)}

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def _load_registry(self) -> dict[str, Any]:
        if self._registry_file.exists():
            with open(self._registry_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"tables": {}, "resolution_seq": 0}

    def _save_registry(self) -> None:
        self._registry["resolution_seq"] = self._resolution_seq
        with open(self._registry_file, "w", encoding="utf-8") as f:
            json.dump(self._registry, f, ensure_ascii=False, indent=2, default=str)

    def _append_credential(self, credential: dict[str, Any]) -> None:
        self._credentials.append(credential)
        with open(self._credential_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(credential, ensure_ascii=False, default=str) + "\n")

    # ------------------------------------------------------------------
    # 部门维表注册与版本管理
    # ------------------------------------------------------------------

    def register_dim_table(
        self,
        table_name: str,
        owner: str,
        scope: str,
        records: dict[str, dict[str, Any]],
        readers: list[str] | None = None,
        note: str = "初始登记",
    ) -> dict[str, Any]:
        """登记一张部门维表。scope=private 为某源私有，shared 为共享主档。"""
        if scope not in ("private", "shared"):
            return {"error": "scope 只支持 private / shared"}
        if scope == "private" and owner not in KNOWN_SOURCES:
            return {"error": f"私有维表 owner 必须是已知日志源: {KNOWN_SOURCES}"}
        if table_name in self._registry["tables"]:
            return {"error": f"维表 {table_name} 已登记，更新请走版本追加"}

        table = {
            "table_name": table_name,
            "owner": owner,
            "scope": scope,
            "readers": list(readers or (KNOWN_SOURCES if scope == "shared" else [owner])),
            "versions": [{
                "version": 1,
                "records": dict(records),
                "updated_at": time.time(),
                "note": note,
            }],
        }
        self._registry["tables"][table_name] = table
        self._save_registry()
        return {"table_name": table_name, "version": 1, "records": len(records)}

    def update_dim_table(
        self, table_name: str, records: dict[str, dict[str, Any]], note: str = "维表更新"
    ) -> dict[str, Any]:
        """追加维表新版本；旧版本与引用旧版本的凭证、立方体保持不变。"""
        table = self._registry["tables"].get(table_name)
        if table is None:
            return {"error": f"维表 {table_name} 未登记"}
        new_version = int(table["versions"][-1]["version"]) + 1
        table["versions"].append({
            "version": new_version,
            "records": dict(records),
            "updated_at": time.time(),
            "note": note,
        })
        self._save_registry()
        return {"table_name": table_name, "version": new_version, "records": len(records)}

    def list_dim_tables(self) -> dict[str, Any]:
        return {
            name: {
                "owner": t["owner"],
                "scope": t["scope"],
                "readers": t["readers"],
                "current_version": t["versions"][-1]["version"],
                "version_count": len(t["versions"]),
            }
            for name, t in self._registry["tables"].items()
        }

    def _latest_version(self, table_name: str) -> dict[str, Any] | None:
        table = self._registry["tables"].get(table_name)
        if not table or not table["versions"]:
            return None
        return table["versions"][-1]

    # ------------------------------------------------------------------
    # 日志接入（线上 / 离线各一份，客户键可重叠）
    # ------------------------------------------------------------------

    def ingest_log_records(self, source_key: str, records: list[dict[str, Any]]) -> dict[str, Any]:
        if source_key not in KNOWN_SOURCES:
            return {"error": f"未知日志源: {source_key}"}
        normalized = []
        for idx, rec in enumerate(records, start=1):
            normalized.append({
                "source_key": source_key,
                "line_no": rec.get("line_no", idx),
                "customer_key": str(rec.get("customer_key", "")).strip(),
                "event": rec.get("event", ""),
                "collected_at": rec.get("collected_at", time.time()),
            })
        log_file = self._log_dir / f"{source_key}.jsonl"
        written = self._write_jsonl(log_file, normalized)
        return {"source_key": source_key, "written": written, "log_file": str(log_file)}

    def _read_source_logs(self, source_key: str) -> list[dict[str, Any]]:
        return self._read_jsonl(self._log_dir / f"{source_key}.jsonl")

    # ------------------------------------------------------------------
    # 取数权限：每个源只能用自己的私有维表 + 只读共享主档
    # ------------------------------------------------------------------

    def _access_scope(self, source_key: str) -> tuple[list[tuple[str, str]], list[dict[str, str]]]:
        """返回 (允许访问的[(维表, 访问方式)], 禁止访问的[{维表, 原因}])。

        顺序保证私有维表在前、共享主档在后，从而“私有优先、共享兜底”是确定的。
        """
        allowed: list[tuple[str, str]] = []
        forbidden: list[dict[str, str]] = []
        for name, table in self._registry["tables"].items():
            if table["scope"] == "private":
                if table["owner"] == source_key:
                    allowed.append((name, "owner_private"))
                else:
                    forbidden.append({
                        "table_name": name,
                        "access_mode": "denied",
                        "reason": "private_of_other_source",
                    })
            else:  # shared
                if source_key in table["readers"]:
                    allowed.append((name, "shared_readonly"))
                else:
                    forbidden.append({
                        "table_name": name,
                        "access_mode": "denied",
                        "reason": "shared_not_granted",
                    })
        allowed.sort(key=lambda pair: 0 if pair[1] == "owner_private" else 1)
        return allowed, forbidden

    def _lookup_authorized(
        self, source_key: str, customer_key: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[dict[str, Any]]]:
        """只在该源有权限的维表里取数，返回 (候选命中, 禁止清单, 允许清单快照)。"""
        allowed, forbidden = self._access_scope(source_key)
        hits: list[dict[str, Any]] = []
        allowed_snapshot: list[dict[str, Any]] = []
        for table_name, access_mode in allowed:
            version = self._latest_version(table_name)
            if version is None:
                continue
            allowed_snapshot.append({
                "table_name": table_name,
                "access_mode": access_mode,
                "table_version": version["version"],
            })
            dim_row = version["records"].get(customer_key)
            if dim_row is None:
                continue
            hits.append({
                "table_name": table_name,
                "table_scope": self._registry["tables"][table_name]["scope"],
                "table_version": version["version"],
                "access_mode": access_mode,
                "lookup_key": customer_key,
                "department": dim_row.get("department"),
                "dim_record": dim_row,
            })
        return hits, forbidden, allowed_snapshot

    # ------------------------------------------------------------------
    # 维表命中归属凭证：每行确认一个可核对部门
    # ------------------------------------------------------------------

    def attest_record(
        self, source_key: str, line_ref: dict[str, Any], customer_key: str
    ) -> dict[str, Any]:
        """为单行日志生成维表命中归属凭证。

        行级规则：候选命中按“私有优先、共享只读兜底”排序，第一条为实际命中，
        其余候选保留为备选；没有任何命中时状态为 unresolved（未决，不猜部门）。
        """
        if source_key not in KNOWN_SOURCES:
            return {"error": f"未知日志源: {source_key}"}

        hits, forbidden, allowed_snapshot = self._lookup_authorized(source_key, customer_key)
        self._credential_seq += 1
        credential: dict[str, Any] = {
            "credential_id": f"CRED-{self._credential_seq:05d}",
            "resolution_no": self._resolution_seq,
            "record_ref": {
                "source_key": source_key,
                "line_no": line_ref.get("line_no"),
                "log_file": line_ref.get("log_file", ""),
            },
            "customer_key": customer_key,
            "access_scope": {
                "allowed_tables": allowed_snapshot,
                "forbidden_tables": forbidden,
                "rule": "owner_private_first_then_shared_readonly",
            },
            "candidate_hits": hits,
            "created_at": time.time(),
        }

        if hits:
            actual = dict(hits[0])
            credential["actual_hit"] = actual
            credential["confirmed_department"] = actual["department"]
            credential["status"] = "confirmed"
        else:
            credential["actual_hit"] = None
            credential["confirmed_department"] = None
            credential["status"] = "unresolved"
            credential["unresolved_reason"] = "no_authorized_dim_match"

        self._append_credential(credential)
        return credential

    def attest_source_logs(self, source_key: str) -> dict[str, Any]:
        """为某日志源的全部日志记录批量确认部门归属。"""
        logs = self._read_source_logs(source_key)
        if not logs:
            return {"error": f"日志源 {source_key} 无接入日志", "attested": 0}

        credentials = []
        for rec in logs:
            cred = self.attest_record(
                source_key,
                {"line_no": rec.get("line_no"), "log_file": f"logs/{source_key}.jsonl"},
                rec["customer_key"],
            )
            credentials.append(cred)

        confirmed = sum(1 for c in credentials if c["status"] == "confirmed")
        return {
            "source_key": source_key,
            "total": len(credentials),
            "attested": len(credentials),
            "confirmed": confirmed,
            "unresolved": len(credentials) - confirmed,
            "credentials": [c["credential_id"] for c in credentials],
        }

    def list_credentials(self, customer_key: str | None = None) -> list[dict[str, Any]]:
        if customer_key is None:
            return list(self._credentials)
        return [c for c in self._credentials if c["customer_key"] == customer_key]

    def get_credential(self, credential_id: str) -> dict[str, Any] | None:
        for cred in self._credentials:
            if cred["credential_id"] == credential_id:
                return cred
        return None

    # ------------------------------------------------------------------
    # 同键复查：用另一源的同键记录核对有效归属，并比对维表最新版本
    # ------------------------------------------------------------------

    def recheck_credential(self, credential_id: str) -> dict[str, Any]:
        """按当前维表版本重新取数，复查一张旧凭证的有效归属是否漂移。"""
        original = self.get_credential(credential_id)
        if original is None:
            return {"error": f"凭证 {credential_id} 不存在"}

        source_key = original["record_ref"]["source_key"]
        customer_key = original["customer_key"]
        current_hits, forbidden, _ = self._lookup_authorized(source_key, customer_key)
        current = current_hits[0] if current_hits else None
        original_hit = original.get("actual_hit")

        drift = False
        if current is None or original_hit is None:
            drift = current != original_hit
        else:
            drift = (
                current["department"] != original_hit["department"]
                or current["table_name"] != original_hit["table_name"]
                or current["table_version"] != original_hit["table_version"]
            )

        return {
            "credential_id": credential_id,
            "customer_key": customer_key,
            "source_key": source_key,
            "original_hit": original_hit,
            "current_effective_hit": current,
            "current_candidates": current_hits,
            "forbidden_tables": forbidden,
            "drift": drift,
            "rechecked_at": time.time(),
        }

    def recheck_customer_attribution(self, customer_key: str) -> dict[str, Any]:
        """跨源同键复查：每个源只用自己有权限的维表给出有效部门。

        在线源复查时显式带出离线源同键凭证作为对方归属证据，反之亦然，
        从而说明“用另一个源同键复查它的有效归属”，且复查全程不越权取数。
        """
        per_source: dict[str, Any] = {}
        for source_key in KNOWN_SOURCES:
            logs = self._read_source_logs(source_key)
            same_key = [r for r in logs if r.get("customer_key") == customer_key]
            if not same_key:
                per_source[source_key] = {"has_log": False}
                continue
            hits, forbidden, _ = self._lookup_authorized(source_key, customer_key)
            latest_cred = next(
                (c for c in reversed(self._credentials)
                 if c["customer_key"] == customer_key
                 and c["record_ref"]["source_key"] == source_key),
                None,
            )
            per_source[source_key] = {
                "has_log": True,
                "line_no": same_key[-1].get("line_no"),
                "effective_department": hits[0]["department"] if hits else None,
                "effective_hit": hits[0] if hits else None,
                "all_authorized_hits": hits,
                "forbidden_tables": forbidden,
                "credential_id": latest_cred["credential_id"] if latest_cred else None,
            }

        departments = {
            s: info.get("effective_department")
            for s, info in per_source.items()
            if info.get("has_log") and info.get("effective_department") is not None
        }
        conflict = len(set(departments.values())) > 1
        return {
            "customer_key": customer_key,
            "conflict": conflict,
            "per_source": per_source,
            "cross_check_note": (
                "每个源仅用本源私有维表与只读共享主档取数；"
                "另一源的同键凭证用于对照，不参与本源取数"
            ),
            "rechecked_at": time.time(),
        }

    # ------------------------------------------------------------------
    # 部门立方体：用凭证写入，每组记录维表来源与实际命中，客户只计一次
    # ------------------------------------------------------------------

    def _latest_credentials_by_record(self) -> list[dict[str, Any]]:
        """同一日志行可能因维表更新重算过多次，只取每个行身份的最新凭证。"""
        latest: dict[tuple[str, Any], dict[str, Any]] = {}
        for cred in self._credentials:
            key = (cred["record_ref"]["source_key"], cred["record_ref"]["line_no"])
            latest[key] = cred
        return list(latest.values())

    def write_department_cube(
        self, source_keys: list[str] | None = None, run_label: str = ""
    ) -> dict[str, Any]:
        """基于维表命中归属凭证核对并写入一版新的部门立方体。"""
        sources = source_keys or list(KNOWN_SOURCES)
        row_credentials = [
            c for c in self._latest_credentials_by_record()
            if c["record_ref"]["source_key"] in sources
        ]
        if not row_credentials:
            return {"error": "无可用凭证，请先执行维表命中归属确认", "groups": 0}

        # 按客户键归集跨行凭证，做客户级核对。
        by_customer: dict[str, list[dict[str, Any]]] = {}
        for cred in row_credentials:
            by_customer.setdefault(cred["customer_key"], []).append(cred)

        self._resolution_seq += 1
        resolution_no = self._resolution_seq
        groups: dict[str, dict[str, Any]] = {}
        customer_reconciliation: dict[str, Any] = {}

        def ensure_group(name: str) -> dict[str, Any]:
            if name not in groups:
                groups[name] = {"count": 0, "members": [], "dim_sources_used": []}
            return groups[name]

        for customer_key in sorted(by_customer):
            creds = by_customer[customer_key]
            departments = {
                c["confirmed_department"]
                for c in creds
                if c["status"] == "confirmed" and c["confirmed_department"] is not None
            }
            row_evidence = [self._credential_evidence(c) for c in creds]

            if any(c["status"] != "confirmed" for c in creds):
                decision = self._unresolved_decision(
                    customer_key, row_evidence, "pending_or_miss"
                )
            elif len(departments) > 1:
                # 两个合法维表返回不同部门：行级凭证各自成立，客户级未决，
                # 只进 UNRESOLVED 一个组，绝不提前计入两个部门组。
                decision = self._unresolved_decision(
                    customer_key, row_evidence, "department_conflict"
                )
            else:
                department = next(iter(departments))
                decision = {
                    "customer_key": customer_key,
                    "decided_group": department,
                    "counted": True,
                    "conflict": False,
                    "row_evidence": row_evidence,
                }

            group = ensure_group(decision["decided_group"])
            # 一个客户键在整个立方体里只贡献 1 个计数。
            group["count"] += 1
            group["members"].append(decision)
            customer_reconciliation[customer_key] = decision

        self._fill_dim_sources_used(groups)

        cube = {
            "cube_version": self._next_cube_version(),
            "resolution_no": resolution_no,
            "run_label": run_label,
            "generated_at": time.time(),
            "scope_sources": sources,
            "counting_rule": (
                "每个客户键全立方体只计一次；部门冲突或未决只入 UNRESOLVED 一个组，"
                "不提前计入两个部门组；组成员携带凭证号、维表来源与实际命中供回查"
            ),
            "groups": groups,
            "customer_reconciliation": customer_reconciliation,
            "totals": {
                "customer_keys": len(by_customer),
                "counted": sum(g["count"] for g in groups.values()),
                "department_groups": len([g for g in groups if g != UNRESOLVED_GROUP]),
                "unresolved": groups.get(UNRESOLVED_GROUP, {}).get("count", 0),
            },
        }

        cube_file = self._cube_dir / f"department_cube_v{cube['cube_version']}.json"
        with open(cube_file, "w", encoding="utf-8") as f:
            json.dump(cube, f, ensure_ascii=False, indent=2, default=str)
        with open(self._cube_dir / "department_cube_current.json", "w", encoding="utf-8") as f:
            json.dump({"cube_version": cube["cube_version"], "file": cube_file.name}, f,
                      ensure_ascii=False, indent=2)
        self._save_registry()
        cube["cube_file"] = str(cube_file)
        return cube

    def _credential_evidence(self, cred: dict[str, Any]) -> dict[str, Any]:
        """压缩成可回查的取数依据：凭证号 + 来源 + 实际命中。"""
        hit = cred.get("actual_hit")
        return {
            "credential_id": cred["credential_id"],
            "source_key": cred["record_ref"]["source_key"],
            "line_no": cred["record_ref"]["line_no"],
            "status": cred["status"],
            "confirmed_department": cred.get("confirmed_department"),
            "dim_source": hit["table_name"] if hit else None,
            "dim_version": hit["table_version"] if hit else None,
            "access_mode": hit["access_mode"] if hit else None,
            "actual_hit": hit,
            "alternative_hits": cred.get("candidate_hits", [])[1:],
        }

    def _unresolved_decision(
        self, customer_key: str, row_evidence: list[dict[str, Any]], reason: str
    ) -> dict[str, Any]:
        # 暂定归属仅作提示：按源优先级取第一个已确认行，不落任何部门组。
        confirmed_rows = [e for e in row_evidence if e["status"] == "confirmed"]
        confirmed_rows.sort(key=lambda e: SOURCE_PRIORITY.get(e["source_key"], 99))
        provisional = confirmed_rows[0] if confirmed_rows else None
        return {
            "customer_key": customer_key,
            "decided_group": UNRESOLVED_GROUP,
            "counted": True,
            "conflict": reason == "department_conflict",
            "unresolved_reason": reason,
            "provisional_owner": (
                {
                    "department": provisional["confirmed_department"],
                    "source_key": provisional["source_key"],
                    "credential_id": provisional["credential_id"],
                }
                if provisional else None
            ),
            "recheck_path": (
                f"用另一日志源同键凭证复查客户 {customer_key} 的有效归属；"
                "维表更新重算前不得提前计入部门组"
            ),
            "row_evidence": row_evidence,
        }

    def _fill_dim_sources_used(self, groups: dict[str, dict[str, Any]]) -> None:
        for group in groups.values():
            usage: dict[str, dict[str, Any]] = {}
            for member in group["members"]:
                for evidence in member.get("row_evidence", []):
                    if evidence["dim_source"] is None:
                        continue
                    key = f"{evidence['dim_source']}@v{evidence['dim_version']}"
                    item = usage.setdefault(key, {
                        "table_name": evidence["dim_source"],
                        "table_version": evidence["dim_version"],
                        "access_mode": evidence["access_mode"],
                        "records_hit": 0,
                    })
                    item["records_hit"] += 1
            group["dim_sources_used"] = list(usage.values())

    def _next_cube_version(self) -> int:
        existing = sorted(self._cube_dir.glob("department_cube_v*.json"))
        versions = []
        for path in existing:
            try:
                versions.append(int(path.stem.split("_v")[-1]))
            except ValueError:
                continue
        return (max(versions) if versions else 0) + 1

    def list_cube_versions(self) -> list[dict[str, Any]]:
        result = []
        for path in sorted(self._cube_dir.glob("department_cube_v*.json")):
            with open(path, "r", encoding="utf-8") as f:
                cube = json.load(f)
            result.append({
                "cube_version": cube["cube_version"],
                "resolution_no": cube["resolution_no"],
                "run_label": cube.get("run_label", ""),
                "generated_at": cube.get("generated_at"),
                "totals": cube.get("totals"),
                "file": path.name,
            })
        return result

    def get_cube(self, version: int | None = None) -> dict[str, Any] | None:
        if version is None:
            pointer = self._cube_dir / "department_cube_current.json"
            if not pointer.exists():
                return None
            with open(pointer, "r", encoding="utf-8") as f:
                version = json.load(f)["cube_version"]
        path = self._cube_dir / f"department_cube_v{version}.json"
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    # ------------------------------------------------------------------
    # 端到端核对场景：两个合法维表为同一客户返回不同部门
    # ------------------------------------------------------------------

    def build_conflict_demo(self, reset: bool = True) -> dict[str, Any]:
        """搭建并执行“线上/离线同客户部门登记冲突”的完整归属核对流程。

        步骤：登记私有维表与共享主档 -> 接入两边日志 -> 各自出凭证（权限隔离）
        -> 写部门立方体（冲突客户只进一个未决组）-> 同键跨源复查 -> 维表更新
        -> 重算产生新版本立方体，旧版本与旧身份保留。
        """
        if reset:
            self.reset_workspace()

        # 1. 登记三张部门维表：两张私有 + 一张只读共享主档。
        #    冲突客户 CUST-1001 在两张合法维表中部门登记不一致。
        self.register_dim_table(
            ONLINE_PRIVATE_TABLE, ONLINE_SOURCE, "private",
            {
                "CUST-1001": {"department": "电商一部", "owner_name": "线上登记"},
                "CUST-1002": {"department": "电商二部", "owner_name": "线上登记"},
            },
            note="线上私有部门维表",
        )
        self.register_dim_table(
            OFFLINE_PRIVATE_TABLE, OFFLINE_SOURCE, "private",
            {
                "CUST-1001": {"department": "渠道发展部", "owner_name": "离线登记"},
                "CUST-2001": {"department": "渠道支持部", "owner_name": "离线登记"},
            },
            note="离线私有部门维表",
        )
        self.register_dim_table(
            MASTER_TABLE, "master_governance", "shared",
            {
                "CUST-1001": {"department": "电商一部", "owner_name": "共享主档"},
                "CUST-9001": {"department": "战略客户部", "owner_name": "共享主档"},
            },
            note="共享客户主档，各源只读",
        )

        # 2. 接入日志：同一客户 CUST-1001 的两份记录分别来自线上、离线。
        self.ingest_log_records(ONLINE_SOURCE, [
            {"line_no": 1, "customer_key": "CUST-1001", "event": "online_order"},
            {"line_no": 2, "customer_key": "CUST-1002", "event": "online_order"},
            {"line_no": 3, "customer_key": "CUST-9001", "event": "online_view"},
        ])
        self.ingest_log_records(OFFLINE_SOURCE, [
            {"line_no": 1, "customer_key": "CUST-1001", "event": "offline_batch"},
            {"line_no": 2, "customer_key": "CUST-2001", "event": "offline_batch"},
        ])

        # 3. 各自确认归属：每个源只用自己的私有维表与只读共享主档。
        online_attest = self.attest_source_logs(ONLINE_SOURCE)
        offline_attest = self.attest_source_logs(OFFLINE_SOURCE)

        # 4. 写第一版部门立方体。
        cube_v1 = self.write_department_cube(run_label="初版核对（含部门冲突）")

        # 5. 用另一个源的同键记录复查 CUST-1001 的有效归属（维表更新前：冲突在案）。
        recheck_before = self.recheck_customer_attribution("CUST-1001")
        online_cred = next(
            c for c in self._credentials
            if c["customer_key"] == "CUST-1001" and c["record_ref"]["source_key"] == ONLINE_SOURCE
        )
        cred_recheck = self.recheck_credential(online_cred["credential_id"])

        # 6. 维表更新（离线私有维表追加新版本，把 CUST-1001 修正为电商一部）。
        offline_table = self._registry["tables"][OFFLINE_PRIVATE_TABLE]
        updated_records = {
            k: dict(v) for k, v in offline_table["versions"][-1]["records"].items()
        }
        updated_records["CUST-1001"] = {"department": "电商一部", "owner_name": "离线修正"}
        update_r = self.update_dim_table(
            OFFLINE_PRIVATE_TABLE, updated_records, note="部门归属修正"
        )

        # 重算：重新确认离线日志并重算立方体；旧立方体身份保留，新版本独立。
        offline_attest_v2 = self.attest_source_logs(OFFLINE_SOURCE)
        cube_v2 = self.write_department_cube(run_label="维表更新后重算（冲突解除）")
        recheck_after = self.recheck_customer_attribution("CUST-1001")

        return {
            "scenario": "shared_department_dim_conflict",
            "dim_tables": self.list_dim_tables(),
            "attestation": {"online": online_attest, "offline": offline_attest},
            "cube_v1": self._cube_summary(cube_v1),
            "cross_source_recheck": recheck_before,
            "credential_recheck": cred_recheck,
            "dim_update": update_r,
            "recalculation": {"offline_attest": offline_attest_v2},
            "cube_v2": self._cube_summary(cube_v2),
            "recheck_after_update": recheck_after,
            "cube_versions": self.list_cube_versions(),
        }

    @staticmethod
    def _cube_summary(cube: dict[str, Any]) -> dict[str, Any]:
        """对外摘要：保留取数位置（维表来源+实际命中），去掉冗余明细。"""
        return {
            "cube_version": cube["cube_version"],
            "resolution_no": cube["resolution_no"],
            "run_label": cube.get("run_label", ""),
            "counting_rule": cube["counting_rule"],
            "totals": cube["totals"],
            "groups": {
                name: {
                    "count": detail["count"],
                    "dim_sources_used": detail["dim_sources_used"],
                    "customer_keys": [m["customer_key"] for m in detail["members"]],
                    "unresolved_reasons": sorted({
                        m.get("unresolved_reason")
                        for m in detail["members"] if m.get("unresolved_reason")
                    }),
                }
                for name, detail in cube["groups"].items()
            },
        }
