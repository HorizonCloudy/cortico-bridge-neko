"""N3 ready 翻转的用例（工单二）：合法 ready 让 `_link_ready` False→True，不合法 ready 不翻转。

改前全仓零覆盖这条翻转：`_link_ready` 只在 `_link_once` 内被赋值，没有任何用例喂过 ready 帧。
跑法：`PY311 -I -m pytest tests/test_ready_flip.py -q`。
"""
from __future__ import annotations

import asyncio
import json
import sys
import types

import support


class _ScriptedWs:
    def __init__(self, raws) -> None:
        self.sent: list[str] = []
        self._raws = list(raws)
        self._gate = asyncio.Event()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, data):
        self.sent.append(data)

    def __aiter__(self):
        outer = self

        async def _gen():
            for raw in outer._raws:
                yield raw
            await outer._gate.wait()
        return _gen()


def _plugin_for(bridge):
    plugin = bridge.CorticoBridgePlugin(support.FakeCtx())
    plugin._pins = support.PINS_SAMPLE
    plugin._link_open = True
    plugin._outgoing = asyncio.Queue()
    plugin._turns = bridge.turns.TurnLedger()
    return plugin


def _drive(bridge, plugin, raws, probe_rounds=30):
    """跑 `_link_once`，喂 raws，在链路存活期间取 `_link_ready` 快照，再收账。"""
    ws = _ScriptedWs(raws)
    fake = types.SimpleNamespace(connect=lambda uri, **kw: ws)
    old = sys.modules.get("websockets")
    sys.modules["websockets"] = fake

    async def _go():
        plugin._link_ready = False
        task = asyncio.create_task(plugin._link_once())
        seen = False
        for _ in range(probe_rounds):
            await asyncio.sleep(0.01)
            if plugin._link_ready:
                seen = True
                break
        mid = plugin._link_ready
        alive = not task.done()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        return seen, mid, alive

    try:
        return asyncio.run(_go())
    finally:
        if old is None:
            sys.modules.pop("websockets", None)
        else:
            sys.modules["websockets"] = old


def _valid_ready(bridge) -> str:
    return bridge.cbp.encode(bridge.cbp.make_frame(
        "ready", caps=["push_message_v2"], tools=[], self=bridge.cbp.make_self_decl()))


def test_valid_ready_flips_link_ready(bridge):
    """合法 ready：翻转成立，且握手判据在链路存活期可见。"""
    plugin = _plugin_for(bridge)
    seen, mid, alive = _drive(bridge, plugin, [_valid_ready(bridge)])
    assert alive, "ready 帧处理不得拆链"
    assert seen and mid, "合法 ready 后 _link_ready 必须为 True"


def test_ready_gates_emit_and_flips_back_on_exit(bridge):
    """翻转的另一半：ready 前 _emit_frame 只计数不排队；ready 后排得上。链路退出翻回 False。"""
    plugin = _plugin_for(bridge)
    quiet = bridge.cbp.make_frame(
        "msg", conv="c1", sender={"id": "u1", "name": "n"}, kind="direct",
        segments=[{"type": "text", "data": {"text": "hi"}}],
        raw={"source": "onebot", "metadata": {"user_id": "u1"}})
    ws = _ScriptedWs([_valid_ready(bridge)])
    fake = types.SimpleNamespace(connect=lambda uri, **kw: ws)
    old = sys.modules.get("websockets")
    sys.modules["websockets"] = fake

    async def _go():
        plugin._link_ready = False
        task = asyncio.create_task(plugin._link_once())
        plugin._emit_frame(quiet)  # ready 未到：丢弃并计数
        dropped_before = plugin._outgoing.qsize() == 0
        seen = False
        for _ in range(30):
            await asyncio.sleep(0.01)
            if plugin._link_ready:
                seen = True
                break
        plugin._emit_frame(quiet)  # ready 已到：排进待发队列
        queued_after = plugin._outgoing.qsize() > 0 or any(
            json.loads(s).get("t") == "msg" for s in ws.sent)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        return dropped_before, seen, queued_after

    try:
        dropped_before, seen, queued_after = asyncio.run(_go())
    finally:
        if old is None:
            sys.modules.pop("websockets", None)
        else:
            sys.modules["websockets"] = old
    assert seen and dropped_before and queued_after
    assert plugin._link_ready is False, "链路退出后 finally 必须翻回 False"


def test_invalid_ready_does_not_flip(bridge):
    """不合法 ready（缺 self、v 不对、t 误标）解码为 None，不翻转、不拆链。"""
    bad = [
        json.dumps({"v": 1, "t": "ready", "id": "b1", "ts": 1.0, "caps": [], "tools": []}),  # 缺 self
        json.dumps({"v": 99, "t": "ready", "id": "b2", "ts": 1.0, "caps": [], "tools": [],
                    "self": {"id": "", "name": "", "proven": False}}),  # v 不合规
        "not json at all",
    ]
    plugin = _plugin_for(bridge)
    seen, mid, alive = _drive(bridge, plugin, bad, probe_rounds=30)
    assert alive, "坏 ready 不得拆链"
    assert not seen and mid is False, "不合法 ready 不许翻转 _link_ready"
    assert plugin._frames_bad == 3, plugin._frames_bad
