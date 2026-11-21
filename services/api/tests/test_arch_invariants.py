"""架构与跨语言不变量：让「文档过期 / 双端副本分叉」变成测试失败。

背景：2026-10-30 的审计发现 docs/architecture.md 描述的还是已废弃的 10 阶段流程
（代码早已是 5 阶段）；4 天后补写的解析文档也错了 4 项（路由 / 表 / 模块数）。
散文文档一定会过期，过期文档比没有更糟 —— AI 会信任它然后返工。
所以这里把三类「过期后代价高」的知识变成可执行检查：

1. v2 状态/生成契约与前端同步；旧阶段 URL 重定向到只读兼容页
2. docs/ARCHITECTURE.generated.md 是否与当前代码一致（过期即红，跑一次脚本修复）
3. specs.ts ↔ main.js 规范预览实现（等价副本，靠人肉比对不可持续）

这些测试不测业务逻辑 —— 业务逻辑归各功能测试文件。它们只回答一个问题：
「AI / 人读到的心智模型，是否还和代码一致」。
"""
from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
from pathlib import Path

import pytest

# 用应用自己的 REPO_ROOT（services/api/app/config.py），而不是手数 parents——
# 数错一层就会把 services/ 当成仓库根（首轮就犯过）。
from app.config import REPO_ROOT  # noqa: E402  conftest 已把 services/api 加进 sys.path
assert (REPO_ROOT / "AGENTS.md").exists(), f"REPO_ROOT 解析错误：{REPO_ROOT}"


def _read(path: str | Path) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def _load_arch_map():
    script = REPO_ROOT / "scripts" / "arch_map.py"
    spec = importlib.util.spec_from_file_location("aiedu_arch_map", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------- v2 双端一致

def test_frontend_generated_contract_is_backend_owned():
    assert _read("apps/web/lib/v2/contracts.generated.ts") == _read("services/api/schema/task-run-v2.generated.ts")
    import sys
    result = subprocess.run([sys.executable, str(REPO_ROOT / "scripts/generate_v2_contracts.py"), "--check"],
                            cwd=REPO_ROOT, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr


def test_frontend_v2_quality_labels_cover_backend_contract():
    from app.v2.responses import QualityReport
    from typing import get_args
    ts = _read("apps/web/lib/v2/status.ts")
    for status in get_args(QualityReport.model_fields["tqi_verdict"].annotation):
        assert f'case "{status}"' in ts
    for status in ("queued", "running", "paused", "completed", "failed", "interrupted", "awaiting_input"):
        assert f'case "{status}"' in ts
    assert "不表示质量通过" in ts


def test_legacy_phase_urls_redirect_to_readonly_and_v2_routes_exist():
    config = _read("apps/web/next.config.ts")
    assert 'source: "/goals/:path*", destination: "/legacy/goals"' in config
    assert 'source: "/tutorials/:path*", destination: "/legacy/tutorials"' in config
    assert "permanent: false" in config
    for route in ("tasks/[id]", "tasks/[id]/read", "tasks/[id]/quality", "legacy/[kind]", "new"):
        assert (REPO_ROOT / "apps/web/app" / route / "page.tsx").is_file()
    assert 'const BASE = "/api/v2"' in _read("apps/web/lib/v2/client.ts")


# ------------------------------------------------------ 架构地图不过期

def test_architecture_map_is_current():
    """docs/ARCHITECTURE.generated.md 必须与当前代码一致。

    该文件由 scripts/arch_map.py 从 openapi + SQLAlchemy metadata + 文件系统生成，
    记录路由 / 数据表 / 模块 / 前端页面。代码变了不重新生成，这里就会失败 ——
    这是让「文档过期」从静默变成显式报警的机制。
    """
    doc = REPO_ROOT / "docs" / "ARCHITECTURE.generated.md"
    assert doc.exists(), (
        "docs/ARCHITECTURE.generated.md 不存在。\n"
        "运行：cd services/api 后 .venv/Scripts/python.exe ../scripts/arch_map.py"
    )
    expected = _load_arch_map().build_report()
    actual = doc.read_text(encoding="utf-8")
    assert actual == expected, (
        "docs/ARCHITECTURE.generated.md 已过期（代码变了，地图没重新生成）。\n"
        "运行：cd services/api 后 .venv/Scripts/python.exe ../scripts/arch_map.py\n"
        + _first_diff_line(actual, expected)
    )


def _first_diff_line(actual: str, expected: str) -> str:
    a, e = actual.splitlines(), expected.splitlines()
    for i, (x, y) in enumerate(zip(a, e), 1):
        if x != y:
            return f"首个差异（行 {i}）：\n  文件: {x[:160]}\n  期望: {y[:160]}"
    longer, which = (a, "文件") if len(a) > len(e) else (e, "期望")
    return f"行数不一致（文件 {len(a)} / 期望 {len(e)}），{which} 多出：{longer[min(len(a), len(e))][:160]}"


# --------------------------------------------- 规范预览双端副本一致

def test_spec_preview_implementations_are_in_sync():
    """specs.ts 与 main.js 的规范预览实现必须逐字等价。

    插件不能 import 前端代码，两份是手工同步的等价副本（skill 有记录）。
    靠人肉比对不可持续，这里交给 scripts/verify_spec_preview_sync.mjs：
    用同一批 fixtures（全字段 / 空字段 / 交付物边界 / scene 分支）跑两份实现，
    逐字比较输出，并核对 SPEC_GROUPS / SPEC_DELIVERABLE_STATUS 数据与分组标签。
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用，无法运行跨语言一致性校验")
    script = REPO_ROOT / "scripts" / "verify_spec_preview_sync.mjs"
    assert script.exists(), f"{script} 不存在"
    result = subprocess.run(
        [node, str(script)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=120,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, (
        "specs.ts 与 main.js 的规范预览实现已分叉（两份必须逐字等价）：\n"
        f"{result.stdout}\n{result.stderr}"
    )
