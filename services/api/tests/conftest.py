"""pytest 配置：使用独立测试数据库，并在导入应用前设置环境变量。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 并行测试隔离：AIEDU_TEST_SUFFIX 给每个测试进程一套独立的库 / 材料 / 备份 / 模型配置路径。
# 必须可隔离，因为 client fixture 在 setup 时对同一个库做 drop_all + create_all：
# 两个 pytest 进程同时跑时，一方的 drop_all 会把另一方正在用的表删掉，产生
# 「no such table」「database is locked」这类与被测代码无关的假失败。
# 不设置该变量时后缀为空串，路径与从前逐字一致（默认行为不变）。
_SUFFIX = os.environ.get("AIEDU_TEST_SUFFIX", "")

TEST_DB = Path(__file__).resolve().parent / f"test_aiedu{_SUFFIX}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
# 上传材料的落盘目录也放到测试目录，避免污染 data/materials
TEST_MATERIALS = Path(__file__).resolve().parent / f"test_materials{_SUFFIX}"
os.environ["MATERIALS_DIR"] = TEST_MATERIALS.as_posix()
# 删除目标前的 JSON 备份放到测试目录
os.environ["BACKUP_DIR"] = (Path(__file__).resolve().parent / f"test_backups{_SUFFIX}").as_posix()
# 测试必须运行在演示模式：显式置空（环境下已有的同名变量优先于 .env，防止测试意外调用真实模型）
os.environ["LLM_BASE_URL"] = ""
os.environ["LLM_API_KEY"] = ""
os.environ["LLM_MODEL"] = ""
os.environ["MODEL_SETTINGS_FILE"] = str(Path(__file__).parent / f"test_model_settings{_SUFFIX}.json")
os.environ["AIEDU_BLOB_DIR"] = str(Path(__file__).parent / f"test_materials{_SUFFIX}" / "blobs")
os.environ["AIEDU_EXPORT_DIR"] = str(Path(__file__).parent / f"test_materials{_SUFFIX}" / "exports")
os.environ["AIEDU_ALLOW_PAID"] = "0"
os.environ["AIEDU_START_WORKER"] = "0"
os.environ["AIEDU_ENABLE_LEGACY_API"] = "1"  # isolated baseline regression only
os.environ.pop("AIEDU_V2_ALLOWED_HOSTS", None)
os.environ.pop("AIEDU_V2_ALLOWED_ORIGIN_HOSTS", None)
os.environ.pop("CORS_ORIGINS", None)

# 反馈证据 / 基准报告等离线分析产物（JSON 文件，板块五）也放到测试目录
os.environ["AIEDU_ANALYSIS_DIR"] = (Path(__file__).resolve().parent / f"test_analysis{_SUFFIX}").as_posix()

# Windows + Python 3.12.10：pathlib.Path.mkdir 会把 POSIX mode 映射进 ACL，而 pytest 的
# tmpdir 固定用 mode=0o700 建目录，产出的 ACL 随后对创建进程自身都拒绝列举/删除
# （tmp_path 用例 setup 报 PermissionError WinError 5，目录连 icacls 都读不了）。
# 只在测试进程内把这一个 mode 放宽为包含组/其它读权限的等价 mode，目录保持可访问；
# 生产代码路径不经过这里，行为不受影响。
if os.name == "nt":
    from pathlib import Path as _Path

    _orig_mkdir = _Path.mkdir

    def _mkdir_windows_safe(self, mode=0o777, parents=False, exist_ok=False):
        if mode & 0o077 == 0:  # 0o700 这类「仅属主」mode 在本机映射出的 ACL 不可访问
            mode = mode | 0o075
        return _orig_mkdir(self, mode, parents, exist_ok)

    _Path.mkdir = _mkdir_windows_safe

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def client():
    Path(os.environ["MODEL_SETTINGS_FILE"]).unlink(missing_ok=True)
    from app.db import Base, engine

    # 保护断言：app.db 必须已经按本 conftest 设置的 DATABASE_URL 创建。
    # 若某个调试脚本在 pytest 之前导入了 app.*（此时尚无 DATABASE_URL），engine 会
    # 指向真实库 data/aiedu.db；下面的 drop_all 会把用户数据整个清掉（2026-11-08
    # 实际发生过一次：bisect 脚本先 import app，再 pytest.main 跑集成测试）。
    expected_db = TEST_DB.resolve().as_posix()
    actual_db = str(engine.url.database or "").replace("\\", "/")
    assert actual_db == expected_db, (
        "测试引擎指向了非测试库，拒绝执行 drop_all/create_all。\n"
        f"  expected: {expected_db}\n  actual:   {actual_db}\n"
        "原因通常是：在 conftest 设置 DATABASE_URL 之前导入了 app.*（例如一次性脚本先 "
        "import app 再 pytest.main）。请改为在子进程/独立 pytest 中运行。"
    )
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    from app.main import app

    with TestClient(app) as c:
        yield c
    from app.services.tutorials import pending_tutorial_workers
    assert not pending_tutorial_workers(), "Refusing to reset schema while background workers are alive"
    Path(os.environ["MODEL_SETTINGS_FILE"]).unlink(missing_ok=True)
