"""质量诊断：分析日志解析质量，生成完整性、成功率、延迟分布等诊断报告。"""
from __future__ import annotations

import math
import statistics
import time
from collections import defaultdict
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class LogQualityService(BaseModuleService):
    MODULE = "log_quality"
    ENTITY = "诊断报告"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["报告编号", "关联源", "解析成功率", "字段完整性", "延迟分布", "脏数据类型", "诊断时间", "报告状态"]
    REQUIRED_FIELDS = ["报告编号", "关联源", "解析成功率"]
    STATUS_ORDER = ["诊断中", "已生成", "已复核", "已归档"]
    ACTION_RULES = {"确认生成": "已生成", "标记复核": "已复核", "提交归档": "已归档"}
    NEGATIVE_ACTIONS = []

    def diagnose_source(self, source_key: str) -> dict[str, Any]:
        """对指定日志源进行质量诊断。"""
        parsed_file = self._data_dir("logs") / f"{source_key}_parsed.jsonl"
        parse_error_file = self._data_dir("logs") / f"{source_key}_parse_errors.jsonl"
        log_file = self._data_dir("logs") / f"{source_key}.jsonl"

        if not parsed_file.exists() and not log_file.exists():
            return {"error": "无可用数据", "diagnosed": False, "source_key": source_key}

        parsed_records = self._read_jsonl(parsed_file) if parsed_file.exists() else []
        error_records = self._read_jsonl(parse_error_file) if parse_error_file.exists() else []
        all_records = self._read_jsonl(log_file) if log_file.exists() else parsed_records

        total = len(all_records) + len(error_records)
        parsed_ok = len(parsed_records)
        parse_errors = len(error_records)

        success_rate = round(parsed_ok / max(total, 1) * 100, 2) if total > 0 else 100

        completeness = self._compute_completeness(parsed_records)
        latency_dist = self._compute_latency_distribution(parsed_records)
        dirty_types = self._classify_dirty_data(error_records)

        report = {
            "report_id": f"QL_{source_key}_{int(time.time())}",
            "source_key": source_key,
            "diagnosed_at": time.time(),
            "parse_success_rate": success_rate,
            "parsed_ok": parsed_ok,
            "parse_errors": parse_errors,
            "field_completeness": completeness,
            "latency_distribution": latency_dist,
            "dirty_data_types": dirty_types,
            "total_records": total,
        }

        report_file = self._data_dir("exports") / f"quality_{source_key}_{int(time.time())}.json"
        self._write_jsonl(
            self._data_dir("exports") / "quality_reports.jsonl",
            [report]
        )

        self._write_checkpoint(f"quality_{source_key}", report)

        # 诊断完成后自动触发告警评估
        if success_rate < 95 or parse_errors > 0:
            alert_r = self._trigger_alert_evaluation(source_key, {
                "parse_success_rate": success_rate,
                "field_completeness": completeness.get("overall", 100),
                "parse_errors": parse_errors,
                "source_key": source_key,
            })
            if "error" not in alert_r:
                report["auto_alert"] = alert_r

        return report

    def _compute_completeness(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        if not records:
            return {"rate": 100, "total": 0, "description": "无记录"}

        field_counts = defaultdict(lambda: {"present": 0, "total": 0})
        for rec in records:
            fields = rec.get("fields", rec)
            for k, v in fields.items():
                field_counts[k]["total"] += 1
                if v is not None and v != "":
                    field_counts[k]["present"] += 1

        result = {"fields": {}}
        for field, counts in field_counts.items():
            rate = round(counts["present"] / max(counts["total"], 1) * 100, 2)
            result["fields"][field] = rate

        rates = [v for v in result["fields"].values()]
        result["overall"] = round(sum(rates) / max(len(rates), 1), 2) if rates else 100
        return result

    def _compute_latency_distribution(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        latencies = []
        for rec in records:
            fields = rec.get("fields", rec)
            for k in ("latency_ms", "latency", "duration_ms"):
                val = fields.get(k)
                if val is not None:
                    try:
                        latencies.append(float(val))
                    except (ValueError, TypeError):
                        pass

        if not latencies:
            return {"count": 0, "description": "无延迟数据"}

        return {
            "count": len(latencies),
            "min": round(min(latencies), 2),
            "max": round(max(latencies), 2),
            "avg": round(statistics.mean(latencies), 2),
            "p50": round(statistics.median(latencies), 2),
            "p95": round(self._percentile(latencies, 95), 2),
            "p99": round(self._percentile(latencies, 99), 2),
        }

    def _percentile(self, data: list[float], percentile: float) -> float:
        if not data:
            return 0
        sorted_data = sorted(data)
        index = (percentile / 100.0) * (len(sorted_data) - 1)
        lower = int(math.floor(index))
        upper = int(math.ceil(index))
        if lower == upper:
            return sorted_data[lower]
        weight = index - lower
        return sorted_data[lower] * (1 - weight) + sorted_data[upper] * weight

    def _classify_dirty_data(self, error_records: list[dict[str, Any]]) -> dict[str, Any]:
        types = defaultdict(int)
        for rec in error_records:
            error_msg = rec.get("parse_error", "unknown")
            if "正则" in str(error_msg):
                types["regex_mismatch"] += 1
            elif "格式" in str(error_msg):
                types["format_error"] += 1
            elif "字段" in str(error_msg):
                types["missing_field"] += 1
            else:
                types["other"] += 1
        return {"total": len(error_records), "categories": dict(types)}

    def compare_quality_trend(self, source_key: str) -> dict[str, Any]:
        """对比最近两次质量诊断的趋势。"""
        current = self._read_checkpoint(f"quality_{source_key}")
        reports = self._read_jsonl(self._data_dir("exports") / "quality_reports.jsonl")
        source_reports = [r for r in reports if r.get("source_key") == source_key]

        if len(source_reports) < 2:
            return {"source_key": source_key, "status": "需要至少两次诊断才能对比趋势"}

        latest = source_reports[-1]
        previous = source_reports[-2]

        return {
            "source_key": source_key,
            "latest_success_rate": latest.get("parse_success_rate", 0),
            "previous_success_rate": previous.get("parse_success_rate", 0),
            "trend": "up" if latest.get("parse_success_rate", 0) > previous.get("parse_success_rate", 0) else "down" if latest.get("parse_success_rate", 0) < previous.get("parse_success_rate", 0) else "stable",
            "delta": round(latest.get("parse_success_rate", 0) - previous.get("parse_success_rate", 0), 2),
        }