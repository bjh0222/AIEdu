"""上下文答疑服务。"""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..llm.provider import OpenAICompatibleProvider
from ..llm.service import AIService, AIServiceError
from ..models import ChatMessage, LearningGoal, Tutorial
from ..v2.contracts import DomainError
from ..v2.model_roles import for_role
from . import goals as goals_service
from .utils import goal_dict, jload


def _bind_chat_role(ai: AIService) -> str:
    """Bind the explicit reader-chat workflow choice, then the model-role fallback."""
    try:
        profile = for_role("chat")
        ai.provider = OpenAICompatibleProvider(profile)
        ai.role_profile_id = getattr(profile, "llm_profile_id", None)
        return str(ai.role_profile_id or "chat")
    except DomainError as exc:
        if exc.code == "MODEL_ROLE_UNCONFIGURED":
            raise HTTPException(status_code=422, detail={
                "code": "MODEL_ROLE_INVALID", "message": "聊天模型配置不存在",
            }) from exc
        ai.provider = None
        raise HTTPException(status_code=422, detail={
            "code": "MODEL_ROLE_INVALID", "message": "聊天模型配置不可用",
        }) from exc


def chat(db: Session, payload, ai: AIService) -> dict:
    goal = None
    context: dict = {}
    if payload.goal_id:
        goal = db.get(LearningGoal, payload.goal_id)
        if goal is None:
            raise HTTPException(status_code=404, detail={"code": "goal_not_found", "message": "学习目标不存在"})
        context["goal"] = goal_dict(goal)
        criteria = goals_service.confirmed_criteria(db, goal.id)
        context["criteria"] = [
            {"id": c["id"], "statement": c["statement"], "pass_rule": c["pass_rule"]} for c in criteria
        ]

    if payload.tutorial_id:
        tutorial = db.get(Tutorial, payload.tutorial_id)
        if tutorial:
            context["tutorial_title"] = tutorial.title
            if tutorial.current_version_id:
                from ..models import TutorialVersion

                version = db.get(TutorialVersion, tutorial.current_version_id)
                if version:
                    content = jload(version.content_json, {})
                    sections = content.get("sections", [])
                    context["sections"] = [s.get("title", "") for s in sections][:20]
                    if payload.section_id:
                        for s in sections:
                            if s.get("id") == payload.section_id:
                                context["section_title"] = s.get("title", "")
                                context["section_summary"] = s.get("goal_statement", "")
                                break

    # Reader chat sends the immutable version content that the user is viewing.
    # The model provider is called only through AIService, whose accounting is reused.
    context["reader_chat"] = {
        "task_id": payload.task_id,
        "artifact_id": payload.artifact_id,
        "scope": payload.context_scope,
        "units": [
            {"unit_id": u.unit_id, "title": u.title, "body": u.body}
            for u in payload.context_units
        ],
        "selected_quote": payload.selected_quote,
    }
    chat_goal = goal or SimpleNamespace(
        title="学习答疑", motivation="", application_context="", current_level="", time_budget=""
    )
    # 让 token 日志 / 训练样本能关联到目标、教程与章节
    ai.goal_id = payload.goal_id
    ai.tutorial_id = payload.artifact_id or payload.tutorial_id
    ai.section_id = payload.section_id or (payload.context_units[0].unit_id if payload.context_units else None)
    ai.task_id = payload.task_id
    _bind_chat_role(ai)
    try:
        data = ai.chat(chat_goal, payload.question, context)
    except AIServiceError as exc:
        raise HTTPException(status_code=502, detail={"code": "llm_error", "message": str(exc)}) from exc

    message_section_id = payload.section_id or (
        payload.context_units[0].unit_id if payload.context_units else None
    )
    message_tutorial_id = payload.artifact_id or payload.tutorial_id
    user_msg = ChatMessage(
        goal_id=payload.goal_id,
        tutorial_id=message_tutorial_id,
        task_id=payload.task_id,
        section_id=message_section_id,
        role="user",
        content=payload.question,
    )
    db.add(user_msg)
    assistant_msg = ChatMessage(
        goal_id=payload.goal_id,
        tutorial_id=message_tutorial_id,
        task_id=payload.task_id,
        section_id=message_section_id,
        role="assistant",
        content=str(data.get("answer", "")),
    )
    db.add(assistant_msg)
    db.commit()
    return {
        "user": {"id": user_msg.id, "role": "user", "content": user_msg.content, "created_at": user_msg.created_at},
        "assistant": {
            "id": assistant_msg.id,
            "role": "assistant",
            "content": assistant_msg.content,
            "created_at": assistant_msg.created_at,
        },
        "demo": ai.is_demo,
        "model_call": None if ai.is_demo else {
            "provider": "openai-compatible",
            "model": ai.provider.model if ai.provider else "",
            "profile_id": getattr(ai, "role_profile_id", None),
            "workflow_role": "chat",
            "usage": dict(ai.usage_last),
        },
    }





