"""turn → 入站记录表（F3 执法腿，SECURITY-CBP §1）。

裁决逐字（SECURITY-CBP.md:37-38）：
- 「桥只用 `turn` 查自己的入站记录来定身份。线上 `actor` 只作对账用。两者不一致时以宿主记录
  为准，并回一条 `event{name:"degraded"}`，`data.reason = "actor-mismatch"`。」
- 「`turn` 查不到、或超出入站记录保留窗 → 拒执行，回 `reply{ok:false,error:{code:60004,
  message:"unknown_session: ..."}}`」。
窗长口径：取 §5.1 的 rid 缓存口径（SECURITY-CBP.md:38 指 §5.1，:222 定 TTL 300 秒、上限 200，
先例 `bridge.py:65-66`）。CHANGELOG-CBP.md:488-490 开放项 2 记的「300 秒 vs 限流窗」之争
按该句原文取 rid 缓存口径，不取 60 帧/分窗。

anon 判定（SECURITY-CBP.md:35）：`member.id == self.id` → self；载荷带匿名位 → anonymous；
宿主没给发信人 → unknown；其余 → identified。N.E.K.O 线 self 永久 `proven:false`
（:72,:270，bus 面无 publish、无实证 bot 主键），所以 `self` 这一支在本线永不成立——
按 §2 规则 5「不在 v1 用启发式冒充实证」，本文件宁可报 unknown 也不猜 self。
"""
from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any, Callable, Iterator

TTL_SECONDS = 300.0  # §5.1 rid 缓存口径（SECURITY-CBP.md:222）
MAX_ENTRIES = 200    # 同上，先例 bridge.py:66


class TurnLedger:
    """有界保时窗的 turn → actor 表。只登记桥自己发出去的 msg 帧。"""

    def __init__(self, *, ttl: float = TTL_SECONDS, cap: int = MAX_ENTRIES,
                 now: Callable[[], float] = time.monotonic) -> None:
        self._ttl = float(ttl)
        self._cap = int(cap)
        self._now = now
        self._rows: "OrderedDict[str, tuple[float, dict[str, Any]]]" = OrderedDict()

    @staticmethod
    def actor_of(frame: dict[str, Any]) -> dict[str, Any] | None:
        """CBP msg 帧 → 宿主实证 actor（四键闭合）。取不到平台名就不编造。"""
        raw = frame.get("raw")
        meta = raw.get("metadata") if isinstance(raw, dict) else None
        source = (raw.get("source") if isinstance(raw, dict) else None) or ""
        conv = frame.get("conv") or ""
        sender = frame.get("sender") or {}
        member_id = sender.get("id") or ""
        if not source or not conv or not member_id:
            return None
        user_id = (meta or {}).get("user_id")
        if user_id:
            anon = "identified"  # 宿主实证发信人（metadata.user_id，watch.py:57）
        elif (meta or {}).get("anonymous"):
            anon = "anonymous"
        else:
            anon = "unknown"
        return {
            "platform": str(source)[:64],
            "conv": str(conv)[:256],
            "member": {"id": str(member_id)[:256], "name": str(sender.get("name") or "")},
            "anon": anon,
        }

    def record(self, frame: dict[str, Any]) -> dict[str, Any] | None:
        """登记一条入站 msg。非 msg 帧、缺平台名的帧返回 None（不登记）。"""
        if frame.get("t") != "msg":
            return None
        actor = self.actor_of(frame)
        if actor is None:
            return None
        turn = frame.get("id")
        if not isinstance(turn, str) or not (1 <= len(turn) <= 64):
            return None
        self._rows.pop(turn, None)
        self._rows[turn] = (self._now(), actor)
        while len(self._rows) > self._cap:  # 有界：超上限逐出最旧
            self._rows.popitem(last=False)
        return actor

    def resolve(self, turn: Any) -> dict[str, Any] | None:
        """查宿主记录；缺位、超窗、非字符串一律 None（调用方转 60004）。"""
        if not isinstance(turn, str):
            return None
        row = self._rows.get(turn)
        if row is None:
            return None
        if self._now() - row[0] > self._ttl:
            del self._rows[turn]
            return None
        return row[1]

    def values(self) -> Iterator[dict[str, Any]]:
        return iter(actor for _ts, actor in self._rows.values())
