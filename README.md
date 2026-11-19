# cortico-bridge-neko

Cortico 与 N.E.K.O 之间的 CBP 帧桥（N.E.K.O 宿主原生插件）。骨架件：清单、入口契约、
bus watch 映射、两条投递门（push_message v2 与 reply_tail）、一条对端调用门（tools.py，K2）、
会话私有表、CBP 编解码副本。本仓不启动宿主、不监听端口、不连 IM。连接只在部署配置显式放行后由 WS 外拨发生。

## 布局

| 件 | 职责 | 证据钉 |
|---|---|---|
| `plugin.toml` | 清单。必填三件 + `[plugin.sdk] supported=">=0.1.0,<0.2.0"`；无 `host` 键 | r-neko §3（schema.py:294 必填 id/name/entry；host 键即拒） |
| `__init__.py` | `@neko_plugin` 入口类。装配钩子 `_on_command_loop_start`（返 None，异常只记日志）；`stop` 带 `@lifecycle(id="shutdown")` 返 Ok；WS 外拨；call 帧分发 | r-neko §4（`__all__` 58 名内）；蓝图 L83 |
| `watch.py` | `bus.messages` watch → CBP `msg` 帧；禁 bus delete/publish；frames 不作来源 | r-neko §7/§10；台账 §2 watch 行 |
| `ingress.py` | CBP → `push_message` v2（kwargs-only）、两轴校验（违规 10003 bad_param）、台账上限本地先拒（`caps.neko.frame_bytes`，超限 60003，零字面量）、reason 四值互译 | C1-6 / C1-16；r-neko §5（schema :63-64）；pins.json caps 段；conformance error_codes |
| `replytail.py` | 第二扇门 `reply_tail`：SDK 本地只查 version/source 两键，宿主另要下发 token；image-only ≤2、`blind` 固定、先 subscribe；绝不回落 push。**当前不可达**，见下节 | r-neko §6；c1 §5.4 窄门约束 |
| `tools.py` | K2 对端调用门：对端 catalog → 宿主 `@llm_tool` 注册、G5 逐名对账、断链清账。模型一次工具调用 = 一帧桥自发 `call`，是投递门之外的第三条出边 | 蓝图 L184；工单一 K2 |
| `turns.py` | `turn` → 宿主实证 actor 的有界账本（F3 执法腿）。窗长同源读取（生成表 `TURN_TTL_SECONDS`，环境变量 `CORTICO_TURN_TTL_SECONDS` 可覆盖，表行缺位回落 300 并记 degraded）；上限 200 | SECURITY-CBP §1 规则 3、§5.1；工单二 N8/F2 |
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

### 窄门今日不可达（结构性，另案）

窄门不是「可选而不开」的门：它在真宿主上当前必失败，与 `egress` 开关无关。
原因：`replytail.tail_context` 造的 context 只有 `version` 与 `source` 两键，
SDK 本地校验（`plugin/sdk/shared/core/reply_tail.py`）也只看这两键，放行；
但宿主侧 `main_logic/reply_tail.py` 的 `_scope()` 另要一枚 `token`，只在宿主工具调用在途时
铸造，桥自造不出。HTTP 到 48911 后宿主查不到 token，回 `invalid_context`，
`replytail.register_tail` 把 `accepted=False` 折成 60005。运维照 README 翻 `egress` 开关永远找不到原因。
判据来源：`FIXORDER.md:97` 已裁定窄门「结构性不可达，另案」，本轮只把文档口径追平，不改实现。
要恢复窄门，需要 `[plugin.entries]` 申报 + 宿主下发 context 的通路，那是另一片工单。

## F1/F3/§3 新口径（2026-11-16 SECURITY-CBP 落地）

- **`call` 的 `turn` 与 `actor`（F3）**：`actor` 由桥**只**从 `turns.TurnLedger` 查自己的入站记录得到。
  线上的 `actor` 只作对账：与宿主记录不一致时以宿主记录为准，另发
  `event{name:"degraded", data:{reason:"actor-mismatch"}}`，本轮不因此失败。
  `turn` 查不到或超出保留窗 → `reply{ok:false, error:{code:60004}}`，动作一次都不执行。
  窗长同源读取：生成表 `TURN_TTL_SECONDS` 优先，环境变量 `CORTICO_TURN_TTL_SECONDS` 覆盖，
  表行缺位回落 300 并记一条 `degraded{reason:"turn-ttl-table-missing"}`（工单二 N8/F2）。
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
本仓自检里这几条全部是活断言，其中 `_check_sdk_compatibility` 用宿主真函数跑出 1 真 4 假
（真形 1 个，错形 `>=0.9.0,<0.10.0`、裸版本、wildcard、garbage 共 4 个）。

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

负例分布（各文件头注明所咬工单，条数以 `pytest tests -q` 实跑为准，不在文档写死）：
`tests/test_cbp_v2_sync.py`（帧表必填位与逐键闭合）、`tests/test_actor_turns.py`
（F3 身份门：60004 / actor-mismatch / anon 写门 / 账本有界）、`tests/test_credentials.py`
（§3：密钥只走头、URI 与日志无密钥）、`tests/test_ingress_v2.py`（广门两轴、码位、规范形 text）、
`tests/test_frame_isolation.py`（N1 逐帧隔离）、`tests/test_ready_flip.py`（N3 ready 翻转）、
`tests/test_link_out_pump.py`（F4/N4 独立出站写手与时间差）、`tests/test_pins_ledger_array.py`
（台账 ports 数组形）、`tests/test_dead_imports.py`（N7 死导入）、`tests/test_turn_ttl_source.py`（N8 窗长同源）。

`tests/pytest.ini` 把 rootdir 钉在 tests/：仓根按工单就是包根（`__init__.py` 在根），
pytest 若把仓根当 Package 采集会按顶层模块 `"__init__"` 导入它并炸相对导入。

同一条坑有两种触发法，都不是代码回归：

1. 不带位置参数的裸跑（`pytest -q`）会让 pytest 以仓根为采集起点，给仓根建 Package 节点，
   于是 `__init__.py` 的相对导入必炸（采集阶段整批 error）。带上 `tests` 就正常。
   两种计数的具体条数以实跑为准，不写进文档（写死即过期）。
2. 想让裸跑也可用只剩两条路：把插件代码移进子包（会破坏宿主按目录名加载插件的配方），
   或让 `__init__.py` 在被当顶层模块导入时降级为绝对导入（掩盖真实加载方式）。两条都不选，
   本仓以「命令必须带 tests」为口径。根级 `pytest.ini` 加 `testpaths = tests` 无效，已实测。

## 部署

1. 安装：把整目录拷进宿主插件根。目录名必须等于插件 id：`cortico_bridge` 或
   `cortico-bridge`。克隆名 `cortico-bridge-neko` 不是合法安装名。
2. 环境变量 `CORTICO_PINS_JSON`、`CORTICO_BRIDGE_TOKEN`、`CORTICO_TURN_TTL_SECONDS`
   只认宿主进程环境。`CORTICO_TURN_TTL_SECONDS` 覆盖 turn 保留窗（秒，正数），
   不设就用生成表 `TURN_TTL_SECONDS`；两端同名（工单二 F2/N8）。
   源码运行：写进 `launcher.py` 同目录的 `.env`。打包态的 `.env` 行为未核验。
   插件清单或运行时配置里写这几个键无效。
3. 两个门开关在清单 `[cortico]` 表。运行时值等于清单缺省与运行时配置的合并结果。
   运行时配置落在 `<storage_root>/plugins/cortico_bridge/config/plugin.toml`
   （落盘目录名以实机为准，未核验）。改完值要重启插件才生效。
4. `auto_start` 缺省 false（与内置 mcp_adapter 同口径）。用户手动启动插件。
5. 沙箱注记：`neko-plugin-sandbox` 起服务前剥掉名字含 TOKEN 的环境变量。
   `CORTICO_PINS_JSON` 过筛保留，`CORTICO_BRIDGE_TOKEN` 会被剥掉。

## 边界与欠账（骨架期一律按保守面）

- 未启动宿主：reply_tail 的 48911 往返、`bus.publish` 可达性、snapshot 读侧调用形
  均未真机验证；`watch.snapshot()` 现返回空表，`BusWatch.attach` 的回调注册名待沙箱卡回填。
- 配置读取走清单 `[cortico]` 表：egress 与 link_enabled 缺省 False（门全关）。
- `[plugin.entries]` wire 字段全集仍欠账（_BUILD-PLAN §6），清单未申报 entries。
- PluginStore 方法名（get/set）是读侧证据口径，真机不符时只改 convmap 一处适配层。

---

## 安全声明（2026-11-19 · 对端能力面已收敛）

本仓是框架侧插件，**不是 Cortico 扩展**，没有 `cortico.tags`，无法在索引仓自声明
`unsafe`。对端 world 包 `cortico-world-neko` 上一轮曾拟按 11-13 判例声明，
但该声明**从未发布即撤回**：`TAGS.md:40` 的不可撤回约束针对已发布版本；平台也没有
`safe` 标签（`TAGS.md:42`），缺席只代表「未声明且未审查」。处理方式改为收敛能力面。

本仓承担 dial 腿的鉴权收口：

- `pins.auth_headers()` 缺 `CORTICO_BRIDGE_TOKEN` 抛 `RuntimeError`，不再返回空头；
- `run()` / `_link_loop()` 入口 fail-shut：缺密钥零拨号、不进重连循环、只留一条日志；
- 凭据只走 `Authorization: Bearer` Upgrade 头，URI 判脏即拒；
- 能力面：`egress` 默认关、工具目录静态、未申报能力本地判 60002。

**迁移（破坏性）**：插件进程环境变量必须设 `CORTICO_BRIDGE_TOKEN`，值与 Cortico 侧
`ctx.secret('CORTICO_BRIDGE_TOKEN')` 逐字相同；未设时桥静默断连是设计行为。

验证：`pytest tests/ -q` 全量通过；`tests/test_credentials.py` 覆盖取数口、
URL 面、拨号面与静态闸。
