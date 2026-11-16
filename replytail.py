"""第二扇门：reply_tail 窄门（两扇门里的**窄门**）。传输（HTTP → 宿主 48911 段）由 SDK ReplyTailClient 自理，
桥代码零端口字面量；超时钳 min(t,30.0) 与回调 15s 也在 SDK 层（r-neko §6 @52e973ed2）。

使用边界（与 `ingress.py` 的广门相对，全文见 README「两条出站门的使用边界」）：
- 窄门只管一件事：给**本轮回复的尾部**贴 ≤2 张 image 段，正文一个字都不发；
  要说话（任意段、`chat`/`hud`、`respond`/`read`/`blind`）是广门的活，别拿窄门凑。
- 窄门的约束是在广门之上**再收**，不是旁路：`egress=false` 时本件同样不得被触达
  （门在 `__init__.handle_frame` 上，两扇共用同一个开关）。
- 需要非 image 段、段数 > 2、或 `ai_behavior != blind` → 这不属于窄门的用途，拒（60005）。

窄门硬约束逐字（c1 §5.4 L182 + r-neko §6/§9-10）：
- context 防伪：version==1 且 source=="plugin:<本插件id>"，不符 SDK 本地直接 invalid_context；
- 仅 ai_behavior=="blind"；0 < len(parts) <= 2；每段 type=="image"；
- 先 subscribe(entry_id) 才可用；
- 前置不满足 → 60005 door_closed；SDK deliberately does not retry or fall back to
  push_message（docs/plugin-reply-tail.md:40）——本件任何分支不得改投 push。
宿主限额备查：hooks 总 8、每插件 2、payload 2 MiB（main_logic/reply_tail.py import 读数）。
"""
from __future__ import annotations

from typing import Any

TAIL_VERSION = 1  # ReplyTailClient.version 实值（r-neko §6）
TAIL_PARTS_MAX = 2
TAIL_ALLOWED_SEG = "image"
ERR_DOOR_CLOSED = 60005


def tail_context(plugin_id: str) -> dict[str, Any]:
    """构造 SDK 防伪要求的 context。source 前缀逐字 `plugin:`。"""
    return {"version": TAIL_VERSION, "source": f"plugin:{plugin_id}"}


def parts_gate(parts: list[dict[str, Any]]) -> str | None:
    """窄门 parts 体检：违规返回拒因字符串，合规返回 None。"""
    if not parts or len(parts) > TAIL_PARTS_MAX:
        return f"parts 数量须在 1..{TAIL_PARTS_MAX}，收到 {len(parts)}"
    for part in parts:
        if not isinstance(part, dict) or part.get("type") != TAIL_ALLOWED_SEG:
            return "reply_tail 只允许 image 段"
    return None


async def register_tail(ctx: Any, *, plugin_id: str, entry_id: str,
                        registration_id: str, parts: list[dict[str, Any]],
                        timeout: float = 5.0) -> dict[str, Any]:
    """subscribe → register 两步窄门。返回统一 dict：
    {"ok": bool, "code": int|None, "detail": dict|str}。不抛、不回退 push_message。"""
    gate = parts_gate(parts)
    if gate is not None:
        return {"ok": False, "code": ERR_DOOR_CLOSED, "detail": f"door_closed: {gate}"}
    client = getattr(ctx, "reply_tail", None)
    if client is None:
        return {"ok": False, "code": ERR_DOOR_CLOSED, "detail": "door_closed: SDK reply_tail 面不可用"}
    try:
        await client.subscribe(entry_id, timeout=min(timeout, 5.0))
        result = await client.register(
            tail_context(plugin_id),
            registration_id=registration_id,
            parts=parts,
            ai_behavior="blind",  # 固定值；窄门不接受其它行为轴取值
        )
    except Exception as exc:  # HTTP 面异常交回调用方上报 degraded，桥不自愈重发
        return {"ok": False, "code": 60006, "detail": f"peer_unreachable: {exc!r}"}
    accepted = isinstance(result, dict) and result.get("accepted") is True
    code = None if accepted else ERR_DOOR_CLOSED
    return {"ok": accepted, "code": code, "detail": result}
