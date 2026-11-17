"""N8 turn 保留窗同源读取（工单二）：`turns.TTL_SECONDS=300` 字面量退场，改读生成表 + 环境变量 + 降级。

读序（工单二 N8/F2，用户 2026-11-17 拍板缺省 300）：
1. 环境变量 `CORTICO_TURN_TTL_SECONDS`（正数）；
2. 生成表 `cbp_table.TURN_TTL_SECONDS`（fleet 落该行）；
3. 表行缺位 → 回落 300 并记 degraded（就近可读、不静默）。
跑法：`PY311 -I -m pytest tests/test_turn_ttl_source.py -q`。
"""
from __future__ import annotations

import asyncio
import json
import sys
import types
from unittest import mock

import support

from test_ready_flip import _ScriptedWs, _valid_ready  # 同款假 ws 与合法 ready


def _empty_table():
    return types.SimpleNamespace()  # getattr(...,"TURN_TTL_SECONDS",None) → None


def test_source_has_no_hardcoded_ttl_literal(bridge):
    """字面量必须消失：`turns.TTL_SECONDS` 不再作为模块常量存在。"""
    assert not hasattr(bridge.turns, "TTL_SECONDS"), "TTL_SECONDS 字面量未退场（工单点名要删）"


def test_env_var_overrides_table(bridge):
    turns = bridge.turns
    with mock.patch.dict("os.environ", {turns.ENV_TURN_TTL: "90"}), \
            mock.patch.object(turns, "cbp_table", _empty_table()):
        ttl, degraded = turns.turn_ttl_seconds()
    assert ttl == 90.0 and degraded is None


def test_table_value_is_read_when_present(bridge):
    """fleet 落了 TURN_TTL_SECONDS 行：就近可读，取表值、无 degraded（跨仓等值的那一头）。"""
    turns = bridge.turns
    table = types.SimpleNamespace(TURN_TTL_SECONDS=42)
    with mock.patch.dict("os.environ", {}, clear=True), \
            mock.patch.object(turns, "cbp_table", table):
        ttl, degraded = turns.turn_ttl_seconds()
    assert ttl == 42.0 and degraded is None


def test_falls_back_to_300_with_degraded_when_table_missing(bridge):
    """生成表没有 TURN_TTL_SECONDS 行、环境变量也没设：回落 300 + degraded=turn-ttl-table-missing。"""
    turns = bridge.turns
    with mock.patch.dict("os.environ", {}, clear=True), \
            mock.patch.object(turns, "cbp_table", _empty_table()):
        ttl, degraded = turns.turn_ttl_seconds()
    assert ttl == 300.0 and degraded == "turn-ttl-table-missing"


def test_env_invalid_uses_table_or_fallback(bridge):
    """环境变量非正数：不许崩，回落到表或 300 并记 degraded(turn-ttl-env-invalid)。"""
    turns = bridge.turns
    with mock.patch.dict("os.environ", {turns.ENV_TURN_TTL: "0"}), \
            mock.patch.object(turns, "cbp_table", _empty_table()):
        ttl, degraded = turns.turn_ttl_seconds()
    assert ttl == 300.0 and degraded == "turn-ttl-env-invalid"
    table = types.SimpleNamespace(TURN_TTL_SECONDS=301)
    with mock.patch.dict("os.environ", {turns.ENV_TURN_TTL: "abc"}), \
            mock.patch.object(turns, "cbp_table", table):
        ttl, degraded = turns.turn_ttl_seconds()
    assert ttl == 301.0 and degraded == "turn-ttl-env-invalid"


def test_ledger_uses_resolved_window_and_reports_degraded(bridge):
    """账本缺省构造走同源解析：ttl_seconds/ttl_degraded 与解析一致；显式 ttl 覆盖不算降级。"""
    turns = bridge.turns
    with mock.patch.dict("os.environ", {turns.ENV_TURN_TTL: "77"}), \
            mock.patch.object(turns, "cbp_table", _empty_table()):
        led = turns.TurnLedger()
    assert led.ttl_seconds == 77.0 and led.ttl_degraded is None
    with mock.patch.dict("os.environ", {}, clear=True), \
            mock.patch.object(turns, "cbp_table", _empty_table()):
        led2 = turns.TurnLedger()
    assert led2.ttl_seconds == 300.0 and led2.ttl_degraded == "turn-ttl-table-missing"
    assert turns.TurnLedger(ttl=5.0).ttl_degraded is None


def test_degraded_event_frame_emitted_after_first_ready(bridge, monkeypatch):
    """表行缺位时的「记一条 degraded」走 CBP 线上形态：首个合法 ready 后发
    `event{name:"degraded", data:{reason:"turn-ttl-table-missing", ttl_seconds:300}}`，只发一次。"""
    turns = bridge.turns
    ws = _ScriptedWs([_valid_ready(bridge), _valid_ready(bridge)])
    fake = types.SimpleNamespace(connect=lambda uri, **kw: ws)
    monkeypatch.setitem(sys.modules, "websockets", fake)
    plugin = bridge.CorticoBridgePlugin(support.FakeCtx())
    plugin._pins = support.PINS_SAMPLE
    plugin._link_open = True
    plugin._outgoing = asyncio.Queue()
    with mock.patch.dict("os.environ", {}, clear=True), \
            mock.patch.object(turns, "cbp_table", _empty_table()):
        plugin._turns = turns.TurnLedger()

    async def _go():
        task = asyncio.create_task(plugin._link_once())
        seen = 0
        for _ in range(150):
            await asyncio.sleep(0.01)
            seen = sum(1 for s in ws.sent
                       if json.loads(s).get("t") == "event"
                       and json.loads(s).get("name") == "degraded")
            if seen:
                break
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        return seen

    asyncio.run(_go())
    events = [json.loads(s) for s in ws.sent
              if json.loads(s).get("t") == "event" and json.loads(s).get("name") == "degraded"]
    assert len(events) == 1, f"degraded 应恰好一条（ready 只报一次）；实收 {events}"
    assert events[0]["data"]["reason"] == "turn-ttl-table-missing"
    assert events[0]["data"]["ttl_seconds"] == 300.0
