"""actor/turn 必填写负例（L2 任务 4；SECURITY-CBP §1 规则 2/4 与 §2，F1/F2/F3）。

四条必填负例：缺 actor 拒、缺 turn 拒、actor 多余键拒、anon 非法值拒；
外加完整 call 正例锚点，防「全拒」伪装成绿。两仓放同一份文件、各钉自己的副本
（SECURITY-CBP.md:60）。首红记录见 reports/L2-python-source.md。
"""
from __future__ import annotations

import json

import support

cbp = support.load_cbp_standalone(support.REPO_ROOT, f"identity_cbp_{support.REPO_ROOT.name}")

SELF = {"id": "", "name": "", "proven": False}  # hello_proven_false 逐字（conformance.json）
ACTOR = {  # call_full_actor_bound 逐字（conformance.json）
    "platform": "onebot",
    "conv": "group_5_4",
    "member": {"id": "2002", "name": "nick"},
    "anon": "identified",
}


def _frame(t, **over):
    f = {"v": 1, "t": t, "id": f"cv-{t}", "ts": 1763136120.75}
    if t == "hello":
        f.update(agent={"name": "cb", "version": "0.1.0", "sha": "s"},
                 self=dict(SELF), v_support=[1], caps=[], tools=[])
    elif t == "ready":
        f.update(caps=[], tools=[], self=dict(SELF))
    elif t == "call":
        f.update(rid="r1", tool="x", args={}, turn="cv-msg", actor=dict(ACTOR))
    f.update(over)
    return f


def _decode(f):
    return cbp.decode(json.dumps(f, ensure_ascii=False, separators=(",", ":")))


# ---- 正例锚点 ----

def test_call_with_turn_and_actor_accepted():
    assert _decode(_frame("call")) is not None


def test_hello_ready_with_self_accepted():
    assert _decode(_frame("hello")) is not None
    assert _decode(_frame("ready")) is not None


# ---- 任务 4 的四条负例 ----

def test_call_missing_actor_rejected():
    f = _frame("call")
    del f["actor"]
    assert _decode(f) is None


def test_call_missing_turn_rejected():
    f = _frame("call")
    del f["turn"]
    assert _decode(f) is None


def test_actor_extra_key_rejected():
    # actor 四键闭合（CBP-v1.json:198-224 additionalProperties:false）
    assert _decode(_frame("call", actor={**ACTOR, "role": "admin"})) is None
    # member 两键闭合（$defs/sender :147-164）
    assert _decode(_frame("call", actor={**ACTOR, "member": {"id": "2002", "name": "n", "role": "a"}})) is None


def test_actor_anon_invalid_rejected():
    # $defs/anon 四值之外全拒（CBP-v1.json:189-197 ← SECURITY-CBP.md:35）
    for bad in ("ghost", "", "IDENTIFIED", " Identified", None, 1):
        assert _decode(_frame("call", actor={**ACTOR, "anon": bad})) is None, bad
