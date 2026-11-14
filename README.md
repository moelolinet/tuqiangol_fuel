# Tuqiangol Fuel (途强油路控制)

一个只做一件事的 Home Assistant 自定义集成：通过**途强在线**网页控制台（<https://www.tuqiangol.com>）的 HTTP 接口，对指定途强定位器下发 **断油路** / **恢复油路** 指令，并暴露为一个开关实体。

- 单一功能，只做「断油路 / 恢复油路」，不含定位、轨迹、地图、传感器等其他实体
- 独立 domain（`tuqiangol_fuel`），唯一 ID 与设备标识均以 IMEI 为基准，不会与其他集成冲突

- 仅使用 HA 内置依赖（`aiohttp` 由 HA 提供），`manifest.json` 中 `requirements` 为空
- 支持 HACS 自定义仓库安装与手动复制安装

---

## 实体

每个被选中的设备生成一个 `switch` 实体：

| 实体 | 含义 |
| --- | --- |
| `switch.<设备名>_<油路/fuel_circuit>` | **on = 断油路**（`...YL1:1`）<br>**off = 恢复油路**（`...YL1:0`） |

> 实体名称跟随 HA 界面语言：中文环境为「油路」（如 `switch.<DEVICE>_油路`），英文环境为「Fuel circuit」（如 `switch.<DEVICE>_fuel_circuit`）。

属性：

| 属性 | 说明 |
| --- | --- |
| `imei` | 设备 IMEI |
| `last_action` | 最近动作：`cut` / `restore` |
| `last_code` | 平台返回码 |
| `last_result` | 返回码含义：指令已执行 / 终端离线，指令已入队 / 终端离线，入队失败 / 异常 |
| `last_message` | 平台原始回执文本 |
| `last_record_time` | 平台指令记录时间 |

状态来源：平台 `/device/getCommandRecord` 中最新的油路记录。由于平台返回**顺序不固定**、且多条指令可能**同一秒**写入，集成内部用记录 `id` 作为版本标记，并以下发成功后的目标状态为准，避免开关回弹。

---

## 安装

### HACS 自定义仓库（推荐）

1. 把本目录内容上传到你自己的 GitHub 仓库，**仓库根目录**直接包含 `hacs.json` 与 `custom_components/`（不要再套一层文件夹）
2. HACS → ⋮ → **自定义存储库**，填入仓库 URL，类别选 **Integration**
3. 搜索 `Tuqiangol Fuel` 安装
4. 重启 Home Assistant

### 手动安装

把 `custom_components/tuqiangol_fuel` 整个文件夹复制到 HA 配置目录，然后重启 HA 并强制刷新浏览器（Ctrl+F5）：

```
<config>/custom_components/tuqiangol_fuel/
```

---

## 配置

1. 设置 → 设备与服务 → **添加集成** → 搜索 `途强油路控制`
2. 填写站点地址（默认 `https://www.tuqiangol.com`）和账号密码
3. 选择设备：只列出真正支持「外置继电器油路控制」指令的设备（登录时逐个校验，不支持的不会出现）
4. 逐台确认设备参数（每选一台填一次，界面会显示当前设备名与剩余台数）

保存后集成自动重载并生成实体。

### 每台设备可单独设置

每台设备都有一套独立参数，互不影响：

| 参数 | 说明 | 留空时 |
| --- | --- | --- |
| **状态刷新间隔（秒）** | 该设备的轮询周期，15–3600 | 用 60 |
| **指令分类 ID** | 该设备指令所在的分类 | 用 `827`（远程控制） |
| **油路参数 ID** | 该设备油路开关的参数 ID | 按记录中的 `YL1` 标记自动识别 |
| **指令密码** | 下发指令时校验的密码 | 用登录密码 |
| **反转开关状态** | 开启后 on = 恢复油路、off = 断油路 | 关闭（on = 断油路） |

> 一个集成条目共用一个轮询器，因此实际轮询频率取所有设备中最短的那个；
> 每台设备仍按自己的间隔决定"这次要不要真的去读"。

之后随时可以在集成的 **配置** 里重新选择设备、逐台改参数。

### 反转开关状态

默认 `on` = 断油路、`off` = 恢复油路。某些场景下（例如希望 `on` 表示"油路正常"）
可以给该设备开启反转：

| 实体状态 | 反转关闭（默认） | 反转开启 |
| --- | --- | --- |
| `on` | 断油路 | 恢复油路 |
| `off` | 恢复油路 | 断油路 |

反转只影响显示与 `switch.turn_on/off` 的语义，实际下发的平台指令始终与你的意图一致。
实体属性 `inverted` 会显示当前是否反转。

---

## 使用

```yaml
# 断油路
service: switch.turn_on
target:
  entity_id: switch.<DEVICE>_fuel_circuit

# 恢复油路
service: switch.turn_off
target:
  entity_id: switch.<DEVICE>_fuel_circuit
```

实体 ID 由设备名 + `油路` 生成，实际值可在「开发者工具 → 状态」中查看。

---

## 接口流程

1. `POST /getNewDomain` → `domainId=www.tuqiangol.com`
2. `POST /api/regdc` → `ver=1&method=login&account=...&password=<编码密码>&language=zh`
   - 编码方式：每个字符的 `charCode` 十进制值用 `|` 连接
   - 登录态由 `Set-Cookie: SHAREJSESSIONID=...` 维持，无 token
3. `POST /customer/getProviderList` → `data.user.userId`
4. `POST /console/getUserGroup` → 分组 `orgId`
5. `POST /console/queryEquipmentList` → 设备列表（`imei` / `deviceName` / `mcType`）
6. `POST /device/getInstructionAndParams`（`imei`、`mcType`、`typeId=827`）→ 指令定义
   - 返回 `orderName=外置继电器油路控制` 的指令
   - 其参数值形如 `恢复油路:...:0;断油路:...:1`，`paramVals=恢复油路:SPBSJ*P:BSJGPS*YL1:0;断油路:SPBSJ*P:BSJGPS*YL1:1`
7. `POST /device/sendIns`
   ```
   imei=...&orderContent=<指令模板>&instructionId=<指令ID>
   &instructionName=<指令名>&param=<参数ID>,<参数值>
   &instructionPwd=<指令密码>&isUsePwd=1&isOffLine=1
   ```
8. `POST /device/getCommandRecord`（`userId=&imei=...`）→ 平台记录

集成**不硬编码**指令 id / 参数值 / 参数 ID：每次下发前实时读取第 6 步并按关键字（`油路`、`断油`、`恢复`）匹配，服务端调整编号后仍可用。

### 返回码

| code | 含义 | 行为 |
| --- | --- | --- |
| `255` | 指令已下发并执行 | 状态更新为目标值 |
| `10005` | 终端离线，指令入队 | 记录先写入，终端上线后执行 |
| `10006` | 终端离线且入队失败 | 指令未生效，日志报错 |
| 其他 | 未知 | 记录为警告日志 |

`data` 常见值：`Non sports mode when constant oil electricity!`（断油回执）、`Supply fuel successfully!`（恢复回执）。

---

## 已知问题与修复记录

以下缺陷在开发过程中被逐一定位、复现并修复，均附可复现的验证方式。

### 1. 多账号串号（严重）

**现象**：同一 Home Assistant 中配置两个不同账号时，A 账号读到的是 B 账号的设备。

**根因**：登录态是 `SHAREJSESSIONID` Cookie，而原实现使用 `async_get_clientsession(hass)`（HA 全局共享会话）。第二个账号登录后 Cookie 被覆盖，A 的 `_logged_in` 仍为 `True`，于是带着 B 的 Cookie 发请求。

**修复**：
- `__init__.py` 改为 `async_create_clientsession(hass)`，每个 config entry 独立会话
- 配置流程用 `async_create_clientsession(hass, auto_cleanup=False)` + `session.detach()`
- `api.py` 增加 Cookie 守卫：记录登录时绑定的 Cookie 值，若发现被替换则自动重新登录

**验证**：两账号各自只看到自己的设备（`AUTO-RECOVERED: True`）。

### 2. 登录重定向无限递归（严重）

**现象**：站点地址填 `http://`、登录态失效、或被 WAF 拦截时，程序卡死数十秒。

**根因**：`_post()` 收到 302 后调用 `login()`，而 `login()` 内部的 `_post()` 又收到 302，形成递归。

**修复**：
- 新增 `_in_login` 标志，登录过程中不再触发重登录
- 改为抛 `TuqiangolAuthError` 并给出明确提示
- 增加 `_login_lock`，并发请求不会同时登录

**验证**：全 302 服务器下只发 **1** 个请求即返回明确错误（修复前发 487 个请求后崩溃）。

### 3. `http://` 站点地址无法使用

**现象**：用户填 `http://www.tuqiangol.com` 时登录必失败。

**根因**：平台会 302 跳转到 https，而 `allow_redirects=False` 把这个跳转当成了失败。

**修复**：自动识别“同主机的 http→https 跳转”，升级 `base_url` 后重试一次；不同主机不升级，避免被恶意跳转利用。

**验证**：`http://www.tuqiangol.com` 可正常登录并读取 3 台设备。

### 4. `10006` 被误判为成功

**现象**：终端离线且入队失败时，开关仍显示已切换。

**根因**：只判断 `result["ok"]`，而平台对 `10006`（`Save_offLine_error`）也返回 `ok: true`。

**修复**：改用白名单 `ACCEPTED_CODES = (255, 10005)` 判断。

**验证**：`code 10006: accepted = False`，状态不再被错误改写。

### 5. 状态回弹 / 变成 `unknown`

**现象**：连续切换时开关会自己弹回去，或显示 `unknown`。

**根因**：
- `/device/getCommandRecord` **返回顺序不固定**，同一秒内的多条记录**时间戳完全相同**，无法从数据本身判断先后
- 原实现把“时间戳相同”当成“更新”，导致旧记录覆盖新状态

**修复**：
- 用记录 `id` 作为版本标记，只有**未见过**的记录才能改变状态
- 同一秒内出现多条互相冲突的记录时，优先采用**本插件刚下发的状态**（已知且确定）
- 同一设备的下发加锁串行化
- 状态写入实体属性**先于**刷新，避免 UI 抖动

**验证**：`turn_on / turn_off` 各 2 轮，4 次全部 `code=255` 且状态正确。

### 6. 实体名未本地化

**修复**：改用 `_attr_translation_key = "fuel"`，中文显示“油路”，英文显示 “Fuel circuit”。

---
### 7. 同一账号可重复添加（产生多个条目）

**现象**：`www.tuqiangol.com`、`https://www.tuqiangol.com`、`https://www.tuqiangol.com/` 会被当成三个不同账号，创建 3 个条目。

**根因**：`unique_id` 直接用原始输入字符串拼接。

**修复**：先用 `normalize_base_url()` 规范化再生成 `unique_id`。

**验证**：三种写法 → 第 2、3 次均 `abort already_configured`，最终只有 1 个条目。

### 8. 未填协议头时地址不可用

**现象**：输入 `www.tuqiangol.com`（不带 `https://`）登录必失败，报错信息毫无意义。

**修复**：`normalize_base_url()` 在缺少 `://` 时自动补 `https://`。

### 9. 单个设备故障导致全部设备不可用（严重）

**现象**：账号下某台设备接口异常时，所有开关实体一起变为不可用。

**根因**：`_async_update_data()` 循环中任一设备抛 `UpdateFailed` 即中断整轮刷新。

**修复**：逐设备捕获异常，失败的设备保留上次状态；仅当**全部**设备都失败时才整体 `UpdateFailed`。

### 10. 慢设备阻塞其他设备

**现象**：一台终端响应慢，其他设备的指令要排队等它完成（实测阻塞 10 秒）。

**根因**：所有设备共用一把 `asyncio.Lock`，且锁包住了整个"下发 + 等待确认"过程。

**修复**：改为按 IMEI 独立加锁（`_locks` 字典）。

**验证**：慢设备 10 秒时，快设备仍在 0.0 秒完成。

### 11. 密码失效后无限重试，用户无法修复（严重）

**现象**：改了平台密码后集成一直处于重试状态，日志刷屏，界面上无从修复。

**根因**：登录失败统一抛 `ConfigEntryNotReady`，HA 只会无限重试。

**修复**：
- 认证失败改抛 `ConfigEntryAuthFailed`，触发 HA 的重新认证流程
- 补充 `async_step_reauth` / `async_step_reauth_confirm` 与对应翻译

**验证**：改坏密码 → `SETUP_ERROR` + 自动弹出 reauth；输错一次仍报 `invalid_auth`；输对后 `reauth_successful` 且恢复 `LOADED`。

### 12. 内部缓存无限增长（内存泄漏）

**现象**：长期运行后记录 ID 缓存持续膨胀（按每分钟 1 条新记录估算，一年约 525,600 条，约 100 MB）。

**修复**：
- 用单个有序字典同时承担"已见集合"与"ID→状态映射"，两者不会不一致
- 上限 `MAX_TRACKED_IDS = 200`，裁剪时**绝不删除本次响应中仍存在的 ID**（否则该记录会被误判为新记录，再次引发状态回弹）

### 13. `_pending_action` 异常路径残留

**修复**：用 `try/finally` 保证清理，避免等待被取消/失败后残留旧动作，污染后续同秒冲突的判定。

### 14. 选项流程 abort 缺少翻译

**修复**：补充 `options.abort.no_devices` 的中英文翻译。

---
### 15. 无效的油路参数 ID 被原样下发

**现象**：用户在选项里把参数 ID 填错一位，平台仍返回成功，但指令打到了不存在的参数上。

**修复**：下发前校验该 ID 是否属于设备当前的参数列表；不属于则记录警告并回退到自动识别。

**验证**：错误 ID 被拦截，实际发出的仍是自动识别的正确 ID。

### 16. 读取与下发使用的参数 ID 不一致

**现象**：轮询只读 `options`，下发回退到 `data`；若值只存在于 `data`，两边会选中不同的参数。

**修复**：两条路径统一为 `options → data` 的查找顺序。

### 17. 设备轮询失败后仍显示"可用"

**现象**：某台设备读取失败后保留旧状态，但实体依然显示为可用，用户无法察觉数据已过期。

**修复**：新增按设备的可用性标记 `async_is_available(imei)`，只有该设备最近一次轮询成功时实体才可用。

**验证**：一台设备失败时，它变为不可用，另一台仍正常可用。

### 18. 设备型号仅在设置阶段可用（设置成功但下发必失败）

**现象**：`mc_type` 只在 `options`/`data` 里查找。若两处都没有（仅在设备列表里），设置阶段有 `device_map` 兜底所以能通过，但下发路径没有兜底，之后每次指令都会失败。

**修复**：`TuqiangolCoordinator` 接收 `device_map`，下发时使用与设置阶段相同的回退链。

**验证**：端到端复现"设置 OK → 下发 FAIL"，修复后一致。

### 19. 参数选项匹配可能命中"装饰版"选项

**现象**：匹配逻辑取第一个包含关键词的选项。若平台将来新增 `恢复油路(延时)` 且排在前面，会被误选。

**修复**：区分"普通标签"（`断油`/`断油路`/`恢复`/`恢复油路` 等精确形式）与"装饰标签"，普通标签优先；只有不存在普通标签时才回退到模糊匹配。

**验证**：4 组参数组合（普通、装饰在后、装饰在前、仅有装饰）均选出正确值。

---
### 20. 终端已在目标状态时白等 20 秒

**现象**：开关已经处于目标状态时再点一次，界面会转圈约 20 秒才返回。

**根因**：平台在这种情况下仍返回 `code=255`，但**不会写入新记录**；而代码会一直等"新记录出现"，直到 `CONFIRM_TIMEOUT` 超时。实测阻塞 **21 秒**，期间还占着该设备的锁。

**修复**：识别平台"已处于该状态"的回执特征串，命中时不再等待新记录，直接确认状态。

**验证**：21 秒 → **0.0 秒**。

### 21. 回执状态与请求动作矛盾时仍乐观更新

**现象**：要求"断油"但平台回复"终端处于通路状态、不会执行"时，开关仍会短暂显示为已断油。

**修复**：
- `_already_in_state()` 增加动作一致性校验：回执点名的状态必须与请求动作一致
- 新增 `_contradicts()`：回执点名相反状态时，放弃乐观更新并记警告，保留真实状态
- 实体文案区分三种情况：「指令已执行」/「终端已处于目标状态，未重复执行」/「未执行：终端处于相反状态」

**验证**：6 组回执 × 动作组合全部判定正确；矛盾场景下状态保持 `None` 而非错误地显示 `cut`。

---
## 兼容性

| 项目 | 值 |
| --- | --- |
| domain | `tuqiangol_fuel` |
| 设备标识 | `("tuqiangol_fuel", imei)` |
| 唯一 ID | `<账号>@<站点>` |
| 依赖 | 仅 Home Assistant 内置 `aiohttp` |

集成使用独立的 domain 与设备标识，可与任何其他集成同时安装，互不覆盖。

---
## 开发与验证

已在 Home Assistant 2024.12.5 实机加载验证：

- 配置流程两步（账号 → 选择设备）通过，选项流程、重载、卸载通过
- 实体创建：`switch.<DEVICE>_fuel_circuit`
- `switch.turn_on` → `code=255`，状态 `on`；`switch.turn_off` → `off`
- 连续多轮切换无回弹；通知刷新后状态稳定
- 与其他集成同时加载时互不干扰

---

## 上传 GitHub 前检查

1. `custom_components/tuqiangol_fuel/manifest.json` 中把 `YOUR_GITHUB_USER` 替换为你的 GitHub 用户名和仓库名（`documentation`、`issue_tracker`、`codeowners`）
2. `hacs.json` 的 `name` 决定 HACS 中显示的名称
3. 仓库根目录不要多套一层文件夹
4. 推送到 GitHub 后，可在 HACS 添加自定义存储库安装

`.github/workflows/validate.yml` 会在推送时自动执行 HACS 与 hassfest 校验。

---

## 注意

- **安全**：断油路会切断车辆油泵/电控供电，请仅在确认安全（车辆静止、相关电路已断开）时使用。
- 平台限制：断油电需 GPS 定位正常且车速 ≤ 20 km/h。
- 终端离线时只能排队（`10005`），不会立即断油。
- 账号密码保存在 HA 的配置条目中，请注意保护备份。
- 本项目与途强官方无关，接口变更可能导致失效。

## 许可

GPL-3.0
