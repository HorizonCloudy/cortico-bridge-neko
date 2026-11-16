"""工单验收：`PY312M -I -m pytest tests/test_cbp_codec.py -q`（neko 侧同套向量，与 maibot 副本对表）。
十型帧逐向量；负例含 null/数组/标量/坏头/缺必填/超长 id。规范源 _BUILD-PLAN §3.1。"""
from __future__ import annotations

import json

HELLO_TOOLS = [{"name": "t1", "risk": "read", "confirm_required": False}]
# F1/F2/F3 之后 hello/ready 必带 self、call 必带 turn+actor（spec/CBP-v1.json:296-380,498-543）。
SELF_FALSE = {"id": "", "name": "", "proven": False}
ACTOR = {"platform": "onebot", "conv": "group_5_4",
         "member": {"id": "2002", "name": "nick"}, "anon": "identified"}

GOOD_FRAMES = [
    {"v": 1, "t": "hello", "id": "h1", "ts": 1762000000.0,
     "agent": {"name": "cortico-bridge-neko", "version": "0.1.0", "sha": "unpinned"},
     "self": SELF_FALSE, "v_support": [1], "caps": ["inbound_msg"], "tools": HELLO_TOOLS},
    {"v": 1, "t": "ready", "id": "r1", "ts": 1.0, "caps": ["inbound_msg"], "tools": [],
     "self": SELF_FALSE},
    {"v": 1, "t": "msg", "id": "m1", "ts": 2.0, "conv": "c1",
     "sender": {"id": "u1", "name": "n"}, "kind": "mention",
     "segments": [{"type": "text", "data": {"text": "hi"}}], "raw": {"x": 1}},
    {"v": 1, "t": "notice", "id": "n1", "ts": 3.0, "kind": "recall", "raw": {}},
    {"v": 1, "t": "notice", "id": "n2", "ts": 3.0, "conv": "c1", "kind": "k", "raw": {}},
    {"v": 1, "t": "ack", "id": "a1", "ts": 4.0, "rid": "m1"},
    {"v": 1, "t": "call", "id": "cl1", "ts": 5.0, "rid": "q1", "tool": "neko_send", "args": {},
     "turn": "m1", "actor": ACTOR},
    {"v": 1, "t": "reply", "id": "rp1", "ts": 6.0, "rid": "q1", "ok": True, "text": "done"},
    {"v": 1, "t": "reply", "id": "rp2", "ts": 6.0, "rid": "q1", "ok": False,
     "error": {"code": 60005, "message": "door_closed"}},
    {"v": 1, "t": "event", "id": "e1", "ts": 7.0, "name": "degraded", "data": {"why": "pdf"}},
    {"v": 1, "t": "tick", "id": "tk1", "ts": 8.0},
    {"v": 1, "t": "catalog", "id": "cat1", "ts": 9.0, "tools": HELLO_TOOLS},
]

BAD_INPUTS = [
    "null", "[1,2]", '"str"', "12", "1.5", "true",
    json.dumps({"v": 2, "t": "tick", "id": "x", "ts": 1.0}),          # 版本外
    json.dumps({"v": 1, "t": "nope", "id": "x", "ts": 1.0}),          # 未知型
    json.dumps({"v": 1, "t": "msg", "id": "x", "ts": 1.0}),           # 缺必填
    json.dumps({"v": 1, "t": "tick", "id": "", "ts": 1.0}),           # id 太短
    json.dumps({"v": 1, "t": "tick", "id": "z" * 65, "ts": 1.0}),     # id 超 64
    json.dumps({"v": 1, "t": "tick", "id": "x"}),                     # 缺 ts
    json.dumps({"v": 1, "t": "tick", "id": "x", "ts": True}),         # bool 冒充数字
    "{not json",
]


def test_good_vectors_roundtrip(bridge):
    for frame in GOOD_FRAMES:
        wire = bridge.cbp.encode(frame)
        assert bridge.cbp.decode(wire) == frame, frame["t"]
        assert bridge.cbp.decode(wire.encode("utf-8")) == frame  # bytes 面同形


def test_bad_vectors_are_dropped(bridge):
    for raw in BAD_INPUTS:
        assert bridge.cbp.decode(raw) is None, raw


def test_encode_refuses_invalid_and_make_frame_fills_header(bridge):
    import pytest

    with pytest.raises(ValueError):
        bridge.cbp.encode({"v": 1, "t": "bogus"})
    f = bridge.cbp.make_frame("tick")
    assert f["v"] == 1 and f["t"] == "tick" and 1 <= len(f["id"]) <= 64 and f["ts"] > 0
    with pytest.raises(ValueError):
        bridge.cbp.make_frame("nope")


def test_frame_type_table_is_ten(bridge):
    assert len(bridge.cbp._REQUIRED) == 10
    assert bridge.cbp.TOOL_RISKS == ("read", "write", "speak", "act", "flow", "snapshot")
