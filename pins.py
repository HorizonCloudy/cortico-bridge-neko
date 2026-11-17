"""pins.json 读取件。端口与字节上限一律从 pins.json 取，本文件与全仓代码零端口、零上限字面量。

单一来源 = fleet 仓 `spec/pins.json`（_BUILD-PLAN §2.1、§4.4）。本仓通过环境变量
`CORTICO_PINS_JSON` 指向该文件；未指向就报错，绝不回退到内置缺省值——回退等于把
端口/上限写死。键名约定（数值只存在于 pins.json，本文件与全仓代码零字面量）：
  ports.cortico_world_neko_ws    Cortico 侧 neko world 监听口（蓝图 §4.4 表钉值，本处不复述数字）
  ports.cortico_world_maibot_ws  Cortico 侧 maibot world 监听口（同上）
  caps.neko.frame_bytes          neko 出站本地先拒上限（台账已折入宿主信封余量，桥侧不再做减法）
宿主侧端口（48911-48916 / 38865-38867 / 48961-48963）不进本桥代码：push_message 与
reply_tail 都由 SDK 自己解析宿主 config（r-neko §6/§8），桥不直连、也不该直连。
"""
from __future__ import annotations

import json
import os
from typing import Any

ENV_VAR = "CORTICO_PINS_JSON"
TOKEN_ENV = "CORTICO_BRIDGE_TOKEN"  # SECURITY-CBP.md:121 钉死的键名；只从环境读，不落插件配置文件
HOST = "127.0.0.1"  # loopback：Cortico 侧监听缺省绑环回（§4.4 绑定面列）
_QUERY_CRED_KEYS = ("token", "access_token")  # §3 拒停的查询参数名（driver.ts 同表）


def load_pins(path: str | None = None) -> dict[str, Any]:
    """读取 pins.json。path 缺省时取环境变量 CORTICO_PINS_JSON；两处皆空即报错。"""
    resolved = path or os.environ.get(ENV_VAR, "").strip()
    if not resolved:
        raise RuntimeError(
            f"pins.json 未指定：设 {ENV_VAR} 指向 fleet spec/pins.json（禁内置端口缺省）"
        )
    with open(resolved, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise RuntimeError(f"{resolved} 顶层不是 JSON 对象")
    return data


# 真台账 ports 是记录数组（fleet spec/pins.json:280 起，每行 owner/port/bind 等），
# 测试夹具走 dict 形。两形都读，本表把键折成台账 owner 全串，零端口字面量。
_OWNER_BY_KEY = {
    "cortico_world_neko_ws": "cortico-world-neko listen",
    "cortico_world_maibot_ws": "cortico-world-maibot listen",
}


def port_of(pins: dict[str, Any], key: str) -> int:
    """从 pins['ports'] 取整数端口；缺键即报错，不做默认值。"""
    ports = pins.get("ports")
    if isinstance(ports, list):
        owner = _OWNER_BY_KEY.get(key, key)
        rows = [e for e in ports if isinstance(e, dict) and e.get("owner") == owner]
        if len(rows) != 1:
            raise RuntimeError(f"pins.json ports 台账里 owner={owner!r} 命中 {len(rows)} 行（需恰好 1 行）")
        value = rows[0].get("port")
    elif isinstance(ports, dict) and key in ports:
        value = ports[key]
    else:
        raise RuntimeError(f"pins.json 缺 ports.{key}")
    if isinstance(value, bool) or not isinstance(value, int) or not (1 <= value <= 65535):
        raise RuntimeError(f"pins.json ports.{key} 非有效端口: {value!r}")
    return int(value)


def neko_payload_cap_bytes(pins: dict[str, Any]) -> int:
    """从 pins['caps']['neko']['frame_bytes'] 取出站本地先拒上限（字节）。
    缺键、null、非正整数一律抛错：静默回落旧值等于把上限重新写死进源码。"""
    if not isinstance(pins, dict):
        raise RuntimeError("pins 不是 dict：caps.neko.frame_bytes 无从读取")
    caps = pins.get("caps")
    neko = caps.get("neko") if isinstance(caps, dict) else None
    if not isinstance(neko, dict) or "frame_bytes" not in neko:
        raise RuntimeError("pins.json 缺 caps.neko.frame_bytes（禁回落内置缺省）")
    value = neko["frame_bytes"]
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RuntimeError(f"pins.json caps.neko.frame_bytes 非有效字节上限: {value!r}")
    return int(value)


def _assert_clean_uri(uri: str) -> str:
    """凭据禁过 URL（SECURITY-CBP.md:115）：带 query 的 URI 一律拒，不做比对直接挡。"""
    if "?" in uri or any(f"{s}=" in uri for s in _QUERY_CRED_KEYS):
        raise ValueError(f"refuse credential-bearing URI: {uri.split('?')[0]!r}")
    return uri


def auth_headers(config: dict[str, Any] | None = None) -> dict[str, str]:
    """共享密钥的唯一出口：HTTP Upgrade 头 `Authorization: Bearer <token>`。

    只从 `CORTICO_BRIDGE_TOKEN` 环境变量取数（SECURITY-CBP.md:121）。形参 `config`
    存在只为把「配置文件里的 token 一律无视」这件事写成可测的码：调用方可以把整个配置
    传进来，本函数不读它的任何键。缺 token 就返回空头——环回免鉴权是 Cortico 侧 listen
    的裁量（§3 第 2 条环回纪律），桥不自己发明缺省密钥。
    """
    del config  # 故意不读：凭据不落配置文件
    token = os.environ.get(TOKEN_ENV, "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def bridge_ws_uri(pins: dict[str, Any], key: str = "cortico_world_neko_ws") -> str:
    """拼 Cortico 侧 world 的 ws:// URI。零凭据：只有 scheme+host+port。"""
    return _assert_clean_uri(f"ws://{HOST}:{port_of(pins, key)}")
