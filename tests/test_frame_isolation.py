"""N1 读帧循环逐帧异常隔离（工单二）：一帧炸不掉整条 WS 链，坏帧吞掉并计数，call 帧 reply 必回。

改前 `__init__.py` 读帧循环没有任何 except：handle_frame 抛非 DoorError（如 pins 缺 caps 的
RuntimeError、真宿主 push 的 TypeError）会拆掉整条链，那一帧的 reply 永远不回，
对端 30 秒关联窗吃满翻成假 60007（VERIFY-U1 的 S1 复现形）。
跑法：`PY311 -I -m pytest tests/test_frame_isolation.py -q`。
"""
from __future__ import annotations

import asyncio
import json
import sys
import types

import support

ACTOR = {"platform": "onebot", "conv": "c1",
         "member": {"id": "u1", "name": "n"}, "anon": "identified"}


class _ScriptedWs:
    """假 ws：先按序吐 raws，之后挂住不吐（模拟无入帧）。send 全记录。"""

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


def _plugin_for(bridge, pins=None):
    ctx = support.FakeCtx(push_results=[{"submitted": True}] * 4)
    plugin = bridge.CorticoBridgePlugin(ctx)
    plugin._pins = support.PINS_SAMPLE if pins is None else pins
    plugin._egress_open = True
    plugin._link_open = True
    plugin._outgoing = asyncio.Queue()
    plugin._turns = bridge.turns.TurnLedger()
    msg = bridge.cbp.make_frame(
        "msg", conv="c1", sender={"id": "u1", "name": "n"}, kind="direct",
        segments=[{"type": "text", "data": {"text": "in"}}],
        raw={"source": "onebot", "metadata": {"user_id": "u1"}})
    plugin._turns.record(msg)  # 有入站记录，call 才走得出身份门、够到 push_kwargs
    plugin._known_turn = msg["id"]
    return plugin


def _run_link(bridge, plugin, raws, settle_s: float = 1.5):
    """在假 websockets 上跑 `_link_once`，喂完 raws 后观察链是否仍活着，再收账。"""
    ws = _ScriptedWs(raws)
    fake = types.SimpleNamespace(connect=lambda uri, **kw: ws)
    old = sys.modules.get("websockets")
    sys.modules["websockets"] = fake

    async def _drive():
        task = asyncio.create_task(plugin._link_once())
        deadline = asyncio.get_running_loop().time() + settle_s
        while asyncio.get_running_loop().time() < deadline and not task.done():
            await asyncio.sleep(0.01)
        running = not task.done()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        return running

    try:
        running = asyncio.run(_drive())
    finally:
        if old is None:
            sys.modules.pop("websockets", None)
        else:
            sys.modules["websockets"] = old
    return ws.sent, running


def _call_wire(bridge, rid: str, turn: str = "missing-turn") -> str:
    return bridge.cbp.encode(bridge.cbp.make_frame(
        "call", rid=rid, tool="neko_send", args={"conv": "c1", "text": "hi"},
        turn=turn, actor=ACTOR))


def _sents_by_code(sent):
    out = {}
    for raw in sent:
        f = json.loads(raw)
        if f.get("t") == "reply":
            out[f["rid"]] = (f.get("ok"), (f.get("error") or {}).get("code"))
    return out


def test_handler_exception_still_replies_and_link_survives(bridge):
    """pins 缺 caps 让 handle_frame 抛 RuntimeError（VERIFY-U1 的 S1 复现形：门全过、够到
    push_kwargs 才炸）。改前：整条链拆掉、r1 无回帧。改后：r1 回 20002，链继续，r2 照处理。"""
    plugin = _plugin_for(bridge, pins={"ports": {"cortico_world_neko_ws": 45991}})  # 无 caps 段
    turn = plugin._known_turn
    sent, running = _run_link(bridge, plugin, [_call_wire(bridge, "r1", turn),
                                               _call_wire(bridge, "r2", turn)])
    codes = _sents_by_code(sent)
    assert codes.get("r1") == (False, 20002), f"call 炸帧必回 20002 reply；实收 {codes}"
    assert codes.get("r2") == (False, 20002), "坏帧之后的 call 仍被处理 = 链没被拆"
    assert running, "读帧循环不得因单帧异常退出"
    assert plugin._frames_bad == 2, plugin._frames_bad


def test_bad_frame_swallowed_and_later_call_still_replied(bridge):
    """坏帧（解码 None）吞掉并计数；之后的正常 call 照拿自己的 60004 回帧。"""
    plugin = _plugin_for(bridge)
    raws = ["{not json", b"\xff\xfe bad bytes", _call_wire(bridge, "r9")]
    sent, running = _run_link(bridge, plugin, raws)
    codes = _sents_by_code(sent)
    assert codes.get("r9") == (False, 60004), f"坏帧后的正常 call 要拿到回帧；实收 {codes}"
    assert running and plugin._frames_bad == 2, plugin._frames_bad


def test_peer_on_frame_exception_swallowed(bridge):
    """非 call 帧交 PeerTools.on_frame 抛错：吞掉 + 计数 + 链存活。"""
    plugin = _plugin_for(bridge)

    class _Boom:
        def on_frame(self, frame):
            raise ValueError("catalog boom")

    plugin._peer_tools = _Boom()
    wire = json.dumps({"v": 1, "t": "reply", "id": "z1", "ts": 1.0, "rid": "x1", "ok": True})
    sent, running = _run_link(bridge, plugin, [wire, wire])
    assert running, "on_frame 炸掉不得拆链"
    assert plugin._frames_bad == 2, plugin._frames_bad
