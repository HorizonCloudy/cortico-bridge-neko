"""CBP v1 帧表 —— GENERATED 文件，别手改、别手抄第二份。
source: cortico-bridge-fleet/spec/CBP-v1.json sha256=070205d7da05dd355233b9e06e342d07d563b3f261e18c43b397da3e6be47ac9
cmd:    python cortico-bridge-fleet/tools/gen_table.py

改动纪律：先改 spec 再重跑生成器，两份副本同批覆盖（SECURITY-CBP.md:60）。
两仓 tests/test_table_parity.py 把本文件钉住生成器输出。
"""

CBP_VERSION = 1

_REQUIRED: dict[str, tuple[str, ...]] = {
    'hello': ('agent', 'v_support', 'caps', 'tools', 'self'),
    'ready': ('caps', 'tools', 'self'),
    'msg': ('conv', 'sender', 'kind', 'segments', 'raw'),
    'notice': ('kind', 'raw'),
    'ack': ('rid',),
    'call': ('rid', 'tool', 'args', 'turn', 'actor'),
    'reply': ('rid', 'ok'),
    'event': ('name', 'data'),
    'tick': (),
    'catalog': ('tools',),
}

_OPTIONAL: dict[str, tuple[str, ...]] = {
    'notice': ('conv',),
    'reply': ('text', 'blobs', 'error'),
}

MSG_KINDS = ('mention', 'direct', 'group', 'other')

TOOL_RISKS = ('read', 'write', 'speak', 'act', 'flow', 'snapshot')

ANON_VALUES = ('identified', 'anonymous', 'self', 'unknown')

_SELF_KEYS = ('id', 'name', 'proven')

_ACTOR_KEYS = ('platform', 'conv', 'member', 'anon')

_MEMBER_KEYS = ('id', 'name')

_ID_MAX = 64

_STR_MAX = 256

TURN_TTL_SECONDS = 300

