"""CBP → push_message v2 出站门（两扇门里的**广门**）。两轴取值与限额按 r-neko §5 实测值，不写死推测值。

使用边界（与 `replytail.py` 的窄门相对，全文见 README「两条出站门的使用边界」）：
- 广门管「有正文要说话」：任意 `Seg[]` 投进 `chat`/`hud`，`ai_behavior` 三值可选；
  尾部贴图（image-only ≤2 且 `blind`）是窄门的活，别拿广门凑。
- 两扇门共用同一个 `egress` 开关，`egress=false` 时一起关（60005 door_closed），
  窄门不得成为广门的旁路。

规则钉自：
- v1 十关键词弃用线已越过（DEPRECATION_REMOVAL_VERSION="v0.9" vs 宿主 0.9.1，push_message_schema.py:61
  @52e973ed2）——本件只按 v2 kwargs 形调用；子进程真实对象 PluginContext 的 11 个 legacy 位参
  （core/context.py:1050 @52e973ed2）一律不用（C1-6）。
- 载荷上限不写死在本文件：从 pins 台账 `caps.neko.frame_bytes` 经
  `pins.neko_payload_cap_bytes` 读取（台账值已折入宿主信封余量，settings.py:588、
  core/context.py:247-252 @52e973ed2，见 pins.json notes.neko_frame_bytes）：
  本地先按 parts 序列化长度拒，省一次往返；台账缺键直接抛错，不回落。
- reason 四值 → 桥错误码互译（c1 §5.4 表 / 蓝图 L74）：
  backpressure→60006、transport_error→20002、transport_unavailable→60006、payload_too_large→60003。
- egress 缺省关：门未开直接 60005 door_closed，不触 SDK。
- F3 之后本函数只在身份门之后被叫：`turn` 查不到宿主入站记录（60004）或 `anon != "identified"`
  （写动作 60005）时，根本走不到这里（SECURITY-CBP.md:36-40，`__init__.handle_frame`）。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from . import pins as pins_mod

# 两轴全集逐字（push_message_schema.py:63-64 @52e973ed2，import 实测）
VISIBILITY_VALUES = ("chat", "hud")
AI_BEHAVIOR_VALUES = ("respond", "read", "blind")

REASON_TO_CODE = {
    "backpressure": 60006,
    "transport_error": 20002,
    "transport_unavailable": 60006,
    "payload_too_large": 60003,
}
# N2：码位与语义逐一对着台账钉（spec/conformance.json error_codes、core errors.ts RET）：
# 60003 只留给 payload_too_large（超限）；轴非法与无正文是参数问题，走 10003 Bad Param。
ERR_DOOR_CLOSED = 60005
ERR_VERSION = 60001
ERR_PAYLOAD_TOO_LARGE = 60003
ERR_UNKNOWN_SESSION = 60004
ERR_BAD_PARAM = 10003
ERR_INTERNAL = 20002
ERR_DEGRADED = 60008


class DoorError(Exception):
    """本地拒收：门未开（60005）、轴非法或无正文（10003 bad_param）、载荷超限（60003）。
    code 对着台账枚举选位（spec/conformance.json error_codes；core errors.ts RET）。"""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"{code} {message}")
        self.code = code
        self.message = message


def build_parts(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """CBP Seg[] → push parts。SDK 不校验 part type（r-neko §5「caller responsible」）；
    未知段兜底 `[<type>]` 是内核 segment.ts 唯一实现点（C1-16），桥侧不重复渲染，
    只把 data 值归一成字符串并保留原 type 透传。"""
    parts: list[dict[str, Any]] = []
    for seg in segments:
        if not isinstance(seg, dict) or not isinstance(seg.get("type"), str):
            continue
        data = seg.get("data") if isinstance(seg.get("data"), dict) else {}
        parts.append({"type": seg["type"], "data": {k: str(v) for k, v in data.items()}})
    return parts


def validate_axes(visibility: list[str], ai_behavior: str) -> None:
    if not visibility or not all(v in VISIBILITY_VALUES for v in visibility):
        raise DoorError(ERR_BAD_PARAM, f"bad_param: visibility 必须落在 {VISIBILITY_VALUES}，收到 {visibility!r}")
    if ai_behavior not in AI_BEHAVIOR_VALUES:
        raise DoorError(ERR_BAD_PARAM, f"bad_param: ai_behavior 必须落在 {AI_BEHAVIOR_VALUES}，收到 {ai_behavior!r}")


def push_kwargs(*, pins: dict[str, Any], egress_open: bool, segments: list[dict[str, Any]],
                text: str = "", visibility: list[str] | None = None,
                ai_behavior: str = "respond",
                metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """CBP 出站料 → push_message kwargs。上限只认 pins 台账（缺 caps 即抛，不回落）。
    规范形 cv-call 的正文在顶层 `text`：`segments` 空且 `text` 非空时合成一个 text 段。
    不合规在这里拦，SDK 调用面永远 kwargs-only。"""
    if not egress_open:
        raise DoorError(ERR_DOOR_CLOSED, "door_closed: 出站门未开（egress=false）")
    cap = pins_mod.neko_payload_cap_bytes(pins)
    vis = list(visibility) if visibility else ["chat"]
    validate_axes(vis, ai_behavior)
    if not segments and text:  # 规范形：正文只在顶层 text，段表为空
        segments = [{"type": "text", "data": {"text": text}}]
    parts = build_parts(segments)
    if not parts:
        # N2：这是参数问题（缺正文/段全畸形），不是超限；60003 只属于 payload_too_large。
        if not segments:
            raise DoorError(ERR_BAD_PARAM, "bad_param: segments 与 text 都空，push 至少需要一个可渲染段")
        raise DoorError(ERR_BAD_PARAM, "bad_param: 段表非空但没有一个合法可渲染段")
    payload = json.dumps({"visibility": vis, "ai_behavior": ai_behavior, "parts": parts},
                         ensure_ascii=False)
    if len(payload.encode("utf-8")) > cap:
        raise DoorError(ERR_PAYLOAD_TOO_LARGE,
                        f"payload_too_large: 超台账 caps.neko.frame_bytes={cap} 本地先拒")
    return {"visibility": vis, "ai_behavior": ai_behavior, "parts": parts,
            "metadata": metadata or {}}


def outcome_from_result(result: Any) -> tuple[bool, int | None, str]:
    """PushMessageResult 两态 → (ok, code, message)。
    submitted=True → (True,None,"submitted")；拒收按 reason 四值互译；未知形态按 60007。"""
    if isinstance(result, dict):
        if result.get("submitted") is True and result.get("ok") is not False:
            return True, None, "submitted"
        reason = str(result.get("reason") or "")
        if reason in REASON_TO_CODE:
            return False, REASON_TO_CODE[reason], reason
    return False, 60007, "send_uncertain: push_message 返回不可解析"


async def push(ctx: Any, kwargs: dict[str, Any]) -> tuple[bool, int | None, str]:
    """执行 push_message。只按 kwargs 形传参（C1-6 硬规则：位参不可依赖）。

    真宿主 push_message 同步（core/context.py:1050 @52e973e）。to_thread 是宿主
    自家 async 壳的形状（core/context.py:1509-1516）：await 同步方法会 TypeError，
    且不被 except DoorError 兜住，会拆掉整条连接。
    """
    result = await asyncio.to_thread(ctx.push_message, **kwargs)
    return outcome_from_result(result)
