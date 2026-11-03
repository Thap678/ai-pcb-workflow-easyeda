# 方案 C 落地清单（B + 保留 IMON）— 引脚交换落地审计

> 版本：v1.2（2026-11-01）　状态：**C 已定稿（`route/swap_c.json`，§2.6 是执行用的实表）；§2.1/§2.3/§2.4/§5.1 的 B 表只作对照**
> 本文只做只读分析与文档，未修改任何 EasyEDA 工程、PCB、原理图或既有脚本。
> 数据基线：`route/board6.json`（capturedAt 2026-11-01T15:11:06Z，上板 PCB6 dump）、`route/swap_chosen.json`、`route/swap_upper.json`、`sch/n1–n5.json`、`sch/p5.json`、`pinmap_sky.py/pinmap_sky.json`。
> 证据标注：`[文件:行号]` 或 `[文件:字段]`；无法在本地文件复核的标 **待核实**。
> 路径均相对 `02_硬件设计\`。

---

## 0. 一页速览

### 0.1 方案 C 到底改多少

| 项 | 数量 | 证据 |
|---|---|---|
| MCU 引脚换位 | B=32 个（18 GPIO + 7 ADC + 3 电机 + 4 编码器）；**C 定稿=38 个**（§2.6） | `swap_chosen.json` / `swap_c.json` 与 `pinmap_sky.py:14-53` 逐条比对 |
| J603 焊盘换网络 | B=23；**C 定稿=22**（38 号脚保持 NC） | `board6.json:components[J603].pads` + `swap_c.json:j603` |
| 下板 J513 焊盘换网络 | B=23；**C 定稿=22**（37 号 IMON 不动） | `sch/p5.json:20-61` 与 §2.6 |
| 上板原理图动到的页 | 3 页：页1(J601/J602)、页2(J603+4 只上拉)、页5(5 座+5 串阻+3 Qwiic) | §3.0 |
| **不动的页** | 页3（人机交互）、页4（灰度/无线/E01）——C 定稿的 `gcol=[0..5]` 恒等，页 4 仍不动 | `swap_chosen.json:gcol`、`swap_c.json:gcol` |
| 改网络的焊盘总数 | B≈85 个；**C 定稿=88 个**（J601 19 + J602 21 + J603 22 + 页5 26） | §7 脚本输出 |
| 受影响网络 | **62 条现存网络换端点（71%）+ 3 条新网络（U2_*）**；`U3_*` 3 条注销，网络总数仍 87 | §7 脚本输出；87 网来自 `board6.json` 计数 |

### 0.2 三条铁律

1. **J603 与 J513 是"奇偶对调"**：J603 第 n 脚 ↔ J513 第 `n±1` 脚（n 奇则 n+1，n 偶则 n−1）。落地任何一行前先过这个公式，别照抄脚号。
2. **"保留 IMON" ≠ "IMON 能用"**：C 只保证下板 J513.37 仍是"只连 U201.7 + R204 15k"的测试点；上板 J603.38 是空脚、无铜箔、无 MCU 引脚，将来真要用 IMON 仍须改板或飞线。
3. **C 定稿之前不要改下板 J513**：C 必须让 J513.37 保持 IMON，把原本要落那里的信号改到别的脚（定稿 seed 落在 34 号，见 §2.6；其他 seed 各不相同）。接错不短路、不违 DRC，只是"编码器不转 / IMON 没了"，最难查。

### 0.3 坑（**已在本工程内修复**）：`SWAP_LOCK_J603` 曾因 str/int 比较不生效

- 我 09:15 读到的那版 `swapopt.py:28-30` 把锁存成 `int` 集合，而槽位 `p` 是 `board6.json` 的 `padNumber` **字符串** → `"38" not in {38}` 恒真，锁形同虚设。我实测过那版：`LOCK_J603 = {38}` / `38 in slots? True` / `n slots 24`。
- **现状**：该文件已在 09:17:16 改成 `int(p) not in LOCK_J603` 并注明 "compare numerically"（见文件当前内容）；`route/swap_C_summary.json:19-22` 的 `"locked_j603": [38]`、`"free_j603": []` 也证明锁现在生效。
- 处置：**09:17 之前产出的任何 C 结果都要丢掉重跑**；现在的 `swap_C_s1..s13.json` 都是修复后的，可用。本文留这段是给"从旧备份恢复 swapopt.py"的人看。

---

## 1. 事实基线（已逐文件核对）

### 1.1 上板 J603 现状 40 脚

`board6.json:components[designator=J603]`（`layer=2` = 底面，`x/y` 单位 mil，×0.0254 = mm）：

| 脚 | 网络 | 脚 | 网络 | 脚 | 网络 | 脚 | 网络 |
|---|---|---|---|---|---|---|---|
| 1 | 5V_LOGIC | 11 | IOX_INT | 21 | ENC_L_A | 31 | SRV1 |
| 2 | 5V_LOGIC | 12 | I2C_SDA | 22 | GND | 32 | GND |
| 3 | 5V_LOGIC | 13 | MOT_EN | 23 | ENC_R_A | 33 | SRV3 |
| 4 | 5V_LOGIC | 14 | GND | 24 | ENC_L_B | 34 | SRV2 |
| 5 | GND | 15 | GND | 25 | GND | 35 | GND |
| 6 | GND | 16 | BATADC | 26 | ENC_R_B | 36 | SRV4 |
| 7 | CAN_RX | 17 | MOT_L2 | 27 | STP_R | 37 | GND |
| 8 | CAN_TX | 18 | MOT_L1 | 28 | STP_L | **38** | **（空 / NC）** |
| 9 | I2C_SCL | 19 | MOT_R2 | 29 | USTEP_RX | 39 | GND |
| 10 | GND | 20 | MOT_R1 | 30 | USTEP_TX | 40 | GND |

- **38 号脚是全连接器唯一空脚**，其余 39 脚都有网络（`board6.json:J603.pads`，`net==""` 只有 38）。
- 原理图侧同样把 38 标成 NC：`sch/n2.json:62-64`（`"nc": ["38"]`）。
- 本表与 `swap_upper.json:j603` 完全一致（方案 A 的 j603 = 现状；我逐条 diff 过，**0 处不同**，见 §7）。

### 1.2 下板 J513 现状 40 脚

`sch/p5.json:20-61`，与 `_tmp_analyze/out/evidence_5_叠板与扩展.md:31`、`电赛小车2026_设计方案详解.md:1787` 三处一致：

| 脚 | 网络 | 脚 | 网络 | 脚 | 网络 | 脚 | 网络 |
|---|---|---|---|---|---|---|---|
| 1 | 5V_LOGIC | 11 | I2C_SDA | 21 | GND | 31 | GND |
| 2 | 5V_LOGIC | 12 | IOX_INT | 22 | ENC_L_A | 32 | SRV1 |
| 3 | 5V_LOGIC | 13 | GND | 23 | ENC_L_B | 33 | SRV2 |
| 4 | 5V_LOGIC | 14 | MOT_EN | 24 | ENC_R_A | 34 | SRV3 |
| 5 | GND | 15 | BATADC | 25 | ENC_R_B | 35 | SRV4 |
| 6 | GND | 16 | GND | 26 | GND | 36 | GND |
| 7 | CAN_TX | 17 | MOT_L1 | 27 | STP_L | **37** | **IMON** |
| 8 | CAN_RX | 18 | MOT_L2 | 28 | STP_R | 38 | GND |
| 9 | GND | 19 | MOT_R1 | 29 | USTEP_TX | 39 | GND |
| 10 | I2C_SCL | 20 | MOT_R2 | 30 | USTEP_RX | 40 | GND |

### 1.3 奇偶对调公式：几何证明 + 39/40 实证

**几何**：J603.1 = (624.8, 2372.8) mil = **(15.87, 60.27) mm**；J603.2 = (624.8, 2272.8) mil = **(15.87, 57.73) mm**（`board6.json:J603.pads[0..1]`）。下板 J513 第 1 脚在 **(15.87, 57.73) mm**（`电赛小车2026_交接.md:33`）。→ J603.2 与 J513.1 同坐标，J603.1 与 J513.2 同坐标。**公式：`J513 = (n 奇 ? n+1 : n−1)`**。

**实证**：把现状 40 脚按公式配对，**39 脚一致**，唯一例外正是 38↔37（上板 38 空、下板 37 = IMON）：

| 校验 | 结果 |
|---|---|
| 1↔2、3↔4（5V_LOGIC 对 5V_LOGIC） | ✅ |
| 5↔6、10↔9、14↔13、15↔16、22↔21、25↔26、32↔31、35↔36、37↔38、39↔40（GND 对 GND） | ✅ 全 12 对都是"地↔地"或"5V↔5V"，所以电源不会因奇偶错而短路 |
| 7↔8 CAN_RX↔CAN_RX、13↔14 MOT_EN↔MOT_EN、16↔15 BATADC↔BATADC、17↔18 MOT_L2↔MOT_L2…… | ✅ 23 个信号全对（除 38） |
| **38↔37** | ❌ 上板 38 = 空，下板 37 = IMON ← **方案 B 要动它，方案 C 不动** |

复算脚本：`_tmp_analyze/plan_c_map.py`（只读，输出 §2 全部表）。

### 1.4 IMON 事实链（这是"保留 IMON"的全部依据）

| 事实 | 证据 |
|---|---|
| 下板 J513.37 = `IMON` | `sch/p5.json:57`；`_tmp_analyze/out/evidence_5_叠板与扩展.md:31` |
| IMON 源 = U201.7（TPS48111 IMON）→ R204 15 kΩ → GND | `电赛小车2026_设计方案详解.md:806`、`:1281`（三焊盘 U201.7 ↔ R204.1 ↔ J513.37） |
| 早已决定"只作测试点、上板不接" | `电赛小车2026_审查处理记录.md:56`（R9）；`电赛小车2026_设计方案详解.md:1062`（R9：量程未定义，若接 ADC 须分压+钳位） |
| 契约里本来就把 IMON 列为"舍弃" | `起航小车_改版需求.md:160`（第 37 脚上板不连）、`:166`（IMON 对应 J603 第 38 脚，不连） |
| 量程风险仍在 | `电赛小车2026_设计方案详解.md:1449`（40 A ≈ 2.73 V / OCP 45.5 A ≈ 3.07 V，按 TI 公式估算） |

结论：C 保留 38 号脚 = **保持现状**（上板 38 NC、下板 37 只连 U201.7+R204），不新增任何电气风险，也不消耗 MCU 引脚。

---

## 2. 逐网络映射表

> **本表 = 方案 B**（`swap_chosen.json`）。**C 定稿不是 B 改一行**（见 §2.6），本表只作对照与"格式模板"用。
> 列序：信号 → 现 MCU → 新 MCU → 现 J603 → 新 J603 → 下板 J513（=新 J603 ±1）→ 下板原信号 → 是否变化。
> 排序按"新 J603 焊盘号"，便于页 2 逐脚落地。

### 2.1 表 A：经 J603 的 23 路信号

| 信号 | 现 MCU | 新 MCU | 现 J603 | 新 J603 (B) | → J513 | J513 现在 | 变? |
|---|---|---|---|---|---|---|---|
| SRV4 | PD15 | PD15 | 36 | **7** | 8 | CAN_RX | 是 |
| SRV3 | PD14 | PD14 | 33 | **8** | 7 | CAN_TX | 是 |
| SRV1 | PD12 | PD12 | 31 | **9** | 10 | I2C_SCL | 是 |
| SRV2 | PD13 | PD13 | 34 | **11** | 12 | IOX_INT | 是 |
| ENC_R_A | PB4 | **PA15** | 23 | **12** | 11 | I2C_SDA | 是 |
| USTEP_TX | PD5 | **PD8**（USART3_TX） | 30 | **13** | 14 | MOT_EN | 是 |
| STP_R | PB14 | PB14 | 27 | **16** | 15 | BATADC | 是 |
| CAN_TX | PD1 | PD1 | 8 | **17** | 18 | MOT_L2 | 是 |
| CAN_RX | PD0 | PD0 | 7 | **18** | 17 | MOT_L1 | 是 |
| USTEP_RX | PD6 | **PD9**（USART3_RX） | 29 | **19** | 20 | MOT_R2 | 是 |
| ENC_L_B | PA1 | **PB4** | 24 | **20** | 19 | MOT_R1 | 是 |
| MOT_R1 | PE13 | **PE14**（TIM1_CH4） | 20 | **21** | 22 | ENC_L_A | 是 |
| IOX_INT | PD11 | **PE12** | 11 | **23** | 24 | ENC_R_A | 是 |
| ENC_L_A | PA15 | **PB5** | 21 | **24** | 23 | ENC_L_B | 是 |
| MOT_L1 | PE9 | **PE13**（TIM1_CH3） | 18 | **26** | 25 | ENC_R_B | 是 |
| MOT_L2 | PE11 | PE11（TIM1_CH2） | 17 | **27** | 28 | STP_R | 是 |
| STP_L | PE5 | PE5 | 28 | **28** | 27 | STP_L | **否** |
| MOT_R2 | PE14 | **PE9**（TIM1_CH1） | 19 | **29** | 30 | USTEP_RX | 是 |
| MOT_EN | PE10 | **PE8** | 13 | **30** | 29 | USTEP_TX | 是 |
| BATADC | PC0 | **PC4**（ADC12_IN14） | 16 | **31** | 32 | SRV1 | 是 |
| **ENC_R_B** | PB5 | **PA1** | 26 | **38 ⚠待C** | **37 ⚠** | **IMON ⚠** | 是 |
| I2C_SDA | PB9 | PB9 | 12 | **34** | 33 | SRV2 | 是 |
| I2C_SCL | PB8 | PB8 | 9 | **36** | 35 | SRV4 | 是 |

注：
- 23 路里只有 STP_L 原位不动（`swap_chosen.json:j603["STP_L"]="28"` = 现状 `board6.json:J603.28`）。
- ENC_R_B 一行是 B 与 C 的唯一分界：B = 38 号脚 → J513.37（吃掉 IMON）；**C = 33 号脚 → J513.34**（见 §2.5）。
- 电机 4 路全部仍在 TIM1 的 CH1–CH4 上，只是通道重排（§5）。

### 2.2 表 A2：不经过 J603、但网络端点会变的信号

| 信号 | MCU（不变） | 现落点 | 新落点 | 备注 |
|---|---|---|---|---|
| U1_TX / U1_RX / U1_RXD | PB6 / PB7 | J621.2 / R638 | **J607.2 / J607.3 / R636** | DAP 调试座从 J621 挪到 J607；USART1 本身不变 |
| U2_TX / U2_RX / U2_RXD | PD5 / PD6 | （去下板步进） | **J619.2 / J619.3 / R616** | USART2 从步进总线改接串口座 |
| U3_TX / U3_RX | PD8 / PD9 | J607 / R615 | **J603.13 / J603.19**（步进总线） | 见 §3.5 命名规则：网络名改为 USTEP_TX/RX |
| U4_TX / U4_RX / U4_RXD | PC10 / PC11 | J619.2 / R636 | **J608.2 / J608.3 / R615** | UART4 挪一站 |
| U5_TX / U5_RX / U5_RXD | PC12 / PD2 | J620.2 / R637 | **J620.2（座不变）/ R638** | 只换串阻，座不动 |
| U6_TX / U6_RX / U6_RXD | PC6 / PC7 | J608.2 / R616 | **J621.2 / J621.3 / R637** | USART6 挪到原 DAP 座 |
| I2C_SCL / I2C_SDA（I2C1） | PB8 / PB9 | Qwiic J616；上拉 R603/R604 | **Qwiic J617；上拉 R632/R633** | 板内 I2C1 器件（OLED/JY901S/EEPROM/J605）不动 |
| I2C2_SCL / I2C2_SDA | PB10 / PB11 | Qwiic J617；上拉 R632/R633 | **Qwiic J624；上拉 R603/R604** | |
| I2C3_SCL / I2C3_SDA | PA8 / PC9 | Qwiic J624；上拉 R634/R635 | **Qwiic J616；上拉 R634/R635（不变）** | 循环：J616←I2C3、J617←I2C1、J624←I2C2 |

来源：`swap_chosen.json:udest/ures/qw/pull`；`swapopt.py:31-33`（UARTS 引脚表）、`:46`（PULL 对）、`:47`（BUS 引脚表）；现状端点取自 `board6.json` 网络表。

### 2.3 表 B：J603 全 40 脚（B 版 / C 版）

| 脚 | 现状 | B | **C** | 脚 | 现状 | B | **C** |
|---|---|---|---|---|---|---|---|
| 1 | 5V_LOGIC | 5V_LOGIC | 5V_LOGIC | 21 | ENC_L_A | MOT_R1 | MOT_R1 |
| 2 | 5V_LOGIC | 5V_LOGIC | 5V_LOGIC | 22 | GND | GND | GND |
| 3 | 5V_LOGIC | 5V_LOGIC | 5V_LOGIC | 23 | ENC_R_A | IOX_INT | IOX_INT |
| 4 | 5V_LOGIC | 5V_LOGIC | 5V_LOGIC | 24 | ENC_L_B | ENC_L_A | ENC_L_A |
| 5 | GND | GND | GND | 25 | GND | GND | GND |
| 6 | GND | GND | GND | 26 | ENC_R_B | MOT_L1 | MOT_L1 |
| 7 | CAN_RX | SRV4 | SRV4 | 27 | STP_R | MOT_L2 | MOT_L2 |
| 8 | CAN_TX | SRV3 | SRV3 | 28 | STP_L | STP_L | STP_L |
| 9 | I2C_SCL | SRV1 | SRV1 | 29 | USTEP_RX | MOT_R2 | MOT_R2 |
| 10 | GND | GND | GND | 30 | USTEP_TX | MOT_EN | MOT_EN |
| 11 | IOX_INT | SRV2 | SRV2 | 31 | SRV1 | BATADC | BATADC |
| 12 | I2C_SDA | ENC_R_A | ENC_R_A | 32 | GND | GND | GND |
| 13 | MOT_EN | USTEP_TX | USTEP_TX | 33 | SRV3 | **（空）** | **ENC_R_B** |
| 14 | GND | GND | GND | 34 | SRV2 | I2C_SDA | I2C_SDA |
| 15 | GND | GND | GND | 35 | GND | GND | GND |
| 16 | BATADC | STP_R | STP_R | 36 | SRV4 | I2C_SCL | I2C_SCL |
| 17 | MOT_L2 | CAN_TX | CAN_TX | 37 | GND | GND | GND |
| 18 | MOT_L1 | CAN_RX | CAN_RX | 38 | **（空）** | **ENC_R_B** | **（空 / NC）** |
| 19 | MOT_R2 | USTEP_RX | USTEP_RX | 39 | GND | GND | GND |
| 20 | MOT_R1 | ENC_L_B | ENC_L_B | 40 | GND | GND | GND |

- B 下唯一空槽 = 33；C 下唯一空槽 = 38（正好是保留位）。
- **C 版不是这张表改一行**：实测 16 个 seed 里 33 号脚上坐的信号各不相同（`ENC_R_A`/`ENC_L_B`/`ENC_L_A`/`BATADC`…，见 §2.5），页 2 的 spec 必须用定稿 seed 重出。本表 C 列仅作"B 的 38→33"占位示例。

### 2.4 表 C：J513 全 40 脚（现状 / B / C）

只列 24 行；其余 16 行 = 4×`5V_LOGIC` + 12×`GND`，B/C 都不变。下表里 27、37 两行也**不改**（列出来是为了对照），实际改 **22 行**：

| J513 | 现状 | B | **C** |
|---|---|---|---|
| 7 | CAN_TX | SRV3 | SRV3 |
| 8 | CAN_RX | SRV4 | SRV4 |
| 10 | I2C_SCL | SRV1 | SRV1 |
| 11 | I2C_SDA | ENC_R_A | ENC_R_A |
| 12 | IOX_INT | SRV2 | SRV2 |
| 14 | MOT_EN | USTEP_TX | USTEP_TX |
| 15 | BATADC | STP_R | STP_R |
| 17 | MOT_L1 | CAN_RX | CAN_RX |
| 18 | MOT_L2 | CAN_TX | CAN_TX |
| 19 | MOT_R1 | ENC_L_B | ENC_L_B |
| 20 | MOT_R2 | USTEP_RX | USTEP_RX |
| 22 | ENC_L_A | MOT_R1 | MOT_R1 |
| 23 | ENC_L_B | ENC_L_A | ENC_L_A |
| 24 | ENC_R_A | IOX_INT | IOX_INT |
| 25 | ENC_R_B | MOT_L1 | MOT_L1 |
| 28 | STP_R | MOT_L2 | MOT_L2 |
| 29 | USTEP_TX | MOT_EN | MOT_EN |
| 30 | USTEP_RX | MOT_R2 | MOT_R2 |
| 32 | SRV1 | BATADC | BATADC |
| 33 | SRV2 | I2C_SDA | I2C_SDA |
| **34** | **SRV3** | **（空）** | **ENC_R_B** |
| 35 | SRV4 | I2C_SCL | I2C_SCL |
| **37** | **IMON** | **ENC_R_B** ⚠ | **IMON（不动）** |
| 27 | STP_L | STP_L | STP_L |

- B 改 23 脚（34 变空、37 被占）；**C 改 22 脚**（34 变 ENC_R_B、37 不动）。
- 校验：C 的 J513 与"J603(C) 逐脚 XOR 1"完全一致，脚本输出 `C changed lower pads: 22`。

### 2.5 C 的差异（**实际 C 已跑出来：不是"B 改一行"**）

**先纠正本报告 v1.0 初稿的假设**：不能拿 B 改一行当 C 用。`route/swap_C_s1..s16.json`（09:18–09:26，并行任务生成，非本报告作者）是"锁住 38"后的真实搜索结果，它们的 `best.j603` 与 B 差异极大；**33 号槽上坐的还是不同信号**（s1 = ENC_R_A、s2 = ENC_L_B、s10 = ENC_R_A、s11 = ENC_R_B、s13 = BATADC…），**没有一个 seed 是"B + ENC_R_B→33"**。

| 结果 | 交叉 | 飞线长 | 目标 | 来源 |
|---|---|---|---|---|
| 现状 | 871 | 3040 mm | 1175.0 | `swap_C_summary.json:14-18` |
| A | 529 | 2781 mm | 807.1 | `交接 §4`（我用 swapopt 复算一致） |
| B | 380 | 2551 mm | 635.1 | `swap_chosen.json`（复算一致） |
| **C 最好（s10）** | **371** | 2577 mm | **628.7** | `swap_C_s10.json:opt` |
| C（summary 选的 s2） | 374 | 2574.9 mm | 631.5 | `swap_C_summary.json:81-87` |
| C 其余 seed | 373–384 | 2532–2605 mm | 631.5–638.5 | `swap_C_s1..s16.json:opt` |

**三条结论**：

1. **38 号槽位的布线价值 ≈ 0**：锁掉 38 之后，C 的最好解目标 **628.7** 反而**低于** B 的 635.1。说明"少一个候选槽"完全可以被重排吸收（另外我做的单点对照"B 只把一行从 38 挪到 33"是 635.8，同一量级）。所以"保留 38 会损失布线收益"这个担心，数据上不成立。
2. **但 C 的具体解与 B 不是一个东西**：16 个 seed 目标散布 631.5–638.5，`j603` 几乎每脚都不同，**连 `udest` 都不同** —— s2/s10 是 **UART4 → 步进总线**（USART3 去串口座），s13 是 **USART3 → 步进总线**。这意味着"哪个外设喂步进"、"左编码器用 TIM2 还是 TIM3"、"GRAY1 在 PC2 还是 PC3"这些 §5 的内容都会随 seed 变。
3. **因此 §2/§3/§4/§5 的表不能直接执行**：它们按 B 写，只能当"格式模板 + 风险判据"。拿到定稿 seed 后，用现成工具重出逐脚清单：

```bat
python 电赛小车2026_工具\route\swap_diff.py ^
  电赛小车2026_工具\route\board6.json ^
  电赛小车2026_工具\pinmap_sky.json ^
  <输出清单.md> 电赛小车2026_工具\route\swap_C_s<定稿>.json
```

`swap_diff.py` 已经内建 `2k−1↔2k` 配对（`swap_diff.py:17`、`:97-110`）和"MCU 侧/J603→J513/其它角色"三段输出（`:85-134`），出的就是本文 §2 那种表。

**已选定的定稿**：`route/swap_c.json`（09:26 落盘，内容 = seed 10）→ 实表见 **§2.6**。若之后再换 seed，§2.6 整段作废，用上面的 `swap_diff.py` 命令重出；选定后**不要再换**，因为下板 J513、固件表、丝印全要跟着它走。

### 2.6 C 定稿实表（`route/swap_c.json`，= seed 10）

> 依据：`route/swap_c.json`（09:26，`swap_C_summary.json:81-87` 记为 seed 10：371 交叉 / 2577 mm / 628.7）。**这一节才是要执行的东西**；§2.1/§2.3/§2.4/§5.1 的 B 表只作对照。
> 规模：MCU 换脚 **38** 个（B 是 32）、J603 换 **22** 脚、J513 换 **22** 脚、38 号脚 NC。

**J603（C 定稿）**

| 脚 | 网络 | 脚 | 网络 | 脚 | 网络 | 脚 | 网络 |
|---|---|---|---|---|---|---|---|
| 1 | 5V_LOGIC | 11 | SRV3 | 21 | MOT_EN | 31 | BATADC |
| 2 | 5V_LOGIC | 12 | ENC_R_B | 22 | GND | 32 | GND |
| 3 | 5V_LOGIC | 13 | STP_L | 23 | IOX_INT | 33 | ENC_R_A |
| 4 | 5V_LOGIC | 14 | GND | 24 | MOT_R2 | 34 | USTEP_RX |
| 5 | GND | 15 | GND | 25 | GND | 35 | GND |
| 6 | GND | 16 | CAN_TX | 26 | STP_R | 36 | I2C_SDA |
| 7 | SRV4 | 17 | CAN_RX | 27 | MOT_L1 | 37 | GND |
| 8 | SRV2 | 18 | ENC_L_A | 28 | MOT_R1 | **38** | **NC** |
| 9 | SRV1 | 19 | MOT_L2 | 29 | I2C_SCL | 39 | GND |
| 10 | GND | 20 | ENC_L_B | 30 | USTEP_TX | 40 | GND |

**J513（C 定稿，22 脚改；37 号 IMON 不动）**

| J513 | 现状 | 改成 | J513 | 现状 | 改成 |
|---|---|---|---|---|---|
| 7 | CAN_TX | **SRV2** | 19 | MOT_R1 | **ENC_L_B** |
| 8 | CAN_RX | **SRV4** | 20 | MOT_R2 | **MOT_L2** |
| 10 | I2C_SCL | **SRV1** | 22 | ENC_L_A | **MOT_EN** |
| 11 | I2C_SDA | **ENC_R_B** | 23 | ENC_L_B | **MOT_R2** |
| 12 | IOX_INT | **SRV3** | 24 | ENC_R_A | **IOX_INT** |
| 14 | MOT_EN | **STP_L** | 25 | ENC_R_B | **STP_R** |
| 15 | BATADC | **CAN_TX** | 27 | STP_L | **MOT_R1** |
| 17 | MOT_L1 | **ENC_L_A** | 28 | STP_R | **MOT_L1** |
| 18 | MOT_L2 | **CAN_RX** | 30 | USTEP_RX | **I2C_SCL** |
| 32 | SRV1 | **BATADC** | 33 | SRV2 | **USTEP_RX** |
| 34 | SRV3 | **ENC_R_A** | 35 | SRV4 | **I2C_SDA** |

> 其余 18 脚不动：1–4（5V_LOGIC）、5/6/9/13/16/21/26/31/36/38/39/40（GND）、**29（USTEP_TX 原位，本 seed 的幸运处）**、**37（IMON）**。

**与 B 的关键差异（按 B 做会全错的地方）**

1. **步进总线改用 UART4（PC10 / PC11）**，不是 B 的 USART3；USART3 改去 J620。
2. **STP_L / STP_R 对调**（STP_L=PB14/TIM12、STP_R=PE5/TIM9）—— B 里这两路是**不动**的，很多检查表会漏。
3. **SRV2 / SRV3 对调**；电机 L2/R1/R2 三路换（L1 仍在 CH1）：最终 **L1=CH1、L2=CH4、R1=CH2、R2=CH3**。
4. ADC：GRAY3=PB1、GRAY4=PC5、**BATADC=PB0**（B 是 GRAY3=PC5、GRAY4=PC0、BAT=PC4）。
5. 编码器：左 TIM3 **不换相**（A=PB4、B=PB5）、右 TIM2 **换相**（A=PA1、B=PA15）—— B 是左换相、右不换。
6. J603.33 是 **ENC_R_A**（不是 B 的 ENC_R_B）；J513.34 也跟着变成 ENC_R_A。
7. 空余脚变成 **PC2/PC3/PC4/PD11/PE7/PE15**（B 是 PC2/PB0/PB1/PE7/PD11/PE15）。

**复核过的一致性**（用 `_tmp_analyze/plan_c_final.py` 生成本表）：
- J603 与 J513 逐脚 `n^1` 对齐：22 个改动的脚全部落在"信号焊盘"上，**没有信号落到 GND/5V**（这是最容易出错的一类）。
- J513.37 仍 = IMON；J603.38 = NC；两板 40 脚仍只有"38↔37"这一处例外。
- `valid()` 通过（无重复脚、无 EXTI 冲突、PC13 不作输出）。

---

## 3. 上板原理图改动清单（按页）

### 3.0 改动面总表

> **本节流程对 B/C 都适用，但脚位数字按 B 写**（例：B 换 32 脚、34 个焊盘；C 定稿换 38 脚、40 个焊盘）。执行时把 §2.6 的数字代入。

| 页 | 名称/UUID | 动？ | 动什么 |
|---|---|---|---|
| 1 | 1_核心板插座 `42fa4b30c4583105` | **动** | J601、J602 全部 80 脚（34 个网络换脚 + 4 个原空脚接信号 + 4 个脚改 NC + 2 个改名） |
| 2 | 2_电源与扩展 `aec7fc9eab802692` | **动** | J603 22 脚；R603/R604、R632/R633 各 2 脚（上拉对互换） |
| 3 | 3_人机交互 `c15a4e9053cdb09e` | 不动 | KEY1–5/EC11_A,B,P/RGB_R,G,B/BEEP 网络名全不变；仅 MCU 侧换脚在页 1 |
| 4 | 4_传感器与无线 `0ed970111fce6268` | **不动** | `gcol=[0,1,2,3,4,5]` → 灰度分压列不换：R617–R624/C614–C617/R639–R642/C623/C624 **全部保持**；E01 的 SPI/E01_* 网络名不变 |
| 5 | 5_串口与I2C `7629f430a37f2138` | **动** | J607/J608/J619/J620/J621 各 2 脚；R615/R616/R636/R637/R638 各 2 脚；J616/J617/J624 各 2 脚 |

> 与任务书列的"灰度分压列 R617–R624…"**不符**：**B 与 C 定稿的 `gcol` 都是恒等置换**，灰度列一列都不换（`swap_chosen.json:gcol`、`swap_c.json:gcol`；`swapopt.py:35-36` 的 GCOL 分组与 `sch/n4.json` 现状一致）。页 4 唯一变化是 `GRAYk` 网络出现在页 1 的另一个 J601/J602 脚上。**但注意**：C 的多数 seed（16 个里 12 个）会改 `gcol`（§2.5），若换 seed 就要动页 4 的 6 组分压列标签。

### 3.1 页 1：J601 / J602

改法：整颗器件重连（80 脚），不是逐脚补。逐项：

1. **32 个信号换脚**（§2.1 的"现 MCU → 新 MCU"减去 SRV/STP/USTEP，即 18 GPIO + 7 ADC + 3 电机 + 4 编码器）。等价说法：J601/J602 上 **34 个焊盘换网络**（32 换 + J601.29/30 改名）。
2. **4 个原先空脚开始接信号**（现在是 `nc`，改完要清 NC 标记）：
   - J602.1 = E01_IRQ（S41 = PD3）
   - J602.9 = KEY4（S49 = PE6）
   - J602.15 = GRAY2（S55 = PC1）
   - J602.17 = GRAY1（S57 = PC3）
3. **4 个脚变空**（要打 NC）：J601.11（PB0）、J601.13（PE7）、J601.14（PB1）、J601.31（PD11）。
4. **2 个改名**：J601.30 `U3_TX → USTEP_TX`、J601.29 `U3_RX → USTEP_RX`（见 §3.5）。
5. **不动**：J601.39/40、J602.37/39/40 的 GND/5V_MAIN；SPI_SCK/MISO/MOSI（J601.8/7/10）；I2C2_*（J601.23/24）；I2C3_*（J602.2/34）；CAN（J602.31/32）；SRV（J601.33–36）；STP_L（J602.10）；U1/U4/U5/U6 的 MCU 脚（J602.22/23、6/7、4/5、35/36）全部不变。

改完后 J601 的 NC = 2,3,5,11,13,14,21,31,37,38；J602 的 NC = 16,26,38（与 §3.7 的 `n1_planC.json` 完全一致；`J602.16=PC2`、`J602.26=BAT`、`J602.38=3V3` 是本来就空的核心板脚）。

### 3.2 页 2：J603 + 上拉对

1. **J603**：按定稿 C 的 `best.j603` 换 21–23 脚（B 是 23 脚，见 §2.3），**38 号脚必须保持 NC 并用 `sch no-connect` 明确打掉**（C 的唯一硬约束）。每个 seed 的 33/34 号脚内容都不同，别照抄 §2.3 的 C 列。
2. **上拉对互换**（`swap_chosen.json:pull = {"I2C1":1,"I2C2":0,"I2C3":2}`，`swapopt.py:46` 的 PULL 顺序 = `[R603/R604, R632/R633, R634/R635]`）：
   - R603.2：`I2C_SCL → I2C2_SCL`；R604.2：`I2C_SDA → I2C2_SDA`
   - R632.2：`I2C2_SCL → I2C_SCL`；R633.2：`I2C2_SDA → I2C_SDA`
   - R634/R635 不变（仍 I2C3_*）
   - 这 4 颗电阻在 PCB 上就是同一列（R603 (40,20.5)、R604 (40,18)、R632 (40,15.5)、R633 (40,13) mm），互换代价很小；但不改也只是 +6 交叉（§6 R11）。
3. **不动**：R605（IOX_INT 上拉）、U607（EEPROM，挂 I2C1，网络名不变）、电源/指示灯、C620/D601。

### 3.3 页 5：串口座 / 串阻 / Qwiic

`swap_chosen.json:udest/ures/qw` → 逐颗：

| 器件 | 新网络 | 现状 |
|---|---|---|
| J621 | 1=5V_MAIN, 2=**U6_TX**, 3=**U6_RXD**, 4=GND | U1_TX / U1_RXD |
| R637 | 1=**U6_RX**, 2=**U6_RXD** | U5_RX / U5_RXD |
| J607 | 2=**U1_TX**, 3=**U1_RXD** | U3_TX / U3_RXD |
| R636 | 1=**U1_RX**, 2=**U1_RXD** | U4_RX / U4_RXD |
| J619 | 2=**U2_TX**, 3=**U2_RXD** | U4_TX / U4_RXD |
| R616 | 1=**U2_RX**, 2=**U2_RXD** | U6_RX / U6_RXD |
| J608 | 2=**U4_TX**, 3=**U4_RXD** | U6_TX / U6_RXD |
| R615 | 1=**U4_RX**, 2=**U4_RXD** | U3_RX / U3_RXD |
| J620 | 2=U5_TX, 3=U5_RXD（**座与网络都不变**） | 同 |
| R638 | 1=**U5_RX**, 2=**U5_RXD** | U1_RX / U1_RXD |
| J617 | 3=**I2C_SDA**, 4=**I2C_SCL** | I2C2_SDA / I2C2_SCL |
| J624 | 3=**I2C2_SDA**, 4=**I2C2_SCL** | I2C3_SDA / I2C3_SCL |
| J616 | 3=**I2C3_SDA**, 4=**I2C3_SCL** | I2C_SDA / I2C_SCL |

（J620 的 TX/RX 座内网络不变，但**串阻从 R637 换成 R638** —— 这是 `ures` 置换的结果，不改也只是 +19 交叉的一部分。）

改完后的"哪个座是谁"：**J621=USART6 / J607=USART1(DAP) / J619=USART2 / J620=UART5 / J608=UART4 / 步进总线=USART3**。丝印/文档要同步（交接 §6 第 4 条）。

### 3.4 页 3、页 4：明确不动

- 页 3：`sch/n3.json` 里 SW601(A–D,CENTER→KEY1–5)、SW602(A/B/D→EC11_A/B/P + R606/R607/C612/C613)、Q601/R608/R609/BZ601、LED602/R610–R612、J605 全部按"网络名"接线，而网络名一个都没变 → 不动。
- 页 4：`gcol` 恒等 → 6 列分压（R617–R624/C614–C617、R639–R642/C623/C624）与 J609–J612/J622/J623 不动；J614 的 SPI1/E01_* 网络名不变（只有 E01_CS/CE/IRQ 在页 1 换 MCU 脚）→ 不动。

### 3.5 网络命名规则（**先定这个，再动手**）

这是全案唯一有"自由度"的地方，也是最容易两个人做出两套板子的地方。建议规则（本清单后续全部按它写）：

> **U#_TX / U#_RX / U#_RXD 只属于 USART#；功能总线的网络名保持功能名。**

推论：
- 步进总线这一对网络**继续叫 `USTEP_TX` / `USTEP_RX`**，但驱动源从 USART2(PD5/PD6) 改成 **USART3(PD8/PD9)**；
- `U3_TX` / `U3_RX` 这两个名字**删除**（USART3 不再有独立串口座），J601.29/30 改名成 `USTEP_RX/USTEP_TX`；
- 下板 `USTEP_TX/RX` 名字**一个都不改**，只把 J513 的脚从 29/30 挪到 14/20 —— 这是选这套命名的最大好处（下板已完成，改动面最小）。

反例（别做）：一半保留 `USTEP_*`、一半把 J603.13/19 改叫 `U3_TX/U3_RX`。那样 `U3_TX` 会同时指"PD8 到 J603.13"和"旧 J607 座"两套语义，接线时十有八九把 TX/RX 接反（**待核实项归零，纯约定问题**）。

### 3.6 执行顺序（先断后连，工具 `sch/rewire.py`）

`rewire.py` 的行为（`sch/rewire.py:39-62`）：对传入的每个 ref，**逐脚 `sch disconnect` + 清 NC → 生成 `rw_<name>.json` → `sch autoconnect --spec ... --replace` → 按 spec 的 `nc` 重新打 NC → `sch save`**。所以"先断后连"已经内建，不要手工一段段断开再连（会漏脚）。

推荐顺序（每步之后 `sch save` + 读回，连接器会"假失败"，见 `电赛小车2026_交接.md:62`）：

```bat
:: 0) 备份（用户偏好：大改前先备份，交接 §7）
::    工程 UUID 用 0486116d…（交接 §2；sch/p1–p6.json 里还是旧 UUID，别用）

:: 1) 页 1：J601、J602（80 脚，最大的一块）
python 电赛小车2026_工具\sch\rewire.py 电赛小车2026_工具\sch\n1_planC.json J601 J602

:: 2) 页 2：J603 + 4 只上拉（spec 里 page 换成页2 UUID）
python 电赛小车2026_工具\sch\rewire.py 电赛小车2026_工具\sch\n2_planC.json J603 R603 R604 R632 R633

:: 3) 页 5：5 座 + 5 串阻 + 3 Qwiic
python 电赛小车2026_工具\sch\rewire.py 电赛小车2026_工具\sch\n5_planC.json J607 J608 J619 J620 J621 R615 R616 R636 R637 R638 J616 J617 J624
```

为什么这个顺序：
- 页 1 是"源"，先落地并立刻用 `sch/verify_file.py` 逐脚核 80 脚；
- 页 2 的 J603 是"汇"，40 脚全表可逐行对照 §2.3；
- 页 5 是 13 颗小件，最后做。
- **中途状态会出现"同一网络多点/缺点"**（例如 USTEP_TX 暂时同时挂 J602.29 和 J603.13），这是正常的：**三页全改完、跨页核对通过之前，绝对不要 `pcb import-changes`**，否则会把两个 MCU 脚并到一个网络里。

改完必做（交接 §4 第 3 步）：
1. `sch/verify_file.py` 逐页逐脚核对（读保存后的 `.esch2`，`sch list` 的 pin.net 回读是空的，交接 §5）；
2. 跨页检查"无单脚网络"（`U3_TX/U3_RX` 删干净后尤其要看）；
3. 确认 J603 的 38 号脚 = NC（C 的硬约束）。

### 3.7 三份 spec（可直接存盘用）

**`sch/n1_planC.json`**（页 1：J601 + J602；`power/ground` 与 `n1.json` 一致）

```json
{
 "project": "0486116df89154733a64c3b0cae3ce7ec6579ab60452801dda909d0bd7aa27e5",
 "page": "42fa4b30c4583105",
 "name": "n1C",
 "power": ["5V_MAIN", "3V3_MAIN"],
 "ground": ["GND"],
 "parts": [
  {"ref": "J601",
   "nets": {"1": "GRAY6", "4": "ENC_R_B", "6": "GRAY5", "7": "SPI_MISO", "8": "SPI_SCK",
            "9": "BATADC", "10": "SPI_MOSI", "12": "GRAY3", "15": "MOT_R2", "16": "MOT_EN",
            "17": "MOT_L2", "18": "E01_CE", "19": "MOT_L1", "20": "IOX_INT", "22": "MOT_R1",
            "23": "I2C2_SCL", "24": "I2C2_SDA", "25": "IMU_INT", "26": "RGB_G", "27": "RGB_R",
            "28": "STP_R", "29": "USTEP_RX", "30": "USTEP_TX", "32": "EC11_P", "33": "SRV2",
            "34": "SRV1", "35": "SRV4", "36": "SRV3", "39": "GND", "40": "5V_MAIN"},
   "nc": ["2", "3", "5", "11", "13", "14", "21", "31", "37", "38"]},
  {"ref": "J602",
   "nets": {"1": "E01_IRQ", "2": "I2C3_SDA", "3": "E01_CS", "4": "U5_TX", "5": "U5_RX",
            "6": "U4_RX", "7": "U4_TX", "8": "KEY5", "9": "KEY4", "10": "STP_L", "11": "KEY1",
            "12": "KEY2", "13": "BEEP", "14": "GRAY4", "15": "GRAY2", "17": "GRAY1", "18": "KEY3",
            "19": "RGB_B", "20": "I2C_SDA", "21": "I2C_SCL", "22": "U1_RX", "23": "U1_TX",
            "24": "ENC_L_A", "25": "ENC_L_B", "27": "EC11_A", "28": "U2_RX", "29": "U2_TX",
            "30": "EC11_B", "31": "CAN_TX", "32": "CAN_RX", "33": "ENC_R_A", "34": "I2C3_SCL",
            "35": "U6_TX", "36": "U6_RX", "37": "GND", "39": "GND", "40": "5V_MAIN"},
   "nc": ["16", "26", "38"]}
 ]
}
```

**`sch/n2_planC.json`**（页 2：J603 + 上拉对。**J603 的 `nets` 先按 B 填，落地前必须换成定稿 C 的 `best.j603`，并把 `nc` 保持为 `["38"]`**）

```json
{
 "project": "0486116df89154733a64c3b0cae3ce7ec6579ab60452801dda909d0bd7aa27e5",
 "page": "aec7fc9eab802692",
 "name": "n2C",
 "power": ["5V_LOGIC", "5V_MAIN", "3V3_MAIN", "3V3_RF"],
 "ground": ["GND"],
 "parts": [
  {"ref": "J603",
   "nets": {"1": "5V_LOGIC", "2": "5V_LOGIC", "3": "5V_LOGIC", "4": "5V_LOGIC",
            "5": "GND", "6": "GND", "7": "SRV4", "8": "SRV3", "9": "SRV1", "10": "GND",
            "11": "SRV2", "12": "ENC_R_A", "13": "USTEP_TX", "14": "GND", "15": "GND",
            "16": "STP_R", "17": "CAN_TX", "18": "CAN_RX", "19": "USTEP_RX", "20": "ENC_L_B",
            "21": "MOT_R1", "22": "GND", "23": "IOX_INT", "24": "ENC_L_A", "25": "GND",
            "26": "MOT_L1", "27": "MOT_L2", "28": "STP_L", "29": "MOT_R2", "30": "MOT_EN",
            "31": "BATADC", "32": "GND", "33": "ENC_R_B", "34": "I2C_SDA", "35": "GND",
            "36": "I2C_SCL", "37": "GND", "39": "GND", "40": "GND"},
   "nc": ["38"]},
  {"ref": "R603", "nets": {"1": "3V3_MAIN", "2": "I2C2_SCL"}},
  {"ref": "R604", "nets": {"1": "3V3_MAIN", "2": "I2C2_SDA"}},
  {"ref": "R632", "nets": {"1": "3V3_MAIN", "2": "I2C_SCL"}},
  {"ref": "R633", "nets": {"1": "3V3_MAIN", "2": "I2C_SDA"}}
 ]
}
```

> 上面 J603 的 40 脚是 **B 的排布 + 把 38 挪到 33**（仅用于演示格式）。**真正落地时**：跑 `swap_diff.py` 拿到定稿 C 的表，把 `J603.nets` 整块替换（GND/5V 那 16 个脚不变），`nc` 保持 `["38"]`。上拉那 4 颗（R603/R604/R632/R633）与 J603 无关，可直接用；若决定不改上拉（§6 R11：+6 交叉），删掉这 4 个 part 即可。

**`sch/n5_planC.json`**（页 5：13 颗）

```json
{
 "project": "0486116df89154733a64c3b0cae3ce7ec6579ab60452801dda909d0bd7aa27e5",
 "page": "7629f430a37f2138",
 "name": "n5C",
 "power": ["5V_MAIN", "3V3_MAIN"],
 "ground": ["GND"],
 "parts": [
  {"ref": "J607", "nets": {"1": "5V_MAIN", "2": "U1_TX", "3": "U1_RXD", "4": "GND"}},
  {"ref": "R636", "nets": {"1": "U1_RX", "2": "U1_RXD"}},
  {"ref": "J608", "nets": {"1": "5V_MAIN", "2": "U4_TX", "3": "U4_RXD", "4": "GND"}},
  {"ref": "R615", "nets": {"1": "U4_RX", "2": "U4_RXD"}},
  {"ref": "J620", "nets": {"1": "5V_MAIN", "2": "U5_TX", "3": "U5_RXD", "4": "GND"}},
  {"ref": "R638", "nets": {"1": "U5_RX", "2": "U5_RXD"}},
  {"ref": "J621", "nets": {"1": "5V_MAIN", "2": "U6_TX", "3": "U6_RXD", "4": "GND"}},
  {"ref": "R637", "nets": {"1": "U6_RX", "2": "U6_RXD"}},
  {"ref": "J619", "nets": {"1": "5V_MAIN", "2": "U2_TX", "3": "U2_RXD", "4": "GND"}},
  {"ref": "R616", "nets": {"1": "U2_RX", "2": "U2_RXD"}},
  {"ref": "J617", "nets": {"1": "GND", "2": "3V3_MAIN", "3": "I2C_SDA", "4": "I2C_SCL"}, "nc": ["5", "6"]},
  {"ref": "J624", "nets": {"1": "GND", "2": "3V3_MAIN", "3": "I2C2_SDA", "4": "I2C2_SCL"}, "nc": ["5", "6"]},
  {"ref": "J616", "nets": {"1": "GND", "2": "3V3_MAIN", "3": "I2C3_SDA", "4": "I2C3_SCL"}, "nc": ["5", "6"]}
 ]
}
```

> spec 生成方式：先改 `pinmap_sky.py` 的 MAP（交接 §4 第 1 步），再跑 `gen_pindoc.py`；这三份 JSON 应与 `pinmap_sky.py` 的输出**逐脚一致**，不一致以 `pinmap_sky.py` 为准（它是唯一真源，`起航小车_引脚映射.md:5`）。

---

## 4. 下板 Schematic5 改动清单

### 4.1 只改第 5 页 J513（22 脚，C 版）

**为什么只改一页**：下板所有信号都靠"全局网络标号"跨页，C 方案**不改任何网络名**（§3.5 命名规则的好处），所以只有第 5 页 `24d40ada118a4a7f`（`sch/p5.json` 的页）里 J513 这 40 脚的网络要重排；页 1–4、6 一个字不用动。

C 版逐脚（= §2.6 的 J513 表；按 J513 脚号排序）：

| J513 | 现在 | 改成 | J513 | 现在 | 改成 |
|---|---|---|---|---|
| 7 | CAN_TX | **SRV2** | 19 | MOT_R1 | **ENC_L_B** |
| 8 | CAN_RX | **SRV4** | 20 | MOT_R2 | **MOT_L2** |
| 10 | I2C_SCL | **SRV1** | 22 | ENC_L_A | **MOT_EN** |
| 11 | I2C_SDA | **ENC_R_B** | 23 | ENC_L_B | **MOT_R2** |
| 12 | IOX_INT | **SRV3** | 24 | ENC_R_A | **IOX_INT** |
| 14 | MOT_EN | **STP_L** | 25 | ENC_R_B | **STP_R** |
| 15 | BATADC | **CAN_TX** | 27 | STP_L | **MOT_R1** |
| 17 | MOT_L1 | **ENC_L_A** | 28 | STP_R | **MOT_L1** |
| 18 | MOT_L2 | **CAN_RX** | 30 | USTEP_RX | **I2C_SCL** |
| 32 | SRV1 | **BATADC** | 33 | SRV2 | **USTEP_RX** |
| 34 | SRV3 | **ENC_R_A** | 35 | SRV4 | **I2C_SDA** |

- **不要改**：1–4（5V_LOGIC）、5/6/9/13/16/21/26/31/36/38/39/40（GND）、**29（USTEP_TX 原位）**、**37（IMON）**。
- 若误按 B 改（37=ENC_R_B、34 变空）：IMON 断、ENC_R_B 落在 IMON 焊盘上。**没有短路、没有 DRC 报错**，只是编码器不工作 + IMON 丢失，非常难查（§6 R10）。
- 下板改完的核对：J513.37 的网络成员必须**只有** `U201.7` 和 `R204.1`（`电赛小车2026_设计方案详解.md:1281`）。

### 4.2 IMON 保持不动的理由

1. **决策依据**：`电赛小车2026_审查处理记录.md:56` R9「IMON 只作测试点，上板不接」；`起航小车_改版需求.md:160/166`「第 37 脚上板不连 / IMON 对应 J603 第 38 脚（不连）」。
2. **电气风险**：IMON 是 TPS48111 的模拟电流输出，40 A≈2.73 V、OCP 45.5 A≈3.07 V（`电赛小车2026_设计方案详解.md:1449`，按 TI 公式估算），满量程未实测（`:1062` R9）。不接 = 风险为 0；接 = 必须先分压+钳位。
3. **代价可量化 ≈ 0**：C 定稿的目标 **628.7** 甚至优于 B 的 635.1（§2.5 表）——锁掉 38 后重排把损失全吸收了；单点对照"B 只挪一行"也只有 +0.7（+0.11%）。原因见 §6 R8：38 号脚本来就是当前唯一空槽，B 用它只是"多一个候选槽"，不是"多一根关键线"。
4. **不占 MCU 脚**：上板 38 号脚本来就没有 MCU 连接（`board6.json:J603.38.net==""` + `sch/n2.json:62-64` 的 `nc:["38"]`）。

### 4.3 IMON 保留的验证方法（4 层，从软到硬）

| 层 | 方法 | 通过判据 |
|---|---|---|
| 1 原理图 | 页 2 J603.38 有 NC 标记；页 5 J513.37 网络 = IMON | 读保存后的 `.esch2`（`sch/verify_file.py`）；`n2_planC.json` 的 `nc` 含 "38" |
| 2 网表 | J513.37 的成员集合 | 恰好 `{U201.7, R204.1}`；`evidence_5` 的 J513 行 37=IMON 不变 |
| 3 PCB | 上板 J603.38 无走线/无铜箔；`conn.py` 的网络表里不出现"J603.38" | `pcb dump --include-copper` → `route/conn.py` 87 网（38 号脚不属于任何网） |
| 4 实物 | 叠板后万用表测 **J513.37 对 GND** | 正常 ≈ **15 kΩ**（R204）；若 ≈0 Ω → 奇偶对调错了或上板 38 被接了地（§6 R1） |

> 注意：**"保留 38 号脚"不等于"IMON 可用"**。上板 38 是空焊盘、无铜箔、无 MCU 引脚（上板空余脚是 PC2/PB0/PB1/PE7/PD11/PE15，都不在 38 上）。将来要真用 IMON，仍须改板或飞线。

---

## 5. 固件影响清单

### 5.1 逐信号（只列变的；**以下按方案 B**）

> **C 定稿后本表必须重出**：定稿（§2.6）的步进总线是 **UART4（PC10/PC11）**，不是本表的 USART3；STP_L/R 还对调了。照抄本表会把固件做错。重出方法同 §2.5（`swap_diff.py` 的第 1 段就是本表）。

| 信号 | 旧 | 新 | 外设/AF | 变化类别 |
|---|---|---|---|---|
| GRAY1 | PA2（ADC123_IN2） | **PC3（ADC123_IN13）** | ADC | ADC 通道 2→13 |
| GRAY2 | PA3（IN3） | **PC1（ADC123_IN11）** | ADC | 3→11 |
| GRAY3 | PB0（ADC12_IN8） | **PC5（ADC12_IN15）** | ADC | 8→15 |
| GRAY4 | PB1（ADC12_IN9） | **PC0（ADC123_IN10）** | ADC | 9→10 |
| GRAY5 | PC4（ADC12_IN14） | **PA3（ADC123_IN3）** | ADC | 14→3 |
| GRAY6 | PC5（ADC12_IN15） | **PA2（ADC123_IN2）** | ADC | 15→2 |
| BATADC | PC0（ADC123_IN10） | **PC4（ADC12_IN14）** | ADC | 10→14 |
| MOT_L1 | PE9 / TIM1_CH1 | **PE13 / TIM1_CH3** | TIM1 AF1 | 通道 1→3 |
| MOT_L2 | PE11 / TIM1_CH2 | PE11 / TIM1_CH2 | TIM1 AF1 | **不变** |
| MOT_R1 | PE13 / TIM1_CH3 | **PE14 / TIM1_CH4** | TIM1 AF1 | 3→4 |
| MOT_R2 | PE14 / TIM1_CH4 | **PE9 / TIM1_CH1** | TIM1 AF1 | 4→1 |
| ENC_L_A | PA15 / TIM2_CH1 | **PB5 / TIM3_CH2** | TIM3 AF2 | 定时器 2→3；A/B 对调 |
| ENC_L_B | PA1 / TIM2_CH2 | **PB4 / TIM3_CH1** | TIM3 AF2 | 定时器 2→3；A/B 对调 |
| ENC_R_A | PB4 / TIM3_CH1 | **PA15 / TIM2_CH1** | TIM2 AF1 | 定时器 3→2 |
| ENC_R_B | PB5 / TIM3_CH2 | **PA1 / TIM2_CH2** | TIM2 AF1 | 定时器 3→2 |
| USTEP_TX | PD5 / USART2_TX | **PD8 / USART3_TX** | USART AF7 | 外设 2→3（波特率无关） |
| USTEP_RX | PD6 / USART2_RX | **PD9 / USART3_RX** | USART AF7 | 外设 2→3 |
| IOX_INT | PD11 / EXTI11 | **PE12 / EXTI12** | GPIO EXTI | EXTI 11→12 |
| IMU_INT | PD10 / EXTI10 | **PB13 / EXTI13** | GPIO EXTI | EXTI 10→13 |
| E01_IRQ | PB15 / EXTI15 | **PD3 / EXTI3** | GPIO EXTI | EXTI 15→3 |
| EC11_A | PE3 / EXTI3 | **PD7 / EXTI7** | GPIO EXTI | EXTI 3→7 |
| EC11_B | PE4 / EXTI4 | **PD4 / EXTI4** | GPIO EXTI | EXTI 4（不变） |
| EC11_P | PE2（GPIO 输入） | **PD10** | GPIO | 端口变 |
| KEY1 | PE0 | **PE4** | GPIO 上拉输入 | |
| KEY2 | PE1 | **PE3** | GPIO 上拉输入 | |
| KEY3 | PC13 | **PE1** | GPIO 上拉输入 | |
| KEY4 | PD4 | **PE6** | GPIO 上拉输入 | |
| KEY5 | PD7 | **PC13** | GPIO 上拉输入 | 只有输入 |
| BEEP | PC8 | **PE2** | GPIO 输出 | |
| RGB_R | PE7 | **PB15** | GPIO 输出 | |
| RGB_G | PE8 | **PB12** | GPIO 输出 | |
| RGB_B | PE12 | **PE0** | GPIO 输出 | |
| E01_CS | PB12 | **PC8** | GPIO 输出 | |
| E01_CE | PB13 | **PE10** | GPIO 输出 | |
| MOT_EN | PE10 | **PE8** | GPIO 输出 | |
| CAN_RX/TX | PD0/PD1 | PD0/PD1 | CAN1 AF9 | **不变** |
| SRV1–4 | PD12–15 | PD12–15 | TIM4_CH1–4 AF2 | **不变** |
| STP_L/R | PE5 / PB14 | PE5 / PB14 | TIM9_CH1 AF3 / TIM12_CH1 AF9 | **不变** |
| U1..U6 的 MCU 脚 | PB6/PB7、PD5/PD6、PD8/PD9、PC10/11、PC12/PD2、PC6/PC7 | **全部不变** | — | 只换"哪个座" |
| SPI1 / I2C1/2/3 | PA5/6/7、PB8/PB9、PB10/PB11、PA8/PC9 | **全部不变** | — | — |

信号 ↔ S 脚 ↔ J601/J602 焊盘的全表见 `pinmap_sky.py:7-11`（SOCK）与 §3.7 的 spec。

### 5.2 外设级要点

1. **USART 只是"换座"，引脚一个没动**：步进总线 USART2→USART3、J619 改 USART2、J608 改 UART4、J607 改 USART1、J621 改 USART6、J620 仍 UART5。**不需要任何 GPIO remap**，但 USART3 与 USART2 的初始化要"角色互换"（原先喂步进的 2 号外设改成喂 J619，3 号改喂步进）。
2. **左编码器 32 位 → 16 位（最容易被忽略的一条）**：现状左编码器在 TIM2（F407 上 TIM2/TIM5 是 32 位，`pinmap_sky.py:23` 特意标注"32 位编码器"），方案 B/C 把左编码器换到 **TIM3（16 位）**。若固件直接用 `TIMx->CNT` 当绝对位置/里程，必须加溢出计数（或接受 16 位回绕）。右编码器反而从 TIM3 升到 TIM2。
3. **左编码器 A/B 对调**：`swap_chosen.json:enc=[1,true,false]`（`swapopt.py:97-101`）→ ENC_L_A 接 TIM3_CH2、ENC_L_B 接 CH1。计数方向与现在相反，PID 符号/速度方向要一起改，否则第一次闭环会反着冲。
4. **TIM1 通道重排**：MOT_L1 CH1→CH3、MOT_R1 CH3→CH4、MOT_R2 CH4→CH1（MOT_L2 不变）。凡是用 `TIM_SetCompare1..4` 或按通道建表的地方都要重排 CCR 映射；ARR/频率/占空比数值不变。
5. **ADC 规则序列重排**：7 个通道号全变（§5.1）。若用 ADC+DMA 扫描，DMA 缓冲的通道顺序必须跟着改，否则灰度/BATADC 会串位（这种错表现为"数值都像但互相错位"，很难查）。
6. **EXTI**：5 条线（3/4/7/12/13）无重复；`swapopt.py:140-143` 的 `valid()` 每次都会查重复与 `PC13 不作输出`。**别给 KEY5(PC13) 开 EXTI**，否则和 IMU_INT(PB13) 抢 EXTI13。
7. **DMA（若用）**：USART2_TX 是 DMA1 Stream6/Ch4、USART3_TX 是 DMA1 Stream3/Ch4；USART2_RX Stream5/Ch4、USART3_RX Stream1/Ch4（F407 参考手册 DMA 请求映射表，**本地无原文 → 待核实**固件是否用 DMA 收发步进总线）。
8. **JTAG/SWD**：PA15(JTDI)、PB4(NJTRST) 换手后仍在用（ENC_R_A/ENC_L_B），必须保持 `SWJ_JTAGDisable`（现状已如此，`pinmap_sky.py:23-24` 就在用这两个脚）。
9. **PA4 仍要驱动高**（天空星板载 Flash CS，`pinmap_sky.py:54`）——本方案没动它，别在初始化里把它配成别的功能。

---

## 6. 风险清单

### R1 奇偶对调搞错（**最致命，但最容易测**）

- **错法**：把下表/下板脚号直接当 J603 脚号用（漏掉 ±1）。
- **签名（可精确预测）**：下板这 8 个信号脚会直接对到上板 GND 脚 → 短路到地：
  `J513.10 I2C_SCL`、`14 MOT_EN`、`15 BATADC`、`22 ENC_L_A`、`25 ENC_R_B`、`32 SRV1`、`35 SRV4`、`37 IMON`
  （因为上板 J603 的 10/14/15/22/25/32/35/37 全是 GND，`board6.json:J603.pads`）。
- **检测 1（实物，最灵敏）**：叠插后测 **J513.37 对 GND**。正常 = 15 kΩ（R204）；≈0 Ω = 上板 37 的 GND 送到了下板 37，即奇偶反了（这是全连接器唯一"信号/地"相邻对，且 C 方案专门保留它）。
- **检测 2（实物）**：上板 J603.38 对任意网络应**开路**；若量到 15 kΩ 到地，说明你把 ENC_R_B 接到了 38（B 的接法）。
- **检测 3（静态）**：改完 PCB 后把上板 `pcb dump` 的 J603 网络表与 `sch/p5.json` 的 J513 表按 `n^1` 逐脚对齐 diff；应为"39 脚一致 + 38↔37 一个例外"。
- **检测 4（软件）**：把 SRV1(PD12) 设 1 kHz 方波 → 测下板 J513.10；同时 J513.32（SRV1 原位）必须不动。

### R2 TIM1 通道重排后 PWM 互补/死区

- **本设计不需要互补**：电机驱动是 DRV8870，IN1/IN2 单端输入（`电赛小车2026_设计方案详解.md:599-600`、`:456`），不是半桥，没有 CHxN/死区需求。
- **但有个真隐患**：TIM1 的三对互补脚正好落在新方案的 GPIO 上 —— `TIM1_CH1N = PB13/PE8`、`CH2N = PB14/PE10`、`CH3N = PB15/PE12`。而这 6 个脚在新方案里分别是 IMU_INT/MOT_EN、STP_R/E01_CE、RGB_R/IOX_INT。**只要固件给 TIM1 配了任何 CHxN 的 AF 或置了 CCxNE，就会把 PWM 打到这些 GPIO 上**（MOT_EN 乱跳 = 电机域乱开关）。→ 必须确认 TIM1 只初始化 CH1–CH4 四个脚，N 通道一律不配。
- **死区**：BDTR.DTG 只作用于 N 输出，不配 N 时无意义；但高级定时器仍需 `TIM_CtrlPWMOutputs(TIM1, ENABLE)`（MOE）才会出波 —— 确认这一句还在。
- **CCR**：见 §5.2 第 4 条。

### R3 PC13 只作输入（确认）

- KEY5 = PC13（`swap_chosen.json:64`），SW601 的 CENTER 接 GND（`sch/n3.json:19-25`），所以是**上拉输入**。
- 规则自动检查过：`swapopt.py:42` 的 `OUT_SIG`（BEEP/RGB_*/E01_CS/E01_CE/MOT_EN）不含 PC13，且 `:143` 每次迭代强校验。方案 B/C 通过。
- 仍需人工确认：固件**不能**把 PC13 配成输出（F407 的 PC13 属备份域，输出能力仅 3 mA，数据手册级限制 **待核实**）；也别给它开 EXTI13（撞 IMU_INT）。

### R4 EXTI 线号重复

- 新方案 5 条：PD7(7)、PD4(4)、PD3(3)、PB13(13)、PE12(12) —— **无重复**（`swapopt.py:140-143` 自动查 + 我逐条列过）。
- 唯一新增隐患：KEY5=PC13 若启用 EXTI 会与 PB13 抢 13；键是轮询的，保持轮询即可。
- 另一个资料性隐患：PD3 在天空星上是 TF 卡插入检测（47 k 上拉，`起航小车_引脚映射.md:23`）。E01_IRQ 是 E01 模块的输出，47 k 上拉只是轻微负载，可接受；但**不要**再把 PD3 当输入配上下拉。

### R5 ADC 脚是否都在 ADC123 上

- 7 个新 ADC 脚：PC3/PC1/PC5/PC0/PA3/PA2/PC4 —— **全部在 ADC1、ADC2 上**。
- 但 **PC4/PC5 只到 ADC12，没有 ADC3**（IN14/IN15）。现状已用 PC4/PC5 采灰度，所以不是新增风险；**若固件原本计划用 ADC3 采灰度，必须改成 ADC1/2**（**待核实**固件当前用的是哪个 ADC）。
- PA2/PA3 的默认复用是 USART2_TX/RX —— 本方案 USART2 走 PD5/PD6，所以 PA2/PA3 可以安全作模拟输入；但初始化顺序上别把 USART2 配到 PA2/PA3。
- 通道号见 §5.1（PC1/PC3 的具体 IN 编号按 F407 数据手册，**本地无原文 → 待核实**；PC0/PC4/PC5/PA2/PA3 的编号有仓库内证据：`pinmap_sky.py:20,39-41`）。

### R6 DMA / 重映射冲突

- **不需要任何 USART 重映射**：6 路 UART 的引脚集合完全不变（PD5/PD6、PD8/PD9、PB6/PB7、PC10/11、PC12/PD2、PC6/PC7，`swapopt.py:31` 与 `pinmap_sky.py:30-34` 相同），变的只是"哪个座"。
- **DMA 若用则要改**：步进总线从 USART2 换到 USART3 → TX/RX 的 DMA Stream/Channel 全变（§5.2 第 7 条，**待核实**）。
- **TIM1 的 DMA**（若用 CCR 预装载 DMA）通道号随 CH 重排。
- **JTAG**：PB4/PA15 仍在用，必须保持 JTAG 关闭（否则 PB4 不能作 TIM3_CH1）。

### R7 上板已布好的线有多少会变飞线

- **网络级**：**62 条现存网络**换端点（62/87 = 71%），另有 **3 条新网络**（`U2_TX/U2_RX/U2_RXD`），`U3_TX/U3_RX/U3_RXD` 注销 → 网络总数仍是 87。
  未变的 **25 条**：4 条电源 + `GND`、6 条 `GRAY*_IN` + `VCC_GRAY`、SPI×3、3 条 `RGB_*K`、`BEEP_B/BEEP_C`、3 条 LED、`STP_L`、`U5_TX`（脚本实测输出）。
- **焊盘级（C 定稿实测）**：**88 个**焊盘要改网络 —— J601 19 + J602 21 + J603 22 + 页 5 的 26（5 座×2=10、3 颗串阻×2=6、4 颗上拉×1=4、3 个 Qwiic×2=6）。
- **几何级无法精确统计**：`board6.json` 里 `routedLines` 是个整数 **2414**（字段含义待核实，可能是线段计数），**没有铜箔几何**，所以算不出"作废多少 mm"。
- **结论（数量级）**：J603 是从 J601/J602 到板中部的主干，22 个脚一换，这 22 条线基本全废；加上 32 个 MCU 脚换位，**实际等于重布大半张板**，原有的"87 网全连通 / DRC 0"不能继承。真正能原样保留的只有那 25 条未变网络。
- **附带**：`ures` 置换还会让 4 条"串口座 RX→串阻"的现成走线失效（R615/R616/R636/R637 与座的配对变了）；若把 `ures` 固定成"每颗串阻跟它现在的座"，可省这 4 条（代价 +19 交叉，见 R11）。

### R8 "保留 38 号脚"对布线收益的影响为什么可能很小

1. **38 号脚本来就是当前唯一空闲槽**：`board6.json:J603` 40 脚里只有 38 的 `net==""`，其余 39 脚全有网络（5V×4 + GND×12 + 信号×23）。
2. **B 用它只是"多一个候选槽"**：B 用完 38 后剩下的空槽是 33（脚本输出 `B free slots: ['33']`）；而 B 相对"J603 完全不动"省了 221 个交叉（878.1 → 635.1），说明 23 个信号本来就需要 23 个槽，38 只是让 B 多一个"落子位"。
3. **量化**：B 380 交叉 / 2551 mm → C 381 / 2548 mm，目标 635.1 → 635.8（**+0.11%**）。相比"J603 完全不动"的 +221 交叉（目标 878.1），保留 38 的代价几乎为零。
4. **它不带来任何电气能力**：上板 38 无铜箔、无 MCU 脚，"保留"只是让下板 J513.37 维持"只连 U201.7+R204"的现状（测试点），不是"以后能直接采 IMON"。

### R9 命名歧义（第二容易错）

见 §3.5。**先定名，再动手**；`U3_TX/U3_RX` 删干净（否则会留单脚网络，跨页检查会报），`USTEP_TX/RX` 上下板同名同义。

### R10 C 定稿前改下板 = 要返工

不是"差 2 个脚"那么轻：实测 16 个 seed 的 J513 改动量是 **21–23 个脚**，而且每个 seed 落点都不同（§2.5 表）。B 的 34/37 之争只是"看起来最小"的那个例子。**先把 C 的 seed 定稿、运行 `swap_diff.py` 出表，再动下板 J513**；否则 20 多个脚要重来一遍，而且改错时**不会有任何报错**（不短路、不违 DRC，只是编码器不转、IMON 丢失、舵机错位）。

### R11 三个"可选置换"的收益/代价（供你裁剪）

用 swapopt 自己的目标函数复算（§7 脚本，只读）：

| 变体 | 交叉 | 飞线长 | 目标 | 相对 B | 代价 |
|---|---|---|---|---|---|
| B（全改） | 380 | 2551 mm | 635.1 | — | — |
| `ures` 保持"串阻跟座" | 399 | 2560 mm | 655.0 | **+19.8** | 省 4 条座→串阻走线 |
| `pull` 保持现状 | 386 | 2549 mm | 640.9 | **+5.8** | 省 4 个焊盘 |
| `qw` 保持现状 | 400 | 2587 mm | 658.7 | **+23.5** | 省 6 个焊盘 |
| `j603` 保持现状（=A） | 601 | 2771 mm | 878.1 | **+242.9** | 省 22 个焊盘 |

→ **J603 置换是唯一"必须做"的**（+243 太贵）；`qw`/`ures`/`pull` 是三个可以商量的中等项。本清单按"全改"写（=B/C）。

### R12 资料同步（不是电气风险，但会误导后人）

- `电赛小车2026_设计方案详解.md:608`、`:786-787` 里引用的主控映射是 v1.0/天猛星时代的旧值（TIM8/TIM9、PC8/PA5 等），交接 §8 已注明过时 —— **改完引脚后必须重生成** `起航小车_引脚映射.md` 与 `qh_core_pinmap.h`（`gen_pindoc.py`），否则文档和板子会打架。
- 丝印：串口座编号全变（J607=DAP、J621=USART6…）、J603 的"38 防呆/方向"标识（交接 §6 第 4 条）。
- `sch/p1–p6.json` 里是旧工程 UUID（交接 §2），改下板时务必用 `0486116d…`。

---

## 7. 验收清单 & 复算脚本

### 7.1 动手前

- [ ] 从 `swap_C_s1..s13` 里**选定一个 seed**（看目标值 + 人眼复核 J603 排布），并用 `swap_diff.py` 重生成 §2/§5 的表；本报告 §2/§5 只是按 B 的模板。
- [ ] 确认定稿 C 的 `best.j603` **不含 "38"**（锁生效，`swap_C_summary.json:19-22` 的 `free_j603` 应为 `[]`）。
- [ ] `交接 §7`：备份（大改前）。

### 7.2 复算/核对脚本（都在 `_tmp_analyze\`，只读，不碰 EDA）

| 脚本 | 作用 | 输出 |
|---|---|---|
| `route/swap_diff.py`（**已有，不是我写的**） | 输入任意 `swap_C_sX.json` → 直接生成"MCU 换脚 / J603→J513 / 其它角色"三段可执行清单 | markdown |
| `_tmp_analyze\plan_c_final.py` | 读 `swap_c.json`，打印 §2.6 三张表（J603/J513/MCU 换脚，含不变量） | 表格 |
| `_tmp_analyze\plan_c_map.py` | 读 `board6.json`+`pinmap_sky.json`+`swap_chosen.json`+`p5.json`，按 `n^1` 生成 §2.1/2.2/2.4 与 J513 差异 | `changed count`、`B free slots` |
| `_tmp_analyze\plan_c_census.py` | 生成完整"新 pad→net"表，统计改动焊盘/网络数、J603 40 脚全表 | `changed pads total`（B 版 = 85；C 版 = 84，再加 §3.5 的 2 个改名）、`nets touched` |
| `_tmp_analyze\plan_c_specs.py` | 打印 §3.7 三份 spec 的 JSON（含位置表） | JSON |
| `_tmp_analyze\plan_c_eval.py` | 把 json 转成 swapopt state 并 `evaluate()`，复现 871/529/380 与 C=381 | 目标值对比 |
| `_tmp_analyze\plan_c_ablate.py` | §6 R11 的四个变体代价 | +19.8/+5.8/+23.5/+242.9 |

> 其中 `census`/`eval`/`ablate` 用 `sys.argv` 喂 `swapopt.py`（只 import，不跑 `__main__`，不写文件）；`map`/`specs` 只读 json。全部不碰 EDA。

### 7.3 改完原理图

- [ ] `sch/verify_file.py` 逐页逐脚（页 1/2/5）。
- [ ] 跨页无单脚网络（重点看 `U3_TX`/`U3_RX` 是否彻底消失、`USTEP_*` 是否只有 J601.29/30 + J603.13/19 + 下板）。
- [ ] J603.38 = NC；J603 其余 39 脚与 §2.3 表 C 列逐字一致。
- [ ] J601/J602 的 40+40 脚与 `n1_planC.json` 逐字一致；NC 标记齐全（J601 10 个、J602 3 个）。
- [ ] 页 3、页 4 **零改动**（`git diff`/文件时间戳核对）。

### 7.4 改完下板

- [ ] J513 只有 §4.1 那 22 脚变了。
- [ ] J513.37 = IMON 且成员 = {U201.7, R204.1}。
- [ ] 下板 1–4、6 页零改动。

### 7.5 改完 PCB6

- [ ] `import-changes` 后 J603.38 无网络、无走线。
- [ ] `pcb drc` 0 违规；`route/conn.py` 87 网全连通。
- [ ] `route/gmetrics.py` 与基线（53.9，`交接 §1`）对比。
- [ ] 叠板实物按 §6 R1 的四条检测走一遍。

---

## 8. 证据索引（本文每条结论的出处）

| 用到的文件 | 行号/字段 | 支撑的结论 |
|---|---|---|
| `电赛小车2026_交接.md` | 33、44-46、49、52-57、62 | J603/J513 几何与奇偶、A/B 方案数字、B 要点、落地步骤、连接器假失败 |
| `route/board6.json` | `components[J603].pads`（40 项）、`routedLines` | J603 现状网络/坐标、38 唯一空脚、布线计数 |
| `route/swap_chosen.json` | `j603`(2-26)、`udest`(27-34)、`ures`(35-41)、`adc`(50-58)、`gpio`(59-78)、`mot`(79-84)、`srv`(85-90)、`enc`(91-95)、`stp`(96-99)、`qw`(100-104)、`pull`(105-109) | 全部目标映射 |
| `route/swap_upper.json` | `j603`(2-26) | 方案 A 的 J603 = 现状（逐条 diff 0 差异） |
| `route/swap_C_s1..s13.json`、`route/swap_C_summary.json` | `best` / `opt` / `locked_j603` / `free_j603` | 真实 C 的 16 个 seed 结果、锁生效的证明（§2.5） |
| `route/swapopt.py` | 26、28-30、31-33、35-36、42-43、45-47、52-59、72-80、97-101、140-145 | 槽位定义、锁 bug 的修复痕迹、UART/编码器/上拉模型、校验规则 |
| `sch/p5.json` | 20-61（特别 57=IMON） | J513 下板现状 |
| `sch/n1.json` / `n2.json` / `n3.json` / `n4.json` / `n5.json` | 各 parts/nets/nc | 各页现状、J603 nc=38 |
| `pinmap_sky.py` | 7-11（SOCK）、14-53（MAP）、54（RESERVED） | S 脚表、现 MCU 脚、AF、空余脚 |
| `起航小车_引脚映射.md` | 5、10、13、23、43-54、67-76 | v2.0 契约、空余脚、USART 分配 |
| `_tmp_analyze/out/evidence_5_叠板与扩展.md` | 31 | J513 40 脚网络表 |
| `电赛小车2026_设计方案详解.md` | 456、599-600、806、1062、1281、1292-1293、1449、1787 | DRV8870 IN1/IN2、IMON 三焊盘、量程、J513 统计 |
| `电赛小车2026_审查处理记录.md` | 56 | R9：IMON 只作测试点 |
| `起航小车_改版需求.md` | 160、166 | 第 37 脚上板不连、IMON 对应 J603.38、奇偶互换 |

**待核实清单**（本文所有"待核实"汇总）：
1. ~~C 的最终 `j603`~~ → 已定稿：`route/swap_c.json`（= seed 10），实表见 §2.6。
2. `board6.json.routedLines=2414` 的确切含义（线段数？网络数？）。
3. PC1/PC3 在 F407 上的确切 ADC 通道编号（本仓库只对 PC0/PC4/PC5/PA2/PA3 有记录）。
4. 固件当前用哪个 ADC（1/2/3）、是否用 DMA 收发步进总线、TIM1 是否配过 CHxN —— 三条都要看固件源码。
5. EasyEDA `import-changes` 在"同一焊盘网络改名"时是否保留既有铜箔（影响 R7 的"4 条串阻走线可留"是否成立）。
