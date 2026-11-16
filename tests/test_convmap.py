"""工单验收：`PY311 -I -m pytest tests/test_convmap.py -q`。私有 store 双向表 + 宿主不可见约定。"""
from __future__ import annotations

import support


def test_roundtrip_and_backfill(bridge):
    store = support.FakeStore()
    cm = bridge.convmap.ConvMap(store)
    assert cm.get("conv-9") is None
    assert cm.resolve("conv-9", "neko-cid-9") == "neko-cid-9"
    assert cm.get("conv-9") == "neko-cid-9"
    assert store.data["cortico_bridge.conv.conv-9"] == "neko-cid-9"


def test_host_invisible_namespace(bridge):
    """表只落在带前缀的私有键下；宿主会话面看不到这些键名以外的结构。"""
    store = support.FakeStore()
    cm = bridge.convmap.ConvMap(store)
    for i in range(3):
        cm.put(f"c{i}", f"id{i}")
    keys = list(store.data)
    assert all(k.startswith("cortico_bridge.conv.") for k in keys)
    assert len(keys) == 4  # 3 条映射 + 1 条自维护索引，不污染其它命名空间


def test_memory_fallback_without_store(bridge):
    cm = bridge.convmap.ConvMap(None)
    cm.put("a", "1")
    assert cm.get("a") == "1"
    assert list(cm) == [("a", "1")]


def test_reload_from_store(bridge):
    store = support.FakeStore()
    bridge.convmap.ConvMap(store).put("x", "y")
    fresh = bridge.convmap.ConvMap(store)
    assert fresh.get("x") == "y"  # 冷读穿透回内存
    assert dict(fresh) == {"x": "y"}
