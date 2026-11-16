"""会话自建表（CBP conv ↔ neko conversation_id）。

宿主不可见声明：这张表只存本插件私有 `ctx.store` KV，不写宿主任何总线或会话存储；
N.E.K.O 侧的会话视图里没有它（蓝图 L185；PluginStore 在 SDK `__all__` 58 名内，r-neko §4/§10）。
store 面未测的口子收在本文件一处：`store.get/set/delete` 名字若与真宿主 PluginStore
不符，只改 `_Raw` 适配层，调用面不动。
shortcut: store 方法名按读侧证据（[plugin.store] 字段存在，r-neko §3/§10）写成 get/set/delete；
真机首跑（沙箱卡）后若签名不符，升级点即此处。
"""
from __future__ import annotations

from typing import Any, Iterator

_KEY_PREFIX = "cortico_bridge.conv."
_CONV_KEY = _KEY_PREFIX + "index"  # 存 conv 键列表，store 无 keys() 前缀枚举时用它


class ConvMap:
    """conv → conversation_id 双向 KV。内存兜底 + 私有 store 持久。"""

    def __init__(self, store: Any | None = None) -> None:
        self._store = store
        self._mem: dict[str, str] = {}

    def _load_index(self) -> list[str]:
        if self._store is None:
            return []
        raw = self._store.get(_CONV_KEY)
        return list(raw) if isinstance(raw, list) else []

    def _save_index(self, keys: list[str]) -> None:
        if self._store is not None:
            self._store.set(_CONV_KEY, keys)

    def get(self, conv: str) -> str | None:
        key = _KEY_PREFIX + conv
        if key in self._mem:
            return self._mem[key]
        if self._store is None:
            return None
        value = self._store.get(key)
        if isinstance(value, str):
            self._mem[key] = value
            return value
        return None

    def put(self, conv: str, conversation_id: str) -> None:
        key = _KEY_PREFIX + conv
        self._mem[key] = conversation_id
        if self._store is not None:
            self._store.set(key, conversation_id)
            index = self._load_index()
            if key not in index:
                index.append(key)
                self._save_index(index)

    def resolve(self, conv: str, conversation_id: str | None = None) -> str | None:
        """读已有映射；传入 conversation_id 时先写后读（watch 首次观察到会话时回填）。"""
        if conversation_id is not None and self.get(conv) != conversation_id:
            self.put(conv, conversation_id)
        return self.get(conv)

    def __iter__(self) -> Iterator[tuple[str, str]]:
        keys = set(self._mem) | set(self._load_index())
        for key in sorted(keys):
            if not key.startswith(_KEY_PREFIX) or key == _CONV_KEY:
                continue
            conv = key[len(_KEY_PREFIX):]
            cid = self._mem.get(key) or (self._store.get(key) if self._store else None)
            if isinstance(cid, str):
                self._mem[key] = cid
                yield conv, cid
