"""SQLAlchemy 数据模型（v2.0 §4 的子集，v1 闭环所需）。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, Float, ForeignKey, Index, Integer, String, Text, text

from .db import Base

# 能力层级默认值（learning.levels.DEFAULT_LEVEL），直接用字符串避免循环导入
CAPABILITY_DEFAULT = "L2"


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at = Column(String(40), default=lambda: _now().isoformat())
    updated_at = Column(String(40), default=lambda: _now().isoformat(), onupdate=lambda: _now().isoformat())


class LearningGoal(Base, TimestampMixin):
    __tablename__ = "learning_goals"
    __table_args__ = (Index("ix_learning_goals_updated_id", "updated_at", "id"),)

    id = Column(String(36), primary_key=True, default=_uuid)
    title = Column(String(200), nullable=False)
    domain_key = Column(String(50), nullable=False, default="computer_cli")
    branch = Column(String(100), default="")
    motivation = Column(Text, default="")
    application_context = Column(Text, default="")
    current_level = Column(Text, default="")
    time_budget = Column(String(100), default="")
    # 用户针对本次目标填写的硬性约束：注入所有生成阶段 prompt，违反任一条即视为交付不合格
    hard_constraints = Column(Text, default="")
    # 难度期望："" 默认（有参考时整体高于参考）/ basic 基础达标 / equal 与参考相当 /
    # harder 明显更难 / explicit 自定义（配合 difficulty_note）。空值 = 沿用默认策略。
    difficulty_mode = Column(String(20), default="")
    # 难度要求的自由文本说明（difficulty_mode=explicit 时为用户原话；可单独补充说明）
    difficulty_note = Column(Text, default="")
    # 能力层级：L1 基础理解 / L2 独立应用 / L3 深入掌握与迁移（旧数据回退 L2）
    capability_level = Column(String(10), default=CAPABILITY_DEFAULT)
    # 是否生成独立的额外练习文件（默认关闭；教程内的必要练习不受此开关影响）
    generate_extra_exercises = Column(Boolean, default=False)
    # 生成挡位：fast / standard / deep；旧数据回退 standard
    generation_tier = Column(String(20), default="standard")
    # 大纲前选定并冻结的格式规范；后台任务只读快照，不重读可变模板
    format_spec_id = Column(String(36), default="", index=True)
    format_spec_source = Column(String(30), default="")
    format_spec_match_note = Column(Text, default="")
    format_spec_snapshot = Column(Text, default="")
    status = Column(String(30), default="goal_definition")
    user_notes = Column(Text, default="")
    archived = Column(Boolean, default=False)
    completed = Column(Boolean, default=False)
    completed_at = Column(String(40), default="")
    input_revision = Column(Integer, default=0)
    task_state_json = Column(Text, default="{}")
    creation_key = Column(String(128), nullable=True, unique=True)
    request_hash = Column(String(64), default="")


class FormatSpec(Base, TimestampMixin):
    """可复用交付规范：结构化字段与自由 Markdown 补充分开保存。"""

    __tablename__ = "format_specs"
    __table_args__ = (
        Index(
            "uq_format_specs_default",
            "is_default",
            unique=True,
            sqlite_where=text("is_default = 1"),
        ),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    name = Column(String(120), nullable=False)
    description = Column(Text, default="")
    scene = Column(String(80), default="")
    is_builtin = Column(Boolean, default=False)
    is_default = Column(Boolean, default=False)
    version = Column(Integer, default=1)

    applicable_tasks = Column(Text, default="")
    applicable_intents = Column(Text, default="[]")
    excluded_scenarios = Column(Text, default="[]")
    excluded_intents = Column(Text, default="[]")
    trigger_keywords = Column(Text, default="[]")
    excluded_keywords = Column(Text, default="[]")
    allow_keyword_trigger = Column(Boolean, default=True)
    allow_semantic_recommendation = Column(Boolean, default=True)
    allow_explicit_selection = Column(Boolean, default=True)

    target_reader = Column(Text, default="")
    output_goal = Column(Text, default="")
    deliverables = Column(Text, default="[]")
    content_structure = Column(Text, default="")
    format_rules = Column(Text, default="")
    required_rules = Column(Text, default="")
    forbidden_rules = Column(Text, default="")
    suggested_rules = Column(Text, default="")
    flexible_rules = Column(Text, default="")
    acceptance_criteria = Column(Text, default="")
    reference_examples = Column(Text, default="")
    custom_markdown = Column(Text, default="")


class GoalAssessment(Base, TimestampMixin):
    __tablename__ = "goal_assessments"

    id = Column(String(36), primary_key=True, default=_uuid)
    goal_id = Column(String(36), ForeignKey("learning_goals.id"), index=True)
    benefits = Column(Text, default="")
    prerequisites = Column(Text, default="")
    alternatives = Column(Text, default="")
    assumptions = Column(Text, default="")
    uncertainties = Column(Text, default="")
    recommendation = Column(Text, default="")
    user_decision = Column(String(30), default="")
    decision_reason = Column(Text, default="")


class LearningPlan(Base, TimestampMixin):
    __tablename__ = "learning_plans"

    id = Column(String(36), primary_key=True, default=_uuid)
    goal_id = Column(String(36), ForeignKey("learning_goals.id"), index=True)
    version = Column(Integer, default=1)
    status = Column(String(30), default="draft")
    generated_at = Column(String(40), default="")
    user_edits = Column(Text, default="")


class PlanStep(Base, TimestampMixin):
    __tablename__ = "plan_steps"

    id = Column(String(36), primary_key=True, default=_uuid)
    plan_id = Column(String(36), ForeignKey("learning_plans.id"), index=True)
    order_index = Column(Integer, default=0)
    title = Column(String(200), default="")
    objective = Column(Text, default="")
    status = Column(String(30), default="pending")
    dependencies = Column(Text, default="[]")  # JSON list
    estimated_effort = Column(String(100), default="")
    completion_evidence_refs = Column(Text, default="[]")  # JSON list


class AcceptanceCriterion(Base, TimestampMixin):
    __tablename__ = "acceptance_criteria"

    id = Column(String(36), primary_key=True, default=_uuid)
    goal_id = Column(String(36), ForeignKey("learning_goals.id"), index=True)
    plan_step_id = Column(String(36), ForeignKey("plan_steps.id"), nullable=True)
    statement = Column(Text, nullable=False)
    conditions = Column(Text, default="")
    evidence_type = Column(String(50), default="product")
    pass_rule = Column(Text, default="")
    status = Column(String(30), default="pending")  # pending / confirmed / rejected
    rationale = Column(Text, default="")


class Tutorial(Base, TimestampMixin):
    __tablename__ = "tutorials"

    id = Column(String(36), primary_key=True, default=_uuid)
    goal_id = Column(String(36), ForeignKey("learning_goals.id"), index=True)
    plan_step_id = Column(String(36), ForeignKey("plan_steps.id"), nullable=True)
    title = Column(String(200), default="")
    status = Column(String(30), default="outline")  # outline / drafted / reviewed
    current_version_id = Column(String(36), nullable=True)
    # 生成状态在 Tutorial / TutorialVersion / GenerationJob；以下只记录单向交付结果
    delivery_status = Column(String(20), default="")
    delivery_path = Column(Text, default="")
    delivery_version = Column(Integer, default=0)


class TutorialVersion(Base):
    __tablename__ = "tutorial_versions"
    __table_args__ = (Index("ix_tutorial_versions_tutorial_version", "tutorial_id", "version"),)

    id = Column(String(36), primary_key=True, default=_uuid)
    tutorial_id = Column(String(36), ForeignKey("tutorials.id"), index=True)
    version = Column(Integer, default=1)
    content_json = Column(Text, default="{}")  # {outline:[], sections:[]}
    prompt_version = Column(String(30), default="v1")
    model_provider = Column(String(50), default="demo")
    model_name = Column(String(100), default="demo")
    quality_report = Column(Text, default="{}")  # JSON
    source_refs = Column(Text, default="[]")
    # 生成本版本消耗的 token（真实 usage 或按字符估算，见 usage_source）
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    usage_source = Column(String(20), default="")
    created_at = Column(String(40), default=lambda: _now().isoformat())
    parent_version_id = Column(String(36), nullable=True)
    run_id = Column(String(64), nullable=True, index=True)
    content_hash = Column(String(64), default="")
    quality_hash = Column(String(64), default="")
    schema_version = Column(String(40), default="generation.v1")
    operation_id = Column(String(128), nullable=True, unique=True)


class PracticeTask(Base, TimestampMixin):
    __tablename__ = "practice_tasks"

    id = Column(String(36), primary_key=True, default=_uuid)
    goal_id = Column(String(36), ForeignKey("learning_goals.id"), index=True)
    plan_step_id = Column(String(36), ForeignKey("plan_steps.id"), nullable=True)
    tutorial_id = Column(String(36), ForeignKey("tutorials.id"), nullable=True)
    title = Column(String(200), default="")
    instructions = Column(Text, default="")
    task_type = Column(String(50), default="practice")
    expected_artifact = Column(Text, default="")
    validation_rule = Column(Text, default="")
    status = Column(String(30), default="todo")  # todo / doing / done


class Attempt(Base):
    __tablename__ = "attempts"

    id = Column(String(36), primary_key=True, default=_uuid)
    task_id = Column(String(36), ForeignKey("practice_tasks.id"), index=True)
    artifact = Column(Text, default="")
    artifact_type = Column(String(50), default="text")
    result = Column(Text, default="")
    errors = Column(Text, default="")
    help_level = Column(String(10), default="A1")  # A0-A3
    user_explanation = Column(Text, default="")
    feedback = Column(Text, default="{}")  # JSON
    created_at = Column(String(40), default=lambda: _now().isoformat())


class Assessment(Base):
    __tablename__ = "assessments"

    id = Column(String(36), primary_key=True, default=_uuid)
    goal_id = Column(String(36), ForeignKey("learning_goals.id"), index=True)
    task_id = Column(String(36), ForeignKey("practice_tasks.id"), nullable=True)
    assessment_type = Column(String(30), default="mixed")  # independent / variant / mixed
    rubric = Column(Text, default="[]")  # JSON
    result = Column(Text, default="")
    confidence = Column(String(20), default="")
    evidence_refs = Column(Text, default="[]")
    weak_points = Column(Text, default="")
    created_at = Column(String(40), default=lambda: _now().isoformat())


class Reflection(Base):
    __tablename__ = "reflections"

    id = Column(String(36), primary_key=True, default=_uuid)
    goal_id = Column(String(36), ForeignKey("learning_goals.id"), index=True)
    summary = Column(Text, default="")
    learned_capabilities = Column(Text, default="")
    unresolved_gaps = Column(Text, default="")
    next_actions = Column(Text, default="")
    evidence_refs = Column(Text, default="[]")
    created_at = Column(String(40), default=lambda: _now().isoformat())


class Concept(Base, TimestampMixin):
    __tablename__ = "concepts"

    id = Column(String(36), primary_key=True, default=_uuid)
    domain_key = Column(String(50), default="general")
    title = Column(String(200), nullable=False)
    # 去重键：规范化标题（小写、去空白与标点），同名/别名视为同一概念
    title_norm = Column(String(240), default="", index=True)
    # 别名 JSON list：跨学科概念在不同领域可能有不同叫法，检索/去重时合并
    aliases = Column(Text, default="[]")
    # 多对多领域：JSON list；domain_key 保留主领域（第一个），兼容旧数据
    domain_keys = Column(Text, default="[]")
    # 服务的学习目标 JSON list：一个概念可同时服务于多个目标
    goal_ids = Column(Text, default="[]")
    definition = Column(Text, default="")
    status = Column(String(20), default="active")  # active / archived
    source_refs = Column(Text, default="[]")
    last_reviewed_at = Column(String(40), default="")


class ConceptRelation(Base, TimestampMixin):
    __tablename__ = "concept_relations"

    id = Column(String(36), primary_key=True, default=_uuid)
    from_concept_id = Column(String(36), ForeignKey("concepts.id"), index=True)
    to_concept_id = Column(String(36), ForeignKey("concepts.id"), index=True)
    relation_type = Column(String(30), default="related")
    # proposed(待核实) / confirmed(已确认) / conflict(存在冲突) / rejected(已驳回) / revoked(已撤销)
    status = Column(String(20), default="proposed")
    rationale = Column(Text, default="")
    source_refs = Column(Text, default="[]")
    evidence_refs = Column(Text, default="[]")
    confidence = Column(Float, default=0.5)
    # manual / tutorial / ai_proposal：关系来源，决定默认可信状态
    origin = Column(String(20), default="manual")
    # 变更历史 JSON list：[{at, action, note}]，重要修改可追溯
    history = Column(Text, default="[]")


class GoalMaterial(Base, TimestampMixin):
    """学习目标下用户上传的参考资料 / 例题来源。

    文件本体落在 MATERIALS_DIR/{goal_id}/{id}{ext}，这里只存元数据与正文摘录，
    摘录供「价值诊断 / 学习计划 / 教程生成」的 prompt 注入使用。
    """

    __tablename__ = "goal_materials"

    id = Column(String(36), primary_key=True, default=_uuid)
    goal_id = Column(String(36), ForeignKey("learning_goals.id"), index=True)
    kind = Column(String(20), default="reference")  # reference / example_source
    filename = Column(String(255), default="")
    stored_name = Column(String(255), default="")
    ext = Column(String(20), default="")
    content_type = Column(String(120), default="")
    size = Column(Integer, default=0)
    status = Column(String(20), default="ready")  # ready / failed
    error_message = Column(Text, default="")
    # ok / empty / unsupported / failed / truncated（truncated = 原文超过入库上限被截断）
    extract_status = Column(String(20), default="pending")
    text_excerpt = Column(Text, default="")
    # 实际入库 / 实际注入的字数（可能被 MAX_EXCERPT_CHARS 截断）
    char_count = Column(Integer, default=0)
    # 文件原文字数（未截断）。source_char_count > char_count 即为被截断，
    # 旧库由 _migrate_sqlite_columns 自动补列，默认 0 表示「未知 / 未截断」。
    source_char_count = Column(Integer, default=0)
    note = Column(Text, default="")
    raw_hash = Column(String(64), default="")
    extracted_hash = Column(String(64), default="")
    extractor_version = Column(String(80), default="")
    manifest_json = Column(Text, default="{}")


class Source(Base):
    __tablename__ = "sources"

    id = Column(String(36), primary_key=True, default=_uuid)
    title = Column(String(200), default="")
    url = Column(String(500), default="")
    source_type = Column(String(50), default="web")
    publisher = Column(String(100), default="")
    reliability_note = Column(Text, default="")
    accessed_at = Column(String(40), default="")


class KnowledgeProposal(Base, TimestampMixin):
    __tablename__ = "knowledge_proposals"

    id = Column(String(36), primary_key=True, default=_uuid)
    proposal_type = Column(String(30), default="relation")  # concept / relation / status_update
    payload = Column(Text, default="{}")  # JSON
    rationale = Column(Text, default="")
    evidence_refs = Column(Text, default="[]")
    status = Column(String(20), default="pending")  # pending / confirmed / rejected / edited
    reviewed_at = Column(String(40), default="")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(String(36), primary_key=True, default=_uuid)
    goal_id = Column(String(36), ForeignKey("learning_goals.id"), nullable=True, index=True)
    tutorial_id = Column(String(36), nullable=True)
    section_id = Column(String(36), nullable=True)
    task_id = Column(String(36), nullable=True)
    role = Column(String(20), default="user")  # user / assistant
    content = Column(Text, default="")
    created_at = Column(String(40), default=lambda: _now().isoformat())


class GenerationJob(Base):
    __tablename__ = "generation_jobs"
    __table_args__ = (Index("ix_generation_jobs_goal_created", "goal_id", "created_at"),
                      Index("ix_generation_jobs_updated_id", "updated_at", "id"),)

    id = Column(String(36), primary_key=True, default=_uuid)
    goal_id = Column(String(36), ForeignKey("learning_goals.id"), index=True)
    job_type = Column(String(50), default="tutorial")
    status = Column(String(20), default="queued")  # queued / running / done / failed / interrupted
    progress = Column(Integer, default=0)
    stage = Column(String(200), default="")
    error_code = Column(String(50), default="")
    error_message = Column(Text, default="")
    retry_count = Column(Integer, default=0)
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    usage_source = Column(String(20), default="")
    # 任务书 04 / ADR-027：运行编排——run 归属、父子任务与真实阶段状态。
    # 老库加列/索引走 db.py 的 _migrate_sqlite_columns / _migrate_sqlite_indexes。
    run_id = Column(String(64), nullable=True, index=True)
    parent_job_id = Column(String(64), nullable=True, index=True)
    root_job_id = Column(String(64), nullable=True, index=True)
    state_revision = Column(Integer, default=0)
    cancel_epoch = Column(Integer, default=0)
    worker_instance_id = Column(String(64), default="")
    lease_until = Column(Float, default=0)
    action_key = Column(String(128), nullable=True, unique=True)
    request_hash = Column(String(64), default="")
    state_json = Column(Text, default="{}")  # stage/unit/progress/checkpoint（JSON，只增不删）
    created_at = Column(String(40), default=lambda: _now().isoformat())
    updated_at = Column(String(40), default=lambda: _now().isoformat(), onupdate=lambda: _now().isoformat())


class ModelCallLog(Base):
    __tablename__ = "model_call_logs"

    id = Column(String(36), primary_key=True, default=_uuid)
    feature = Column(String(50), default="")
    provider = Column(String(50), default="")
    model = Column(String(100), default="")
    goal_id = Column(String(36), nullable=True, index=True)
    tutorial_id = Column(String(36), nullable=True, index=True)
    job_id = Column(String(36), nullable=True)
    # 任务书 04 / ADR-027：调用边界记账。call_id 在 _real_json/_real_text 进入
    # provider 前生成；started_at/finished_at 是真实调用起止（created_at 保留历史
    # 语义=记录时间）；失败/超时/取消也落一行（outcome != ok），不产生训练样本。
    call_id = Column(String(64), nullable=True, index=True)
    run_id = Column(String(64), nullable=True, index=True)
    unit_id = Column(String(64), nullable=True)
    attempt = Column(Integer, default=1)  # provider 内部重试各记一行，从 1 计
    started_at = Column(String(40), default="")
    finished_at = Column(String(40), default="")
    duration_ms = Column(Integer, default=0)
    outcome = Column(String(20), default="")  # ok / error / timeout / cancelled
    latency_ms = Column(Integer, default=0)
    status = Column(String(20), default="ok")
    error_category = Column(String(50), default="")
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    usage_source = Column(String(20), default="")  # provider / estimated
    # 同 TrainingRecord：goal_usage 按 created_at 聚合，建索引避免磁盘 TEMP B-TREE
    created_at = Column(String(40), default=lambda: _now().isoformat(), index=True)


class TrainingRecord(Base):
    """测试版训练数据：生成材料（material）与用户反馈（feedback）。"""

    __tablename__ = "training_records"

    id = Column(String(36), primary_key=True, default=_uuid)
    source_type = Column(String(20), default="material", index=True)  # material / feedback
    feature = Column(String(50), default="")  # diagnose / plan / criteria / outline / section / ...
    goal_id = Column(String(36), nullable=True, index=True)
    tutorial_id = Column(String(36), nullable=True, index=True)
    section_id = Column(String(36), nullable=True)
    system_prompt = Column(Text, default="")
    user_prompt = Column(Text, default="")
    output_text = Column(Text, default="")
    model_provider = Column(String(50), default="")
    model_name = Column(String(100), default="")
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    usage_source = Column(String(20), default="")
    rating = Column(Integer, nullable=True)  # feedback: 1 有帮助 / -1 需改进
    tags = Column(Text, default="[]")
    comment = Column(Text, default="")
    context_json = Column(Text, default="{}")
    # 排序键必须建索引：训练数据列表 / 导出都按 created_at 排序，而 system_prompt /
    # output_text 单行就有几万字。没有索引时 SQLite 会把整表连同大字段一起塞进
    # 磁盘 TEMP B-TREE 再排序，一旦临时文件目录不可写就整条查询 500
    # （sqlite3.OperationalError: unable to open database file）。有索引则走索引序，
    # 不产生临时文件，分页也从「全表排序」变成 O(limit)。
    created_at = Column(String(40), default=lambda: _now().isoformat(), index=True)


# ============================================================ 命题知识库（P2）
#
# 设计依据（见架构调研汇报）：AIG 的 item model（radicals / incidentals / isomorphs，
# 起源于 Bormuth 1960s）+ 变式教学理论（顾泠沅：概念性变式 / 过程性变式）+ 复用
# KnowledgeProposal 的三态 review 语义（proposed / confirmed / rejected，ADR-010）。
#
# 为什么 ItemModel 与 ItemInstance 分表（这是本设计的核心，不是规范化洁癖）：
# 实测那份微积分卷里，同一母题 `x=ln(t+sqrt(1+t^2)), y=arctan t` 出现 5 次、给出
# 4 个不同答案（sqrt2 / sqrt2/2 / -2 / sqrt2），`f_n=x^n+2nx-2` 出现 4 次给出
# e / e^2 / e^-1 三个答案。根因是每次都**独立重新生成**。分表后，决定答案结构的
# radicals 只在 ItemModel 存一份，同构变式（isomorph）的答案由 solution_template
# 代入新的 incidentals **机械求解**得到，从架构上消除「同母题答案漂移」。
#
# 项目规范一贯禁止溯源括注（内置试卷生成规范 forbidden_rules：「禁止标注题目来源、
# 原题编号，或（原题…）（对应第 N 题的变式…）等溯源括注」），所以变式↔原题的对应
# 关系**不能写在输出 Markdown 里，只能存在 ItemInstance.item_model_id**。
# 注：曾有一份「变式与综合题汇编规范」（data/spec_variant_v2.json）已于 2026-10-31
# 停用，数据库中已无此规范；本模块不依赖它，radicals/incidentals 的区分来自 AIG 与
# 变式教学理论本身，不来自任何特定规范。


class ItemModel(Base, TimestampMixin):
    """题目模型（母题）：一组同构题共享的结构，是「模仿能力」的载体。"""

    __tablename__ = "item_models"

    id = Column(String(36), primary_key=True, default=_uuid)
    domain_key = Column(String(50), default="general", index=True)
    title = Column(String(200), default="")          # 母题名，如「参数方程 x=ln(t+√(1+t²)),y=arctan t 求导」
    chapter = Column(String(200), default="")        # 所属章节（逐字取自材料）
    knowledge_points = Column(Text, default="[]")    # JSON list[str]
    # radicals：决定难度与认知要求的结构元素（AIG 术语）。exam_check._definition_index
    # 抽出的定义式指纹就是 radicals 的自动来源。
    radicals = Column(Text, default="{}")            # JSON dict
    # incidentals：可变表面特征的**槽位定义**（不是具体值），如 {"参数":"n","情境":"数列极限"}
    incidentals = Column(Text, default="{}")         # JSON dict
    # solution_template：参数化解法（sympy 表达式 + 步骤骨架）。同构变式据此机械求答案。
    solution_template = Column(Text, default="")
    answer_expr = Column(Text, default="")           # 答案的参数化表达式（sympy 可 evalf）
    difficulty = Column(String(20), default="")      # 难度档：basic/intermediate/advanced 或自由文本
    # provenance：manual（用户上传典型题）/ ai_constructed（AI 构造，无法核实出处）/ material（材料原题）
    provenance = Column(String(30), default="manual")
    status = Column(String(20), default="draft")     # draft / confirmed / archived
    source_ref = Column(Text, default="")            # 来源出处（材料名/页码/URL，可空）
    # 人工批注原文（逐字保留）：用户上传典型卷时的「为什么好/难在哪」手写经验。
    # 是 AI 提炼命题规律的原始素材——只增不覆盖（重复抽取同一卷不会冲掉已有批注）。
    human_note = Column(Text, default="")


class ItemInstance(Base, TimestampMixin):
    """同构题实例：ItemModel 代入一组具体 incidentals 后的一道题。"""

    __tablename__ = "item_instances"

    id = Column(String(36), primary_key=True, default=_uuid)
    item_model_id = Column(String(36), ForeignKey("item_models.id"), index=True)
    incidental_values = Column(Text, default="{}")   # JSON dict：本次实例化的参数值
    stem = Column(Text, default="")                  # 题面 Markdown
    answer = Column(Text, default="")                # 答案（同构变式由 solution_template 机械求解）
    # variation_type：isomorph（同构，radicals 不变）/ procedural（过程性，放松条件、难度递减）
    # / conceptual（概念性，故意改 radicals，含反例/非例）。三类验证方式不同，见 proposition.py。
    variation_type = Column(String(20), default="isomorph")
    # verified_by：tool_symbolic / tool_executable / model / human / none
    verified_by = Column(String(30), default="none")
    verified_at = Column(String(40), default="")
    source_tutorial_id = Column(String(36), nullable=True, index=True)


class DesignPrinciple(Base, TimestampMixin):
    """命题规律：从 QualityRecord / 典型题分析提炼出的可迁移经验。

    proposed 是候选，confirmed 是人工确认；新增 auto_accepted 表示满足独立家族
    证据门槛的自动采纳，不冒充人工核实。rejected / 撤回历史保留，不自动复活。
    """

    __tablename__ = "design_principles"

    id = Column(String(36), primary_key=True, default=_uuid)
    domain_key = Column(String(50), default="general", index=True)
    statement = Column(Text, default="")             # 规律文本（注入提示词的就是它）
    principle_type = Column(String(20), default="discipline")  # general（跨学科）/ discipline（学科）
    scope_knowledge_points = Column(Text, default="[]")        # JSON list[str]；空=全学科通用
    positive_refs = Column(Text, default="[]")       # JSON list：正例来源（QualityRecord.id / ItemModel.id）
    negative_refs = Column(Text, default="[]")       # JSON list：反例来源
    evidence_count = Column(Integer, default=0)      # 支撑证据数（达阈值才建议 confirm）
    status = Column(String(20), default="proposed")  # proposed / auto_accepted / confirmed / rejected
    rationale = Column(Text, default="")
    reviewed_at = Column(String(40), default="")
    version = Column(Integer, default=1)
    title = Column(String(200), default="")
    mechanism = Column(Text, default="")
    applicability = Column(Text, default="")
    counterexamples = Column(Text, default="[]")
    guidance = Column(Text, default="")
    scope = Column(Text, default="{}")
    confidence = Column(Float, default=0.0)
    approval_source = Column(String(30), default="")
    framework_id = Column(String(36), nullable=True, index=True)
    evidence_family_count = Column(Integer, default=0)
    __table_args__ = (Index("ix_principle_rank", "domain_key", "status", "evidence_count", "created_at"),)


class QualityRecord(Base):
    """质量记录：L0（exam_check）/ L1（exam_verify）/ Tier-1（tool_verify）发现的缺陷回流。

    是 DesignPrinciple 的冷启动素材——不需要用户先上传典型题，系统生成时发现的
    「答案算错」「选项不完备」「同题多解」自动沉淀于此，跨版本累积。
    """

    __tablename__ = "quality_records"

    id = Column(String(36), primary_key=True, default=_uuid)
    domain_key = Column(String(50), default="general", index=True)
    goal_id = Column(String(36), nullable=True, index=True)
    tutorial_id = Column(String(36), nullable=True, index=True)
    version = Column(Integer, default=0)
    # source：l0_account / l0_cross / l1_defect / l1_fixed / tool_refuted / tool_undecidable
    source = Column(String(20), default="")
    code = Column(String(20), default="")            # E018 / DEFECT / ...
    paper_title = Column(String(200), default="")
    question_number = Column(Integer, default=0)
    motif = Column(String(200), default="")          # 定义式指纹（exam_check.question_motifs）
    detail = Column(Text, default="")
    severity = Column(String(20), default="")        # blocking / advisory
    question_text = Column(Text, default="")         # 题干截断（供人复核，不存全文）
    created_at = Column(String(40), default=lambda: _now().isoformat(), index=True)
    source_key = Column(String(64), nullable=True, index=True)
    motif_key = Column(String(64), default="", index=True)
    motif_full = Column(Text, default="")


class EvaluationFramework(Base, TimestampMixin):
    __tablename__ = "evaluation_frameworks"
    id = Column(String(36), primary_key=True, default=_uuid)
    title = Column(String(200), default="")
    domain_key = Column(String(50), default="general", index=True)
    version = Column(Integer, default=1)
    source_text = Column(Text, default="")
    source_hash = Column(String(64), default="", index=True)
    standards = Column(Text, default="[]")
    source_map = Column(Text, default="{}")
    status = Column(String(20), default="proposed", index=True)
    extraction_method = Column(String(30), default="model")
    warnings = Column(Text, default="[]")
    created_at = Column(String(40), default=lambda: _now().isoformat(), index=True)


class PropositionBatch(Base, TimestampMixin):
    __tablename__ = "proposition_batches"
    id = Column(String(36), primary_key=True, default=_uuid)
    filename = Column(String(250), default="")
    domain_key = Column(String(50), default="general", index=True)
    branch = Column(String(100), default="")
    learner = Column(String(200), default="")
    source_text = Column(Text, default="")
    source_hash = Column(String(64), default="", index=True)
    status = Column(String(20), default="parsed")
    created_at = Column(String(40), default=lambda: _now().isoformat(), index=True)


class PropositionQuestion(Base, TimestampMixin):
    __tablename__ = "proposition_questions"
    id = Column(String(36), primary_key=True, default=_uuid)
    batch_id = Column(String(36), ForeignKey("proposition_batches.id"), index=True)
    item_model_id = Column(String(36), ForeignKey("item_models.id"), nullable=True, index=True)
    domain_key = Column(String(50), default="general", index=True)
    branch = Column(String(100), default="")
    learner = Column(String(200), default="")
    source_key = Column(String(64), default="", index=True)
    family_key = Column(String(64), default="", index=True)
    raw_text = Column(Text, default="")
    raw_stem = Column(Text, default="")
    raw_notes = Column(Text, default="")
    raw_answer = Column(Text, default="")
    raw_explanation = Column(Text, default="")
    external_evaluation = Column(Text, default="")
    external_source = Column(String(30), default="external_unknown")
    paper_title = Column(String(250), default="")
    question_number = Column(String(50), default="")
    source_line_start = Column(Integer, default=0, index=True)
    source_line_end = Column(Integer, default=0)
    source_map = Column(Text, default="{}")
    knowledge_points = Column(Text, default="[]")
    question_type = Column(String(100), default="")
    parse_status = Column(String(20), default="ready", index=True)
    analysis_status = Column(String(20), default="pending", index=True)
    error_message = Column(Text, default="")
    created_at = Column(String(40), default=lambda: _now().isoformat(), index=True)


class PropositionEvaluation(Base, TimestampMixin):
    __tablename__ = "proposition_evaluations"
    id = Column(String(36), primary_key=True, default=_uuid)
    question_id = Column(String(36), ForeignKey("proposition_questions.id"), index=True)
    framework_id = Column(String(36), ForeignKey("evaluation_frameworks.id"), index=True)
    framework_version = Column(Integer, default=1)
    analysis_version = Column(Integer, default=1, index=True)
    fingerprint = Column(String(64), default="", index=True)
    blind_solution = Column(Text, default="{}")
    report = Column(Text, default="{}")
    status = Column(String(20), default="pending", index=True)
    demo = Column(Boolean, default=False)
    model = Column(String(100), default="")
    error_message = Column(Text, default="")
    created_at = Column(String(40), default=lambda: _now().isoformat(), index=True)


class DesignPrincipleRevision(Base):
    __tablename__ = "design_principle_revisions"
    id = Column(String(36), primary_key=True, default=_uuid)
    principle_id = Column(String(36), ForeignKey("design_principles.id"), index=True)
    version = Column(Integer, default=1)
    snapshot = Column(Text, default="{}")
    action = Column(String(30), default="created")
    actor = Column(String(30), default="model")
    rationale = Column(Text, default="")
    created_at = Column(String(40), default=lambda: _now().isoformat(), index=True)


class DesignPrincipleLink(Base):
    __tablename__ = "design_principle_links"
    id = Column(String(36), primary_key=True, default=_uuid)
    source_id = Column(String(36), ForeignKey("design_principles.id"), index=True)
    target_id = Column(String(36), ForeignKey("design_principles.id"), index=True)
    relation = Column(String(30), default="related")
    rationale = Column(Text, default="")
    status = Column(String(20), default="proposed")
    created_at = Column(String(40), default=lambda: _now().isoformat(), index=True)


class PropositionAnalysisJob(Base, TimestampMixin):
    __tablename__ = "proposition_analysis_jobs"
    id = Column(String(36), primary_key=True, default=_uuid)
    batch_id = Column(String(36), ForeignKey("proposition_batches.id"), index=True)
    framework_id = Column(String(36), ForeignKey("evaluation_frameworks.id"), nullable=True)
    status = Column(String(20), default="queued", index=True)
    progress = Column(Integer, default=0)
    counts = Column(Text, default="{}")
    question_ids = Column(Text, default="[]")
    force = Column(Boolean, default=False)
    error_message = Column(Text, default="")
    created_at = Column(String(40), default=lambda: _now().isoformat(), index=True)


class PropositionGuidanceTrace(Base):
    __tablename__ = "proposition_guidance_traces"
    id = Column(String(36), primary_key=True, default=_uuid)
    goal_id = Column(String(36), nullable=True, index=True)
    tutorial_id = Column(String(36), nullable=True, index=True)
    section_id = Column(String(50), default="")
    task_kind = Column(String(50), default="")
    context = Column(Text, default="{}")
    result = Column(Text, default="{}")
    created_at = Column(String(40), default=lambda: _now().isoformat(), index=True)


class PropositionEmbedding(Base):
    __tablename__ = "proposition_embeddings"
    id = Column(String(36), primary_key=True, default=_uuid)
    source_id = Column(String(100), default="")
    source_type = Column(String(30), default="principle")
    revision = Column(Integer, default=1)
    model_key = Column(String(100), default="")
    vector = Column(Text, default="[]")
    created_at = Column(String(40), default=lambda: _now().isoformat(), index=True)
    __table_args__ = (Index("uq_proposition_embedding", "source_id", "source_type", "revision", "model_key", unique=True),)


class PropositionConfig(Base):
    __tablename__ = "proposition_config"
    id = Column(String(36), primary_key=True, default="default")
    settings_json = Column(Text, default="{}")
