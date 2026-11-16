"""跨仓等值闸（L2 任务 3；SECURITY-CBP.md:60「副本各跑同一向量表，漂移即红」、
CHANGELOG-CBP.md:471,479）。两仓放同一份文件，各自从自己视角跑。

三道锁：
 1. 两仓盘上 cbp_table.py 与 fleet tools/gen_table.py 的 render() 逐字节一致；
 2. 两仓 cbp_table.py 互相逐字节一致（由 1 传递，单列以隔离误诊方向）；
 3. conformance.json 全部 frames/frame_negatives wire 在两桥 decode 判定相同。

红→绿记录见 reports/L2-python-source.md：首红于 2026-11-16，当时 maibot 手抄旧表
（cbp.py:20 hello 无 self、:25 call 无 turn/actor），五个安全负例判定分叉。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import support

REPO_ROOT: Path = support.REPO_ROOT
BASE = REPO_ROOT.parent
SIBLING_NAME = "cortico-bridge-maibot" if REPO_ROOT.name == "cortico-bridge-neko" else "cortico-bridge-neko"
SIBLING_ROOT = BASE / SIBLING_NAME
GEN_PATH = BASE / "cortico-bridge-fleet" / "tools" / "gen_table.py"
CONFORMANCE_PATH = BASE / "cortico-bridge-fleet" / "spec" / "conformance.json"

# 本周期入表的五个安全负例（validate_cbp.mjs:59-64 台账同源）。Python 桥只镜像
# 必填位与 self/actor 闭合，全量 additionalProperties 由 ajv 闸负责——所以其余
# 负例只要求两桥判定一致，不要求等于 schema 判定。
SECURITY_NEGATIVES = frozenset({
    "call_missing_actor", "call_missing_turn", "actor_extra_key_closed",
    "hello_missing_self", "ready_self_extra_key",
})


def _gen_module():
    spec = importlib.util.spec_from_file_location("_fleet_gen_table", GEN_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_cbp_table_bytes_match_generator():
    data = _gen_module().render(BASE).encode("utf-8")
    for root in (REPO_ROOT, SIBLING_ROOT):
        on_disk = (root / "cbp_table.py").read_bytes()
        assert on_disk == data, (
            f"{root.name}/cbp_table.py 与 gen_table.render() 不逐字节一致——"
            f"要么有人手改/手抄了表，要么忘了重跑生成器"
        )


def test_two_cbp_tables_identical():
    a = (REPO_ROOT / "cbp_table.py").read_bytes()
    b = (SIBLING_ROOT / "cbp_table.py").read_bytes()
    assert a == b, "两仓 cbp_table.py 不逐字节一致（生成器只覆盖了一份？）"


def test_conformance_verdicts_parity():
    conf = json.loads(CONFORMANCE_PATH.read_text(encoding="utf-8"))
    here = support.load_cbp_standalone(REPO_ROOT, f"parity_here_{REPO_ROOT.name}")
    other = support.load_cbp_standalone(SIBLING_ROOT, f"parity_{SIBLING_NAME}")
    mismatches = []
    for vec in conf["frames"]["vectors"]:
        va = here.decode(vec["expect"]["wire"]) is not None
        vb = other.decode(vec["expect"]["wire"]) is not None
        # 正例：Python 桥只镜像必填位与 hello/ready/call 的闭合，逐条都能收；两桥判定必同。
        if va != vb or va != vec["expect"]["valid"]:
            mismatches.append(
                f'frames.{vec["name"]}: expect={vec["expect"]["valid"]} '
                f'{REPO_ROOT.name}={va} {SIBLING_NAME}={vb}'
            )
    for vec in conf["frame_negatives"]["vectors"]:
        va = here.decode(vec["input"]) is not None
        vb = other.decode(vec["input"]) is not None
        if va != vb:
            mismatches.append(f'frame_negatives.{vec["name"]}: {REPO_ROOT.name}={va} {SIBLING_NAME}={vb}')
        elif vec["name"] in SECURITY_NEGATIVES and (va or vb):
            mismatches.append(
                f'frame_negatives.{vec["name"]}: 安全负例两桥都收了（应判坏帧）'
            )
    assert not mismatches, "同一张向量表两桥判定不一致：\n" + "\n".join(mismatches)
