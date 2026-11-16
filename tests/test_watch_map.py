"""工单验收：`PY311 -I -m pytest tests/test_watch_map.py -q`。纯映射 + attach 成败两条面。"""
from __future__ import annotations

import support


def _rec(**over):
    rec = {
        "type": "MESSAGE",
        "timestamp": 1762000000.5,
        "plugin_id": None,
        "source": "user:123",
        "priority": 0,
        "content": "你好",
        "metadata": {"conversation_id": "conv-1", "user_id": "123", "user_name": "阿神"},
        "message_id": "m1",
        "message_type": "MESSAGE",
        "description": None,
    }
    rec.update(over)
    return rec


def test_record_maps_to_msg_frame(bridge):
    frame = bridge.watch.record_to_msg_frame(_rec())
    assert frame["t"] == "msg" and frame["v"] == 1
    assert frame["conv"] == "conv-1"
    assert frame["sender"] == {"id": "123", "name": "阿神"}
    assert frame["kind"] in bridge.watch.cbp.MSG_KINDS
    assert frame["segments"] == [{"type": "text", "data": {"text": "你好"}}]
    assert frame["raw"]["message_id"] == "m1"  # meta.raw 恒留


def test_missing_conv_and_empty_segments_yield_none(bridge):
    w = bridge.watch
    assert w.record_to_msg_frame(_rec(metadata={}))["conv"] == "user:123"  # source 退路兜 conv
    assert w.record_to_msg_frame(_rec(source="", metadata={})) is None
    assert w.record_to_msg_frame(_rec(content="", metadata={"conversation_id": "c"})) is None
    assert w.record_to_msg_frame("nonsense") is None


def test_rich_parts_pass_through_unknown_types(bridge):
    rec = _rec(content="", metadata={"conversation_id": "c2",
                                     "parts": [{"type": "pdf", "data": {"file": "a.pdf"}}]})
    frame = bridge.watch.record_to_msg_frame(rec)
    assert frame["segments"] == [{"type": "pdf", "data": {"file": "a.pdf"}}]  # 桥不吞，内核兜底


def test_dump_object_supported(bridge):
    class Rec:
        def dump(self):
            return _rec()

    assert bridge.watch.record_to_msg_frame(Rec())["conv"] == "conv-1"


def test_buswatch_delta_and_attach(bridge):
    frames: list[dict] = []
    w = bridge.watch.BusWatch(emit=frames.append)
    w.on_delta(support.FakeDelta(added=[_rec()], changed=[], removed=["k"]))
    assert len(frames) == 1 and frames[0]["t"] == "msg"

    ctx = support.FakeCtx()
    assert w.attach(ctx) is True
    delta_cb = ctx.bus.messages._watcher.cb
    delta_cb(support.FakeDelta(added=[_rec(content="二次")]))
    assert len(frames) == 2

    fail_ctx = support.FakeCtx(bus=support.FakeBus(fail=True))
    assert bridge.watch.BusWatch(emit=lambda f: None).attach(fail_ctx) is False


def test_snapshot_stub_honors_limit_constant(bridge):
    import asyncio

    out = asyncio.run(bridge.watch.BusWatch(emit=lambda f: None).snapshot())
    assert out == []
    assert bridge.watch.GET_RECENT_MAX_LIMIT == 1000  # r-neko §7 实值，不是推测
