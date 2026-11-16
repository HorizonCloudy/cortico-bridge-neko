"""CBP v1 帧编解码（vendored 副本）。规范单一来源 = fleet `spec/CBP-v1.json`；
帧表常量不手抄——由 fleet `tools/gen_table.py` 生成 `cbp_table.py`，本文件只 import
（跨仓字节闸：tests/test_table_parity.py；纪律 SECURITY-CBP.md:60、cbp.py:1-6 头注）。
改动顺序：先改 spec/CBP-v1.json 与 conformance.json，重跑生成器，再动本文件的逻辑。

线上编码 = 单帧一条 UTF-8 JSON 文本 WS 消息。公共头逐型必有 {v,t,id,ts}；
ts 为 float64 秒。防御解析：拒 null / 数组 / 标量 / 缺头 / 未知 t，坏帧返回 None 由调用方吞掉。
"""
from __future__ import annotations

import json
import math
import time
import uuid
from typing import Any

from .cbp_table import (  # MSG_KINDS/TOOL_RISKS/ANON_VALUES 经本模块对外再导出（watch.py:63 等）
    ANON_VALUES,
    CBP_VERSION,
    MSG_KINDS,
    TOOL_RISKS,
    _ACTOR_KEYS,
    _ID_MAX,
    _MEMBER_KEYS,
    _OPTIONAL,
    _REQUIRED,
    _SELF_KEYS,
    _STR_MAX,
)


def new_frame_id() -> str:
    return uuid.uuid4().hex


def make_frame(t: str, **fields: Any) -> dict[str, Any]:
    """构造一条带公共头的帧；类型未注册直接 ValueError（写侧从紧）。"""
    if t not in _REQUIRED:
        raise ValueError(f"unknown CBP frame type: {t!r}")
    ts = fields.pop("ts", None)
    fid = fields.pop("id", None)
    frame: dict[str, Any] = {
        "v": CBP_VERSION,
        "t": t,
        "id": fid or new_frame_id(),
        "ts": time.time() if ts is None else ts,
    }
    frame.update(fields)
    return frame


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _closed_str(obj: Any, keys: tuple[str, ...], *, allow_empty: tuple[str, ...] = ()) -> bool:
    """闭合的 {str:...} 对象：键集恰等于 keys，值全为 str，非 allow_empty 的键不得空。"""
    if not isinstance(obj, dict) or set(obj) != set(keys):
        return False
    for key in keys:
        value = obj[key]
        if not isinstance(value, str) or (key not in allow_empty and not value):
            return False
    return True


def _is_self_decl(obj: Any) -> bool:
    """$defs/self：三键闭合，id/name ≤256 可为空串（实证失败也要诚实申报），proven 必为 bool。"""
    if not isinstance(obj, dict) or set(obj) != set(_SELF_KEYS):
        return False
    if not isinstance(obj["id"], str) or not isinstance(obj["name"], str):
        return False
    return len(obj["id"]) <= _STR_MAX and len(obj["name"]) <= _STR_MAX and isinstance(obj["proven"], bool)


def _is_actor(obj: Any) -> bool:
    """$defs/actor：四键闭合；member 两键闭合且 id 非空；anon 限四值。"""
    if not isinstance(obj, dict) or set(obj) != set(_ACTOR_KEYS):
        return False
    if not isinstance(obj["platform"], str) or not (1 <= len(obj["platform"]) <= _ID_MAX):
        return False
    if not isinstance(obj["conv"], str) or not (1 <= len(obj["conv"]) <= _STR_MAX):
        return False
    if not _closed_str(obj.get("member"), _MEMBER_KEYS, allow_empty=("name",)):
        return False
    if not obj["member"]["id"] or len(obj["member"]["id"]) > _STR_MAX:
        return False
    return obj["anon"] in ANON_VALUES


def _body_ok(t: str, obj: dict[str, Any]) -> bool:
    """逐型结构校验。F1/F2 与 F3 的新必填位在这里判坏帧（吞帧面，非 60004 面）。"""
    if t in ("hello", "ready") and not _is_self_decl(obj.get("self")):
        return False
    if t == "hello" and not _closed_str(obj.get("agent"), ("name", "version", "sha")):
        return False
    if t == "call":
        turn = obj.get("turn")
        if not isinstance(turn, str) or not (1 <= len(turn) <= _ID_MAX):
            return False
        if not _is_actor(obj.get("actor")):
            return False
        if not isinstance(obj.get("args"), dict):  # args 保持开映射（SECURITY-CBP.md:41）
            return False
    return True


def parse_frame(obj: Any) -> dict[str, Any] | None:
    """防御解析。任何不合形的输入（含合法 JSON 标量/数组）返回 None。"""
    if not isinstance(obj, dict):  # 同时挡掉 null/数组/字符串/数字
        return None
    if obj.get("v") != CBP_VERSION:
        return None
    t = obj.get("t")
    if not isinstance(t, str) or t not in _REQUIRED:
        return None
    fid = obj.get("id")
    if not isinstance(fid, str) or not (1 <= len(fid) <= 64):
        return None
    if not _is_number(obj.get("ts")):
        return None
    for key in _REQUIRED[t]:
        if key not in obj:
            return None
    return obj if _body_ok(t, obj) else None


def encode(frame: dict[str, Any]) -> str:
    """序列化已构造帧。不合规帧拒发（发侧校验=收侧同一函数）。"""
    if parse_frame(frame) is None:
        raise ValueError(f"refuse to encode invalid CBP frame: {frame!r:.200}")
    return json.dumps(frame, ensure_ascii=False, separators=(",", ":"))


def decode(raw: str | bytes) -> dict[str, Any] | None:
    try:
        obj = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    return parse_frame(obj)


def make_self_decl(self_id: str = "", name: str = "", proven: bool = False) -> dict[str, Any]:
    """$defs/self 构造器。N.E.K.O 线永久 proven:false（SECURITY-CBP.md:72,270）：
    bus 面只有读与 watch、无 publish，唯一取值口是 role=="assistant" 启发式，不算实证。
    空 id 配 false 是合法申报——不许用「没有 self」冒充「没有 self 需求」。"""
    return {"id": self_id, "name": name, "proven": proven}


def hello_frame(agent_name: str, version: str, sha: str, caps: list[str], tools: list[dict],
                self_decl: dict[str, Any] | None = None) -> dict:
    """F1：桥拨出的第一个握手帧必须带 self（SECURITY-CBP.md:74）。"""
    return make_frame(
        "hello",
        agent={"name": agent_name, "version": version, "sha": sha},
        self=self_decl or make_self_decl(),
        v_support=[CBP_VERSION],
        caps=list(caps),
        tools=list(tools),
    )
