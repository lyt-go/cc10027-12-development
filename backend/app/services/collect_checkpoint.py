"""采集位点管理：采集进度记录、位点提交、回滚和过期管理。"""
from __future__ import annotations

import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class CollectCheckpointService(BaseModuleService):
    MODULE = "collect_checkpoint"
    ENTITY = "位点记录"
    OPERATION_TERM = "采集"
    LIST_FIELDS = ["记录编号", "关联源", "文件偏移", "处理行号", "位点时间", "校验摘要", "提交状态", "回滚位点"]
    REQUIRED_FIELDS = ["记录编号", "关联源", "文件偏移"]
    STATUS_ORDER = ["采集中", "已提交", "已回滚", "已过期"]
    ACTION_RULES = {"确认提交": "已提交", "发起回滚": "已回滚", "标记过期": "已过期"}
    NEGATIVE_ACTIONS = []

    def save_checkpoint(self, source_key: str, offset: int, line_number: int) -> dict[str, Any]:
        """保存采集位点状态。"""
        state = {
            "source_key": source_key,
            "offset": offset,
            "line_number": line_number,
            "status": "active",
            "updated_at": time.time(),
        }
        self._write_checkpoint(source_key, state)

        ckpt_dir = self._data_dir("checkpoints")
        self._write_jsonl(ckpt_dir / "checkpoint_history.jsonl", [{
            "source_key": source_key, "offset": offset,
            "line_number": line_number, "at": time.time(), "action": "save",
        }])
        return state

    def rollback_to(self, source_key: str, to_offset: int) -> dict[str, Any]:
        """将指定位点回滚到指定偏移位置。"""
        cp = self._read_checkpoint(source_key)
        if cp is None:
            return {"error": f"位点 {source_key} 不存在"}

        cp["offset"] = to_offset
        cp["status"] = "rolled_back"
        cp["rolled_back_at"] = time.time()
        cp["previous_offset"] = cp.get("previous_offset", cp["offset"])
        self._write_checkpoint(source_key, cp)

        self._write_jsonl(self._data_dir("checkpoints") / "checkpoint_history.jsonl", [{
            "source_key": source_key, "offset": to_offset, "at": time.time(), "action": "rollback",
        }])
        return cp

    def get_checkpoint_state(self, source_key: str) -> dict[str, Any]:
        """获取指定源的位点信息。"""
        cp = self._read_checkpoint(source_key)
        if cp is None:
            return {"source_key": source_key, "status": "no_checkpoint", "offset": 0}
        return cp

    def list_all_checkpoints(self) -> list[dict[str, Any]]:
        """列出所有位点状态。"""
        all_cps = []
        for src in self._collect_sources():
            key = src["源编号"]
            cp = self._read_checkpoint(key)
            if cp:
                all_cps.append(cp)
        return all_cps

    def commit_checkpoint(self, source_key: str) -> dict[str, Any]:
        """提交采集位点并自动触发下游格式化输出。"""
        cp = self._read_checkpoint(source_key)
        if cp is None:
            return {"error": f"位点 {source_key} 不存在"}
        cp["status"] = "committed"
        cp["committed_at"] = time.time()
        self._write_checkpoint(source_key, cp)

        # 写入位点历史
        self._write_jsonl(self._data_dir("checkpoints") / "checkpoint_history.jsonl", [{
            "source_key": source_key, "offset": cp.get("offset", 0),
            "at": time.time(), "action": "commit",
        }])

        result = {
            "source_key": source_key,
            "committed_at": time.time(),
            "offset": cp.get("offset", 0),
            "line_number": cp.get("line_number", 0),
        }

        # 提交后自动触发格式输出
        export_r = self._trigger_export_for_source(source_key)
        if "error" not in export_r:
            result["auto_export"] = export_r

        # 同时触发质量诊断
        quality_r = self._trigger_quality_diagnosis(source_key)
        if "error" not in quality_r:
            result["auto_quality"] = quality_r

        return result
        """验证采集位点的校验摘要。"""
        cp = self._read_checkpoint(source_key)
        if cp is None:
            return {"source_key": source_key, "status": "no_checkpoint", "match": False}

        actual = cp.get("checksum", "")
        match = actual == expected_checksum
        cp["last_verified_at"] = time.time()
        cp["last_verified_match"] = match
        self._write_checkpoint(source_key, cp)

        return {"source_key": source_key, "match": match, "expected": expected_checksum, "actual": actual}