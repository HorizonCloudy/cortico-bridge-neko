"""端口从 pins.json 取数的验收：未指向即报错、缺键即报错、URI 只从文件来。"""
from __future__ import annotations

import os

import pytest
import support


def test_missing_env_refuses_hard(bridge, monkeypatch):
    monkeypatch.delenv("CORTICO_PINS_JSON", raising=False)
    with pytest.raises(RuntimeError) as ei:
        bridge.pins.load_pins()
    assert "CORTICO_PINS_JSON" in str(ei.value)
    # 关键：错误路径里没有兜底端口可走（函数体内不存在 39701/39702 常量）
    import inspect

    src = inspect.getsource(bridge.pins)
    assert "39701" not in src and "39702" not in src


def test_uri_from_fixture_file(bridge, pins_file):
    pins = bridge.pins.load_pins()
    uri = bridge.pins.bridge_ws_uri(pins)  # neko 缺省键 cortico_world_neko_ws
    assert uri == f"ws://127.0.0.1:{support.PINS_SAMPLE['ports']['cortico_world_neko_ws']}"
    uri2 = bridge.pins.bridge_ws_uri(pins, "cortico_world_maibot_ws")
    assert uri2.endswith(str(support.PINS_SAMPLE["ports"]["cortico_world_maibot_ws"]))


def test_bad_pins_payloads_rejected(bridge, tmp_path):
    p = tmp_path / "p1.json"
    p.write_text('{"foo": 1}', encoding="utf-8")
    with pytest.raises(RuntimeError):
        bridge.pins.port_of(bridge.pins.load_pins(str(p)), "cortico_world_neko_ws")
    p.write_text('{"ports": {"cortico_world_neko_ws": 70000}}', encoding="utf-8")
    with pytest.raises(RuntimeError):
        bridge.pins.port_of(bridge.pins.load_pins(str(p)), "cortico_world_neko_ws")
    p.write_text("[1,2]", encoding="utf-8")
    with pytest.raises(RuntimeError):
        bridge.pins.load_pins(str(p))


def test_env_pointed_by_fixture(bridge, pins_file):
    assert os.environ.get("CORTICO_PINS_JSON") == pins_file
