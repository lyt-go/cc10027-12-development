"""采集代理管理：代理注册、心跳上报、离线检测和代理状态管理。"""
from __future__ import annotations

import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class AgentHeartbeatService(BaseModuleService):
    MODULE = "agent_heartbeat"
    ENTITY = "采集代理"
    OPERATION_TERM = "采集"
    LIST_FIELDS = ["代理编号", "部署主机", "代理版本", "上次心跳", "采集量", "错误率", "内存占用", "代理状态"]
    REQUIRED_FIELDS = ["代理编号", "部署主机", "代理版本"]
    STATUS_ORDER = ["草稿", "已注册", "运行中", "已离线"]
    ACTION_RULES = {"确认注册": "已注册", "启动运行": "运行中", "标记离线": "已离线"}
    NEGATIVE_ACTIONS = []

    HEARTBEAT_TIMEOUT = 300  # 5分钟无心跳视为离线

    def send_heartbeat(self, agent_code: str, metrics: dict[str, Any] | None = None) -> dict[str, Any]:
        """代理上报心跳及运行指标。"""
        entry = None
        for a in store.rows(self.MODULE):
            if a.get("代理编号") == agent_code:
                entry = a
                break

        if entry is None:
            return {"error": f"代理 {agent_code} 未注册"}

        now = time.time()
        entry["上次心跳"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))
        entry["_last_heartbeat_ts"] = now

        if metrics:
            entry["采集量"] = str(metrics.get("collected", entry.get("采集量", "0")))
            entry["错误率"] = str(metrics.get("error_rate", entry.get("错误率", "0")))
            entry["内存占用"] = str(metrics.get("memory_mb", entry.get("内存占用", "0")))

        if entry.get("status") in ("已注册", "已离线", "草稿"):
            entry["status"] = "运行中"

        hb_file = self._data_dir("checkpoints") / f"heartbeat_{agent_code}.jsonl"
        self._write_jsonl(hb_file, [{
            "agent_code": agent_code,
            "timestamp": now,
            "collected": metrics.get("collected") if metrics else None,
            "error_rate": metrics.get("error_rate") if metrics else None,
        }])

        self._write_checkpoint(f"agent_{agent_code}", {
            "last_heartbeat": now,
            "status": entry["status"],
            "host": entry.get("部署主机", ""),
        })

        return {"agent_code": agent_code, "status": entry["status"], "heartbeat_at": now}

    def detect_offline_agents(self) -> list[dict[str, Any]]:
        """检测超时未心跳的代理，标记为离线并自动触发位点回滚和告警。"""
        now = time.time()
        offline = []
        for a in store.rows(self.MODULE):
            last_hb = a.get("_last_heartbeat_ts", 0)
            if last_hb and (now - last_hb) > self.HEARTBEAT_TIMEOUT and a.get("status") == "运行中":
                a["status"] = "已离线"
                agent_code = a["代理编号"]
                offline_item = {"代理编号": agent_code, "last_heartbeat": a.get("上次心跳"), "seconds_since": now - last_hb}

                # 自动触发位点回滚：将关联源位点回滚到0
                source_key = agent_code.replace("AGENT-", "")
                if source_key:
                    try:
                        rollback_r = self._trigger_checkpoint_rollback(source_key, 0)
                        if "error" not in rollback_r:
                            offline_item["auto_rollback"] = rollback_r
                    except Exception:
                        pass

                # 触发告警通知
                try:
                    self._trigger_alert_evaluation(source_key, {
                        "agent_status": "已离线",
                        "message": f"代理 {agent_code} 离线，已超时 {int(now - last_hb)}秒",
                        "source_key": source_key,
                    })
                    offline_item["alert_triggered"] = True
                except Exception:
                    offline_item["alert_triggered"] = False

                offline.append(offline_item)
        return offline

    def get_agent_health(self, agent_code: str) -> dict[str, Any]:
        """获取单个代理的健康状态。"""
        cp = self._read_checkpoint(f"agent_{agent_code}")
        entry = None
        for a in store.rows(self.MODULE):
            if a.get("代理编号") == agent_code:
                entry = a
                break
        if entry is None:
            return {"agent_code": agent_code, "status": "未注册"}
        return {
            "agent_code": agent_code,
            "status": entry["status"],
            "host": entry.get("部署主机"),
            "last_heartbeat": entry.get("上次心跳"),
            "collected": entry.get("采集量"),
            "error_rate": entry.get("错误率"),
        }