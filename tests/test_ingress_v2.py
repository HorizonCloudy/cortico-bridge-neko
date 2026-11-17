"""工单验收：`PY311 -I -m pytest tests/test_ingress_v2.py -q`。两轴、限额、kwargs-only、互译表。"""
from __future__ import annotations

import asyncio
import json

import pytest
import support

SEGS = [{"type": "text", "data": {"text": "hello"}}]


def test_axes_values_are_the_measured_six(bridge):
    ing = bridge.ingress
    assert ing.VISIBILITY_VALUES == ("chat", "hud")  # push_message_schema.py:63 逐字
    assert ing.AI_BEHAVIOR_VALUES == ("respond", "read", "blind")  # :64 逐字


def test_egress_closed_is_door_error(bridge):
    ing = bridge.ingress
    with pytest.raises(ing.DoorError) as ei:
        ing.push_kwargs(pins=support.PINS_SAMPLE, egress_open=False, segments=SEGS)
    assert ei.value.code == 60005


def test_bad_axes_rejected_locally(bridge):
    ing = bridge.ingress
    with pytest.raises(ing.DoorError) as ei:
        ing.push_kwargs(pins=support.PINS_SAMPLE, egress_open=True, segments=SEGS,
                        visibility=["sms"])
    assert ei.value.code == 10003, "轴非法是参数问题，不是超限（60003 只留给 payload_too_large）"
    assert ei.value.message.startswith("bad_param:")
    with pytest.raises(ing.DoorError) as ei:
        ing.push_kwargs(pins=support.PINS_SAMPLE, egress_open=True, segments=SEGS,
                        ai_behavior="ignore")
    assert ei.value.code == 10003
    assert ei.value.message.startswith("bad_param:")


def test_error_code_positions_match_the_ledger(bridge):
    """N2 码位↔语义对应闸：对着台账 error_codes 逐位钉，防 mnemonic 再漂移。

    台账 = fleet spec/conformance.json（10003 Bad Param；60003 payload_too_large；60005 door_closed）。
    各码的唯一触发路径在本文件与 test_actor_turns.py 都有活断言，这里钉「谁发哪一位」。
    """
    ing = bridge.ingress
    assert ing.ERR_BAD_PARAM == 10003 and ing.ERR_PAYLOAD_TOO_LARGE == 60003
    assert ing.ERR_DOOR_CLOSED == 60005 and ing.REASON_TO_CODE["payload_too_large"] == 60003
    with pytest.raises(ing.DoorError) as ei:  # 门未开 → 60005
        ing.push_kwargs(pins=support.PINS_SAMPLE, egress_open=False, segments=SEGS)
    assert ei.value.code == 60005
    cap = support.PINS_SAMPLE["caps"]["neko"]["frame_bytes"]
    with pytest.raises(ing.DoorError) as ei:  # 超限 → 60003，且只有超限发 60003
        ing.push_kwargs(pins=support.PINS_SAMPLE, egress_open=True,
                        segments=[{"type": "text", "data": {"text": "x" * (cap + 1)}}])
    assert ei.value.code == 60003 and ei.value.message.startswith("payload_too_large:")
    with pytest.raises(ing.DoorError) as ei:  # 双空正文 → 10003，不再借 60003
        ing.push_kwargs(pins=support.PINS_SAMPLE, egress_open=True, segments=[], text="")
    assert ei.value.code == 10003 and ei.value.message.startswith("bad_param:")
    with pytest.raises(ing.DoorError) as ei:  # 段表非空但全畸形 → 10003，文案不得谎称双空
        ing.push_kwargs(pins=support.PINS_SAMPLE, egress_open=True,
                        segments=[{"type": 123}], text="hello")
    assert ei.value.code == 10003 and "都空" not in ei.value.message


def test_payload_limit_refused_before_sdk(bridge):
    """上限从注入的 pins 台账读；源码常量位已删，超限拒收与错误文案同用这一个值。"""
    ing = bridge.ingress
    cap = support.PINS_SAMPLE["caps"]["neko"]["frame_bytes"]
    fat = [{"type": "text", "data": {"text": "x" * (cap + 1)}}]
    with pytest.raises(ing.DoorError) as ei:
        ing.push_kwargs(pins=support.PINS_SAMPLE, egress_open=True, segments=fat)
    assert ei.value.code == 60003
    assert str(cap) in ei.value.message  # 文案数值来自台账注入，不落源码字面量
    ok = ing.push_kwargs(pins=support.PINS_SAMPLE, egress_open=True,
                         segments=[{"type": "text", "data": {"text": "x" * (cap - 256)}}])
    assert ok["parts"]  # 台账内余量够（序列化包络 < 256B）→ 放行
    assert not hasattr(ing, "PAYLOAD_MAX_BYTES") and not hasattr(ing, "ENVELOPE_HEADROOM_BYTES")


def test_caps_missing_fails_shut(bridge):
    """负例：caps 段缺失/null/非正整数一律 RuntimeError，不许静默回落旧值。"""
    ing = bridge.ingress
    bad_pins = ({}, {"caps": {}}, {"caps": {"neko": {}}}, {"caps": {"neko": {"frame_bytes": None}}},
                {"caps": {"neko": {"frame_bytes": 0}}}, {"caps": {"neko": {"frame_bytes": True}}},
                {"caps": {"neko": {"frame_bytes": "4096"}}}, {"caps": None})
    for pins in bad_pins:
        with pytest.raises(RuntimeError):
            ing.push_kwargs(pins=pins, egress_open=True, segments=SEGS)


def test_push_kwargs_shape_matches_v2_contract(bridge):
    kw = bridge.ingress.push_kwargs(pins=support.PINS_SAMPLE, egress_open=True, segments=SEGS + [
        {"type": "image", "data": {"url": "u", "n": 3}}, {"type": "weird", "data": {}}])
    assert set(kw) == {"visibility", "ai_behavior", "parts", "metadata"}
    assert kw["visibility"] == ["chat"] and kw["ai_behavior"] == "respond"
    assert {"type": "image", "data": {"url": "u", "n": "3"}} in kw["parts"]  # data 值全字符串
    assert {"type": "weird", "data": {}} in kw["parts"]  # SDK 不校验 part type，透传（C1-16）


def test_push_is_kwargs_only_and_maps_results(bridge):
    ing = bridge.ingress
    for result, want in (
        ({"submitted": True}, (True, None)),
        ({"ok": False, "submitted": False, "reason": "backpressure"}, (False, 60006)),
        ({"ok": False, "submitted": False, "reason": "transport_error"}, (False, 20002)),
        ({"ok": False, "submitted": False, "reason": "transport_unavailable"}, (False, 60006)),
        ({"ok": False, "submitted": False, "reason": "payload_too_large"}, (False, 60003)),
        ("junk", (False, 60007)),
    ):
        ctx = support.FakeCtx(push_results=[result] * 4)
        ok, code, _ = asyncio.run(ing.push(ctx, {"visibility": ["chat"], "ai_behavior": "blind",
                                                 "parts": [{"type": "text", "data": {"text": "x"}}]}))
        assert (ok, code) == want, result
        for args, _kwargs in ctx.push_calls:
            assert args == ()  # C1-6：位参不可依赖


def test_handle_frame_e2e_with_fake_ctx(bridge):
    """F3 之后 call 必须先过 turn 身份门才轮到出站门；这里两条都验。"""
    ctx = support.FakeCtx(push_results=[{"submitted": True}] * 4)
    plugin = bridge.CorticoBridgePlugin(ctx)
    plugin._pins = support.PINS_SAMPLE
    plugin._egress_open = False
    plugin._link_open = True
    plugin._link_ready = True
    plugin._outgoing = asyncio.Queue()
    plugin._turns = bridge.turns.TurnLedger()

    turnless = bridge.cbp.make_frame("call", rid="r0", tool="neko_send",
                                     args={"segments": SEGS}, turn="no-such-msg",
                                     actor={"platform": "onebot", "conv": "c1",
                                            "member": {"id": "u1", "name": "n"},
                                            "anon": "identified"})
    assert asyncio.run(plugin.handle_frame(turnless))["code"] == 60004
    assert ctx.push_calls == []  # 身份门不过，SDK 一次都不该被叫

    inbound = bridge.cbp.make_frame(
        "msg", conv="c1", sender={"id": "u1", "name": "n"}, kind="direct",
        segments=SEGS, raw={"source": "onebot", "metadata": {"user_id": "u1"}})
    plugin._emit_frame(inbound)
    call = bridge.cbp.make_frame("call", rid="r1", tool="neko_send", args={"segments": SEGS},
                                 turn=inbound["id"], actor=bridge.turns.TurnLedger.actor_of(inbound))
    result = asyncio.run(plugin.handle_frame(call))
    assert result == {"ok": False, "code": 60005, "detail": result["detail"]}
    assert json.loads(bridge.cbp.encode(plugin.reply_frame(call, result)))["rid"] == "r1"


def test_canonical_text_only_synthesizes_segment(bridge):
    """认规范顶层 text 工单：规范形 cv-call 把正文放在顶层 text、segments 为空。合成一个 text 段，不再回 60003。
    掩盖源是旧用例把正文塞进 segments；那条路仍要保住，所以同时钉死「不重复合成」与「双空仍旧 60003」。"""
    ing = bridge.ingress
    kw = ing.push_kwargs(pins=support.PINS_SAMPLE, egress_open=True, segments=[], text="hello")
    assert kw["parts"] == [{"type": "text", "data": {"text": "hello"}}]
    kw2 = ing.push_kwargs(pins=support.PINS_SAMPLE, egress_open=True, segments=SEGS, text="hello")
    assert kw2["parts"] == SEGS  # 段表非空以段表为准，text 是纯文本回声，不重复拼
    with pytest.raises(ing.DoorError) as ei:
        ing.push_kwargs(pins=support.PINS_SAMPLE, egress_open=True, segments=[], text="")
    assert ei.value.code == 10003  # 两轴都空仍旧本地先拒；N2 后码位是 bad_param，不再借 60003


def test_handle_frame_canonical_text_only_reaches_push(bridge):
    """认规范顶层 text 工单：规范形 call（args 只有 conv+text）过身份门后拼出 text 段并触达 SDK。
    改前 __init__ 调用点只透 segments、不透 text → 这一条必红。"""
    ctx = support.FakeCtx(push_results=[{"submitted": True}] * 4)
    plugin = bridge.CorticoBridgePlugin(ctx)
    plugin._pins = support.PINS_SAMPLE
    plugin._egress_open = True
    plugin._link_open = True
    plugin._link_ready = True
    plugin._outgoing = asyncio.Queue()
    plugin._turns = bridge.turns.TurnLedger()
    inbound = bridge.cbp.make_frame(
        "msg", conv="c1", sender={"id": "u1", "name": "n"}, kind="direct",
        segments=SEGS, raw={"source": "onebot", "metadata": {"user_id": "u1"}})
    plugin._emit_frame(inbound)
    call = bridge.cbp.make_frame("call", rid="rc", tool="neko_send",
                                 args={"conv": "c1", "text": "hello"},
                                 turn=inbound["id"],
                                 actor=bridge.turns.TurnLedger.actor_of(inbound))
    result = asyncio.run(plugin.handle_frame(call))
    assert result["ok"] is True, result
    assert len(ctx.push_calls) == 1
    _args, kwargs = ctx.push_calls[0]
    assert kwargs["parts"] == [{"type": "text", "data": {"text": "hello"}}]
