"""事件埋点：管理埋点事件定义、触发上报和事件数据持久化。"""
from __future__ import annotations

import json
import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class EventTrackerService(BaseModuleService):
    MODULE = "event_tracker"
    ENTITY = "埋点事件"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["事件编号", "事件名称", "埋点页面", "触发条件", "上报字段", "采样率", "事件版本", "生效状态"]
    REQUIRED_FIELDS = ["事件编号", "事件名称", "埋点页面"]
    STATUS_ORDER = ["草稿", "待校验", "已生效", "已停用"]
    ACTION_RULES = {"提交校验": "待校验", "标记生效": "已生效", "停用事件": "已停用"}
    NEGATIVE_ACTIONS = ["停用事件"]

    def activate_event(self, entry_id: int) -> dict[str, Any]:
        """生效事件并自动创建配套校验规则。"""
        entry, msg = self.run_action(entry_id, "标记生效")
        if entry is None:
            return {"error": msg, "entry_id": entry_id}
        event_code = entry["事件编号"]

        triggers = {}
        # 自动创建配套event_validate校验规则
        try:
            validate_entry = self._auto_create_seed("event_validate", {
                "规则编号": f"VALIDATE-{event_code}",
                "关联事件": event_code,
                "字段检查": event_code,
                "格式约束": "auto_format",
                "取值边界": "auto_bound",
                "采样校验": "全量",
                "阻断动作": "warn",
                "规则版本": "v1.0",
            })
            if validate_entry:
                triggers["event_validate"] = {"created": validate_entry["规则编号"], "status": "已生效"}
        except Exception as e:
            triggers["event_validate"] = {"error": str(e)}

        # 自动触发事件校验
        validate_r = self._trigger_validation_for_event(event_code)
        if "error" not in validate_r:
            triggers["validation"] = validate_r

        return {
            "event_entry": entry,
            "event_code": event_code,
            "activated_at": time.time(),
            "auto_triggers": triggers,
        }

    def track_event(self, event_code: str, payload: dict[str, Any]) -> dict[str, Any]:
        """触发一次事件埋点上报。"""
        event = None
        for e in store.rows(self.MODULE):
            if e.get("事件编号") == event_code and e.get("status") == "已生效":
                event = e
                break
        if event is None:
            return {"error": f"事件 {event_code} 未生效或不存在"}

        record = {
            "event_code": event_code,
            "event_name": event["事件名称"],
            "page": event["埋点页面"],
            "payload": payload,
            "tracked_at": time.time(),
            "event_version": event.get("事件版本", "v1"),
        }

        events_dir = self._data_dir("events")
        output_file = events_dir / f"{event_code}.jsonl"
        self._write_jsonl(output_file, [record])

        cp = self._read_checkpoint(f"event_{event_code}") or {"count": 0}
        cp["count"] = cp.get("count", 0) + 1
        cp["last_fired"] = time.time()
        self._write_checkpoint(f"event_{event_code}", cp)

        result = {"status": "tracked", "event_code": event_code, "count": cp["count"]}

        # 事后自动触发校验
        validate_r = self._trigger_validation_for_event(event_code)
        if "error" not in validate_r:
            result["auto_validate"] = validate_r

        return result

    def get_event_stats(self, event_code: str) -> dict[str, Any]:
        """获取事件埋点统计数据。"""
        events_file = self._data_dir("events") / f"{event_code}.jsonl"
        records = self._read_jsonl(events_file) if events_file.exists() else []
        return {
            "event_code": event_code,
            "total_fired": len(records),
            "first_fired": records[0]["tracked_at"] if records else None,
            "last_fired": records[-1]["tracked_at"] if records else None,
        }