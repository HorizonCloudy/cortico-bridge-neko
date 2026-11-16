"""reply_tail 窄门合同：防伪 context、image-only ≤2、blind 固定、subscribe 先行、绝不回退 push。"""
from __future__ import annotations

import asyncio

import support

IMG = {"type": "image", "data": {"handle": "up-1"}}


def test_context_shape(bridge):
    ctx = bridge.replytail.tail_context("cortico_bridge")
    assert ctx == {"version": 1, "source": "plugin:cortico_bridge"}


def test_parts_gate(bridge):
    rt = bridge.replytail
    assert rt.parts_gate([IMG]) is None
    assert rt.parts_gate([IMG, IMG]) is None
    assert rt.parts_gate([]) is not None
    assert rt.parts_gate([IMG, IMG, IMG]) is not None
    assert rt.parts_gate([{"type": "text", "data": {}}]) is not None


def test_register_flow_two_calls_in_order(bridge):
    tail = support.FakeReplyTail()
    ctx = support.FakeCtx(reply_tail=tail)
    out = asyncio.run(bridge.replytail.register_tail(
        ctx, plugin_id="cortico_bridge", entry_id="e1",
        registration_id="reg1", parts=[IMG]))
    assert out["ok"] is True and out["code"] is None
    assert [c[0] for c in tail.calls] == ["subscribe", "register"]  # 先 subscribe 才可用
    reg_kwargs = tail.calls[1][2]
    assert reg_kwargs["ai_behavior"] == "blind" and reg_kwargs["registration_id"] == "reg1"
    assert tail.calls[1][1][0] == bridge.replytail.tail_context("cortico_bridge")
    assert ctx.push_calls == []  # 绝不回退 push_message（docs/plugin-reply-tail.md:40）


def test_door_closed_without_touching_client(bridge):
    tail = support.FakeReplyTail()
    out = asyncio.run(bridge.replytail.register_tail(
        support.FakeCtx(reply_tail=tail), plugin_id="p", entry_id="e",
        registration_id="r", parts=[{"type": "pdf", "data": {}}]))
    assert out["ok"] is False and out["code"] == 60005
    assert tail.calls == []


def test_host_rejection_and_http_failure_surface(bridge):
    tail = support.FakeReplyTail(register_result={"accepted": False, "status": "failed",
                                                  "reason": "invalid_context"})
    out = asyncio.run(bridge.replytail.register_tail(
        support.FakeCtx(reply_tail=tail), plugin_id="p", entry_id="e",
        registration_id="r", parts=[IMG]))
    assert out["ok"] is False and out["code"] == 60005

    boom = support.FakeReplyTail(raise_on="subscribe")
    out2 = asyncio.run(bridge.replytail.register_tail(
        support.FakeCtx(reply_tail=boom), plugin_id="p", entry_id="e",
        registration_id="r", parts=[IMG]))
    assert out2["ok"] is False and out2["code"] == 60006
    out3 = asyncio.run(bridge.replytail.register_tail(
        support.FakeCtx(reply_tail=None), plugin_id="p", entry_id="e",
        registration_id="r", parts=[IMG]))
    assert out3["code"] == 60005
