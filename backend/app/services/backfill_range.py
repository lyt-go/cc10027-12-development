"""回溯采集：管理历史数据的回溯采集任务，支持时间范围、断点续做和失败重试。"""
from __future__ import annotations

import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class BackfillRangeService(BaseModuleService):
    MODULE = "backfill_range"
    ENTITY = "回溯任务"
    OPERATION_TERM = "回填"
    LIST_FIELDS = ["任务编号", "关联源", "时间范围", "数据量估计", "并发度", "跳过空文件", "校验策略", "任务状态"]
    REQUIRED_FIELDS = ["任务编号", "关联源", "时间范围"]
    STATUS_ORDER = ["排队中", "采集", "已完成", "已失败"]
    ACTION_RULES = {"发起采集": "采集", "确认完成": "已完成", "标记失败": "已失败"}
    NEGATIVE_ACTIONS = []

    def create_backfill(self, source_key: str, time_range: str, concurrency: int = 1) -> dict[str, Any]:
        """创建回溯采集任务。"""
        task_id = f"BACK-{int(time.time())}"
        entry, _ = self.create_entry({
            "任务编号": task_id,
            "关联源": source_key,
            "时间范围": time_range,
            "并发度": str(concurrency),
            "数据量估计": "待计算",
            "跳过空文件": "是",
            "校验策略": "完整性校验",
        })
        self._write_checkpoint(f"backfill_{task_id}", {
            "task_id": task_id, "source_key": source_key,
            "time_range": time_range, "status": "created", "created_at": time.time(),
            "offset": 0, "line_no": 0,
        })
        return entry or {}

    def execute_backfill(self, task_id: str, batch_size: int = 50) -> dict[str, Any]:
        """执行回溯采集（模拟历史日志采集）。"""
        task = None
        for t in store.rows(self.MODULE):
            if t.get("任务编号") == task_id:
                task = t
                break
        if task is None:
            return {"error": f"回溯任务 {task_id} 不存在"}

        source_key = task["关联源"]
        cp = self._read_checkpoint(f"backfill_{task_id}") or {"offset": 0, "line_no": 0, "source_key": source_key}

        # 模拟回溯日志
        lines = []
        base_ts = time.time() - 86400 * 30  # 30天前
        for i in range(batch_size):
            ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(base_ts + i * 3600))
            lines.append(f"{ts} [backfill] INFO source={source_key} task={task_id} record_id={cp['line_no'] + i + 1}")

        records = [{
            "source_key": source_key, "task_id": task_id,
            "line_no": cp["line_no"] + i + 1, "raw": line, "backfilled_at": time.time(),
        } for i, line in enumerate(lines)]

        output_file = self._data_dir("logs") / f"{source_key}_backfill.jsonl"
        written = self._write_jsonl(output_file, records)

        cp["offset"] = cp.get("offset", 0) + sum(len(r["raw"].encode("utf-8")) + 1 for r in records)
        cp["line_no"] = cp["line_no"] + written
        cp["status"] = "in_progress"
        cp["last_batch_at"] = time.time()
        self._write_checkpoint(f"backfill_{task_id}", cp)

        if task.get("status") != "采集":
            task["status"] = "采集"

        return {"task_id": task_id, "written": written, "total_lines": cp["line_no"], "offset": cp["offset"]}

    def complete_backfill(self, task_id: str) -> dict[str, Any]:
        """完成回溯任务并自动触发质量诊断。"""
        cp = self._read_checkpoint(f"backfill_{task_id}")
        source_key = ""
        if cp:
            cp["status"] = "completed"
            cp["completed_at"] = time.time()
            self._write_checkpoint(f"backfill_{task_id}", cp)
            source_key = cp.get("source_key", "")

        result = {"task_id": task_id, "status": "completed", "result": cp}

        # 回溯完成后自动触发质量诊断
        if source_key:
            quality_r = self._trigger_quality_diagnosis(source_key)
            if "error" not in quality_r:
                result["auto_quality"] = quality_r
                # 质量诊断后触发告警评估
                alert_r = self._trigger_alert_evaluation(source_key, {
                    "parse_success_rate": quality_r.get("parse_success_rate", 100),
                    "field_completeness": quality_r.get("field_completeness", {}).get("overall", 100),
                    "source_key": source_key,
                })
                if "error" not in alert_r:
                    result["auto_alert"] = alert_r

        return result