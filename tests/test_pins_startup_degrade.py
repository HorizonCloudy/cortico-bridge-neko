"""N6 pins 异常族收一行（工单二）：坏路径不许从装配钩子里漏出去。

`_on_command_loop_start` 读 pins 的失败面不止 RuntimeError：
- `open()` 坏路径 → FileNotFoundError（OSError）；
- 文件是坏 JSON → json.JSONDecodeError（ValueError）；
- 顶层不是对象 / 环境变量没给 → RuntimeError。
改前只捕 RuntimeError，前两种会把异常抛进宿主 command loop。跑法：
`PY311 -I -m pytest tests/test_pins_startup_degrade.py -q`。
"""
from __future__ import annotations

import asyncio

import support


class _CfgThatExplodes:
    """pins 没读成时钩子必须提前 return，压根不该走到 config.dump()。"""

    async def dump(self):
        raise AssertionError("pins 失败后不许继续装配")


def _plugin(bridge):
    plugin = bridge.CorticoBridgePlugin(support.FakeCtx())
    plugin.config = _CfgThatExplodes()
    return plugin


def _drain_warnings(plugin):
    logs = []

    class _L:
        def warning(self, m, *a):
            logs.append(m)

        def info(self, *a):
            None

    plugin.logger = _L()
    return logs


def test_missing_pins_file_does_not_escape_hook(bridge, monkeypatch):
    monkeypatch.setenv("CORTICO_PINS_JSON", "Z:/no/such/pins.json")
    plugin = _plugin(bridge)
    logs = _drain_warnings(plugin)
    asyncio.run(plugin._on_command_loop_start())  # 改前：FileNotFoundError 抛出来
    assert plugin._link_task is None
    assert any("pins.json load failed" in m for m in logs), logs


def test_malformed_json_pins_does_not_escape_hook(bridge, tmp_path, monkeypatch):
    bad = tmp_path / "pins.json"
    bad.write_text("{ this is not json ", encoding="utf-8")
    monkeypatch.setenv("CORTICO_PINS_JSON", str(bad))
    plugin = _plugin(bridge)
    _drain_warnings(plugin)
    asyncio.run(plugin._on_command_loop_start())  # 改前：JSONDecodeError 抛出来
    assert plugin._link_task is None


def test_top_level_not_object_still_caught(bridge, tmp_path, monkeypatch):
    bad = tmp_path / "pins.json"
    bad.write_text("[1, 2, 3]", encoding="utf-8")
    monkeypatch.setenv("CORTICO_PINS_JSON", str(bad))
    plugin = _plugin(bridge)
    _drain_warnings(plugin)
    asyncio.run(plugin._on_command_loop_start())  # RuntimeError 分支，本来就该捕
    assert plugin._link_task is None


def test_no_env_still_caught(bridge, monkeypatch):
    monkeypatch.delenv("CORTICO_PINS_JSON", raising=False)
    plugin = _plugin(bridge)
    _drain_warnings(plugin)
    asyncio.run(plugin._on_command_loop_start())
    assert plugin._link_task is None
