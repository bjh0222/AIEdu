"""Owner-only asset adapters. No old generation/config/analysis endpoint is reopened."""
from __future__ import annotations

from fastapi import APIRouter,Depends,BackgroundTasks
from pydantic import Field,ValidationError,ConfigDict
from typing import Literal

from ..db import SessionLocal
from ..models import FormatSpec,PropositionBatch,PropositionQuestion,PropositionEvaluation,DesignPrinciple,DesignPrincipleRevision,TutorialVersion,GenerationJob
from ..schemas import FormatSpecIn,FormatSpecOut
from ..v2.contracts import Contract,DomainError,TaskStart,KnowledgeRequest,digest
from ..v2.security import owner,action_key
from ..v2.runtime import write,load,dump

router = APIRouter(prefix="/v2",tags=["assets.v2"],dependencies=[Depends(owner)])


class PackageInput(Contract):
    domain_key:str
    branch:str = ""
    scene:str
    seed:str | None = None
    sources:list[str] = Field(default_factory=list)


@router.post("/spec-packages/preview")
def package_preview(payload:PackageInput,key=Depends(action_key)):
    from ..v2.spec_packages import build_package
    return build_package(payload.domain_key,payload.branch,payload.scene,seed=payload.seed,sources=payload.sources)


@router.get("/format-specs")
def specs():
    from ..services.format_specs import list_specs
    with SessionLocal() as db:
        from ..v2.management_lifecycle import overlay_ids
        hidden = set(db.scalars(overlay_ids("specs")))
        return {"items":[s for s in list_specs(db) if s["id"] not in hidden]}


class SpecWrite(Contract):
    expected_version: int | None = Field(default=None,ge=1)
    spec: FormatSpecIn


class SpecImportRecord(FormatSpecOut):
    """A full source snapshot, not an editable form that trims or resets history."""
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,36}$")
    version: int = Field(ge=1)
    created_at: str = Field(min_length=1,max_length=80)
    updated_at: str = Field(min_length=1,max_length=80)


class SpecImport(Contract):
    records: list[SpecImportRecord] = Field(min_length=1,max_length=100)
    source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


@router.post("/format-spec-imports",status_code=201)
def import_specs(payload:SpecImport,key=Depends(action_key)):
    """Owner-authorized, insert-only migration. Never replaces a user's destination row."""
    from ..models import PropositionConfig
    from ..services.format_specs import spec_dict
    records = [r.model_dump() for r in payload.records]
    if digest(records) != payload.source_hash:
        raise DomainError("SPEC_IMPORT_HASH_MISMATCH",422)
    if len({r["id"] for r in records}) != len(records):
        raise DomainError("SPEC_IMPORT_DUPLICATE_ID",422)
    fingerprint = digest(payload.model_dump())
    json_fields = {"applicable_intents","excluded_scenarios","excluded_intents","trigger_keywords","excluded_keywords","deliverables"}
    with write() as db:
        receipt_id = "action:"+digest(key)
        prior = db.get(PropositionConfig,receipt_id)
        if prior:
            saved = load(prior.settings_json)
            if saved["request_hash"] != fingerprint:
                raise DomainError("IDEMPOTENCY_CONFLICT")
            return saved["response"]
        inserted,unchanged = [],[]
        for value in records:
            old = db.get(FormatSpec,value["id"])
            if old is not None:
                if spec_dict(old) != value:
                    raise DomainError("SPEC_IMPORT_ID_CONFLICT",409,spec_id=value["id"])
                unchanged.append(value["id"])
                continue
            if value["is_default"] and db.query(FormatSpec).filter_by(is_default=True).first():
                raise DomainError("SPEC_IMPORT_DEFAULT_CONFLICT",409)
            row = FormatSpec(**{k:dump(v) if k in json_fields else v for k,v in value.items()})
            db.add(row)
            db.flush()
            if spec_dict(row) != value:
                raise DomainError("SPEC_IMPORT_VALUE_CHANGED",409)
            inserted.append(value["id"])
        result = {"source_hash":payload.source_hash,"inserted_ids":inserted,"unchanged_ids":unchanged,
                  "record_hashes":{r["id"]:digest(r) for r in records}}
        db.add(PropositionConfig(id=receipt_id,settings_json=dump({"request_hash":fingerprint,"response":result})))
        return result


def write_spec(payload,key,spec_id=None):
    from ..services.format_specs import _apply_payload,spec_dict
    from ..models import PropositionConfig
    fingerprint = digest({"payload":payload.model_dump(),"id":spec_id})
    with write() as db:
        # A local asset action receipt is data, never a model permission.
        receipt = db.get(PropositionConfig,"action:"+digest(key))
        if receipt:
            saved = load(receipt.settings_json)
            if saved["request_hash"] != fingerprint:
                raise DomainError("IDEMPOTENCY_CONFLICT")
            return saved["response"]
        row = db.get(FormatSpec,spec_id) if spec_id else FormatSpec(is_builtin=False,is_default=False,version=1)
        if row is None:
            raise DomainError("SPEC_NOT_FOUND",404)
        if spec_id and row.is_builtin:
            raise DomainError("BUILTIN_SPEC_IMMUTABLE",403)
        if spec_id and row.version != payload.expected_version:
            raise DomainError("SPEC_VERSION_STALE",409)
        _apply_payload(row,payload.spec,is_update=bool(spec_id))
        if payload.spec.is_default:
            for old in db.query(FormatSpec).filter_by(is_default=True):
                old.is_default = False
            db.flush()  # partial unique index: clear old default in the SAME transaction.
            row.is_default = True
        else:
            row.is_default = False
        db.add(row)
        db.flush()
        result = spec_dict(row)
        db.add(PropositionConfig(id="action:"+digest(key),settings_json=dump({"request_hash":fingerprint,"response":result})))
        return result


@router.post("/format-specs",status_code=201)
def create_spec(payload:SpecWrite,key=Depends(action_key)):
    return write_spec(payload,key)


@router.put("/format-specs/{spec_id}")
def update_spec(spec_id:str,payload:SpecWrite,key=Depends(action_key)):
    return write_spec(payload,key,spec_id)


@router.get("/legacy-compatibility")
def compatibility():
    return {"policy":"explicit adapters; no legacy paid-call bypass", "items":[
        {"legacy":"/format-specs","adapter":"/api/v2/format-specs","read":"available","write":"CAS+receipt"},
        {"legacy":"/model-settings","adapter":"/api/v2/model-settings","read":"redacted","write":"owner only; no network"},
        {"legacy":"/goals/*/generate|/tutorials/*/revise|/practice/*","adapter":"/api/v2/task-starts","status":"new root only"},
        {"legacy":"/materials","adapter":"/api/v2/tasks/{id}/materials","status":"stream+operator policy"},
        {"legacy":"/proposition/*","adapter":"/api/v2/knowledge/assets/{kind}","read":"available","analysis":"same-root knowledge task"},
        {"legacy":"/proposition/batches/import","adapter":"/api/v2/knowledge/batches/import","status":"receipt+immutable source; no auto analysis"},
        {"legacy":"/proposition/principles/{id}","adapter":"/api/v2/knowledge/principles/{id}","status":"CAS+revision+trace invalidation"},
        {"legacy":"historical goals/tutorials/practice/knowledge","adapter":"/api/v2/legacy-assets/{kind}","status":"read-only; no promotion to v2 quality"},
        {"legacy":"/training|/io|/knowledge destructive graph","status":"quarantined; no destructive bulk adapter"}],
        "legacy_frontend_compatible":False}


@router.get("/model-settings")
def models():
    from ..model_settings import public_config,read_config
    from ..v2.model_roles import public_roles,role_config
    config = public_config()
    saved = read_config()
    available,assignments = role_config()
    choices = [{"id":p["id"],"name":p.get("name",p["id"]),"model":p.get("model"),"model_family":p.get("model_family","unknown")} for p in available.values()]
    return {"config":config,"config_hash":digest({"config":config,"roles":assignments,"choices":choices,"revision":saved.get("v2_revision",0)}),
            "roles":public_roles(),"role_assignments":assignments,"role_profiles":choices}


class ConfigWrite(Contract):
    expected_config_hash:str
    action:str
    value:dict


@router.put("/model-settings")
def configure_models(payload:ConfigWrite,key=Depends(action_key)):
    from .. import model_settings as store
    from ..v2.model_roles import role_config
    request_hash = digest(payload.model_dump())
    with store._lock:
        config = store.read_config()
        old = config.setdefault("v2_receipts",{}).get(key)
        if old:
            if old["request_hash"] != request_hash:
                raise DomainError("IDEMPOTENCY_CONFLICT")
            return {"applied_revision":old["applied_revision"],"applied":True}
        if models()["config_hash"] != payload.expected_config_hash:
            raise DomainError("MODEL_CONFIG_STALE",409)
        if payload.action == "profile":
            try:
                profile = store.ModelProfile.model_validate(payload.value)
            except ValidationError:
                raise DomainError("MODEL_PROFILE_INVALID",422) from None
            if config.get("v2_profile_management", {}).get(profile.id, {}).get("trashed"):
                raise DomainError("RESOURCE_TRASHED",409)
            resolved = store.resolve_profile(profile)
            config["profiles"] = [p for p in config["profiles"] if p["id"] != profile.id]+[resolved]
        elif payload.action == "select":
            profile_id = payload.value.get("profile_id")
            if config.get("v2_profile_management", {}).get(profile_id, {}).get("trashed"):
                raise DomainError("RESOURCE_TRASHED",409)
            if profile_id is not None and profile_id not in {p["id"] for p in config["profiles"]}:
                raise DomainError("MODEL_PROFILE_NOT_FOUND",422)
            config["active_id"] = profile_id
        elif payload.action == "user_profile":
            content = payload.value.get("content")
            if not isinstance(content,str) or len(content)>4000:
                raise DomainError("USER_PROFILE_INVALID",422)
            config["user_profile"] = content
        elif payload.action == "roles":
            available,_ = role_config()
            roles = payload.value
            # chat：阅读页聊天角色（2026-11-21 前端优化轮2）。值为 null 表示清除该角色。
            allowed = {"generation","review","blind_solver","secondary_review","benchmark_panel","benchmark_refiner","chat"}
            if set(roles)-allowed or not {v for v in roles.values() if v is not None} <= available.keys():
                raise DomainError("MODEL_ROLE_INVALID",422)
            merged = {**config.get("model_roles", {}), **{k: v for k, v in roles.items() if v is not None}}
            for k in roles:
                if roles[k] is None:
                    merged.pop(k, None)
            config["model_roles"] = merged
        elif payload.action == "delivery_policy":
            if set(payload.value) - {"auto_waiver_enabled"}:
                raise DomainError("MODEL_CONFIG_ACTION_INVALID", 422)
            enabled = payload.value.get("auto_waiver_enabled")
            if type(enabled) is not bool:
                raise DomainError("MODEL_CONFIG_ACTION_INVALID", 422)
            config["delivery_policy"] = {"auto_waiver_enabled": enabled}
        else:
            raise DomainError("MODEL_CONFIG_ACTION_INVALID",422)
        # One atomic private-file replacement includes the idempotency receipt.
        config["v2_revision"] = config.get("v2_revision",0)+1
        config["v2_receipts"][key] = {"request_hash":request_hash,"applied_revision":config["v2_revision"]}
        store.write_config(config)
        return {"applied_revision":config["v2_revision"],"applied":True}


class DiscoverRequest(Contract):
    base_url:str
    api_key:str|None=None
    profile_id:str|None=None


@router.post("/model-settings/discover")
def discover_models(payload:DiscoverRequest):
    """供应商模型列表探测：显式地址+密钥，或复用同地址已保存配置的密钥。

    只读网络探测：不写入配置、不产生动作回执、不做任何生成调用。
    """
    from .. import model_settings as store
    from ..llm.provider import LLMError
    from .model_settings import provider_for
    base_url = payload.base_url.strip().rstrip("/")
    if not base_url.startswith(("http://","https://")):
        raise DomainError("MODEL_PROFILE_INVALID",422)
    api_key = (payload.api_key or "").strip()
    if not api_key and payload.profile_id:
        saved = next((p for p in store.read_config()["profiles"] if p["id"] == payload.profile_id),None)
        if saved is None or saved.get("base_url","").rstrip("/") != base_url:
            raise DomainError("MODEL_PROFILE_NOT_FOUND",404)
        api_key = saved.get("api_key") or ""
    profile = store.ModelProfile(id="aiedu-discover",name="discover",base_url=base_url,model="",api_key=api_key or None)
    try:
        return {"models":provider_for(profile).list_models()}
    except LLMError as exc:
        raise DomainError("MODEL_DISCOVERY_FAILED",502,reason=str(exc)) from None


@router.get("/knowledge/assets/{kind}")
def assets(kind:str,limit:int=50,offset:int=0):
    from ..services.proposition_knowledge import question_dict,evaluation_dict,batch_dict
    mapping = {"batches":PropositionBatch,"questions":PropositionQuestion,"evaluations":PropositionEvaluation,
               "principles":DesignPrinciple,"principle-revisions":DesignPrincipleRevision}
    if kind not in mapping or not 1 <= limit <= 200 or offset < 0:
        raise DomainError("ASSET_QUERY_INVALID",422)
    with SessionLocal() as db:
        from ..v2.management_lifecycle import visible
        query = visible(db.query(mapping[kind]), kind, mapping[kind].id)
        rows = query.order_by(mapping[kind].id).offset(offset).limit(limit).all()
        def value(row):
            if kind == "batches":
                return batch_dict(db,row)
            if kind == "questions":
                return question_dict(row)
            if kind == "evaluations":
                return evaluation_dict(row)
            return {c.name:getattr(row,c.name) for c in row.__table__.columns}
        return {"items":[value(r) for r in rows],"total":query.count(),"limit":limit,"offset":offset}


@router.get("/evaluation-frameworks")
def evaluation_frameworks():
    from ..models import EvaluationFramework
    from ..services.proposition_knowledge import framework_dict
    with SessionLocal() as db:
        return {"items": [framework_dict(r, source=False) for r in db.query(EvaluationFramework).order_by(EvaluationFramework.domain_key, EvaluationFramework.id)],
            "scope": "authored frameworks; not professional certification"}


@router.post("/evaluation-frameworks/math/install", status_code=201)
def install_math_framework(key=Depends(action_key)):
    from ..models import EvaluationFramework, PropositionConfig
    from ..v2.math_framework import build_math_framework
    data = build_math_framework()
    identity = digest([data["framework_id"], data["version"], data["source_hash"]])[:32]
    value = {k: data[k] for k in ("title", "domain_key", "version", "source_text", "source_hash", "extraction_method")}
    value.update(standards=dump(data["standards"]), source_map=dump(data["source_map"]), warnings=dump(data["warnings"]), status="active")
    fingerprint = digest(value)
    with write() as db:
        old = db.get(EvaluationFramework, identity)
        if old and any(getattr(old, k) != v for k, v in value.items()):
            raise DomainError("EVALUATION_FRAMEWORK_CONFLICT", 409)
        if not old:
            db.add(EvaluationFramework(id=identity, **value))
        response = {"id": identity, "domain_key": "math_logic", "version": data["version"],
            "framework_hash": fingerprint, "status": "active", "calibration_status": "not_calibrated",
            "unchanged": bool(old), "source_hash": data["source_hash"]}
        receipt_id = "framework-install:" + digest(key)
        prior = db.get(PropositionConfig, receipt_id)
        if prior:
            saved = load(prior.settings_json)
            if saved["request_hash"] != fingerprint:
                raise DomainError("IDEMPOTENCY_CONFLICT")
            return saved["response"]
        db.add(PropositionConfig(id=receipt_id, settings_json=dump({"request_hash": fingerprint, "response": response})))
        return response


class Feedback(Contract):
    event_id:str = Field(min_length=1,max_length=80)
    text:str = Field(min_length=1,max_length=20000)
    expected_source_hash:str


class KnowledgeImport(Contract):
    filename:str = Field(min_length=1,max_length=250)
    markdown:str = Field(min_length=1,max_length=2_000_000)
    domain_key:str = "general"
    branch:str = ""
    learner:str = ""
    source_identity:dict = Field(default_factory=dict)


@router.post("/knowledge/batches/import",status_code=201)
def import_questions(payload:KnowledgeImport,key=Depends(action_key)):
    from ..services.proposition_knowledge import import_batch
    from ..schemas import PropositionBatchImport
    from ..models import PropositionConfig
    if "/" in payload.filename or "\\" in payload.filename:
        raise DomainError("FILENAME_INVALID",422)
    request_hash = digest(payload.model_dump())
    with write() as db:
        receipt_key = "knowledge-import:"+digest(key)
        prior = db.get(PropositionConfig,receipt_key)
        if prior:
            saved = load(prior.settings_json)
            if saved["request_hash"] != request_hash:
                raise DomainError("IDEMPOTENCY_CONFLICT")
            return saved["response"]
        result = import_batch(db, PropositionBatchImport(
            filename=payload.filename, markdown=payload.markdown,
            domain_key=payload.domain_key, branch=payload.branch,
            learner=payload.learner, auto_analyse=False,
        ), start=False, commit=False)
        result["analysis"] = "not_started; use knowledge_analysis task-start for explicit same-root execution"
        if payload.source_identity:
            from ..models import PropositionQuestion
            from ..v2.insights import save as save_insight
            source_identity = {
                **payload.source_identity,
                "filename": payload.filename,
                "source_hash": result["source_hash"],
                "calibration_status": "not_calibrated",
            }
            save_insight(db, "knowledge-batch-meta:" + result["id"], source_identity)
            for question in db.query(PropositionQuestion).filter_by(batch_id=result["id"]):
                source_map = load(question.source_map)
                source_map["source_identity"] = source_identity
                question.source_map = dump(source_map)
            result["source_identity"] = source_identity
        db.add(PropositionConfig(id=receipt_key,settings_json=dump({"request_hash":request_hash,"response":result})))
        return result


class ResearchAssetImport(Contract):
    source_hash:str = Field(pattern=r"^[a-f0-9]{64}$")
    cards:list[dict] = Field(default_factory=list,max_length=100)
    principles:list[dict] = Field(default_factory=list,max_length=100)
    frameworks:list[dict] = Field(default_factory=list,max_length=30)


@router.post("/knowledge/asset-imports", status_code=201)
def import_research_assets(payload:ResearchAssetImport,key=Depends(action_key)):
    """Idempotent import of model/research-synthesized reusable knowledge assets."""
    from ..models import PropositionConfig
    from ..v2.knowledge_assets import publish_card,publish_framework,publish_principle
    body = payload.model_dump()
    if digest({"cards":body["cards"],"principles":body["principles"],"frameworks":body["frameworks"]}) != payload.source_hash:
        raise DomainError("RESEARCH_ASSET_IMPORT_HASH_MISMATCH",422)
    request_hash = digest(body)
    with write() as db:
        receipt_key = "research-asset-import:" + digest(key)
        prior = db.get(PropositionConfig,receipt_key)
        if prior:
            saved = load(prior.settings_json)
            if saved["request_hash"] != request_hash:
                raise DomainError("IDEMPOTENCY_CONFLICT")
            return saved["response"]
        cards = [publish_card(db, item) for item in payload.cards]
        principles = [publish_principle(db, item) for item in payload.principles]
        frameworks = [publish_framework(db, item) for item in payload.frameworks]
        result = {
            "status": "imported",
            "calibration_status": "not_calibrated",
            "source_hash": payload.source_hash,
            "cards": [{"ref": ref,"hash": wrapped["hash"]} for ref, wrapped in cards],
            "principles": [ref for ref, _ in principles],
            "frameworks": [{"ref": ref,"source_hash": value["source_hash"]}
                           for ref, value in frameworks],
            "receipt_hash": digest({
                "source_hash": payload.source_hash,
                "cards": [wrapped["hash"] for _, wrapped in cards],
                "principles": [ref["evidence_hash"] for ref, _ in principles],
                "frameworks": [value["source_hash"] for _, value in frameworks],
            }),
        }
        db.add(PropositionConfig(
            id=receipt_key,
            settings_json=dump({"request_hash": request_hash, "response": result}),
        ))
        return result


@router.get("/legacy-assets/{kind}")
def legacy_assets(kind:str,limit:int=50,offset:int=0):
    from ..models import LearningGoal,Tutorial,GoalMaterial,PracticeTask,Attempt,Concept,ConceptRelation,EvaluationFramework
    mapping = {"goals":LearningGoal,"tutorials":Tutorial,"versions":TutorialVersion,"materials":GoalMaterial,
        "practice":PracticeTask,"attempts":Attempt,"concepts":Concept,"relations":ConceptRelation,"frameworks":EvaluationFramework}
    if kind not in mapping or not 1 <= limit <= 200 or offset < 0:
        raise DomainError("ASSET_QUERY_INVALID",422)
    with SessionLocal() as db:
        model = mapping[kind]
        query = db.query(model)
        rows = query.order_by(model.id).offset(offset).limit(limit).all()
        return {"items":[{c.name:getattr(row,c.name) for c in model.__table__.columns} for row in rows],
            "total":query.count(),"limit":limit,"offset":offset,"policy":"immutable legacy read; no generated quality upgrade or model calls"}


class PrincipleWrite(Contract):
    expected_version:int = Field(ge=1)
    status:Literal["proposed","confirmed","rejected","retracted"]
    statement:str | None = Field(default=None,min_length=1,max_length=8000)
    mechanism:str | None = Field(default=None,max_length=8000)
    guidance:str | None = Field(default=None,max_length=8000)
    scope:dict | None = None
    rationale:str = ""


@router.put("/knowledge/principles/{principle_id}")
def revise_principle(principle_id:str,payload:PrincipleWrite,key=Depends(action_key)):
    from ..models import PropositionConfig,PropositionGuidanceTrace
    request_hash = digest({"id":principle_id,**payload.model_dump()})
    with write() as db:
        receipt_key = "principle-action:"+digest(key)
        prior = db.get(PropositionConfig,receipt_key)
        if prior:
            saved = load(prior.settings_json)
            if saved["request_hash"] != request_hash:
                raise DomainError("IDEMPOTENCY_CONFLICT")
            return saved["response"]
        row = db.get(DesignPrinciple,principle_id)
        if not row:
            raise DomainError("PRINCIPLE_NOT_FOUND",404)
        if row.version != payload.expected_version:
            raise DomainError("PRINCIPLE_VERSION_STALE",409)
        for field in ("statement","mechanism","guidance"):
            if getattr(payload,field) is not None:
                setattr(row,field,getattr(payload,field))
        scope = payload.scope if payload.scope is not None else load(row.scope)
        # A user approval is not an ablation certificate. Every revision loses
        # prior experimental eligibility; only an evidence workflow may restore it.
        scope = {**scope,"guidance_ablation_status":"not_validated_after_revision"}
        row.scope,row.status,row.version = dump(scope),payload.status,row.version+1
        row.approval_source = "user_revision"
        snapshot = {c.name:getattr(row,c.name) for c in row.__table__.columns}
        db.add(DesignPrincipleRevision(principle_id=row.id,version=row.version,action="user_revision",actor="user",
            snapshot=dump(snapshot),rationale=payload.rationale))
        invalidated = 0
        for trace in db.query(PropositionGuidanceTrace):
            selected = load(trace.context).get("bundle",{}).get("selected",[])
            if any(e.get("id") == row.id and e.get("version") == payload.expected_version for e in selected):
                value = load(trace.result)
                value.setdefault("invalidations",[]).append({"principle_id":row.id,"old_version":payload.expected_version,"new_version":row.version})
                value["current_binding_status"] = "invalidated"
                trace.result = dump(value)
                invalidated += 1
        result = {"id":row.id,"version":row.version,"status":row.status,"invalidated_traces":invalidated,"calibration_status":"not_calibrated"}
        db.add(PropositionConfig(id=receipt_key,settings_json=dump({"request_hash":request_hash,"response":result})))
        return result


@router.post("/knowledge/questions/{question_id}/feedback",status_code=201)
def feedback(question_id:str,payload:Feedback,key=Depends(action_key)):
    with write() as db:
        q = db.get(PropositionQuestion,question_id)
        if not q:
            raise DomainError("QUESTION_NOT_FOUND",404)
        metadata = load(q.source_map)
        events = metadata.setdefault("v2_feedback",[])
        record = {"question_id":question_id,**payload.model_dump(),"kind":"user_feedback_not_truth"}
        old = next((e for e in events if e["event_id"] == payload.event_id),None)
        if old:
            if old != record:
                raise DomainError("FEEDBACK_EVENT_IMMUTABLE",409)
            return old
        if digest(q.source_map) != payload.expected_source_hash:
            raise DomainError("KNOWLEDGE_SOURCE_STALE",409)
        events.append(record)
        q.source_map = dump(metadata)
        return record


@router.post("/benchmarks/controlled",status_code=201)
def controlled(key=Depends(action_key)):
    from ..v2.benchmark import run_controlled_benchmark,store_report
    from ..models import PropositionConfig
    with write() as db:
        receipt_id = "controlled-action:"+digest(key)
        prior = db.get(PropositionConfig,receipt_id)
        if prior:
            return load(prior.settings_json)
        report = run_controlled_benchmark()
        result = {"report_hash":store_report(db,report),"report":report,"calibration_status":"not_calibrated"}
        db.add(PropositionConfig(id=receipt_id,settings_json=dump(result)))
        return result


@router.get("/benchmarks/reports/{report_hash}")
def benchmark_report(report_hash:str):
    from ..models import PropositionConfig
    from ..v2.storage import BlobStore
    with SessionLocal() as db:
        if not db.get(PropositionConfig,"benchmark-report:"+report_hash):
            raise DomainError("BENCHMARK_REPORT_NOT_FOUND",404)
    return load(BlobStore().get(report_hash).decode())


@router.post("/benchmarks/sealed/{dataset_hash}/consume")
def read_sealed_once(dataset_hash:str,key=Depends(action_key)):
    from ..v2.benchmark import consume_sealed
    from ..v2.storage import BlobStore
    # Consumption commits before the first label byte is read. Even a lost HTTP
    # response cannot reopen the set; this is deliberately not a retryable GET.
    with write() as db:
        blob_hash = consume_sealed(db,dataset_hash,key)
    return {"dataset":load(BlobStore().get(blob_hash).decode()),"status":"consumed_once",
            "calibration_status":"not_calibrated"}


class SemanticBenchmark(Contract):
    source_versions:list[str] = Field(min_length=1)
    purpose:Literal["calibration","repair_progress","sealed"] = "calibration"


@router.post("/benchmarks/semantic-build",status_code=202)
def semantic_build(payload:SemanticBenchmark,background:BackgroundTasks,key=Depends(action_key)):
    from ..v2.tasks import create_task,mutate_input,start_run,goal
    from ..v2.contracts import TaskInput,Confirmation,StartRun
    from ..v2.worker import execute_run
    # Recipe/source selection lives only in this evaluation envelope, never in a
    # regular product's default task requirements or generation context.
    with SessionLocal() as db:
        from ..v2.benchmark import source_roles_for_versions
        source_roles_for_versions(db,payload.source_versions,payload.purpose)
    created = create_task(TaskInput(title="独立语义合成基准构建",raw_input="按显式评测封套构建，不签校准或人类一致性；封套hash="+digest(payload.model_dump()),operation="benchmark_build"),
                          "benchmark:"+digest(key))
    with SessionLocal() as db:
        saved = load(goal(db,created["task_id"]).task_state_json)
        prior = saved.get("receipts",{}).get("benchmark-start:"+digest(key))
    if prior:
        return prior["response"]|{"task_id":created["task_id"]}
    confirmed = mutate_input(created["task_id"],Confirmation(expected_revision=1,context_hash=created["context_hash"]),
                             "benchmark-confirm:"+digest(key),True)
    started = start_run(created["task_id"],StartRun(expected_revision=1,confirmed_context_hash=confirmed["context_hash"],mode="configured"),
                        "benchmark-start:"+digest(key),envelope=payload.model_dump())
    background.add_task(execute_run,started["run_id"])
    return started|{"task_id":created["task_id"]}


@router.get("/storage/report")
def storage_report():
    from ..v2.storage import BlobStore,ExportSet,CallDispatch,MigrationDelta
    store = BlobStore()
    count,size = 0,0
    # BlobStore validates the configured root against reparse components on access.
    for path in store.root.glob("[0-9a-f][0-9a-f]/*"):
        if path.is_file() and not path.is_symlink():
            store.path(path.name)
            count += 1
            size += path.stat().st_size
    with SessionLocal() as db:
        return {"blob_files":count,"blob_bytes":size,"export_sets":db.query(ExportSet).count(),
            "delta_events":db.query(MigrationDelta).count(),"late_candidates":db.query(CallDispatch).filter_by(status="late").count(),
            "cleanup_preview":{"delete_count":0,"reason":"versions, calls, delta and immutable exports retained; no automatic cleanup"}}
