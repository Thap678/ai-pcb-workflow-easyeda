---
name: pcb-release
description: 嘉立创EDA 专业版 PCB 的出板文件导出和出板检查（阶段 6）：Gerber、钻孔、BOM、贴片坐标、IPC-356、工程备份，自动检查后交给用户下单。用户说「出板」「导出 Gerber」「准备下单」「打样」时用。绝不下单：下单、付款只能用户本人做。Export and check fabrication files for an EasyEDA Pro board; never places the order.
---

# 阶段 6：出板

先读 `pcb-workflow` 技能里的硬规则和协作规矩（本次会话读过就不用再读）。`ROOT` = 本文件所在目录的上两级。
详细说明：`ROOT/docs/06_出板.md`。

## 绝不下单

EasyEDA 有 `placePcbOrder`、`placeComponentsOrder` 等接口。任何情况都不调用，用户口头授权也不调用。
下单、付款、公开发布由用户本人在嘉立创完成。你的工作到「文件和检查报告交到用户手里」为止。

## 做法

```bash
python <ROOT>/kit/backup.py before-release
python <ROOT>/kit/fab/export.py                      # -> release/<板>_<时间>/
python <ROOT>/kit/fab/check.py release/<板>_<时间>    # 写 release_check.md
```

前提：阶段 5 已过关（DRC、连通检查）。没过关不导出。

- `FAIL` 必须修，修完重新导出。
- `WARN` 逐条交给用户判断，每条说明是什么、通常要不要紧（例如测试点没有 LCSC 料号是正常的）。

## 交给用户的东西

1. 出板文件夹的路径和 `release_check.md`。
2. 成品图（顶层、底层）。
3. 一份出板说明（出板文件夹里的 `README.md`），写清：
   - 下单时要填的参数：层数、板厚、铜厚（内层铜厚有要求时单独写明）、是否贴片、贴片面；
   - **固件要跟着改的地方**（做板过程中对调过的引脚、反过的方向，从交接文档抄过来）；
   - 已知的例外：DRC 里核实过可以接受的项、需要手焊的件；
   - 多块板时：叠板连接器的公母和高度，两块板一起下。
4. 下单前请用户自己做的检查：在嘉立创 Gerber 查看器里逐层看一遍；丝印的位号、极性、1 脚标记、版本号和日期。

## 过关

- `check.py` 无 FAIL；
- WARN 项用户确认过；
- 出板说明写好；交接文档更新到「已出板，未下单」。
