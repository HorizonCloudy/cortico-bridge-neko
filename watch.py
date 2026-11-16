"""bus.messages watch → CBP msg 帧。

入站唯一来源 = `ctx.bus` 的 messages watch（蓝图 L83：「入站只订阅 bus.messages watch」；
SDK bus 面只有读与 watch，r-neko §10）。硬禁：不调 bus delete；不假设有 publish/emit；
`bus.publish` RpcOp 虽在 protocol 层存在，插件可达性未测（台账 §2），桥一律按「注入不可」写。
frames 是有损小窗（默认 4，FRAMES_STORE_MAXLEN，r-neko §7），只当兜底提示，不当消息来源。
shortcut: delta 回调参数名与 snapshot 读取调用按真机卡实测回填（台账 §2 watch 行）；
回填前 snapshot 返回空表，watch 回调对任何带 dump() 的 record 逐条映射。
"""
from __future__ import annotations

import time
from typing import Any, Callable

from . import cbp

GET_RECENT_MAX_LIMIT = 1000  # settings 实测（r-neko §7），仅作 snapshot 上限钳值，不是端口
_FRAMES_MAX = 4  # FRAMES_STORE_MAXLEN 实值（r-neko §7）；仅注释级证据，代码不读 frames


def _record_to_dict(record: Any) -> dict[str, Any]:
    if isinstance(record, dict):
        return record
    dump = getattr(record, "dump", None)
    if callable(dump):
        out = dump()
        if isinstance(out, dict):
            return out
    return {}


def record_to_msg_frame(record: Any, *, sender_name: str = "lanlan") -> dict[str, Any] | None:
    """bus message record → CBP msg 帧。无法定 conv 的 record 返回 None（不产帧、不吞异常）。

    record 键 = SdkBusMessageRecord.dump()（bus_context.py:83-132 @52e973ed2）：
    type/timestamp/plugin_id/source/priority/content/metadata/message_id/...
    conv 取 metadata.conversation_id，缺则退 source 串；两者皆空不产帧。
    """
    rec = _record_to_dict(record)
    if not rec:
        return None
    meta = rec.get("metadata") if isinstance(rec.get("metadata"), dict) else {}
    conv = meta.get("conversation_id") or rec.get("source") or ""
    if not conv:
        return None
    content = rec.get("content")
    segments: list[dict[str, Any]] = []
    if isinstance(content, str) and content:
        segments = [{"type": "text", "data": {"text": content}}]
    parts = meta.get("parts")
    if isinstance(parts, list):  # 富段透传：SDK 不校验 part type（r-neko §5），桥在内核侧兜底
        segments.extend(p for p in parts if isinstance(p, dict) and p.get("type"))
    if not segments:
        return None
    ts = rec.get("timestamp")
    kind = str(meta.get("kind") or "direct")
    sender_id = str(meta.get("user_id") or rec.get("plugin_id") or rec.get("source") or "unknown")
    return cbp.make_frame(
        "msg",
        ts=float(ts) if isinstance(ts, (int, float)) else time.time(),
        conv=str(conv),
        sender={"id": sender_id, "name": str(meta.get("user_name") or sender_name)},
        kind=kind if kind in cbp.MSG_KINDS else "other",
        segments=segments,
        raw=rec,  # meta.raw 恒留（硬规则）
    )


class BusWatch:
    """把 bus messages 增量映射成 CBP msg 帧并交给 emit(frame)。"""

    def __init__(self, emit: Callable[[dict[str, Any]], Any]) -> None:
        self._emit = emit
        self._watcher: Any = None
        self.started = False

    def on_delta(self, delta: Any) -> None:
        """watch 回调：added/changed 两条通道都映射；removed 忽略（桥不追删）。"""
        items = []
        for bucket in ("added", "changed"):
            items.extend(list(getattr(delta, bucket, ()) or ()))
        if not items and isinstance(delta, (list, tuple)):  # 直接给 record 列表的宿主形态
            items = list(delta)
        for record in items:
            frame = record_to_msg_frame(record)
            if frame is not None:
                self._emit(frame)

    def attach(self, ctx: Any) -> bool:
        """订阅 ctx.bus.messages。watch 面不可用返回 False（start 里只 warn 不抛）。"""
        try:
            messages = ctx.bus.messages
            watcher = messages.watch(ctx)
            self._watcher = watcher.subscribe(on="add")(self.on_delta)
            start = getattr(watcher, "start", None)
            if callable(start):
                start()
            self.started = True
        except Exception:
            self.started = False
        return self.started

    async def snapshot(self) -> list[dict[str, Any]]:
        """快照兜底位。读侧调用形状待真机卡；当前返回空表（见模块头 shortcut）。"""
        return []
