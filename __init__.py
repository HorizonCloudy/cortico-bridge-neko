"""cortico-bridge-neko —— Cortico ↔ N.E.K.O 的 CBP 桥（宿主原生插件，Python 3.11 桶）。

骨架范围（_BUILD-PLAN §2.4/§3.4-1；蓝图 L83）：入口契约、bus watch 映射、
push_message v2 / reply_tail 两条出站门、CBP WS 外拨链路。
不启动宿主、不监听任何端口、不连 IM；连接只在配置显式放行后发生。
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from plugin.sdk.plugin import Err, NekoPluginBase, Ok, neko_plugin  # __all__ 58 名内（r-neko §4）

from . import cbp, ingress, pins as pins_mod, replytail, turns
from .convmap import ConvMap
from .watch import BusWatch

BRIDGE_NAME = "cortico-bridge-neko"
BRIDGE_VERSION = "0.1.0"
BRIDGE_SHA = "unpinned"  # 装机由 fleet pins.json gen 流程回填（G1），代码不写死

# hello/catalog 申报的工具表在这里写一次，风险门从同一张表取数（不再两处漂移）。
_BRIDGE_TOOLS: list[dict[str, Any]] = [
    {"name": "neko_send", "risk": "write", "confirm_required": True},
    {"name": "neko_reply_tail_register", "risk": "write", "confirm_required": False},
]
_TOOL_RISK = {t["name"]: t["risk"] for t in _BRIDGE_TOOLS}
_BRIDGE_CAPS = ["inbound_msg", "push_message_v2", "reply_tail"]


def _warn(self: Any, message: str) -> None:
    """宿主同款 logger 取法（plugin/sdk/plugin/base.py: getattr(self, "logger", None)）。"""
    logger = getattr(self, "logger", None)
    if logger is not None:
        logger.warning(message)

# WS 参数照内核表（driver.ts @4725feee6 先例，蓝图 §2.1：退避 1000ms×2 封顶 30000ms）
_BACKOFF_BASE_MS, _BACKOFF_CAP_MS = 1000, 30000


@neko_plugin
class CorticoBridgePlugin(NekoPluginBase):
    """两条出站门 + 一条入站 watch。禁用面见各模块头：
    不调 ctx.finish（core.PluginContext 无此属性，C1-6）；不依赖 bus 注入（按不可写）；
    frames 不作消息来源。

    出站门的边界（详见 README 同名小节）：`neko_send` 走 `push_message` v2 是**广门**
    （任意段、chat/hud、三值 ai_behavior）；`neko_reply_tail_register` 走 `reply_tail` 是**窄门**
    （image-only ≤2、`blind` 固定、先 subscribe、绝不回落 push）。两扇共用同一个 `egress`
    开关：门没开就两扇都不开，窄门不是广门的旁路。
    两扇门都在 F3 身份门**之后**：`turn` 查不到入站记录先 60004，写动作再要求 `anon=="identified"`。
    """

    # ---------- 配置面（全保守缺省：门全关） ----------
    def _cfg(self, key: str, default: Any = False) -> Any:
        cfg = getattr(self.ctx, "config", None)
        if isinstance(cfg, dict):
            return cfg.get(key, default)
        getter = getattr(cfg, "get", None)
        if callable(getter):
            try:
                return getter(key, default)
            except Exception:
                return default
        return default

    # ---------- 生命周期（入口一律 async，返 Ok/Err 不靠 raise） ----------
    async def start(self) -> Any:
        try:
            self._pins = pins_mod.load_pins()
        except RuntimeError as exc:
            return Err(str(exc))
        self._egress_open = bool(self._cfg("egress", False))
        self._link_open = bool(self._cfg("link_enabled", False))
        self._convmap = ConvMap(getattr(self.ctx, "store", None))
        self._turns = turns.TurnLedger()  # F3：call 的身份来源只有这张表
        self._outgoing: asyncio.Queue[str] = asyncio.Queue()
        self._link_task: asyncio.Task | None = None
        self._watch = BusWatch(emit=self._emit_frame)
        if not self._watch.attach(self.ctx):
            _warn(self, "bus.messages watch 不可用：入站关闭，出站不受影响")
        if self._link_open:
            self._link_task = asyncio.create_task(self._link_loop())
        return Ok(None)

    async def stop(self) -> Any:
        if self._link_task is not None:
            self._link_task.cancel()
            try:
                await self._link_task
            except (asyncio.CancelledError, Exception):
                pass
            self._link_task = None
        watcher = getattr(getattr(self, "_watch", None), "_watcher", None)
        stop = getattr(watcher, "stop", None) if watcher is not None else None
        if callable(stop):
            stop()
        return Ok(None)

    @property
    def _turn_ledger(self) -> turns.TurnLedger:
        """懒建：没 start() 也要有一张空账本可用，缺位一律 60004（fail-shut，不是放行）。"""
        ledger = getattr(self, "_turns", None)
        if ledger is None:
            ledger = turns.TurnLedger()
            self._turns = ledger
        return ledger

    # ---------- 帧路由 ----------
    def _emit_frame(self, frame: dict[str, Any]) -> None:
        """入站 CBP 帧排队外拨。桥侧未连接时丢弃并计数（骨架期 Cortico 侧不在场属常态）。

        F3：只有真排出去的 msg 帧才进 turn 账本——Cortico 只可能拿它见过的帧 id 当 turn。
        """
        if not (self._link_open and self._link_task is not None):
            self._dropped = getattr(self, "_dropped", 0) + 1
            return
        if frame.get("t") == "msg":
            self._turn_ledger.record(frame)
        self._outgoing.put_nowait(cbp.encode(frame))

    async def handle_frame(self, frame: dict[str, Any]) -> dict[str, Any]:
        """Cortico→桥 帧处理。只认 call/tick；call 先过 F3 身份门，再落两条出站门，其余 60002。

        F3 执法顺序逐字（SECURITY-CBP.md:36-40）：
        1. `turn` 查自己的入站记录；查不到或超窗 → 60004 `unknown_session`（不触任何门）。
        2. 线上 `actor` 只作对账：与宿主记录不一致时以宿主记录为准，另发
           `event{name:"degraded",data:{reason:"actor-mismatch"}}`，本轮不因此失败。
        3. 写动作只在 `anon=="identified"` 时放行；`anonymous`/`unknown` 的 turn 只允许只读动作
           （本桥申报的 tools 全是 write，所以等价于「 unidentified 一律 60005」）。
        """
        if frame["t"] == "tick":
            return {"ok": True, "code": None, "detail": "tick"}
        if frame["t"] != "call":
            return {"ok": False, "code": 60002, "detail": f"capability_unavailable: 不消费 {frame['t']} 帧"}
        tool, args = frame["tool"], frame.get("args") or {}
        rid = frame["rid"]
        host = self._turn_ledger.resolve(frame.get("turn"))
        if host is None:
            return {"ok": False, "code": 60004,
                    "detail": f"unknown_session: turn={frame.get('turn')!r} 无对应入站记录"}
        wire = frame.get("actor")
        if wire != host:
            self._emit_frame(cbp.make_frame(
                "event", name="degraded",
                data={"reason": "actor-mismatch", "rid": rid, "host": host, "wire": wire}))
        if _TOOL_RISK.get(tool) == "write" and host["anon"] != "identified":
            return {"ok": False, "code": 60005,
                    "detail": f"door_closed: 写动作要求 anon=identified，宿主记录为 {host['anon']}"}
        try:
            if tool == "neko_send":
                kwargs = ingress.push_kwargs(
                    pins=self._pins,
                    egress_open=self._egress_open,
                    segments=list(args.get("segments") or []),
                    visibility=args.get("visibility"),
                    ai_behavior=args.get("ai_behavior") or "respond",
                    metadata={"cbp_rid": rid},
                )
                ok, code, detail = await ingress.push(self.ctx, kwargs)
                return {"ok": ok, "code": code, "detail": detail}
            if tool == "neko_reply_tail_register":
                if not self._egress_open:  # 两扇门共用 egress 开关；窄门不得绕开广门没开的出站
                    return {"ok": False, "code": 60005,
                            "detail": "door_closed: 出站门未开（egress=false）"}
                return await replytail.register_tail(
                    self.ctx,
                    plugin_id=str(getattr(self.ctx, "plugin_id", "cortico_bridge")),
                    entry_id=str(args.get("entry_id") or ""),
                    registration_id=str(args.get("registration_id") or ""),
                    parts=list(args.get("parts") or []),
                )
            return {"ok": False, "code": 60002, "detail": f"capability_unavailable: {tool}"}
        except ingress.DoorError as exc:
            return {"ok": False, "code": exc.code, "detail": exc.message}

    def reply_frame(self, call: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        return cbp.make_frame(
            "reply", rid=call["rid"], ok=bool(result.get("ok")),
            **({"error": {"code": result["code"], "message": str(result.get("detail", ""))}}
               if result.get("code") is not None else {}),
        )

    # ---------- WS 外拨（唯一联网路径；dial Cortico 侧 listen 口） ----------
    def _ws_uri(self) -> str:
        return pins_mod.bridge_ws_uri(self._pins, "cortico_world_neko_ws")

    async def _link_loop(self) -> None:
        """断线重连照内核参数；start 永不 reject（本协程不抛，异常只回退避）。"""
        backoff = _BACKOFF_BASE_MS / 1000.0
        while True:
            try:
                await self._link_once()
                backoff = _BACKOFF_BASE_MS / 1000.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _warn(self, f"CBP link down: {exc!r}; retry in {backoff:.1f}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_CAP_MS / 1000.0)

    async def _link_once(self) -> None:
        import websockets  # 延迟导入：自省/单测不触网

        hello = cbp.hello_frame(
            agent_name=BRIDGE_NAME, version=BRIDGE_VERSION, sha=BRIDGE_SHA,
            caps=list(_BRIDGE_CAPS), tools=list(_BRIDGE_TOOLS),
            # F1/§2：握手首帧必带 self。N.E.K.O 线永久 proven:false——bus 面只有读与 watch、
            # 无 publish，没有实证 bot 主键的口子（SECURITY-CBP.md:72,270）。空 id 是诚实申报，
            # 不许拿 role=="assistant" 启发式冒充实证，也不许因为报不了就不带 self。
            self_decl=cbp.make_self_decl(),
        )
        # F4/§3：共享密钥只走 HTTP Upgrade 头，禁过 URL query（SECURITY-CBP.md:115）。
        # 缺 token → 空头，本桥不自行降级成 query 形式。
        async with websockets.connect(self._ws_uri(), open_timeout=12,
                                      additional_headers=pins_mod.auth_headers()) as ws:
            await ws.send(cbp.encode(hello))
            async for raw in ws:
                frame = cbp.decode(raw if isinstance(raw, (str, bytes)) else bytes(raw))
                if frame is None:
                    continue  # 坏帧吞掉（内核硬规则）
                if frame["t"] == "call":
                    result = await self.handle_frame(frame)
                    await ws.send(cbp.encode(self.reply_frame(frame, result)))
                while not self._outgoing.empty():
                    await ws.send(self._outgoing.get_nowait())
