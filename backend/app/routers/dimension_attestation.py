"""维表命中归属凭证路由：部门维表登记、归属确认、同键复查与部门立方体。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.services.dimension_attestation import DimensionAttestationService

router = APIRouter(prefix="/api/dim-attestation", tags=["维表命中归属凭证"])

service = DimensionAttestationService()


class DimTablePayload(BaseModel):
    table_name: str
    owner: str
    scope: str
    records: dict[str, dict[str, Any]]
    readers: list[str] | None = None
    note: str = "初始登记"


class DimUpdatePayload(BaseModel):
    records: dict[str, dict[str, Any]]
    note: str = "维表更新"


class IngestPayload(BaseModel):
    source_key: str
    records: list[dict[str, Any]]


class AttestPayload(BaseModel):
    source_key: str
    customer_key: str
    line_no: int | None = None


# -- 部门维表管理 --

@router.get("/dim-tables")
def list_dim_tables() -> dict[str, Any]:
    return {"items": service.list_dim_tables()}


@router.post("/dim-tables")
def register_dim_table(payload: DimTablePayload) -> dict[str, Any]:
    return service.register_dim_table(
        payload.table_name, payload.owner, payload.scope,
        payload.records, payload.readers, payload.note,
    )


@router.post("/dim-tables/{table_name}/versions")
def update_dim_table(table_name: str, payload: DimUpdatePayload) -> dict[str, Any]:
    return service.update_dim_table(table_name, payload.records, payload.note)


# -- 日志接入与归属凭证 --

@router.post("/logs/ingest")
def ingest_logs(payload: IngestPayload) -> dict[str, Any]:
    return service.ingest_log_records(payload.source_key, payload.records)


@router.post("/attestations")
def attest_record(payload: AttestPayload) -> dict[str, Any]:
    return service.attest_record(
        payload.source_key,
        {"line_no": payload.line_no, "log_file": f"logs/{payload.source_key}.jsonl"},
        payload.customer_key,
    )


@router.post("/sources/{source_key}/attest")
def attest_source(source_key: str) -> dict[str, Any]:
    return service.attest_source_logs(source_key)


@router.get("/attestations")
def list_credentials(customer_key: str | None = None) -> dict[str, Any]:
    return {"items": service.list_credentials(customer_key)}


@router.get("/attestations/{credential_id}")
def get_credential(credential_id: str) -> dict[str, Any]:
    result = service.get_credential(credential_id)
    if result is None:
        return {"ok": False, "message": f"凭证 {credential_id} 不存在"}
    return result


@router.get("/attestations/{credential_id}/recheck")
def recheck_credential(credential_id: str) -> dict[str, Any]:
    return service.recheck_credential(credential_id)


# -- 同键跨源复查与部门立方体 --

@router.get("/customers/{customer_key}/recheck")
def recheck_customer(customer_key: str) -> dict[str, Any]:
    return service.recheck_customer_attribution(customer_key)


@router.post("/department-cube")
def write_department_cube(run_label: str = "") -> dict[str, Any]:
    return service.write_department_cube(run_label=run_label)


@router.get("/department-cube")
def get_current_cube() -> dict[str, Any]:
    result = service.get_cube()
    return result or {"ok": False, "message": "尚无部门立方体，请先执行归属确认并写入"}


@router.get("/department-cube/versions")
def list_cube_versions() -> dict[str, Any]:
    return {"items": service.list_cube_versions()}


@router.get("/department-cube/v{version}")
def get_cube_version(version: int) -> dict[str, Any]:
    result = service.get_cube(version)
    if result is None:
        return {"ok": False, "message": f"立方体版本 v{version} 不存在"}
    return result


# -- 端到端核对场景 --

@router.post("/conflict-demo")
def run_conflict_demo() -> dict[str, Any]:
    """两个合法维表为同一客户返回不同部门的完整归属核对演示。"""
    return service.build_conflict_demo()
