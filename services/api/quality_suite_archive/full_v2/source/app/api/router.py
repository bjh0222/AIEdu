"""路由聚合。"""
from __future__ import annotations

from fastapi import APIRouter
from . import v2,v2_assets,v2_insights,v2_management,v2_import_profiles,v2_optimization,v2_research,v2_synthesis

from . import chat, format_specs, goals, health, io, knowledge, materials, practice, proposition, proposition_knowledge, training, tutorials, model_settings

api_router = APIRouter(prefix="/api")
api_router.include_router(v2.router)
api_router.include_router(v2_assets.router)
api_router.include_router(v2_insights.router)
api_router.include_router(v2_management.router)
api_router.include_router(v2_import_profiles.router)
api_router.include_router(v2_optimization.router)
api_router.include_router(v2_research.router)
api_router.include_router(v2_synthesis.router)

api_router.include_router(health.router)
api_router.include_router(format_specs.router)
api_router.include_router(model_settings.router)
api_router.include_router(goals.router)
api_router.include_router(materials.router)
api_router.include_router(tutorials.router)
api_router.include_router(practice.router)
api_router.include_router(knowledge.router)
api_router.include_router(proposition.router)
api_router.include_router(proposition_knowledge.router)
api_router.include_router(chat.router)
api_router.include_router(training.router)
api_router.include_router(io.router)
