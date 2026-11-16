"""SECURITY-CBP §1 执法规则（neko 腿）：桥只用 `turn` 查自己的入站记录定身份。

规则逐字（SECURITY-CBP.md:36-40）：
- `turn` 查不到或超出入站记录保留窗 → `reply{ok:false,error:{code:60004,
  message:"unknown_session: ..."}}`（60004 在册 errors.ts:26）。
- 线上 `actor` 只作对账：不一致时以宿主记录为准，并回 `event{name:"degraded"}`
  `data.reason="actor-mismatch"`。
- 写动作只在 `anon=="identified"` 时生效；`anonymous`/`unknown` 的 turn 只允许只读动作。
"""
from __future__ import annotations

import asyncio

import support


def _msg_frame(bridge, *, msg_id="m-turn-1", source="onebot", conv="group_5_4",
               user_id="2002", name="nick"):
    meta = {"conversation_id": conv, "user_name": name}
    if user_id is not None:
        meta["user_id"] = user_id
    rec = {"type": "MESSAGE", "timestamp": 1763136000.5, "plugin_id": None, "source": source,
           "priority": 0, "content": "你好", "metadata": meta, "message_id": msg_id}
    frame = bridge.watch.record_to_msg_frame(rec)
    frame["id"] = msg_id  # turn 的值就是这条入站 msg 帧的 id（SECURITY-CBP.md:31）
    return frame


def _plugin(bridge, *, egress=True, ttl=300.0, clock=None):
    ctx = support.FakeCtx(push_results=[{"submitted": True}] * 4)
    plugin = bridge.CorticoBridgePlugin(ctx)
    plugin._pins = support.PINS_SAMPLE
    plugin._egress_open = egress
    plugin._link_open = True
    plugin._link_task = object()  # 只需非 None：_emit_frame 走入队分支
    plugin._outgoing = asyncio.Queue()
    kwargs = {"ttl": ttl}
    if clock is not None:
        kwargs["now"] = clock
    plugin._turns = bridge.turns.TurnLedger(**kwargs)
    plugin._fake = ctx  # 宿主把 ctx 包成 SdkContext，push_calls 只记在最外层对象上
    return plugin


def _call(bridge, turn, actor=None, tool="neko_send", args=None):
    return bridge.cbp.make_frame(
        "call", rid="q1", tool=tool,
        args={"segments": [{"type": "text", "data": {"text": "hi"}}]} if args is None else args,
        turn=turn, actor=actor or {"platform": "onebot", "conv": "group_5_4",
                                   "member": {"id": "2002", "name": "nick"}, "anon": "identified"})


def _run(plugin, frame):
    return asyncio.run(plugin.handle_frame(frame))


def _drain(bridge, plugin):
    """取出已入队的外拨帧（msg 与 degraded event 排在同一条出边上）。"""
    out = []
    while not plugin._outgoing.empty():
        out.append(bridge.cbp.decode(plugin._outgoing.get_nowait()))
    return out


# ---------- 60004：turn 无对应入站记录 ----------

def test_unknown_turn_is_60004(bridge):
    plugin = _plugin(bridge)
    out = _run(plugin, _call(bridge, "never-seen"))
    assert out["ok"] is False and out["code"] == 60004, out
    assert out["detail"].startswith("unknown_session:")
    assert plugin._outgoing.qsize() == 0  # 门都没到，不产任何出站


def test_missing_turn_field_is_60004(bridge):
    plugin = _plugin(bridge)
    frame = _call(bridge, "whatever")
    del frame["turn"]
    out = _run(plugin, frame)
    assert out["code"] == 60004, out


def test_expired_turn_window_is_60004(bridge):
    t = [1000.0]
    plugin = _plugin(bridge, ttl=300.0, clock=lambda: t[0])
    plugin._emit_frame(_msg_frame(bridge))
    t[0] += 301.0
    out = _run(plugin, _call(bridge, "m-turn-1"))
    assert out["code"] == 60004, out


def test_turn_inside_window_is_accepted(bridge):
    t = [1000.0]
    plugin = _plugin(bridge, ttl=300.0, clock=lambda: t[0])
    plugin._emit_frame(_msg_frame(bridge))
    t[0] += 299.0
    out = _run(plugin, _call(bridge, "m-turn-1"))
    assert out["ok"] is True, out


# ---------- actor 只作对账：不一致以宿主记录为准 + degraded ----------

def test_actor_mismatch_reports_degraded_and_host_wins(bridge):
    plugin = _plugin(bridge)
    plugin._emit_frame(_msg_frame(bridge, user_id="2002"))
    wire = {"platform": "onebot", "conv": "group_5_4",
            "member": {"id": "9999", "name": "自称"}, "anon": "identified"}
    out = _run(plugin, _call(bridge, "m-turn-1", actor=wire))
    assert out["ok"] is True, out  # 宿主记录（2002）说了算，动作照执行
    events = [f for f in _drain(bridge, plugin) if f and f["t"] == "event"]
    assert len(events) == 1, events
    frame = events[0]
    assert frame["name"] == "degraded"
    assert frame["data"]["reason"] == "actor-mismatch"
    assert frame["data"]["host"]["member"]["id"] == "2002"
    assert frame["data"]["wire"]["member"]["id"] == "9999"


def test_matching_actor_emits_no_degraded(bridge):
    plugin = _plugin(bridge)
    plugin._emit_frame(_msg_frame(bridge))
    out = _run(plugin, _call(bridge, "m-turn-1"))
    assert out["ok"] is True
    assert [f for f in _drain(bridge, plugin) if f and f["t"] == "event"] == []


def test_anon_self_never_claimed_without_proven_self(bridge):
    """§2 规则 5：不许用启发式冒充实证。neko 线 self 永久 proven:false，
    所以账本里不会出现 anon:"self"。"""
    plugin = _plugin(bridge)
    frame = _msg_frame(bridge)
    plugin._emit_frame(frame)
    assert plugin._turns.resolve("m-turn-1")["anon"] == "identified"
    assert all(a["anon"] != "self" for a in plugin._turns.values())


# ---------- 写动作的 anon 门 ----------

def test_write_action_refused_when_anon_unknown(bridge):
    plugin = _plugin(bridge)
    plugin._emit_frame(_msg_frame(bridge, user_id=None))  # 宿主没给发信人 → unknown
    actor = {"platform": "onebot", "conv": "group_5_4",
             "member": {"id": "user:123", "name": "n"}, "anon": "identified"}  # 线上自报也没用
    out = _run(plugin, _call(bridge, "m-turn-1", actor=actor))
    assert out["ok"] is False and out["code"] == 60005, out
    assert out["detail"].startswith("door_closed:")
    assert plugin._fake.push_calls == []  # 对账以宿主记录为准，写门不开就不触达 SDK


def test_write_action_allowed_when_identified(bridge):
    plugin = _plugin(bridge)
    plugin._emit_frame(_msg_frame(bridge, user_id="2002"))
    out = _run(plugin, _call(bridge, "m-turn-1"))
    assert out["ok"] is True, out
    assert len(plugin._fake.push_calls) == 1


# ---------- 账本自身的口径 ----------

def test_ledger_refuses_record_without_host_platform(bridge):
    """platform 必须逐字取宿主字段（SECURITY-CBP.md:32 neko 线取 bus 记录 source）；
    没有就不登记，绝不编造平台名。"""
    led = bridge.turns.TurnLedger()
    assert led.record(_msg_frame(bridge, source="")) is None
    assert led.resolve("m-turn-1") is None


def test_ledger_capacity_is_bounded(bridge):
    t = [0.0]
    led = bridge.turns.TurnLedger(ttl=300.0, cap=3, now=lambda: t[0])
    for i in range(5):
        t[0] = float(i)
        led.record(_msg_frame(bridge, msg_id=f"m{i}", conv=f"c{i}"))
    assert len(list(led.values())) == 3
    assert led.resolve("m0") is None and led.resolve("m4") is not None


def test_ledger_actor_keys_are_the_closed_four(bridge):
    led = bridge.turns.TurnLedger()
    led.record(_msg_frame(bridge))
    assert set(led.resolve("m-turn-1")) == {"platform", "conv", "member", "anon"}


# ---------- 两条门共用 egress 开关（使用边界） ----------

def test_reply_tail_is_also_egress_gated(bridge, monkeypatch):
    """边界：`egress=false` 关的是出站本身，两扇门都关。
    reply_tail 是窄门不是旁路——窄门的额外约束（image-only ≤2、blind、先 subscribe）
    只在广门已开的前提上再收，不得反过来绕过 egress。"""
    plugin = _plugin(bridge, egress=False)
    plugin._emit_frame(_msg_frame(bridge))
    touched = []

    async def _spy(*a, **kw):
        touched.append((a, kw))
        return {"ok": True, "code": None, "detail": "should not happen"}

    monkeypatch.setattr(bridge.replytail, "register_tail", _spy)
    out = _run(plugin, _call(bridge, "m-turn-1", tool="neko_reply_tail_register",
                             args={"entry_id": "e1", "registration_id": "r1",
                                   "parts": [{"type": "image", "data": {"handle": "u"}}]}))
    assert out["ok"] is False and out["code"] == 60005, out
    assert touched == []  # 门未开就不该触达窄门实现


def test_push_door_still_egress_gated(bridge):
    plugin = _plugin(bridge, egress=False)
    plugin._emit_frame(_msg_frame(bridge))
    out = _run(plugin, _call(bridge, "m-turn-1"))
    assert out["ok"] is False and out["code"] == 60005, out
    assert plugin._fake.push_calls == []
