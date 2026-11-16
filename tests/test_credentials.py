"""SECURITY-CBP §3 凭据流向（neko 腿）：共享密钥只走 HTTP Upgrade 头，禁过 URL。

裁决逐字（SECURITY-CBP.md:115-122）：
- 「CBP 的共享密钥只走 HTTP Upgrade 头 `Authorization: Bearer <token>`」；
- 「框架侧 Python 桥只从环境变量读（键名钉 `CORTICO_BRIDGE_TOKEN`），不落插件配置文件」；
- 「`log()`、`onState.lastError`、`doctor` 输出…里不得出现 token 值或带 query 的 URL」。
测试抓到（同件 :144）：「URI 串不含 `token` 子串」的活闸。全程禁网：websockets 用替身。
"""
from __future__ import annotations

import asyncio
import logging
import sys

import pytest
import support

TOKEN = "s3cr3t-do-not-leak"


class _FakeWs:
    def __init__(self, sink):
        self._sink = sink

    async def send(self, raw):
        self._sink.setdefault("sent", []).append(raw)

    async def __aenter__(self):
        self._sink["entered"] = True
        return self

    async def __aexit__(self, *a):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


def _install_fake_websockets(monkeypatch, captured, *, boom=False):
    class _Mod:
        @staticmethod
        def connect(uri, **kwargs):
            captured["uri"] = uri
            captured["kwargs"] = kwargs
            if boom:
                raise ConnectionError("dial refused")
            return _FakeWs(captured)

    monkeypatch.setitem(sys.modules, "websockets", _Mod)


def _plugin(bridge, ctx=None):
    plugin = bridge.CorticoBridgePlugin(ctx or support.FakeCtx())
    plugin._pins = support.PINS_SAMPLE
    plugin._egress_open = False
    plugin._link_open = True
    plugin._outgoing = asyncio.Queue()
    plugin._turns = bridge.turns.TurnLedger()
    return plugin


# ---------- 取数口：只认环境变量 ----------

def test_token_env_var_name_is_pinned(bridge):
    assert bridge.pins.TOKEN_ENV == "CORTICO_BRIDGE_TOKEN"  # SECURITY-CBP.md:121 逐字


def test_auth_header_is_bearer_form(bridge, monkeypatch):
    monkeypatch.setenv(bridge.pins.TOKEN_ENV, TOKEN)
    assert bridge.pins.auth_headers() == {"Authorization": f"Bearer {TOKEN}"}


def test_no_token_means_no_header(bridge, monkeypatch):
    monkeypatch.delenv(bridge.pins.TOKEN_ENV, raising=False)
    assert bridge.pins.auth_headers() == {}


def test_config_token_is_ignored(bridge, monkeypatch):
    """配置文件里的 token 一律无视：不落插件配置文件（SECURITY-CBP.md:121）。"""
    monkeypatch.delenv(bridge.pins.TOKEN_ENV, raising=False)
    assert bridge.pins.auth_headers({"token": TOKEN}) == {}
    monkeypatch.setenv(bridge.pins.TOKEN_ENV, "from-env")
    assert bridge.pins.auth_headers({"token": TOKEN})["Authorization"] == "Bearer from-env"


# ---------- URL 面：不得带凭据 ----------

def test_uri_never_carries_the_token(bridge, pins_file, monkeypatch):
    monkeypatch.setenv(bridge.pins.TOKEN_ENV, TOKEN)
    uri = bridge.pins.bridge_ws_uri(bridge.pins.load_pins())
    assert uri == f"ws://127.0.0.1:{support.PINS_SAMPLE['ports']['cortico_world_neko_ws']}"
    assert "token" not in uri.lower() and TOKEN not in uri and "?" not in uri


@pytest.mark.parametrize("dirty", ["ws://127.0.0.1:1?token=x", "ws://127.0.0.1:1/a?access_token=y"])
def test_clean_uri_guard_rejects_query(bridge, dirty):
    with pytest.raises(ValueError):
        bridge.pins._assert_clean_uri(dirty)


# ---------- 拨号面：密钥进 headers，不进 URL ----------

def test_link_once_passes_headers_not_url(bridge, monkeypatch):
    captured: dict = {}
    _install_fake_websockets(monkeypatch, captured)
    monkeypatch.setenv(bridge.pins.TOKEN_ENV, TOKEN)

    asyncio.run(_plugin(bridge)._link_once())

    assert "?" not in captured["uri"] and TOKEN not in captured["uri"]
    assert captured["kwargs"]["additional_headers"] == {"Authorization": f"Bearer {TOKEN}"}
    hello = bridge.cbp.decode(captured["sent"][0])
    assert hello["t"] == "hello"
    assert hello["self"] == {"id": "", "name": "", "proven": False}  # §2：neko 线永久 false
    assert "token" not in hello and "auth" not in {k.lower() for k in hello}  # F4：帧面无 token 位


def test_dial_failure_log_never_carries_token(bridge, monkeypatch):
    """退避日志面同类 `emitState(String(e))`：异常串进日志前必须不含密钥值。"""
    captured: dict = {}
    _install_fake_websockets(monkeypatch, captured, boom=True)
    monkeypatch.setenv(bridge.pins.TOKEN_ENV, TOKEN)

    logs = []

    class _H(logging.Handler):
        def emit(self, record):
            logs.append(record.getMessage())

    ctx = support.FakeCtx()
    ctx.logger = logging.getLogger("neko-token-fence")
    ctx.logger.handlers[:] = [_H()]
    ctx.logger.setLevel(logging.WARNING)

    async def _stop_soon(_delay):  # 只跑一轮退避，不真等
        raise asyncio.CancelledError

    monkeypatch.setattr(bridge.asyncio, "sleep", _stop_soon)
    try:
        asyncio.run(_plugin(bridge, ctx)._link_loop())
    except asyncio.CancelledError:
        pass
    assert logs, "拨号失败必须留一条日志"
    assert all(TOKEN not in line for line in logs), logs
    assert all("?" not in line for line in logs), logs


# ---------- 静态闸：全仓不拼 query 凭据 ----------

def test_no_query_token_concatenation_in_source(bridge):
    for name in ("pins.py", "__init__.py", "watch.py", "ingress.py", "replytail.py", "cbp.py"):
        src = (support.REPO_ROOT / name).read_text(encoding="utf-8")
        for bad in ("?token=", "&token=", "?access_token=", "&access_token="):
            assert bad not in src, f"{name} 出现 {bad}"
