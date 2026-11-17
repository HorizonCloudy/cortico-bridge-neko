"""工单 F4/N4 验收：`PY311 -I -m pytest tests/test_link_out_pump.py -q`。

改前 `_link_once` 只在收到对端帧之后的 drain 排空 `_outgoing`。没有入帧时自发帧永远发不出去
（内核 PING 20 秒 → 最坏迟 20 秒，吃掉对端 30 秒关联窗，造出假 60007）。
工单验收语：ready 之后调 `_emit_frame`，在**没有任何入帧**的假 ws 上看到那一帧，并给出时间差实测。
"""
from __future__ import annotations

import asyncio
import sys
import types

import support

SEGS = [{"type": "text", "data": {"text": "hello"}}]


class _BlockingWs:
    """假 ws：可 `async with`，记录每次 send，入站永不产帧（`__anext__` 一直等到被 cancel）。"""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self._gate = asyncio.Event()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, data):
        self.sent.append(data)

    def __aiter__(self):
        return self

    async def __anext__(self):
        await self._gate.wait()
        raise StopAsyncIteration


async def _drive_link(bridge, ws):
    """启动 `_link_once`（零入帧），握手就位后排一条自发帧，实测从入队到上线的时间差。"""
    plugin = bridge.CorticoBridgePlugin(support.FakeCtx())
    plugin._pins = support.PINS_SAMPLE
    plugin._link_open = True
    plugin._outgoing = asyncio.Queue()
    plugin._turns = bridge.turns.TurnLedger()
    msg = bridge.cbp.make_frame(
        "msg", conv="c1", sender={"id": "u1", "name": "n"}, kind="direct",
        segments=SEGS, raw={"source": "onebot", "metadata": {"user_id": "u1"}})
    want = bridge.cbp.encode(msg)
    task = asyncio.create_task(plugin._link_once())
    try:
        loop = asyncio.get_running_loop()
        for _ in range(200):  # 等 hello 上线：链路已建立、独立写手已挂上
            await asyncio.sleep(0.01)
            if ws.sent:
                break
        assert ws.sent, "hello 未上线：链路没建立"
        plugin._link_ready = True  # 等价于收到合法 ready（N3 另有用例钉翻转本身）
        t0 = loop.time()
        plugin._emit_frame(msg)
        assert plugin._outgoing.qsize() == 1
        while loop.time() - t0 < 5.0 and want not in ws.sent:
            await asyncio.sleep(0.001)
        dt = loop.time() - t0
        return want, ws.sent, dt
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


def test_out_frame_goes_out_with_no_inbound(bridge, monkeypatch):
    """F4 工单验收：零入帧的假 ws 上，ready 后排的自发帧被独立写手发出。回退修复后此断言转红。"""
    ws = _BlockingWs()
    fake = types.SimpleNamespace(connect=lambda uri, **kw: ws)
    monkeypatch.setitem(sys.modules, "websockets", fake)
    want, sent, _dt = asyncio.run(_drive_link(bridge, ws))
    assert want in sent, f"出站帧未在无入帧时发出；ws.sent={sent!r}"


def test_out_frame_latency_measured(bridge, monkeypatch):
    """N4 时间差实测：自发帧从入队到上线是毫秒级，不是搭车模式的 20 秒级。

    阈值 1.0 秒对照内核 PING_INTERVAL_MS=20000（driver.ts:37）：搭车排空最坏迟 20 秒，
    还会吃掉对端 30 秒关联窗（tools.py:72）造出假 60007。回退修复后帧永不发出，转红。
    """
    ws = _BlockingWs()
    fake = types.SimpleNamespace(connect=lambda uri, **kw: ws)
    monkeypatch.setitem(sys.modules, "websockets", fake)
    want, _sent, dt = asyncio.run(_drive_link(bridge, ws))
    assert want in _sent
    assert dt < 1.0, f"自发帧上线耗时 {dt:.3f}s，不在独立写手量级（对照 PING 20s）"
