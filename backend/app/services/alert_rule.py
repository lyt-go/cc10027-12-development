"""告警规则：基于聚合结果和质量诊断触发告警通知。"""
from __future__ import annotations

import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class AlertRuleService(BaseModuleService):
    MODULE = "alert_rule"
    ENTITY = "告警规则"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["规则编号", "关联源", "触发条件", "告警级别", "通知渠道", "冷却时长", "静默策略", "规则版本"]
    REQUIRED_FIELDS = ["规则编号", "关联源", "触发条件"]
    STATUS_ORDER = ["草稿", "待校验", "已生效", "已静默"]
    ACTION_RULES = {"提交校验": "待校验", "标记生效": "已生效", "静默规则": "已静默"}
    NEGATIVE_ACTIONS = []

    # 告警冷却记录
    _cooldowns: dict[str, float] = {}

    def evaluate_rules(self, source_key: str, metrics: dict[str, Any] | None = None) -> dict[str, Any]:
        """评估告警规则，触发符合条件的告警。"""
        rules = [r for r in store.rows(self.MODULE) if r.get("status") == "已生效"]
        if not rules:
            return {"evaluated": 0, "fired": 0, "status": "no_rules"}

        if metrics is None:
            metrics = self._gather_metrics(source_key)

        fired = []
        for rule in rules:
            if self._check_rule(rule, metrics):
                now = time.time()
                cooldown = float(rule.get("冷却时长", "60"))
                if now - self._cooldowns.get(rule["规则编号"], 0) >= cooldown:
                    alert = {
                        "rule_id": rule["规则编号"],
                        "level": rule.get("告警级别", "warning"),
                        "channel": rule.get("通知渠道", "internal"),
                        "message": f"告警触发: {rule.get('触发条件', '')}",
                        "metrics": metrics,
                        "fired_at": now,
                    }
                    self._cooldowns[rule["规则编号"]] = now

                    alerts_file = self._data_dir("exports") / "alerts.jsonl"
                    self._write_jsonl(alerts_file, [alert])
                    fired.append(alert)

        return {
            "source_key": source_key,
            "rules_evaluated": len(rules),
            "alerts_fired": len(fired),
            "details": fired,
        }

    def _gather_metrics(self, source_key: str) -> dict[str, Any]:
        """从各模块收集指标用于告警评估。"""
        metrics: dict[str, Any] = {"source_key": source_key}

        # 解析统计
        parse_cp = self._read_checkpoint(f"{source_key}_parse")
        if parse_cp:
            metrics["parsed"] = parse_cp.get("parsed", 0)
            metrics["parse_errors"] = parse_cp.get("errors", 0)
            if metrics["parsed"] > 0:
                metrics["parse_error_rate"] = round(metrics["parse_errors"] / metrics["parsed"] * 100, 2)

        # 采集状态
        collect_cp = self._read_checkpoint(source_key)
        if collect_cp:
            metrics["collected_at"] = collect_cp.get("last_collected_at")
            metrics["offset"] = collect_cp.get("offset", 0)

        # 代理健康
        agent_cp = self._read_checkpoint(f"agent_{source_key}")
        if agent_cp:
            metrics["agent_status"] = agent_cp.get("status", "unknown")

        # 质量报告
        quality_cp = self._read_checkpoint(f"quality_{source_key}")
        if quality_cp:
            metrics["parse_success_rate"] = quality_cp.get("parse_success_rate", 100)
            metrics["field_completeness"] = quality_cp.get("field_completeness", 100)
        else:
            metrics["parse_success_rate"] = 100
            metrics["field_completeness"] = 100

        return metrics

    def _check_rule(self, rule: dict[str, Any], metrics: dict[str, Any]) -> bool:
        condition = rule.get("触发条件", "")
        level = rule.get("告警级别", "warning")

        if condition in ("", "告警规则样例1"):
            # 默认条件：解析成功率 < 95%
            return metrics.get("parse_success_rate", 100) < 95

        if "error" in condition.lower():
            return metrics.get("parse_errors", 0) > 0
        if "timeout" in condition.lower():
            agent_status = metrics.get("agent_status")
            return agent_status == "已离线"
        if "rate" in condition.lower():
            return metrics.get("parse_error_rate", 0) > 5

        return False

    def get_alert_history(self, source_key: str | None = None) -> list[dict[str, Any]]:
        alerts = self._read_jsonl(self._data_dir("exports") / "alerts.jsonl")
        if source_key:
            alerts = [a for a in alerts if a.get("metrics", {}).get("source_key") == source_key]
        return alerts