"""工单 K2-neko-tools 验收：`PY311 -I -m pytest tests/test_tools_llm.py -q`。

门禁条件逐条对表（work-phase2/K2-neko-tools/gate.md §6 放行判据）：
- G-K2-1 ann 变化重注册不抛（EntryConflictError 回归位）。
- G-K2-2 注销/shutdown 只走 self._ann 键集，表外（装饰器路/别人的）工具免疫。
- G-K2-3 断链 finally → drop_pending 把每个 Future 落 is_error，不悬空。
- G-K2-4 表外 write 名跳过并报 degraded tool-schema-missing；read 名空参注册。
- must_not handler 全程只经 _emit_frame，零 ctx.push_message / ctx.reply_tail 触达。
"""
from __future__ import annotations

import asyncio
import sys
import types

import support

CATALOG_4 = [
    {"name": "neko_send_message", "risk": "write", "confirm_required": False,
     "description": "send text into a neko conversation"},
    {"name": "neko_status", "risk": "read", "confirm_required": False},
    {"name": "neko_history", "risk": "read", "confirm_required": False},
    {"name": "neko_peer_call", "risk": "write", "confirm_required": True},
]


def _plugin(bridge, *, egress=True, link=True):
    """手工装配（对齐 test_actor_turns 配方）；PeerTools 挂上，等价 command-loop 的产物。"""
    ctx = support.FakeCtx()
    plugin = bridge.CorticoBridgePlugin(ctx)
    plugin._pins = support.PINS_SAMPLE
    plugin._egress_open = egress
    plugin._link_open = link
    plugin._link_ready = link
    plugin._link_task = object() if link else None
    plugin._outgoing = asyncio.Queue()
    plugin._turns = bridge.turns.TurnLedger()
    plugin._peer_tools = bridge.tools.PeerTools(plugin)
    plugin._fake = ctx
    return plugin


def _msg(bridge, plugin, msg_id="m-tool-1"):
    rec = {"type": "MESSAGE", "timestamp": 1763136000.5, "plugin_id": None, "source": "onebot",
           "priority": 0, "content": "你好", "message_id": msg_id,
           "metadata": {"conversation_id": "group_5_4", "user_id": "2002", "user_name": "nick"}}
    frame = bridge.watch.record_to_msg_frame(rec)
    frame["id"] = msg_id
    plugin._emit_frame(frame)  # 与真链路同路：_emit_frame 的 msg 分支喂 note_msg + turn 账本
    return frame


def _handler(plugin, name):
    """宿主 trigger 的取法（entry id 前缀 __llm_tool__，llm_tool.py:53 @52e973ed2）。"""
    return plugin._dynamic_entries[f"__llm_tool__{name}"]["handler"]


def _registered(plugin):
    return {x["name"] for x in plugin.list_llm_tools()}


def _sent_call(bridge, plugin):
    frames = []
    while not plugin._outgoing.empty():
        frames.append(bridge.cbp.decode(plugin._outgoing.get_nowait()))
    calls = [f for f in frames if f and f["t"] == "call"]
    assert len(calls) == 1, frames
    return calls[0]


def _roundtrip(bridge, plugin, name, args=None, *, reply=None):
    """发一次工具调用：handler 起帧 → 喂回 reply → 拿信封。"""
    async def _drive():
        task = asyncio.ensure_future(_handler(plugin, name)(**(args or {})))
        await asyncio.sleep(0)
        call = _sent_call(bridge, plugin)
        if reply is not None:
            plugin._peer_tools.on_frame(dict(reply, t="reply", rid=call["rid"]))
        return await task
    return asyncio.run(_drive())


# ---------- G5 对账：注册面 ----------

def test_catalog_registers_announced_tools(bridge):
    plugin = _plugin(bridge)
    plugin._peer_tools.reconcile(CATALOG_4)
    assert _registered(plugin) == {t["name"] for t in CATALOG_4}
    listed = {x["name"]: x for x in plugin.list_llm_tools()}
    assert listed["neko_send_message"]["parameters"]["required"] == ["conv", "text"]  # SCHEMA_SEED 生效
    assert "conv" in listed["neko_send_message"]["parameters"]["properties"]


def test_catalog_replaces_previous_set(bridge):
    plugin = _plugin(bridge)
    plugin._peer_tools.reconcile(CATALOG_4)
    plugin._peer_tools.reconcile([CATALOG_4[1]])
    assert _registered(plugin) == {"neko_status"}


def test_ann_change_reregisters_without_raise(bridge):
    """G-K2-1 回归：world.ts:628 egress 翻转 → confirm_required 变 → 重发 catalog。"""
    plugin = _plugin(bridge)
    plugin._peer_tools.reconcile([CATALOG_4[0]])
    changed = dict(CATALOG_4[0], confirm_required=True)
    plugin._peer_tools.reconcile([changed])  # 旧草图在这里撞 base.py:531 EntryConflictError
    assert _registered(plugin) == {"neko_send_message"}
    assert plugin._peer_tools._ann["neko_send_message"] == changed
    # 未变化的重放不重注册（entry id 幂等，也不该 churn）
    before = _handler(plugin, "neko_send_message")
    plugin._peer_tools.reconcile([changed])
    assert _handler(plugin, "neko_send_message") is before


def test_bad_tool_name_degrades_not_crashes(bridge):
    plugin = _plugin(bridge)
    plugin._peer_tools.reconcile([{"name": "bad name!", "risk": "read", "confirm_required": False}])
    assert _registered(plugin) == set()
    assert any(r.startswith("tool-name-rejected") for r in plugin._peer_tools.degraded)


def test_name_conflict_with_foreign_tool_degrades_not_crashes(bridge):
    """重名冲突走 RuntimeError：EntryConflictError = ConflictError → SdkError → RuntimeError
    （shared/models/exceptions.py:6/37 @52e973ed2；base.py:532 重名即抛）。
    旧代码只接 ValueError，异常会抛进帧泵，整条 reconcile 断掉，后面的名一个都注册不上。"""
    plugin = _plugin(bridge)
    foreign = {"name": "someone_elses_tool", "risk": "read", "confirm_required": False}
    orig = plugin.register_llm_tool

    def boom(*, name, **kw):
        if name == foreign["name"]:
            raise RuntimeError(f"duplicate LLM tool name: {name!r}")
        return orig(name=name, **kw)

    plugin.register_llm_tool = boom
    plugin._peer_tools.reconcile([foreign, CATALOG_4[1]])
    assert _registered(plugin) == {"neko_status"}, "冲突名不许顶掉别人的工具，其余名照常注册"
    assert any(r.startswith("tool-register-rejected") and foreign["name"] in r
               for r in plugin._peer_tools.degraded)
    assert foreign["name"] not in plugin._peer_tools._ann


def test_unknown_write_name_skipped_read_name_empty_schema(bridge):
    """G-K2-4：SCHEMA_SEED 外的名——read 空参注册，非 read 跳过报 degraded。"""
    plugin = _plugin(bridge)
    plugin._peer_tools.reconcile([
        {"name": "mystery_read", "risk": "read", "confirm_required": False},
        {"name": "mystery_write", "risk": "write", "confirm_required": True},
    ])
    assert _registered(plugin) == {"mystery_read"}
    listed = {x["name"]: x for x in plugin.list_llm_tools()}
    assert listed["mystery_read"]["parameters"] == {"type": "object", "properties": {}}
    assert any(r.startswith("tool-schema-missing") and "mystery_write" in r
               for r in plugin._peer_tools.degraded)


def test_foreign_tools_are_immune(bridge):
    """G-K2-2：对账/shutdown 只动 self._ann 键集，别人（含未来装饰器件）不碰。"""
    plugin = _plugin(bridge)
    async def _noop(**kw):
        return {"output": "foreign", "is_error": False}
    plugin.register_llm_tool(name="foreign_tool", description="别家的", parameters=None,
                             handler=_noop, timeout=30.0)
    plugin._peer_tools.reconcile(CATALOG_4)
    plugin._peer_tools.reconcile([])  # 全量清空：不许把 foreign_tool 一起扫掉
    assert "foreign_tool" in _registered(plugin)
    plugin._peer_tools.shutdown()
    assert _registered(plugin) == {"foreign_tool"}
    assert "__llm_tool__foreign_tool" in plugin._dynamic_entries  # 动态入口 id（llm_tool.py:53 @52e973ed2）


# ---------- handler 出站腿：F3 + 两扇门 ----------

def test_handler_emits_call_with_ledger_identity(bridge):
    plugin = _plugin(bridge)
    plugin._peer_tools.reconcile([CATALOG_4[0]])
    _msg(bridge, plugin)
    out = _roundtrip(bridge, plugin, "neko_send_message",
                     {"conv": "group_5_4", "text": "hi", "segments": []},
                     reply={"ok": True, "text": "queued"})
    # 上面已消费掉 call 帧，这里核信封 + 两扇门 spy
    assert out == {"output": {"text": "queued"}, "is_error": False}
    assert plugin._fake.push_calls == []       # 零 ctx.push_message
    assert plugin._fake.reply_tail is None     # 零 reply_tail（handler 没门可走）


def test_handler_call_frame_shape(bridge):
    plugin = _plugin(bridge)
    plugin._peer_tools.reconcile([CATALOG_4[0]])
    _msg(bridge, plugin)
    async def _drive():
        task = asyncio.ensure_future(_handler(plugin, "neko_send_message")(conv="c1", text="t"))
        await asyncio.sleep(0)
        call = _sent_call(bridge, plugin)
        task.cancel()
        return call
    call = asyncio.run(_drive())
    assert call["tool"] == "neko_send_message"
    assert call["args"] == {"conv": "c1", "text": "t"}
    assert call["turn"] == "m-tool-1"  # 身份只取桥自己最近一条入站 msg（world.ts:544 对称）
    assert call["actor"]["platform"] == "onebot"
    assert call["actor"]["member"]["id"] == "2002"
    assert call["actor"]["anon"] == "identified"


def test_handler_without_turn_is_60004_zero_frames(bridge):
    plugin = _plugin(bridge)
    plugin._peer_tools.reconcile([CATALOG_4[0]])
    out = asyncio.run(_handler(plugin, "neko_send_message")(conv="c", text="t"))
    assert out["is_error"] is True and "60004" in out["error"]
    assert plugin._outgoing.qsize() == 0  # fail-shut：一帧不发


def test_handler_link_down_is_20002_offline(bridge):
    """c3 §7：断链语义=工具置离线、调用回 20002，不注销。"""
    plugin = _plugin(bridge, link=False)
    plugin._peer_tools.reconcile([CATALOG_4[0]])
    _msg(bridge, plugin)  # link 关时 _emit_frame 丢弃，不进账本
    out = asyncio.run(_handler(plugin, "neko_send_message")(conv="c", text="t"))
    assert out["is_error"] is True and "20002" in out["error"]


def test_handler_reply_error_maps_envelope(bridge):
    plugin = _plugin(bridge)
    plugin._peer_tools.reconcile([CATALOG_4[1]])
    _msg(bridge, plugin)
    out = _roundtrip(bridge, plugin, "neko_status",
                     reply={"ok": False, "error": {"code": 60005, "message": "door_closed"}})
    assert out["is_error"] is True
    assert "60005" in out["error"] and "door_closed" in str(out["output"])


def test_handler_reply_blobs_map_to_images(bridge):
    plugin = _plugin(bridge)
    plugin._peer_tools.reconcile([CATALOG_4[2]])
    _msg(bridge, plugin)
    out = _roundtrip(bridge, plugin, "neko_history",
                     reply={"ok": True, "text": "3 rows",
                            "blobs": [{"mime": "image/png", "name": "a.png", "b64": "AAAA"}]})
    assert out["is_error"] is False
    assert out["images"] == [{"data_b64": "AAAA", "mime": "image/png"}]  # CBP blob→llm_tool images


def test_handler_timeout_is_60007_send_uncertain(bridge, monkeypatch):
    plugin = _plugin(bridge)
    plugin._peer_tools.reconcile([CATALOG_4[1]])
    _msg(bridge, plugin)
    monkeypatch.setattr(bridge.tools, "_REPLY_TIMEOUT", 0.02)
    out = _roundtrip(bridge, plugin, "neko_status", reply=None)  # 没人回 reply
    assert out["is_error"] is True and "60007" in out["error"]
    assert plugin._peer_tools._pending == {}  # 超时后不留悬挂 rid


# ---------- G-K2-3：断链 drop_pending ----------

def test_drop_pending_settles_every_future(bridge):
    plugin = _plugin(bridge)
    plugin._peer_tools.reconcile([CATALOG_4[1], CATALOG_4[0]])
    _msg(bridge, plugin)
    async def _drive():
        t1 = asyncio.ensure_future(_handler(plugin, "neko_status")())
        t2 = asyncio.ensure_future(_handler(plugin, "neko_send_message")(conv="c", text="t"))
        await asyncio.sleep(0)
        assert len(plugin._peer_tools._pending) == 2
        plugin._peer_tools.drop_pending()
        return await asyncio.gather(t1, t2)
    outs = asyncio.run(_drive())
    for out in outs:
        assert out["is_error"] is True and "20002" in out["error"]
    assert plugin._peer_tools._pending == {}


def test_link_once_exit_drops_pending(bridge, monkeypatch):
    """__init__.py try/finally 接线验证：链路一退出，pending 必须全部落账。"""
    plugin = _plugin(bridge)
    plugin._peer_tools.reconcile([CATALOG_4[1]])
    _msg(bridge, plugin)

    class _WS:
        async def send(self, data): None
        def __aiter__(self):
            async def _empty():
                if False:
                    yield
            return _empty()

    class _Conn:
        async def __aenter__(self): return _WS()
        async def __aexit__(self, *exc): return False

    fake = types.ModuleType("websockets")
    fake.connect = lambda *a, **k: _Conn()
    monkeypatch.setitem(sys.modules, "websockets", fake)

    async def _drive():
        task = asyncio.ensure_future(_handler(plugin, "neko_status")())
        await asyncio.sleep(0)
        await plugin._link_once()  # 退出即触发 finally
        return await task
    out = asyncio.run(_drive())
    assert out["is_error"] is True and "20002" in out["error"]


# ---------- 生命周期接线（__init__.py 增量） ----------

def test_start_wires_peer_tools_and_tools_cap(bridge, pins_file, monkeypatch):
    assert "tools" in bridge._BRIDGE_CAPS  # 九名词表内唯一新增名（gate §4）
    ctx = support.FakeCtx()
    plugin = bridge.CorticoBridgePlugin(ctx)

    class _Cfg:  # 宿主配置面替身：command loop 只消费 dump() 的 [cortico] 表
        async def dump(self):
            return {"cortico": {"egress": True, "link_enabled": False}}

    monkeypatch.setattr(plugin, "config", _Cfg())
    asyncio.run(plugin._on_command_loop_start())
    assert isinstance(plugin._peer_tools, bridge.tools.PeerTools)
    assert asyncio.run(plugin.stop()).is_ok()
    assert plugin._link_task is None  # link_enabled=false：不触网


def test_on_frame_only_contracts_catalog_and_reply(bridge):
    plugin = _plugin(bridge)
    plugin._peer_tools.on_frame(bridge.cbp.make_frame("ready", caps=[], tools=[CATALOG_4[1]],
                                                      self=bridge.cbp.make_self_decl()))
    assert _registered(plugin) == {"neko_status"}
    plugin._peer_tools.on_frame({"t": "tick"})  # 无关帧不许炸
