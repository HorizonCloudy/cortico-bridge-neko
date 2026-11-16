"""pytest 装配：把 tests/ 与仓父目录放进 sys.path，暴露 bridge/fake-ctx/pins 夹具。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
sys.path.insert(0, str(TESTS_DIR))

import support  # noqa: E402


@pytest.fixture(scope="session")
def bridge():
    """宿主同款配方加载出来的 cortico_bridge_neko 包（含子模块命名空间）。"""
    return support.load_bridge_package()


@pytest.fixture()
def pins_file(tmp_path, monkeypatch):
    """临时 pins.json + 环境变量指向；测试内不出现任何写死端口来源。"""
    path = support.write_pins(tmp_path)
    monkeypatch.setenv("CORTICO_PINS_JSON", path)
    monkeypatch.delenv("MAIN_SERVER_PORT", raising=False)
    monkeypatch.delenv("NEKO_MESSAGE_PLANE_RPC", raising=False)
    return path


@pytest.fixture()
def fake_ctx():
    return support.FakeCtx()


@pytest.fixture()
def no_credentials(monkeypatch):
    """凭据围栏：自省/单测期间相关环境变量一律清空（安装面隔离要求）。"""
    for name in ("NEKO_TOKEN", "BOT_TOKEN", "QQ_TOKEN", "API_KEY"):
        monkeypatch.delenv(name, raising=False)
    yield
