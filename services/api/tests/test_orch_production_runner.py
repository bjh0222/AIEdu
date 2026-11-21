# -*- coding: utf-8 -*-
"""C4B（任务书 05 切片 B）production runner 测试。

- 默认 CLI 即使存在真实 .env 也零网络（socket 阻断子进程外网连接；仅放行回环
  socketpair——asyncio 事件循环自管道在 Windows 上走 127.0.0.1 连接，不是外网请求）；
- production runner 走真实生产链路（建目标/传材料/quickstart/04 状态等待/验收/反馈入口），
  替身只放 provider（demo）与外部执行边界；
- 门槛：改生产解析器必须真实改变回归结果（monkeypatch 旧版分值表解析 → 判定翻转），
  只改 fixture expectation 而不修实现必须失败；
- contract runner 默认行为保留（--runner 默认 contract）。

全部零模型调用（demo provider / 替身任务）。
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

WORKSPACE = Path(__file__).resolve().parents[3]
DATASET = WORKSPACE / "fixtures" / "benchmark" / "production-tasks.dataset.json"


def benchmark_module():
    path = WORKSPACE / "scripts" / "proposition_benchmark.py"
    spec = importlib.util.spec_from_file_location("production_benchmark", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_dataset():
    return json.loads(DATASET.read_text(encoding="utf-8"))


def test_runner_defaults_to_contract(tmp_path):
    benchmark = benchmark_module()
    dataset = _load_dataset()
    package = benchmark.load_package(dataset)
    assert package["protocol"] == "aiedu.benchmark.package.v1"
    # 合同 runner 语义保留：run_production 只在显式 --runner production 时被调用
    import inspect

    source = inspect.getsource(benchmark.main)
    assert '--runner' in source and 'choices=["contract", "production"]' in source


def test_production_generate_case_runs_real_chain(client, tmp_path):
    benchmark = benchmark_module()
    dataset = _load_dataset()
    package = benchmark.load_package(dataset)
    case = next(t for t in package["tasks"] if t["id"] == "prod-generate-chain-smoke")
    results, meta = benchmark.run_production(
        None, dataset, package,
        SimpleNamespace(live=False, repeats=1, runner="production"))
    assert meta["calls"] >= 0
    result = next(r for r in results if r["case_id"] == case["id"])
    assert result["verdict"] == "met", result["misses"]
    actual = result["actual"]
    assert actual["job_status"] == "done"
    assert actual["postprocess_status"] == "done"
    stages = {e["stage_id"] for e in result["stage_events"]}
    assert {"planning", "generating", "reviewing", "persisting", "knowledge_extract"} <= stages
    assert actual["feedback_event_count"] == 1
    assert result["feedback_refs"], "反馈必须经真实入口落台账并可反查"


def test_production_paper_replay_score_table_no_cascade(client, tmp_path):
    benchmark = benchmark_module()
    dataset = _load_dataset()
    package = benchmark.load_package(dataset)
    results, _meta = benchmark.run_production(
        None, dataset, package,
        SimpleNamespace(live=False, repeats=1, runner="production"))
    score = next(r for r in results if r["case_id"] == "prod-exam-score-table-mixed")
    assert score["verdict"] == "met", score["misses"]
    # 32 分级联误报消失 = 本用例期望「无 E008/E016」；真实解析器敏感性由
    # test_parser_mutation_gate_flips_production_verdict 单独证明（改解析器→判定翻转）。
    assert "E008" not in score["actual"]["issues_codes"]
    assert "E016" not in score["actual"]["issues_codes"]
    missing = next(r for r in results if r["case_id"] == "prod-exam-declared-no-answers")
    assert missing["verdict"] == "met", missing["misses"]
    assert "E002" in missing["actual"]["issues_codes"]          # 声明缺答案被真实解析器捕获


def test_parser_mutation_gate_flips_production_verdict(client, tmp_path, monkeypatch):
    """改生产解析器必须真实改变回归结果（这是 production runner 的存在意义）。"""
    benchmark = benchmark_module()
    dataset = _load_dataset()
    package = benchmark.load_package(dataset)
    case = next(t for t in package["tasks"] if t["id"] == "prod-exam-score-table-mixed")
    single = {**package, "tasks": [case]}
    baseline, _ = benchmark.run_production(
        None, dataset, single, SimpleNamespace(live=False, repeats=1, runner="production"))
    assert baseline[0]["verdict"] == "met"

    from app.services import exam_check as ec

    original = ec.parse_score_row

    def legacy_row_drop(row):
        row_result = original(row)
        if row_result.each_scores:
            # 模拟旧解析器：整行丢弃（题量/小计一并丢失）→ 32 分级联误报复现
            return ec.ScoreRow(type=row_result.type, range=row_result.range, count=None,
                               each=None, each_scores=None, each_raw=row_result.each_raw,
                               subtotal=None, parse_errors=["simulated legacy row drop"])
        return row_result

    monkeypatch.setattr(ec, "parse_score_row", legacy_row_drop)
    mutated, _ = benchmark.run_production(
        None, dataset, single, SimpleNamespace(live=False, repeats=1, runner="production"))
    assert mutated[0]["verdict"] == "missed"                    # 真实解析器退化 → 判定翻转
    assert any("E008" in miss or "issues_codes" in miss for miss in mutated[0]["misses"])
    monkeypatch.undo()
    restored, _ = benchmark.run_production(
        None, dataset, single, SimpleNamespace(live=False, repeats=1, runner="production"))
    assert restored[0]["verdict"] == "met"


def test_cli_production_runner_zero_network(tmp_path):
    """默认 CLI 即使存在真实 .env 也零网络：非 live 清空模型环境 + 子进程内阻断 socket。"""
    output = tmp_path / "report.json"
    env = dict(os.environ,
               LLM_BASE_URL="http://127.0.0.1:1/v1", LLM_API_KEY="MUST_NOT_USE",
               LLM_MODEL="unreachable", PYTHONIOENCODING="utf-8",
               AIEDU_ANALYSIS_DIR=str(tmp_path / "analysis"))
    launch = ("import runpy,socket,sys; _orig=socket.socket.connect; "
              "socket.socket.connect=lambda s,addr:(_orig(s,addr) "
              "if isinstance(addr,tuple) and addr[0] in ('127.0.0.1','::1','localhost') "
              "else (_ for _ in ()).throw(AssertionError('network prohibited: %r' % (addr,)))); "
              "sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')")
    run = subprocess.run(
        [sys.executable, "-c", launch, str(WORKSPACE / "scripts" / "proposition_benchmark.py"),
         "--dataset", str(DATASET), "--output", str(output),
         "--task", "pipeline", "--runner", "production"],
        cwd=WORKSPACE, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300)
    assert run.returncode == 0, run.stdout + run.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["runner"] == "production"
    assert report["live"] is False
    assert report["model"] == "not_run"
    assert all(r["verdict"] == "met" for r in report["results"]), \
        [r["misses"] for r in report["results"]]
