"""领域服务注册表：通用资源路由按模块 key 分派到独立业务服务。"""
from __future__ import annotations

from app.services.base import BaseModuleService
from app.services.log_source import LogSourceService
from app.services.log_parser import LogParserService
from app.services.event_tracker import EventTrackerService
from app.services.stream_buffer import StreamBufferService
from app.services.field_mapping import FieldMappingService
from app.services.agent_heartbeat import AgentHeartbeatService
from app.services.event_validate import EventValidateService
from app.services.log_rotate import LogRotateService
from app.services.enrich_join import EnrichJoinService
from app.services.alert_rule import AlertRuleService
from app.services.log_partition import LogPartitionService
from app.services.collect_checkpoint import CollectCheckpointService
from app.services.dedup_window import DedupWindowService
from app.services.aggregate_olap import AggregateOlapService
from app.services.format_output import FormatOutputService
from app.services.backfill_range import BackfillRangeService
from app.services.sampling_rule import SamplingRuleService
from app.services.log_quality import LogQualityService

SERVICES: dict[str, BaseModuleService] = {
    "log_source": LogSourceService(),
    "log_parser": LogParserService(),
    "event_tracker": EventTrackerService(),
    "stream_buffer": StreamBufferService(),
    "field_mapping": FieldMappingService(),
    "agent_heartbeat": AgentHeartbeatService(),
    "event_validate": EventValidateService(),
    "log_rotate": LogRotateService(),
    "enrich_join": EnrichJoinService(),
    "alert_rule": AlertRuleService(),
    "log_partition": LogPartitionService(),
    "collect_checkpoint": CollectCheckpointService(),
    "dedup_window": DedupWindowService(),
    "aggregate_olap": AggregateOlapService(),
    "format_output": FormatOutputService(),
    "backfill_range": BackfillRangeService(),
    "sampling_rule": SamplingRuleService(),
    "log_quality": LogQualityService(),
}


def service_for(module: str) -> BaseModuleService | None:
    return SERVICES.get(module)
