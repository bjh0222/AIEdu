"""生成运行编排运行时（任务书 04）：run 上下文、阶段状态、预算控制与取消。

边界（对齐契约 .audit/next-round-20261107/contracts/c-orchestration-run-runtime.md）：
- 本模块是纯内存/纯逻辑层：不碰数据库、不发网络调用、不依赖 SQLAlchemy；
  编排侧（tutorials.py）负责把 state_json 写进 GenerationJob（短事务）。
- 质量模块经 on_stage 回调上报阶段，绝不直接写 job 状态；回调异常必须被吞掉，
  不能影响生成与检查结果。
- BudgetController 在请求前原子 reserve、调用结束 settle；线程安全（threading.Lock）；
  父/子 run 共享同一预算池（子预算须预先声明为独立池，不自动继承）。
- 取消后不再 reserve 新调用；已在途响应照常 settle 落账（outcome=cancelled）。
- provider 重试也计调用：每次 attempt 单独 reserve/settle。
"""
from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger("aiedu")

# 任务书 04 规定的最小阶段集；编排侧按序推进，质量模块只上报其中一段。
STAGES: tuple[str, ...] = (
    "planning", "generating", "companions", "reviewing", "repairing",
    "verifying", "accepting", "persisting", "knowledge_extract",
)
STAGE_STATUSES: tuple[str, ...] = ("running", "done", "failed", "skipped")

StageCallback = Callable[[str, str, dict], None]

DEFAULT_EXECUTION_POLICY: dict[str, Any] = {"knowledge_extract_timing": "outline_and_final"}


def new_run_id() -> str:
    return f"run-{uuid.uuid4().hex[:20]}"


def new_call_id() -> str:
    """AIService 真实调用边界的 call_id（ModelCallLog.call_id / TrainingRecord.context_json.call_id）。"""
    return uuid.uuid4().hex


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunCancelled(RuntimeError):
    """run 已取消后仍试图派发新任务。"""


class BudgetExceeded(RuntimeError):
    """预算（调用数 / token / deadline / 单元修复上限）不允许本次预留。"""


class CancelToken:
    """协作式取消：取消后由编排侧/预算侧拒绝新任务，在途响应不强行撤销。"""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._reason = ""

    def cancel(self, reason: str = "") -> None:
        if reason:
            self._reason = reason
        self._event.set()

    @property
    def reason(self) -> str:
        return self._reason

    def cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self._event.is_set():
            raise RunCancelled(f"run 已取消：{self._reason or 'user requested'}")


# ---------------------------------------------------------------------------
# 预算
# ---------------------------------------------------------------------------


@dataclass
class BudgetReservation:
    """一次（或一批）attempt 的预留凭证；settle 后按实际用量对账。"""

    calls: int = 1
    est_input_tokens: int = 0
    max_output_tokens: int = 0
    purpose: str = ""
    reserved_at: str = field(default_factory=_now_iso)
    call_ids: list[str] = field(default_factory=list)
    settled: bool = False
    outcome: str = ""
    actual: dict = field(default_factory=dict)

    @property
    def reserved_tokens(self) -> int:
        return self.est_input_tokens + self.max_output_tokens


class BudgetController:
    """请求前原子 reserve、结束 settle。

    - 父/子 run 共享预算池：``BudgetController(shared=parent)`` 直接消耗父池额度，
      父 snapshot 可见子 run 的消耗（父子账本合计准确）。
    - ``soft=True`` 表示 provider 不支持每次输出上限：token 预算是软上限，
      实际超额不抛错，只如实记入 ``overrun_tokens``（诚实标注，不声称硬上限）。
    - ``unit_repair_limit`` 是每单元定向修复次数上限（run 内计数，不进共享池）。
    """

    def __init__(
        self,
        *,
        max_calls: int | None = None,
        token_allowance: int | None = None,
        deadline: datetime | None = None,
        unit_repair_limit: int | None = None,
        shared: "BudgetController | None" = None,
        soft: bool = False,
        cancel_token: CancelToken | None = None,
        name: str = "run",
    ) -> None:
        self._lock = threading.Lock()
        self._shared = shared
        self._max_calls = max_calls
        self._token_allowance = token_allowance
        self._deadline = deadline
        if shared is not None:
            self._soft = bool(soft) or bool(shared._soft)
        else:
            self._soft = bool(soft)
        self._cancel_token = cancel_token or (shared._cancel_token if shared else None)
        self.name = name
        # 本控制器自己的账（子控制器是父池消耗的本地视图）
        self.calls_used = 0
        self.calls_reserved = 0
        self.tokens_used = 0
        self.tokens_reserved = 0
        self.overrun_tokens = 0
        self.unit_repair_limit = unit_repair_limit
        self._unit_repairs: dict[str, int] = {}

    # -- 池操作（根控制器持有唯一锁，父子链共用，避免锁序问题） ----

    def _root(self) -> "BudgetController":
        return self._shared._root() if self._shared else self

    def _try_consume_reserved(self, calls: int, tokens: int) -> None:
        root = self._root()
        with root._lock:
            if root._max_calls is not None and root.calls_used + root.calls_reserved + calls > root._max_calls:
                raise BudgetExceeded(
                    f"预算不足：调用数预留 {root.calls_used}+{root.calls_reserved}+{calls} 超过 max_calls={root._max_calls}"
                )
            if root._token_allowance is not None and (
                root.tokens_used + root.tokens_reserved + tokens > root._token_allowance
            ):
                raise BudgetExceeded(
                    f"预算不足：token 预留 {root.tokens_used}+{root.tokens_reserved}+{tokens} 超过 allowance={root._token_allowance}"
                )
            root.calls_reserved += calls
            root.tokens_reserved += tokens
        if self is not root:
            self.calls_reserved += calls
            self.tokens_reserved += tokens

    def _release_reserved(self, calls: int, tokens: int) -> None:
        root = self._root()
        with root._lock:
            root.calls_reserved = max(0, root.calls_reserved - calls)
            root.tokens_reserved = max(0, root.tokens_reserved - tokens)
        if self is not root:
            self.calls_reserved = max(0, self.calls_reserved - calls)
            self.tokens_reserved = max(0, self.tokens_reserved - tokens)

    def _record_used(self, calls: int, tokens: int, reserved_tokens: int) -> None:
        root = self._root()
        with root._lock:
            root.calls_used += calls
            root.tokens_used += tokens
            if tokens > reserved_tokens:
                root.overrun_tokens += tokens - reserved_tokens
        if self is not root:
            self.calls_used += calls
            self.tokens_used += tokens
            if tokens > reserved_tokens:
                self.overrun_tokens += tokens - reserved_tokens

    # -- 公开接口 ----

    def reserve(
        self,
        calls: int = 1,
        est_input_tokens: int = 0,
        max_output_tokens: int = 0,
        *,
        purpose: str = "",
    ) -> BudgetReservation:
        """原子预留；额度不足 / 已过 deadline / run 已取消时抛错，不产生部分预留。"""
        if self._cancel_token is not None and self._cancel_token.cancelled():
            raise RunCancelled(f"run 已取消，不再派发新任务：{self._cancel_token.reason}")
        root = self._root()
        with root._lock:
            if root._deadline is not None and datetime.now(timezone.utc) > root._deadline:
                raise BudgetExceeded(f"预算 deadline 已过（{root._deadline.isoformat()}）")
        reservation = BudgetReservation(
            calls=calls, est_input_tokens=est_input_tokens,
            max_output_tokens=max_output_tokens, purpose=purpose,
        )
        self._try_consume_reserved(calls, reservation.reserved_tokens)
        return reservation

    def settle(
        self,
        reservation: BudgetReservation,
        *,
        usage: dict[str, Any] | None,
        outcome: str = "ok",
    ) -> None:
        """调用结束后对账：实际用量替换预留估计，差额退还；超支如实记 overrun。

        provider 未报 usage 时保留估算并把 usage_source 标为 estimated（不冒充 provider）。
        失败/取消的调用同样计账（outcome=error/cancelled），只是不产生训练样本。
        """
        if reservation.settled:
            return
        calls = int((usage or {}).get("calls") or max(1, len(reservation.call_ids)) or 1)
        if usage and usage.get("total_tokens") is not None:
            tokens = int(usage["total_tokens"])
            actual = dict(usage)
        else:
            tokens = reservation.reserved_tokens
            actual = {
                "prompt_tokens": reservation.est_input_tokens,
                "completion_tokens": reservation.max_output_tokens,
                "total_tokens": tokens,
                "usage_source": "estimated",
            }
        actual.setdefault("outcome", outcome)
        self._release_reserved(reservation.calls, reservation.reserved_tokens)
        self._record_used(calls, tokens, reservation.reserved_tokens)
        reservation.settled = True
        reservation.outcome = outcome
        reservation.actual = actual

    def reserve_unit_repair(self, unit_id: str) -> None:
        """每单元定向修复次数上限（ADR-024：最多每单元一次，上限可配）。"""
        with self._lock:
            done = self._unit_repairs.get(unit_id, 0)
            if self.unit_repair_limit is not None and done >= self.unit_repair_limit:
                raise BudgetExceeded(
                    f"单元 {unit_id} 定向修复次数已达上限 {self.unit_repair_limit}"
                )
            self._unit_repairs[unit_id] = done + 1

    def snapshot(self) -> dict[str, Any]:
        root = self._root()
        with root._lock:
            pool = {
                "calls_used": root.calls_used,
                "calls_reserved": root.calls_reserved,
                "tokens_used": root.tokens_used,
                "tokens_reserved": root.tokens_reserved,
                "overrun_tokens": root.overrun_tokens,
                "max_calls": root._max_calls,
                "token_allowance": root._token_allowance,
                "soft": root._soft,
                "deadline": root._deadline.isoformat() if root._deadline else None,
            }
        pool["cancelled"] = bool(self._cancel_token and self._cancel_token.cancelled())
        pool["name"] = self.name
        return pool


# ---------------------------------------------------------------------------
# run 上下文与阶段状态
# ---------------------------------------------------------------------------


class GenerationRunContext:
    """一次生成运行（主生成 / 单节重生成 / 后台知识提取子 run）的内存上下文。

    编排侧持有它穿越快启 → 正文/后台任务；持久化只发生 GenerationJob.run_id /
    state_json（编排侧写），本对象自身不落库。
    """

    def __init__(
        self,
        *,
        goal_id: str,
        tutorial_id: str | None = None,
        version: int | None = None,
        execution_policy: dict[str, Any] | None = None,
        budget: BudgetController | None = None,
        cancel_token: CancelToken | None = None,
        parent_run_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        self.run_id = run_id or new_run_id()
        self.goal_id = goal_id
        self.tutorial_id = tutorial_id
        self.version = version
        self.execution_policy: dict[str, Any] = dict(DEFAULT_EXECUTION_POLICY)
        if execution_policy:
            self.execution_policy.update(execution_policy)
        self.budget = budget
        self.cancel_token = cancel_token or CancelToken()
        self.parent_run_id = parent_run_id
        self._lock = threading.Lock()
        self._callbacks: list[StageCallback] = []
        self._state: dict[str, Any] = {
            "stage_id": "",
            "stage_status": "",
            "unit_id": None,
            "completed_units": 0,
            "total_units": 0,
            "delivery_status": "",
            "postprocess_status": "",
            "last_heartbeat": _now_iso(),
            "content_hash": None,
            "checkpoint": {},
            "execution_policy": dict(self.execution_policy),
        }

    # -- 阶段回调（质量模块经此上报；编排侧回调负责写 job 状态） ----

    def register_stage_callback(self, callback: StageCallback) -> None:
        with self._lock:
            self._callbacks.append(callback)

    def stage_callback(self) -> StageCallback:
        """供质量/检查服务的可选 on_stage 注入（契约 §2）：内部转 stage()，
        异常在 stage() 内吞掉；质量模块只上报，绝不直接写 job 状态。"""
        def _callback(stage_id: str, status: str, meta: dict) -> None:
            meta = meta if isinstance(meta, dict) else {}
            self.stage(stage_id, status,
                       unit_id=meta.get("unit_id"),
                       completed_units=meta.get("completed_units"),
                       total_units=meta.get("total_units"),
                       detail=str(meta.get("detail") or ""))
        return _callback

    def stage(self, stage_id: str, status: str, *, unit_id: str | None = None,
              completed_units: int | None = None, total_units: int | None = None,
              detail: str = "") -> None:
        """推进阶段；回调异常一律吞掉（观测不能打断生成）。"""
        meta = {"unit_id": unit_id, "completed_units": completed_units,
                "total_units": total_units, "detail": detail}
        with self._lock:
            self._state.update({
                "stage_id": stage_id, "stage_status": status, "unit_id": unit_id,
                "last_heartbeat": _now_iso(),
            })
            if completed_units is not None:
                self._state["completed_units"] = completed_units
            if total_units is not None:
                self._state["total_units"] = total_units
            # 任务书 04：阶段历史随状态落 state_json（轮询抓不到的瞬态以权威记录为准）
            history = self._state.get("stage_history")
            if not isinstance(history, list):
                history = []
            history.append({"stage_id": stage_id, "stage_status": status,
                            "unit_id": unit_id, "detail": detail,
                            "at": _now_iso()})
            self._state["stage_history"] = history[-200:]
            callbacks = list(self._callbacks)
        for callback in callbacks:
            try:
                callback(stage_id, status, meta)
            except Exception as exc:  # noqa: BLE001  回调故障只留痕
                logger.warning("on_stage 回调失败（stage=%s status=%s）：%s", stage_id, status, exc)

    # -- 状态与 checkpoint ----

    def state(self) -> dict[str, Any]:
        with self._lock:
            snapshot = json_safe(dict(self._state))
            snapshot["last_heartbeat"] = _now_iso()
        return snapshot

    def update_state(self, **fields: Any) -> None:
        with self._lock:
            self._state.update(fields)
            self._state["last_heartbeat"] = _now_iso()

    def set_content_hash(self, content_hash: str | None) -> None:
        self.update_state(content_hash=content_hash)

    def set_checkpoint(self, **checkpoint: Any) -> None:
        with self._lock:
            merged = dict(self._state.get("checkpoint") or {})
            merged.update(checkpoint)
            self._state["checkpoint"] = merged
            self._state["last_heartbeat"] = _now_iso()

    # -- 执行策略 ----

    def execution_policy_value(self, key: str, default: Any = None) -> Any:
        return self.execution_policy.get(key, default)

    # -- 子 run ----

    def child(self, *, purpose: str = "", budget: BudgetController | None = None) -> "GenerationRunContext":
        """后台任务子 run：指回父 run；预算默认共享父池（可传独立子预算池）。"""
        return GenerationRunContext(
            goal_id=self.goal_id, tutorial_id=self.tutorial_id, version=self.version,
            execution_policy=self.execution_policy,
            budget=budget or self.budget, cancel_token=self.cancel_token,
            parent_run_id=self.run_id,
        )


def json_safe(value: Any) -> Any:
    """state_json 只存 JSON 可序列化内容；不可序列化对象降级为字符串。"""
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


# ---------------------------------------------------------------------------
# 活动运行注册表（进程内）：cancel / 孤儿清点用。仅内存态，不持久化。
# ---------------------------------------------------------------------------

_RUN_REGISTRY: dict[str, GenerationRunContext] = {}
_RUN_REGISTRY_LOCK = threading.Lock()


def register_run(ctx: GenerationRunContext) -> None:
    with _RUN_REGISTRY_LOCK:
        _RUN_REGISTRY[ctx.run_id] = ctx


def unregister_run(run_id: str | None) -> None:
    if not run_id:
        return
    with _RUN_REGISTRY_LOCK:
        _RUN_REGISTRY.pop(run_id, None)


def active_run(run_id: str | None) -> GenerationRunContext | None:
    if not run_id:
        return None
    with _RUN_REGISTRY_LOCK:
        return _RUN_REGISTRY.get(run_id)


def active_run_ids() -> set[str]:
    with _RUN_REGISTRY_LOCK:
        return set(_RUN_REGISTRY)
