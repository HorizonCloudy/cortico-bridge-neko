"""Cortico 侧动作 → @llm_tool 面（K2-neko-tools，蓝图 L184）。

对端 hello/ready/catalog 申报的工具由 PeerTools 命令式注册给猫娘模型——工具集合随帧
到达、类定义期不可知（SDK 命令式路的设计场景，base.py:560-566 @52e973ed2）。模型调用
→ handler 发一条出站 `call` 帧；F3 身份只取桥自己最近一条入站 msg（与 Cortico 侧
world.ts:544 的 lastTurn 只被 msg 更新对称，call 帧不覆盖账本，回环不缺账）。

硬边界（gate must_not）：handler 说话只经 `_emit_frame` 与既有 `handle_frame` 门，
绝不直调 ctx.push_message / ctx.reply_tail——入站只有两扇门，不存在第三条注入路。
"""
from __future__ import annotations

import asyncio
from typing import Any

from . import cbp

# 起点值：逐字抄 fleet cortico-world-neko/src/world.ts:130-181 @c85a87b24 的 TOOL_DECLS。
# 线上无形参表（$defs/toolAnn 只有 name/risk/confirm_required/description）——本地镜像，
# world.ts 改动要靠沙箱对拍捕获（recon §7 已知欠账）。
SCHEMA_SEED: dict[str, dict[str, Any]] = {
    "neko_send_message": {
        "description": (
            "Send text (plus optional inline image segments) into a N.E.K.O conversation via "
            "the bridge. Only works while egress is open; closed door returns 60005 door_closed. "
            "Needs a bridge message in this turn: without one it returns 60004 unknown_session "
            "(the call identity comes from the inbound record, never from arguments). "
            "External-URL images and audio/video segments are refused locally "
            "(host has no upload API for them)."
        ),
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "conv": {"type": "string",
                         "description": "neko conversation_id（neko.message 的 senderKey/conv）。"},
                "text": {"type": "string"},
                "segments": {"type": "array",
                             "description": "CBP v11 形段（image 只认内联 base64）。"},
            },
            "required": ["conv", "text"],
        },
    },
    "neko_status": {
        "description": ("Report bridge connection, role, negotiated CBP caps, peer tool catalog, "
                        "consent state and degraded list."),
        "parameters": {"type": "object", "additionalProperties": False, "properties": {}, "required": []},
    },
    "neko_history": {
        "description": ("Read archived neko.* events from the Cortico event store. Never queries "
                        "the peer (convmap lives in the bridge plugin and is host-invisible)."),
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"conv": {"type": "string"}, "limit": {"type": "integer"}},
            "required": [],
        },
    },
    "neko_peer_call": {
        "description": ("Call a peer tool by name. Only names in the peer catalog are accepted. "
                        "Runs with the identity of the message that started this turn; a turn "
                        "without a bridge message is refused."),
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"tool": {"type": "string"}, "args": {"type": "object"}},
            "required": ["tool"],
        },
    },
}

_REPLY_TIMEOUT = 30.0  # 与内核相关超时同值：correlate.ts:13 `PENDING_TTL_MS = 30_000` @c85a87b24，
                      # 另有 CBP-v1.json $defs/reply「30000ms→60007」独立支撑（原注引 :112-113 是错行）
_TOOL_TIMEOUT = 35.0   # shortcut: 35s 余量是起点值，可对拍；handler 内 30s 须先于宿主 trigger 收口
_EMPTY_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}  # base.py:593 同款兜底形


class PeerTools:
    """对端申报目录 → 桥的 LLM 工具。注册域 = self._ann 键集，表外工具一概不碰（G-K2-2）。"""

    def __init__(self, plugin: Any) -> None:
        self._plugin = plugin
        self._ann: dict[str, dict[str, Any]] = {}      # name → 已登记的 toolAnn
        self._pending: dict[str, asyncio.Future] = {}  # rid → reply 帧
        self.degraded: list[str] = []
        self._last_turn: Any = None

    # ---------- 申报输入 ----------

    def note_msg(self, frame: dict[str, Any]) -> None:
        """_emit_frame 的 msg 分支喂进来：出站 call 的 turn 只认最近这条。"""
        self._last_turn = frame.get("id")

    def on_frame(self, frame: dict[str, Any]) -> None:
        t = frame.get("t")
        if t == "reply":
            self._settle(frame)
        elif t in ("hello", "ready", "catalog"):
            self.reconcile(frame.get("tools") or [])

    # ---------- G5 对账（全量替换语义） ----------

    def reconcile(self, tools: Any) -> None:
        want: dict[str, dict[str, Any]] = {}
        for item in tools if isinstance(tools, list) else []:
            if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"]:
                want[item["name"]] = item
        for name in [n for n in self._ann if n not in want]:
            self._plugin.unregister_llm_tool(name)
            self._ann.pop(name, None)
        for name, ann in want.items():
            if name in self._ann:
                if self._ann[name] == ann:
                    continue
                # G-K2-1：ann 变化=正常流量（world.ts:628 随 egress 翻 confirm_required）。
                # 先注销再注册，否则 base.py:531 的 EntryConflictError 冒出去炸整条链路。
                self._plugin.unregister_llm_tool(name)
                self._ann.pop(name, None)
            seed = SCHEMA_SEED.get(name)
            risk = ann.get("risk")
            if risk not in cbp.TOOL_RISKS:
                self._degrade(f"tool-risk-unknown: {name} risk={risk!r}")
                continue
            if seed is None:
                # G-K2-4：表外名只有 read 允许空参注册；写类没 schema = 裸调写动作，不可接受。
                if risk != "read":
                    self._degrade(f"tool-schema-missing: {name} (risk={risk})")
                    continue
            try:
                self._plugin.register_llm_tool(
                    name=name,
                    description=str(ann.get("description") or (seed or {}).get("description") or ""),
                    parameters=(seed or {}).get("parameters") or _EMPTY_SCHEMA,
                    handler=self._make_handler(name),
                    timeout=_TOOL_TIMEOUT,
                )
            except ValueError as exc:  # SDK 名字闸 llm_tool.py:70-75（CBP 线上只限长度）
                self._degrade(f"tool-name-rejected: {name}: {exc}")
                continue
            except RuntimeError as exc:
                # EntryConflictError = ConflictError → SdkError → RuntimeError
                # （shared/models/exceptions.py:6/37 @52e973ed2；base.py:532 重名即抛）。
                # 典型场景：catalog 报来的名已被别的插件注册，_ann 里没有它。漏接这一类
                # 会把异常抛进帧泵，整条 reconcile 链断掉，后面的名一个都注册不上。
                self._degrade(f"tool-register-rejected: {name}: {exc}")
                continue
            self._ann[name] = ann

    # ---------- handler：模型面 → 出站 call 帧 ----------

    def _make_handler(self, name: str):
        async def _call(**arguments: Any) -> dict[str, Any]:
            if not (self._plugin._link_open and getattr(self._plugin, "_link_ready", False)):
                # 离线判据与 _emit_frame 同款（ready 未到=没握手，帧会被丢弃，不如立刻回 20002）
                return self._error(20002, "PEER_UNREACHABLE", f"外拨链路未就绪：{name}")
            turn = self._current_turn()
            if turn is None:
                # fail-shut：本轮没有桥自己记下的 msg，一帧不发（与 handle_frame 门 60004 同型）
                return self._error(60004, "unknown_session", f"本轮无桥入站 msg 记录：{name}")
            rid = cbp.new_frame_id()
            fut: asyncio.Future = asyncio.get_running_loop().create_future()
            self._pending[rid] = fut
            try:
                self._plugin._emit_frame(cbp.make_frame(
                    "call", rid=rid, tool=name, args=dict(arguments), **turn))
                reply = await asyncio.wait_for(fut, _REPLY_TIMEOUT)
            except asyncio.TimeoutError:
                self._pending.pop(rid, None)
                return self._error(60007, "send_uncertain", f"对端 {name} 未在关联窗内应答")
            except BaseException:
                self._pending.pop(rid, None)
                raise
            return self._reply_to_envelope(reply)
        return _call

    def _current_turn(self) -> dict[str, Any] | None:
        actor = self._plugin._turn_ledger.resolve(self._last_turn)
        return None if actor is None else {"turn": self._last_turn, "actor": actor}

    # ---------- reply 结算 / 离线 / 停机 ----------

    def _settle(self, frame: dict[str, Any]) -> None:
        fut = self._pending.pop(frame.get("rid"), None)
        if fut is not None and not fut.done():
            fut.set_result(frame)

    def drop_pending(self) -> None:
        """断链（_link_once 退出的 finally）：悬挂调用立刻落 20002，不空转 30s。"""
        pending, self._pending = self._pending, {}
        for fut in pending.values():
            if not fut.done():
                fut.set_result({"ok": False, "error": {"code": 20002, "message": "link down"}})

    def shutdown(self) -> None:
        """stop() 收尾。只注销 self._ann 名（宿主 clear_plugin_tools 另有兜底）。"""
        for name in list(self._ann):
            self._plugin.unregister_llm_tool(name)
        self._ann.clear()
        self.drop_pending()

    # ---------- 信封（routes/llm_tools.py:241-262 直通形） ----------

    @staticmethod
    def _error(code: int, label: str, message: str) -> dict[str, Any]:
        return {"output": {"error": f"CBP {code} {label}: {message}"},
                "is_error": True, "error": f"CBP {code} {label}"}

    @staticmethod
    def _reply_to_envelope(reply: dict[str, Any]) -> dict[str, Any]:
        if reply.get("ok"):
            env: dict[str, Any] = {"output": {"text": reply.get("text") or ""}, "is_error": False}
            images = [{"data_b64": b.get("b64", ""), "mime": b.get("mime", "")}
                      for b in reply.get("blobs") or [] if isinstance(b, dict)]
            if images:
                env["images"] = images  # CBP blob{mime,b64} → llm_tool images（recon §1 Q4）
            return env
        err = reply.get("error") or {}
        return PeerTools._error(err.get("code", "?"), "peer_error", str(err.get("message", "")))

    def _degrade(self, reason: str) -> None:
        if reason not in self.degraded:
            self.degraded.append(reason)
