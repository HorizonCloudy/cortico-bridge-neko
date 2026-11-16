"""零字面量闸：caps 台账里的上限数值不许以任何形态（代码、f-string 文案、docstring）再出现在仓内 .py 里。

事实来源 = fleet `spec/pins.json` 的 caps 段（优先级第 1 档）。本测试从台账现取数值集合，
不在本文件复写任何数字；台账缺失 → 判红（闸不许静默放行）。
台账路径解析：环境变量 `CORTICO_PINS_JSON` 优先，否则取兄弟仓默认布局
`<仓父目录>/cortico-bridge-fleet/spec/pins.json`。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
_SKIP_DIRS = {"__pycache__", ".pytest_cache", "node_modules", "dist", ".git"}


def _ledger_path() -> Path:
    raw = os.environ.get("CORTICO_PINS_JSON", "").strip()
    return Path(raw) if raw else REPO_ROOT.parent / "cortico-bridge-fleet" / "spec" / "pins.json"


def _caps_numbers() -> list[int]:
    path = _ledger_path()
    if not path.is_file():
        pytest.fail(f"caps 台账不可达：{path}（设 CORTICO_PINS_JSON 或保持 fleet 兄弟目录布局；台账缺失时本闸必须红）")
    caps = json.loads(path.read_text(encoding="utf-8")).get("caps")
    if not isinstance(caps, dict):
        pytest.fail(f"{path} 缺 caps 段——闸失去参照，判红")
    out: list[int] = []
    for section in caps.values():
        if not isinstance(section, dict):
            continue  # notes 等说明节不参与扫描
        for key in ("frame_bytes", "text_bytes", "media_bytes"):
            value = section.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                out.append(value)
    return out


def test_repo_has_no_caps_ledger_literals():
    numbers = sorted(set(_caps_numbers()))
    assert numbers, "台账 caps 段没有任何数值上限——闸失去参照，判红"
    pattern = re.compile(r"(?<!\d)(" + "|".join(str(n) for n in numbers) + r")(?!\d)")
    hits: list[str] = []
    for py in sorted(REPO_ROOT.rglob("*.py")):
        if _SKIP_DIRS & set(py.relative_to(REPO_ROOT).parts):
            continue
        for lineno, line in enumerate(py.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern.search(line):
                hits.append(f"{py.relative_to(REPO_ROOT).as_posix()}:{lineno}")
    assert not hits, "源码出现 caps 台账上限字面量（单源 = pins.json caps 段）：" + ", ".join(hits)
