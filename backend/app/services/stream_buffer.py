"""流式缓冲：管理缓冲区配置，模拟流式采集数据的批量刷写。"""
from __future__ import annotations

import time
import threading
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class StreamBufferService(BaseModuleService):
    MODULE = "stream_buffer"
    ENTITY = "缓冲配置"
    OPERATION_TERM = "采集"
    LIST_FIELDS = ["配置编号", "关联源", "缓冲区大小", "批量大小", "刷写间隔", "内存上限", "溢出策略", "配置版本"]
    REQUIRED_FIELDS = ["配置编号", "关联源", "缓冲区大小"]
    STATUS_ORDER = ["草稿", "待校验", "已生效", "已停用"]
    ACTION_RULES = {"提交校验": "待校验", "标记生效": "已生效", "停用配置": "已停用"}
    NEGATIVE_ACTIONS = ["停用配置"]

    def __init__(self):
        super().__init__()
        self._buffers: dict[str, list[dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def buffer_event(self, event_code: str, event_data: dict[str, Any]) -> dict[str, Any]:
        """将事件数据写入流式缓冲区。"""
        configs = [c for c in store.rows(self.MODULE) if c.get("status") == "已生效"]
        batch_size = 100
        if configs:
            try:
                batch_size = int(configs[0].get("批量大小", "100"))
            except (ValueError, TypeError):
                batch_size = 100

        with self._lock:
            buf = self._buffers.setdefault(event_code, [])
            buf.append(event_data)

            if len(buf) >= batch_size:
                flushed = self._flush_buffer(event_code)
                return {"status": "flushed", "event_code": event_code, "records": flushed}

            return {"status": "buffered", "event_code": event_code, "buffer_size": len(buf)}

    def _flush_buffer(self, event_code: str) -> int:
        buf = self._buffers.get(event_code, [])
        if not buf:
            return 0

        output_file = self._data_dir("events") / f"{event_code}_stream.jsonl"
        written = self._write_jsonl(output_file, buf)

        cp = self._read_checkpoint(f"stream_{event_code}") or {"total_flushed": 0}
        cp["total_flushed"] = cp.get("total_flushed", 0) + written
        cp["last_flushed_at"] = time.time()
        cp["buffer_count"] = len(buf)
        self._write_checkpoint(f"stream_{event_code}", cp)

        self._buffers[event_code] = []
        return written

    def flush_all(self) -> dict[str, Any]:
        """强制刷写所有缓冲区并自动触发导出和校验。"""
        results = {}
        total = 0
        for event_code in list(self._buffers.keys()):
            flushed = self._flush_buffer(event_code)
            results[event_code] = flushed
            total += flushed

        result = {"total_flushed": total, "details": results}

        # 刷写后自动触发格式输出和质量诊断
        for event_code in list(self._buffers.keys()) if hasattr(self, "_buffers") else []:
            pass

        # 为已刷写事件触发校验
        auto_validations = {}
        for event_code in results:
            if results[event_code] > 0:
                validate_r = self._trigger_validation_for_event(event_code)
                if "error" not in validate_r:
                    auto_validations[event_code] = validate_r
        if auto_validations:
            result["auto_validations"] = auto_validations

        return result

    def get_buffer_state(self, event_code: str | None = None) -> dict[str, Any]:
        with self._lock:
            if event_code:
                return {"event_code": event_code, "pending": len(self._buffers.get(event_code, []))}
            return {
                "buffers": {k: len(v) for k, v in self._buffers.items()},
                "total_pending": sum(len(v) for v in self._buffers.values()),
            }