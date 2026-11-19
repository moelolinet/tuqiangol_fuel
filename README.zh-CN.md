<div align="center">

<img src="custom_components/tuqiangol_fuel/brand/icon@2x.png" width="150" alt="途强油路">

# 途强油路

Home Assistant 途强在线定位器外置继电器油路控制集成

[English](README.md) | **简体中文**

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/v/release/moelolinet/tuqiangol_fuel)](https://github.com/moelolinet/tuqiangol_fuel/releases)

</div>

---

在 Home Assistant 中控制途强在线（`tuqiangol.com`）定位器设备的外置继电器油路。

仅此一项功能：**断油路 / 恢复油路**。

---

## 特性

- 每台设备独立参数，互不影响
- 开关图标随实际油路状态变化
- 支持状态反转（按设备）
- 独立 domain，不与其它集成冲突
- 无第三方依赖

---

## 实体

每台启用的设备生成一个开关。

| 状态 | 默认含义 |
| --- | --- |
| `on` | 断油路 |
| `off` | 恢复油路 |

开启「反转」后含义相反。

---

## 安装

**HACS**

1. HACS → ⋮ → 自定义存储库
2. 填入本仓库地址，类别选 Integration
3. 安装并重启 Home Assistant

**手动**

将 `custom_components/tuqiangol_fuel` 复制到 HA 配置目录，重启并强制刷新浏览器。

---

## 配置

添加集成后：

1. 填写站点地址与账号密码
2. 选择设备
3. 逐台确认参数

### 设备参数

| 参数 | 说明 |
| --- | --- |
| 状态刷新间隔 | 15–3600 秒，留空用默认值 |
| 指令分类 ID | 留空用默认值 |
| 油路参数 ID | 留空自动识别 |
| 指令密码 | 留空用登录密码 |
| 反转开关状态 | 开启后 on = 恢复油路 |

参数随时可在集成的「配置」中逐台修改。

---

## 使用

```yaml
service: switch.turn_on
target:
  entity_id: switch.<DEVICE>_fuel_circuit
```

`turn_off` 为反向操作。

---

## 说明

- 断油需终端定位正常且车速较低，由平台判定
- 终端离线时指令进入队列，上线后执行
- 账号与设备参数保存在 Home Assistant 本地配置中
- 本项目与途强官方无关
- 图标需 Home Assistant 2026.3 或更高；升级后若仍显示旧图标，请强制刷新浏览器缓存

---

## 许可

GPL-3.0
