"""测试支撑件：宿主同款加载配方 + 假 ctx。自省专用，全程不触网、不起宿主。

加载方式对齐宿主 import 回退路（_import_current_plugin_from_config，host.py:557 @52e973ed2：
按目录找 __init__.py、kebab 目录名 ≡ snake 包名）。假面对象只覆盖骨架用到的面。
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
import types
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "cortico_bridge_neko"


def load_cbp_standalone(repo_root: Path, pkg_name: str):
    """合成包命名空间下加载 cbp.py（存在时连 cbp_table.py），不碰宿主依赖。

    跨仓 parity 闸用：任何 venv 都能加载两仓的纯 stdlib 编解码层。
    """
    full = f"{pkg_name}.cbp"
    if full in sys.modules:
        return sys.modules[full]
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(repo_root)]
    sys.modules[pkg_name] = pkg
    if (repo_root / "cbp_table.py").exists():
        importlib.import_module(f"{pkg_name}.cbp_table")
    return importlib.import_module(f"{pkg_name}.cbp")

# 测试夹具数据。真值单一来源仍是 fleet spec/pins.json；此处只为自检造的替身
# （caps 用替身值：真台账数值若写进夹具，零字面量闸 test_zero_literal_caps 就会红）。
PINS_SAMPLE: dict[str, Any] = {
    "ports": {"cortico_world_neko_ws": 39701, "cortico_world_maibot_ws": 39702},
    "neko": {"sdk_version": "0.1.0", "app_version": "0.9.1"},
    "caps": {"neko": {"frame_bytes": 4096}},
}


def write_pins(tmp_path: Path) -> str:
    import json

    p = tmp_path / "pins.json"
    p.write_text(json.dumps(PINS_SAMPLE), encoding="utf-8")
    return str(p)


def load_bridge_package():
    """宿主配方加载整仓为 cortico_bridge_neko 包。"""
    if PACKAGE_NAME in sys.modules:
        return sys.modules[PACKAGE_NAME]
    init_file = REPO_ROOT / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        PACKAGE_NAME, str(init_file), submodule_search_locations=[str(REPO_ROOT)]
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE_NAME] = module
    spec.loader.exec_module(module)
    return module


class FakeStore:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value


class FakeReplyTail:
    def __init__(self, *, register_result: dict | None = None, raise_on: str | None = None) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self._register_result = register_result or {"accepted": True, "status": "ok"}
        self._raise_on = raise_on

    async def subscribe(self, entry_id: str, **kwargs) -> dict:
        self.calls.append(("subscribe", (entry_id,), kwargs))
        if self._raise_on == "subscribe":
            raise ConnectionError("simulated http failure")
        return {"accepted": True}

    async def register(self, context: dict, **kwargs) -> dict:
        self.calls.append(("register", (context,), kwargs))
        if self._raise_on == "register":
            raise ConnectionError("simulated http failure")
        return dict(self._register_result)


class FakeBus:
    class _Messages:
        def __init__(self, *, fail: bool = False) -> None:
            self.fail = fail
            self.watched = False

        def watch(self, ctx: Any = None, **kwargs: Any):
            if self.fail:
                raise TypeError("watch is not available for this bus list")
            self.watched = True
            outer = self

            class _W:
                def __init__(self) -> None:
                    self.cb = None
                    self.started = False

                def subscribe(self, *, on: str):
                    def deco(fn):
                        self.cb = fn
                        return fn
                    return deco

                def start(self) -> None:
                    self.started = True

            self._watcher = _W()
            return self._watcher

    def __init__(self, *, fail: bool = False) -> None:
        self.messages = FakeBus._Messages(fail=fail)


class FakeCtx:
    """覆盖骨架用到的 ctx 面：push_message / reply_tail / bus / store / config / plugin_id。"""

    def __init__(self, *, push_results: list[Any] | None = None, config: dict | None = None,
                 store: Any = "default", reply_tail: Any = None, bus: Any = None) -> None:
        self.plugin_id = "cortico_bridge"
        self.config = dict(config or {})
        self.store = FakeStore() if store == "default" else store
        self.bus = bus if bus is not None else FakeBus()
        self.reply_tail = reply_tail
        self._push_results = list(push_results or [{"submitted": True}])
        self.push_calls: list[tuple[tuple, dict]] = []
        self.logger = logging.getLogger("fake-neko-ctx")

    def push_message(self, *args: Any, **kwargs: Any) -> Any:  # 同步：与真宿主同形
        self.push_calls.append((args, kwargs))
        return self._push_results.pop(0) if len(self._push_results) > 1 else self._push_results[0]


class FakeDelta:
    def __init__(self, *, added=(), changed=(), removed=()) -> None:
        self.added = tuple(added)
        self.changed = tuple(changed)
        self.removed = tuple(removed)
        self.kind = "delta"
