"""SECURITY-CBP §1/§2 收口负例：call 必填 turn+actor、hello/ready 必填 self、
actor/self 逐键闭合而 call.args 仍开（§1 规则 4 与 F18 的组合）。

向量逐字取自 fleet `spec/CBP-v1.json:166-224,296-343,498-543` 与
`spec/conformance.json` 的 `call_full_actor_bound` / `hello_proven_false` /
`ready_repush_proven_true` / `call_args_extra_key_open` 四条正例。
改动纪律（cbp.py:1-6）：先改 spec 再同步本副本；本文件验的是副本有没有跟上 spec。
"""
from __future__ import annotations

import json

# $defs/anon 四值逐字（CBP-v1.json:189-197，SECURITY-CBP.md:35）
ANON_VERBATIM = ("identified", "anonymous", "self", "unknown")

SELF_FALSE = {"id": "", "name": "", "proven": False}  # conformance hello_proven_false 逐字
SELF_TRUE = {"id": "2838634085", "name": "Cortico", "proven": True}  # ready_repush_proven_true
ACTOR = {  # call_full_actor_bound 逐字
    "platform": "onebot",
    "conv": "group_5_4",
    "member": {"id": "2002", "name": "nick"},
    "anon": "identified",
}


def _frame(t, **over):
    f = {"v": 1, "t": t, "id": f"cv-{t}", "ts": 1763136001.0}
    if t == "hello":
        f.update(agent={"name": "cortico-bridge-neko", "version": "0.1.0", "sha": "123456789"},
                 self=dict(SELF_FALSE), v_support=[1], caps=["images", "notices"], tools=[])
    elif t == "ready":
        f.update(caps=["notices", "tools", "self_echo"], tools=[], self=dict(SELF_TRUE))
    elif t == "call":
        f.update(rid="cv-call-2-rid", tool="neko_send", args={}, turn="cv-msg", actor=dict(ACTOR))
    f.update(over)
    f.pop("_drop", None)
    return f


def _dropped(t, key):
    f = _frame(t)
    del f[key]
    return f


def _wire(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


# ---------- 正例：新必填位进得了线 ----------

def test_hello_with_self_roundtrips(bridge):
    f = _frame("hello")
    assert bridge.cbp.decode(_wire(f)) == f


def test_ready_with_self_roundtrips(bridge):
    f = _frame("ready")
    assert bridge.cbp.decode(_wire(f)) == f


def test_call_with_turn_and_actor_roundtrips(bridge):
    f = _frame("call")
    assert bridge.cbp.decode(_wire(f)) == f


def test_call_args_stays_open(bridge):
    """§1 规则 4：闭合的是新位，args 是旧透传位，多键必须仍合法。"""
    f = _frame("call", args={"anything": 1, "nested": {"ok": True}})
    assert bridge.cbp.decode(_wire(f)) == f


# ---------- 反例：结构性缺位与开键必须判坏帧 ----------

def test_hello_missing_self_is_bad_frame(bridge):
    assert bridge.cbp.decode(_wire(_dropped("hello", "self"))) is None


def test_ready_missing_self_is_bad_frame(bridge):
    assert bridge.cbp.decode(_wire(_dropped("ready", "self"))) is None


def test_call_missing_turn_is_bad_frame(bridge):
    assert bridge.cbp.decode(_wire(_dropped("call", "turn"))) is None


def test_call_missing_actor_is_bad_frame(bridge):
    assert bridge.cbp.decode(_wire(_dropped("call", "actor"))) is None


def test_self_extra_key_is_closed(bridge):
    f = _frame("hello", self={**SELF_FALSE, "role": "assistant"})
    assert bridge.cbp.decode(_wire(f)) is None


def test_self_proven_must_be_boolean(bridge):
    f = _frame("hello", self={"id": "", "name": "", "proven": "false"})
    assert bridge.cbp.decode(_wire(f)) is None


def test_actor_extra_key_is_closed(bridge):
    f = _frame("call", actor={**ACTOR, "role": "admin"})
    assert bridge.cbp.decode(_wire(f)) is None


def test_actor_member_extra_key_is_closed(bridge):
    f = _frame("call", actor={**ACTOR, "member": {"id": "2002", "name": "n", "role": "admin"}})
    assert bridge.cbp.decode(_wire(f)) is None


def test_actor_anon_must_be_one_of_four(bridge):
    for bad in (" Identified", "IDENTIFIED", "ghost", "", None, 1):
        f = _frame("call", actor={**ACTOR, "anon": bad})
        assert bridge.cbp.decode(_wire(f)) is None, bad


def test_actor_platform_length_bound(bridge):
    for bad in ("", "x" * 65):
        f = _frame("call", actor={**ACTOR, "platform": bad})
        assert bridge.cbp.decode(_wire(f)) is None, bad


def test_turn_must_be_frame_id_shape(bridge):
    for bad in ("", "t" * 65, 12, None):
        f = _frame("call", turn=bad)
        assert bridge.cbp.decode(_wire(f)) is None, bad


# ---------- 表与构造器 ----------

def test_required_table_carries_the_new_bits(bridge):
    req = bridge.cbp._REQUIRED
    assert "self" in req["hello"] and "self" in req["ready"]
    assert "turn" in req["call"] and "actor" in req["call"]
    assert len(req) == 10  # 十型不变（F17：不 mint 第 11 型）


def test_anon_values_are_the_four_verbatim(bridge):
    assert tuple(bridge.cbp.ANON_VALUES) == ANON_VERBATIM


def test_encode_refuses_call_without_actor(bridge):
    import pytest

    with pytest.raises(ValueError):
        bridge.cbp.encode({"v": 1, "t": "call", "id": "c", "ts": 1.0,
                           "rid": "r", "tool": "x", "args": {}})


def test_hello_frame_declares_self_proven_false(bridge):
    """§2 规则 2：N.E.K.O 线永久 proven:false——bus 面无 publish，无实证 bot 主键。"""
    f = bridge.cbp.hello_frame("cortico-bridge-neko", "0.1.0", "unpinned",
                               ["inbound_msg", "push_message_v2", "reply_tail"], [])
    assert f["self"] == {"id": "", "name": "", "proven": False}
    assert bridge.cbp.parse_frame(f) is not None
    assert list(f).index("self") == list(f).index("agent") + 1  # 线序照 conformance wire


def test_hello_frame_accepts_explicit_self(bridge):
    f = bridge.cbp.hello_frame("n", "1", "sha", [], [], self_decl=SELF_TRUE)
    assert f["self"] == SELF_TRUE
