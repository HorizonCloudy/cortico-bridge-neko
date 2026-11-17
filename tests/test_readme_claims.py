"""N5 README 口径闸（工单二）：自述与实现不一致本身就是缺陷，这里钉四处过期口径不许回来。

钉住：
1. 不写死测试条数（禁写清单第 6 条）。
2. `reply_tail` 窄门不得写成「可选门」：必须有「今日不可达」与原因。
3. 生命周期行不得再用「返 Ok/Err」旧契约（装配钩子返 None）。
4. 布局表必须有 tools.py（K2 面）与 turn 窗同源行（N8）。
回退修复（把旧文案塞回 README）必让对应用例转红。跑法：
`PY311 -I -m pytest tests/test_readme_claims.py -q`。
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
README = (REPO_ROOT / "README.md").read_text(encoding="utf-8")


def test_no_hardcoded_test_counts():
    hits = re.findall(r"\d+\s*(?:passed|errors|failed)\b", README)
    assert not hits, f"README 写死了测试条数（禁写清单）：{hits}"


def test_narrow_gate_written_as_unreachable_not_optional():
    assert "今日不可达" in README, "窄门必须写明今日不可达（FIXORDER 裁：结构性不可达，另案）"
    assert "invalid_context" in README, "要写明不可达的原因：宿主 _scope 查 token"
    assert "结构性" in README, "口径是结构性不可达，不是没配置"


def test_lifecycle_line_is_current_contract():
    assert "生命周期全 async、返 Ok/Err" not in README, "旧生命周期契约行回来了（钩子返 None，无 Ok/Err 契约）"
    assert "_on_command_loop_start" in README, "布局行要写真实入口钩子"


def test_k2_tools_face_present():
    assert "`tools.py`" in README, "K2 面（tools.py）在 README 不存在 = 自述漏第三条出边"
    assert "对端调用门" in README or "第三条出边" in README, "要写明对端 call 是投递门之外的出边"


def test_turn_window_documented_as_single_source():
    assert "TURN_TTL_SECONDS" in README, "N8 后 turn 窗口径必须写同源"
    assert "CORTICO_TURN_TTL_SECONDS" in README, "环境变量覆盖面要写进部署节"
