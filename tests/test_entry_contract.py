"""工单验收：`PY311 -I -m pytest tests/test_entry_contract.py -q`。
清单 schema 实跑 + SDK 版本闸语法闸 + 入口类装饰与禁用面扫描。证据 r-neko §2/§3。"""
from __future__ import annotations

import ast
import inspect
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# 端口全集（_BUILD-PLAN §4.4 表 + r-neko §8）：代码里出现任何一枚当字面量用即违规，
# 必须走 pins.json。注释/文档字符串提到数字不算硬编码，所以扫 AST 常量。
KNOWN_PORTS = {
    39701, 39702, 48911, 48912, 48913, 48914, 48915, 48916,
    38865, 38866, 38867, 48961, 48962, 48963, 39539,
    8001, 6185, 6199, 5140, 8080, 5700, 6700, 48965,
}


def _toml() -> dict:
    with open(REPO_ROOT / "plugin.toml", "rb") as fh:
        return tomllib.load(fh)


def test_plugin_toml_passes_real_schema():
    from plugin.config.schema import PluginConfigSchema

    PluginConfigSchema.model_validate(_toml())  # 离线校验（editable 环境 import 属自省）


def test_host_key_forbidden_and_missing_required_rejected():
    from plugin.config.schema import PluginConfigSchema
    from pydantic import ValidationError

    bad_host = _toml()
    bad_host["plugin"]["host"] = "0.9.1"
    with pytest.raises(ValidationError):
        PluginConfigSchema.model_validate(bad_host)
    for key in ("id", "name", "entry"):
        miss = _toml()
        del miss["plugin"][key]
        with pytest.raises(ValidationError):
            PluginConfigSchema.model_validate(miss)


def test_sdk_gate_space_and_specifier_syntax():
    from packaging.specifiers import SpecifierSet
    from plugin._types.version import SDK_VERSION

    sdk = _toml()["plugin"]["sdk"]
    spec = SpecifierSet(sdk["supported"])  # 裸版本/wildcard 会在此抛，复刻 registry 拒载
    assert SDK_VERSION == "0.1.0"  # 闸只认这个空间，不引用 APP_VERSION（R1）
    assert SDK_VERSION in spec
    assert "0.9.1" not in spec  # T1：按 0.9.x 钉 = 永远加载不上
    with pytest.raises(Exception):
        SpecifierSet("0.1.*")  # wildcard 非法（r-neko §2 实测）
    with pytest.raises(Exception):
        SpecifierSet("0.2.0")  # 裸版本串同样判 invalid specifier


def test_gate_with_real_host_function():
    """T1 红绿面用宿主真函数复核（import 级自省，不启动宿主）。返回 5 元组（C1-9）。"""
    from plugin.core.registry import _check_sdk_compatibility

    class _Sink:  # registry 用 loguru 风格 "{}" 传参，stdlib logger 会 % 格式化炸
        def info(self, *a, **k): None
        def warning(self, *a, **k): None
        def error(self, *a, **k): None
        def debug(self, *a, **k): None

    log = _Sink()
    ok = _check_sdk_compatibility("cortico_bridge", _toml()["plugin"]["sdk"], log)
    assert ok[0] is True
    for wrong in (">=0.9.0,<0.10.0", "0.2.0", "0.1.*", "garbage!!"):
        assert _check_sdk_compatibility("p", {"supported": wrong}, log)[0] is False, wrong


def test_entry_class_decorated_and_lifecycle_async(bridge):
    from plugin.sdk.plugin import NEKO_PLUGIN_TAG

    cls = bridge.CorticoBridgePlugin
    assert getattr(cls, NEKO_PLUGIN_TAG, None) is True  # @neko_plugin 唯一动作 = 类标记
    for name in ("_on_command_loop_start", "stop", "handle_frame"):
        assert inspect.iscoroutinefunction(getattr(cls, name)), f"{name} 必须 async（入口约定）"


def test_banned_surface_absent_from_source():
    """禁用面静态扫描：不调 ctx.finish、不依赖 bus publish/delete、端口零字面量。"""
    banned_attrs = {"finish", "publish"}
    for py in sorted(REPO_ROOT.glob("*.py")):
        src = py.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in banned_attrs, (
                    f"{py.name}:{node.lineno} 调用禁用面 .{node.func.attr}()"
                )
            if isinstance(node, ast.Constant) and isinstance(node.value, int):
                assert node.value not in KNOWN_PORTS, (
                    f"{py.name}:{node.lineno} 端口 {node.value} 写死——从 pins.json 取"
                )
