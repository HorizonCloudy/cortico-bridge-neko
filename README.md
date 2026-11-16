# cortico-bridge-neko

Cortico 与 N.E.K.O 之间的 CBP 帧桥（N.E.K.O 宿主原生插件）。骨架件：清单、入口契约、
bus watch 映射、两条出站门（push_message v2 与 reply_tail）、会话私有表、CBP 编解码副本。
本仓不启动宿主、不监听端口、不连 IM。连接只在部署配置显式放行后由 WS 外拨发生。

## 布局

| 件 | 职责 | 证据钉 |
|---|---|---|
| `plugin.toml` | 清单。必填三件 + `[plugin.sdk] supported=">=0.1.0,<0.2.0"`；无 `host` 键 | r-neko §3（schema.py:294 必填 id/name/entry；host 键即拒） |
| `__init__.py` | `@neko_plugin` 入口类。生命周期全 async、返 Ok/Err；WS 外拨；call 帧分发 | r-neko §4（`__all__` 58 名内）；蓝图 L83 |
| `watch.py` | `bus.messages` watch → CBP `msg` 帧；禁 bus delete/publish；frames 不作来源 | r-neko §7/§10；台账 §2 watch 行 |
| `ingress.py` | CBP → `push_message` v2（kwargs-only）、两轴校验、台账上限本地先拒（`caps.neko.frame_bytes`，零字面量）、reason 四值互译 | C1-6 / C1-16；r-neko §5（schema :63-64）；pins.json caps 段 |
| `replytail.py` | 第二扇门 `reply_tail`：context 防伪两键、image-only ≤2、`blind` 固定、先 subscribe；绝不回落 push | r-neko §6；c1 §5.4 窄门约束 |
| `turns.py` | `turn` → 宿主实证 actor 的有界账本（F3 执法腿）。TTL 300 秒、上限 200 | SECURITY-CBP §1 规则 3、§5.1 |
| `convmap.py` | conv ↔ conversation_id 私有表（ctx.store KV）。宿主不可见 | 蓝图 L185 |
| `cbp.py` `pins.py` | 帧编解码 vendored 副本；端口从 pins.json 取数；密钥只走头 | 蓝图 G8 / §4.4；SECURITY-CBP §3 |

## 两条出站门的使用边界（push_message 广门 / reply_tail 窄门）

两扇门都是**出站**，都由同一个 `egress` 配置开关把关，`egress=false` 时两扇一起关。
窄门不是广门的旁路：它的额外约束只在广门已开的前提上**再收**，不得反过来绕过 `egress`。

| 维度 | `neko_send` → `push_message` v2（广门） | `neko_reply_tail_register` → `reply_tail`（窄门） |
|---|---|---|
| 用途 | 把一段对话内容投进聊天面 / HUD | 给**本轮回复的尾部**贴一张图，正文一个字都不发 |
| 段型 | 任意 `Seg[]`，未知段透传给内核兜底（C1-16） | **只允许 `image`**，且 `1 ≤ 段数 ≤ 2`（`replytail.TAIL_PARTS_MAX`） |
| `ai_behavior` | `respond` / `read` / `blind` 三值可选 | **固定 `blind`**，其它值一律 60005，不接受调用方指定 |
| `visibility` | `chat` / `hud` 两值可选 | 无此轴：尾部图随本轮回复走，不选投递面 |
| 前置 | 无 | 必须先 `subscribe(entry_id)` 才能 `register` |
| 载荷上限 | 从 pins 台账 `caps.neko.frame_bytes` 读（`pins.neko_payload_cap_bytes`；缺 caps 抛错不回落，源码零字面量） | 宿主 2 MiB（`hooks` 总 8、每插件 2） |
| 失败时 | reason 四值互译 60006 / 20002 / 60006 / 60003 | 宿主拒 → 60005；HTTP 面异常 → 60006 |
| 禁止 | 不得用位参调用（C1-6：只走 kwargs） | **绝不回落 `push_message`**（`docs/plugin-reply-tail.md:40`），任何分支都不行 |

选择规则的口径：有正文要说话 → 广门；只想在本轮答案尾巴上挂图 → 窄门。
需要非 image 段、或段数 > 2、或 `ai_behavior != blind` → 那不是窄门的活。

## F1/F3/§3 新口径（2026-11-16 SECURITY-CBP 落地）

- **`call` 的 `turn` 与 `actor`（F3）**：`actor` 由桥**只**从 `turns.TurnLedger` 查自己的入站记录得到。
  线上的 `actor` 只作对账：与宿主记录不一致时以宿主记录为准，另发
  `event{name:"degraded", data:{reason:"actor-mismatch"}}`，本轮不因此失败。
  `turn` 查不到或超出 300 秒保留窗 → `reply{ok:false, error:{code:60004}}`，动作一次都不执行。
  写动作只在 `anon=="identified"` 时放行（本桥申报的 tools 全是 `write`，等价于身份不明一律 60005）。
- **`hello` 的 `self`（F1/F2）**：握手首帧必带 `self:{id,name,proven}`。N.E.K.O 线**永久 `proven:false`**
  ——bus 面只有读与 watch、无 publish，没有实证 bot 主键的口子。空 id 配 `false` 是诚实申报，
  不许用 `role=="assistant"` 启发式冒充实证，也不许报不了就不带。**部署后果**：这条线的回声抑制
  永远是 off（`self_echo` cap 不得申报，与 `proven:false` 同时出现即 caps-lie），
  全部入站照常投递、不吞消息。
- **凭据不过 URL（§3/F4）**：共享密钥只从环境变量 `CORTICO_BRIDGE_TOKEN` 读，
  只走 HTTP Upgrade 头 `Authorization: Bearer <token>`（`pins.auth_headers()`）。
  不落插件配置文件（`auth_headers` 收配置形参但**故意不读**），不拼进 URI
  （`pins._assert_clean_uri` 见 query 就拒），不进日志。CBP 帧面没有 token 位。


## SDK 版本闸（最容易踩的坑）

闸只认 `SDK_VERSION = "0.1.0"` 空间，不认 `APP_VERSION = "0.9.1"`（registry 实测，r-neko §2）。
`supported` 必须写成 SpecifierSet 语法：裸版本串 `"0.2.0"` 与 wildcard `"0.1.*"` 都判
invalid specifier 直接拒载；写成 0.9.x 区间则永远加载不上且 CI 全程绿灯（_BUILD-PLAN T1）。
本仓自检里这四条全部是活断言，其中三条用宿主真函数 `_check_sdk_compatibility` 跑出
`True/False/False/False`。

## 端口纪律

代码零端口字面量（`tests/test_entry_contract.py` AST 级扫描）。Cortico 侧监听口从
`spec/pins.json` 读取，路径由环境变量 `CORTICO_PINS_JSON` 给出；没给就 RuntimeError。
宿主侧 48911-48916 / 38865-38867 / 48961-48963 由 SDK 自己解析（push_message 与
reply_tail 都在 SDK 面），桥不直连。

## 自检

```
PY311=C:/Users/54301/tmp/botfw-envs/neko-py311/Scripts/python.exe
cd <本仓>
$PY311 -I -m pytest tests -q        # 必须带 tests 这个位置参数，见下方说明
$PY311 -I -m pytest tests/test_entry_contract.py -q   # 工单命令逐条可跑
$PY311 -I -m compileall .                             # 语法门（本桶无 mypy/pyright/ruff）
```

负例分布：`tests/test_cbp_v2_sync.py`（帧表必填位与逐键闭合）、`tests/test_actor_turns.py`
（F3 身份门：60004 / actor-mismatch / anon 写门 / 账本有界）、`tests/test_credentials.py`
（§3：密钥只走头、URI 与日志无密钥）。

`tests/pytest.ini` 把 rootdir 钉在 tests/：仓根按工单就是包根（`__init__.py` 在根），
pytest 若把仓根当 Package 采集会按顶层模块 `"__init__"` 导入它并炸相对导入。

同一条坑有两种触发法，都不是代码回归：

1. 不带位置参数的裸跑（`pytest -q`）会让 pytest 以仓根为采集起点，给仓根建 Package 节点，
   于是 `__init__.py` 的相对导入必炸（实测 91 errors）。带上 `tests` 就正常（实测 91 passed）。
2. 想让裸跑也可用只剩两条路：把插件代码移进子包（会破坏宿主按目录名加载插件的配方），
   或让 `__init__.py` 在被当顶层模块导入时降级为绝对导入（掩盖真实加载方式）。两条都不选，
   本仓以「命令必须带 tests」为口径。根级 `pytest.ini` 加 `testpaths = tests` 无效，已实测。

## 边界与欠账（骨架期一律按保守面）

- 未启动宿主：reply_tail 的 48911 往返、`bus.publish` 可达性、snapshot 读侧调用形
  均未真机验证；`watch.snapshot()` 现返回空表，`BusWatch.attach` 的回调注册名待沙箱卡回填。
- 配置读取走防御 getattr：egress 与 link_enabled 缺省 False（门全关）。
- `[plugin.entries]` wire 字段全集仍欠账（_BUILD-PLAN §6），清单未申报 entries。
- PluginStore 方法名（get/set）是读侧证据口径，真机不符时只改 convmap 一处适配层。
