"""业务路由汇总：专用领域路由在前注册，避免被通用 /api/{module} 动态路径抢先匹配。"""
from __future__ import annotations

from app.routers import dimension_attestation
from app.routers import resource

ROUTERS = [dimension_attestation, resource]
