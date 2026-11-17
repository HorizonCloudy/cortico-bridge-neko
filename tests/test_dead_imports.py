"""N7 死导入清零（工单二）：全仓顶层 import 不许有未使用名。

桶内没有 ruff/pyflakes 兜底，所以这里用 AST 自建一道闸：逐个模块比对「顶层绑定的名字」
与「模块体内出现过的名字」，差集非空即红。回退修复（把 `json`/`time`/`Err`/`_OPTIONAL`
塞回 import）必让本用例转红。跑法：`PY311 -I -m pytest tests/test_dead_imports.py -q`。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULES = ["__init__.py", "cbp.py", "cbp_table.py", "ingress.py", "pins.py", "turns.py",
           "watch.py", "replytail.py", "convmap.py", "tools.py"]
_SKIP_DIRS = {"__pycache__", ".git", ".pytest_cache"}


def _bound_and_used(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bound: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname or "." not in a.name:  # `import a.b` 只绑 a，交给 asname/name 判定
                    bound[a.asname or a.name.split(".")[0]] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            if node.module == "__future__":
                continue
            for a in node.names:
                if a.name == "*":
                    return None
                bound[a.asname or a.name] = node.lineno
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            x = node
            while isinstance(x, ast.Attribute):
                x = x.value
            if isinstance(x, ast.Name):
                used.add(x.id)
    # 字符串注解（`from __future__ import annotations` 下类型注解不产生 Name 节点）也算用到
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines()
    dead = []
    for name, lineno in bound.items():
        if name in used:
            continue
        # 注释/文档串里提到不算「代码引用」；按整词扫源码，排除绑定那一行的 import 语句本身
        hits = [ln for ln, text in enumerate(lines, start=1)
                if re.search(r"\b" + re.escape(name) + r"\b", text) and ln != lineno]
        # 只要那一行之外任何地方出现（含再导出说明、类型注解、docstring），保守放行
        if not hits:
            dead.append(name)
    return dead


@pytest.mark.parametrize("mod", MODULES)
def test_no_dead_top_level_imports(mod):
    dead = _bound_and_used(REPO_ROOT / mod)
    assert dead == [], f"{mod} 顶层死导入（绑了不用）：{dead}——删掉，桶内无 lint 兜底"


# 工单点名的死导入逐名钉死：通用扫描会被 `pins.json` 一类的注释词蒙混，这里按顶层绑定名硬挡。
# 回退 N7（把 import 塞回来）必让这一条转红——名字重新出现在顶层绑定里。
_NAMED_DEAD = {
    "__init__.py": {"json", "time", "Err"},
    "cbp.py": {"_OPTIONAL"},
}


def test_named_dead_imports_are_not_bound(bridge):
    for mod, names in _NAMED_DEAD.items():
        tree = ast.parse((REPO_ROOT / mod).read_text(encoding="utf-8"))
        bound = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                bound.update((a.asname or a.name.split(".")[0]) for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                bound.update((a.asname or a.name) for a in node.names)
        leaked = names & bound
        assert not leaked, f"{mod} 仍绑定工单点名的死导入：{sorted(leaked)}"
    # 再导出面（MSG_KINDS/TOOL_RISKS/ANON_VALUES）不得被误删：watch.py/tools.py 从 cbp 取数
    for keep in ("MSG_KINDS", "TOOL_RISKS", "ANON_VALUES", "CBP_VERSION", "_REQUIRED"):
        assert hasattr(bridge.cbp, keep), f"cbp 再导出缺失 {keep}——N7 只删 _OPTIONAL，别连带砍错"
