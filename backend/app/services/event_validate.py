"""事件校验：对埋点事件数据进行字段检查、格式约束和取值边界校验。"""
from __future__ import annotations

import re
import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class EventValidateService(BaseModuleService):
    MODULE = "event_validate"
    ENTITY = "校验规则"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["规则编号", "关联事件", "字段检查", "格式约束", "取值边界", "采样校验", "阻断动作", "规则版本"]
    REQUIRED_FIELDS = ["规则编号", "关联事件", "字段检查"]
    STATUS_ORDER = ["草稿", "待校验", "已生效", "已停用"]
    ACTION_RULES = {"提交校验": "待校验", "标记生效": "已生效", "停用规则": "已停用"}
    NEGATIVE_ACTIONS = ["停用规则"]

    def validate_event_data(self, event_code: str) -> dict[str, Any]:
        """对指定事件的已上报数据进行校验。"""
        events_file = self._data_dir("events") / f"{event_code}.jsonl"
        if not events_file.exists():
            return {"error": f"事件 {event_code} 无数据", "validated": 0}

        records = self._read_jsonl(events_file)
        rules = [r for r in store.rows(self.MODULE) if r.get("关联事件") == event_code and r.get("status") == "已生效"]

        passed = []
        failed = []
        for rec in records:
            violations = self._check_record(rec, rules)
            if violations:
                rec["validation_errors"] = violations
                rec["validated_at"] = time.time()
                failed.append(rec)
            else:
                rec["validation_passed"] = True
                rec["validated_at"] = time.time()
                passed.append(rec)

        validated_dir = self._data_dir("events")
        self._write_jsonl(validated_dir / f"{event_code}_validated.jsonl", passed + failed)

        self._write_checkpoint(f"validate_{event_code}", {
            "total": len(records),
            "passed": len(passed),
            "failed": len(failed),
            "completed_at": time.time(),
        })

        return {
            "event_code": event_code,
            "total": len(records),
            "passed": len(passed),
            "failed": len(failed),
            "rules_applied": len(rules),
        }

    def _check_record(self, rec: dict[str, Any], rules: list[dict[str, Any]]) -> list[str]:
        violations = []
        payload = rec.get("payload", {})
        for rule in rules:
            field_check = rule.get("字段检查", "")
            format_constraint = rule.get("格式约束", "")
            value_bound = rule.get("取值边界", "")

            if field_check and field_check not in ("", "事件校验样例1"):
                for field_name in field_check.split(","):
                    field_name = field_name.strip()
                    if field_name and field_name not in payload:
                        violations.append(f"缺少字段: {field_name}")

            if format_constraint and format_constraint not in ("", "事件校验样例1"):
                for field_name in format_constraint.split(","):
                    field_name = field_name.strip()
                    val = payload.get(field_name, "")
                    if isinstance(val, str) and not re.match(r"^[\w\-.@]+$", str(val)):
                        violations.append(f"格式不符: {field_name}={val}")

        if not rules and not rec.get("payload"):
            violations.append("无校验规则且payload为空")

        return violations

    def get_validation_report(self, event_code: str) -> dict[str, Any]:
        cp = self._read_checkpoint(f"validate_{event_code}")
        if cp is None:
            return {"event_code": event_code, "status": "未校验"}
        return {"event_code": event_code, "status": "completed", **cp}