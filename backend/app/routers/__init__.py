"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import attribution, resource

# 固定前缀的专用路由要先于 /api/{module} 通用路由注册。
ROUTERS = [attribution, resource]
