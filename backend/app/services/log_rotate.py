"""日志轮转：管理日志文件的轮转策略、归档和清理。"""
from __future__ import annotations

import time
import shutil
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class LogRotateService(BaseModuleService):
    MODULE = "log_rotate"
    ENTITY = "轮转策略"
    OPERATION_TERM = "归档"
    LIST_FIELDS = ["策略编号", "关联源", "轮转间隔", "归档路径", "压缩格式", "保留天数", "清理策略", "策略版本"]
    REQUIRED_FIELDS = ["策略编号", "关联源", "轮转间隔"]
    STATUS_ORDER = ["草稿", "待校验", "已生效", "已停用"]
    ACTION_RULES = {"提交校验": "待校验", "标记生效": "已生效", "停用策略": "已停用"}
    NEGATIVE_ACTIONS = ["停用策略"]

    def rotate_source(self, source_key: str) -> dict[str, Any]:
        """对指定日志源执行轮转（将当前日志归档）。"""
        policies = [p for p in store.rows(self.MODULE) if p.get("关联源") == source_key and p.get("status") == "已生效"]
        if not policies:
            return {"source_key": source_key, "rotated": False, "reason": "无生效轮转策略"}

        log_file = self._data_dir("logs") / f"{source_key}.jsonl"
        if not log_file.exists():
            return {"source_key": source_key, "rotated": False, "reason": "无日志文件"}

        policy = policies[0]
        ts = time.strftime("%Y%m%d_%H%M%S")
        archive_dir = self._data_dir("logs") / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_file = archive_dir / f"{source_key}_{ts}.jsonl"

        shutil.copy2(log_file, archive_file)
        # 清空原文件（保留空文件作为壳）
        log_file.write_text("")

        self._write_jsonl(self._data_dir("logs") / "rotate_history.jsonl", [{
            "source_key": source_key, "action": "rotate",
            "archive_file": str(archive_file), "policy_id": policy["策略编号"], "at": time.time(),
        }])

        self._write_checkpoint(f"rotate_{source_key}", {
            "last_rotated_at": time.time(),
            "archive_file": str(archive_file),
            "policy_id": policy["策略编号"],
        })

        return {
            "source_key": source_key,
            "rotated": True,
            "archive_file": str(archive_file),
            "policy_id": policy["策略编号"],
        }

    def cleanup_expired(self) -> dict[str, Any]:
        """清理超过保留天数的归档日志。"""
        cleaned = []
        total_removed = 0
        for policy in store.rows(self.MODULE):
            if policy.get("status") != "已生效":
                continue
            retention = int(policy.get("保留天数", "30"))
            archive_dir = self._data_dir("logs") / "archive"
            if not archive_dir.exists():
                continue
            source_key = policy["关联源"]
            cutoff = time.time() - retention * 86400
            for f in archive_dir.glob(f"{source_key}_*"):
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    cleaned.append({"file": str(f), "policy_id": policy["策略编号"], "age_days": retention})
                    total_removed += 1

        return {"cleaned_files": len(cleaned), "details": cleaned}

    def get_rotate_history(self, source_key: str) -> list[dict[str, Any]]:
        history = self._read_jsonl(self._data_dir("logs") / "rotate_history.jsonl")
        return [h for h in history if h.get("source_key") == source_key]