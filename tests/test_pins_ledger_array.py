"""台账数组形 ports 读取闸（G-10）。夹具端口是合成值，绝不写 fleet 台账真值。

跑法：`PY311 -I -m pytest tests/test_pins_ledger_array.py -q`。
"""
from __future__ import annotations

import pytest

import support


def _ledger(*rows):
    return {"ports": list(rows), "caps": {"neko": {"frame_bytes": 4096}}}


def test_array_ports_resolves_by_owner(bridge):
    pins = _ledger(
        {"port": 45991, "proto": "tcp", "owner": "cortico-world-neko listen", "bind": "127.0.0.1"},
        {"port": 45992, "proto": "tcp", "owner": "cortico-world-maibot listen", "bind": "127.0.0.1"},
    )
    assert bridge.pins.port_of(pins, "cortico_world_neko_ws") == 45991
    assert bridge.pins.bridge_ws_uri(pins) == "ws://127.0.0.1:45991"


def test_array_ports_missing_owner_raises(bridge):
    with pytest.raises(RuntimeError, match="cortico-world-neko listen"):
        bridge.pins.port_of(_ledger({"port": 45991, "owner": "someone-else listen"}),
                            "cortico_world_neko_ws")


def test_array_ports_duplicate_owner_raises(bridge):
    dup = _ledger({"port": 45991, "owner": "cortico-world-neko listen"},
                  {"port": 45992, "owner": "cortico-world-neko listen"})
    with pytest.raises(RuntimeError, match="恰好 1 行"):
        bridge.pins.port_of(dup, "cortico_world_neko_ws")
