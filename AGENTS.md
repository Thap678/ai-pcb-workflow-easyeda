# AGENTS.md — 给 AI agent 的操作手册

你要用这个仓库，在嘉立创EDA 专业版里替用户从零做出一块能下单的板子。先读完本文件，再按阶段推进。
每个阶段的详细方法在 `docs/`；本文件只写**必须遵守的规则、每步命令和过关标准**。

## 硬规则（每条都来自真实踩坑）

1. **读回为准，不信回执。** easyeda 连接器会「假失败」（写进去了却报失败）和「假成功」（报成功却没写进去）。每次写操作后用读命令（`sch list`、`pcb list`、`pcb dump`、读存盘的 `.esch2`）确认，再决定重试还是继续。不要盲目重发写命令。`kit/common.py` 的 `run()` 只对读命令和幂等写（存盘、移动、锁定、按 id 删除）自动重试，创建类命令（导线、过孔、挖槽、铺铜、导入）只发一次。
2. **大改前先备份，备份放工作区外。** `python kit/backup.py <标签>` 导出整个工程 `.epro2`。重新导入、重新布线、改规则、批量改原理图之前都要做。工作文件夹被删过一次，工程只能整套重建。
3. **永远不下单、不发布。** EasyEDA 有 `placePcbOrder` 等下单接口，任何情况都不要调用；下单、付款、公开发布只能由用户本人做。
4. **给选项，不问开放题。** 需要用户决定时，给 2–3 个现成选项，标出推荐项和代价（时间、成本、风险），让用户选。用户没意见就按推荐走。
5. **不覆盖用户的手工修改。** 用户会在 EDA 里自己改（换封装、改编号、挪元件）。动手前先 `pcb dump` / 读存盘文件，和上次的快照比，找出用户改过的地方；改动要并进规格文件，而不是被脚本冲掉。
6. **每阶段过关才进下一阶段。** 过关标准见下文。没过关就停下来修，或者把问题和选项交给用户。
7. **长任务放后台。** 布线一版 15–35 分钟（单线程），几组参数并行跑（`route.py`、`plane_par.sh`）；不要前台卡住，也不要轮询日志刷屏。
8. **原地改板的 CLI 命令要经过守护脚本。** `pcb auto-place`、`pcb route-short`、`pcb beautify` 失败时不回滚，会留下重叠/出界、短路或断网（issue #4/#5/#6）。要用就跑 `python kit/pcb/guard.py place auto-place ...` / `guard.py route-short ...` / `guard.py beautify ...`：先快照，跑完读回，变差的部分自动退回。中途断掉时用 `guard.py restore-copper <快照>` 恢复。
9. **记录决策。** 用户拍板的方案、参数、例外，写进项目的交接文档（模板见 `templates/requirements.md` 末尾），下一个 agent 接手时先读它。

## 开始之前

```bash
pip install -r requirements.txt
python tools/check_env.py            # 全部 OK 才往下走；daemon 没起：easyeda daemon start（后台常驻）
```

- 用户在 EDA 里建好空工程（每块板一个 Board），保持打开。
- 复制 `board.example.toml` 为 `board.toml`，按 `easyeda doc ls --project <工程名>` 和工程文件 `.eprj3` 填 UUID。工程 UUID 以 `easyeda health` 当前显示的为准（重新打开后可能变），`check_env.py` 会核对。
- 所有 kit 脚本读 `board.toml`（或 `--cfg <路径>` / 环境变量 `KIT_CFG`），多块板用 `--board <名字>`。

## 阶段 1：需求与方案（docs/01）

- 从用户的目标、旧板资料、比赛题目出发，用 `templates/requirements.md` 写需求文档：功能清单按 P0（必须）/P1（应该）/P2（可选）分级，接口、尺寸、安装孔、供电、电流预算都要有数。
- 电源树：每路电压、最大电流、器件（LDO/DC-DC）、线宽按电流估。
- **过关**：用户确认了分级、板子尺寸和孔位、电源方案。

## 阶段 2：选型与引脚（docs/02）

- 每个器件落到 LCSC 料号：`easyeda lib by-lcsc --lcsc C123,C456`、`easyeda lib search --query <型号>`。确认有库、有货、封装和焊盘尺寸合理。
- MCU 引脚分配写成代码（`templates/pinmap_template.py`），自检：0 重脚，外设复用功能都对，EXTI 线不冲突，调试口和板载占用脚不碰。
- **过关**：引脚表自检全过，用户确认关键器件。

## 阶段 3：原理图（docs/03）

1. 每页写一份规格 JSON（格式见 `templates/page_spec.example.json`）：页面 UUID、每个元件（料号、坐标、每个引脚的网络、不接的脚）、电源/地网络名。
2. 离线检查：`python kit/sch/check_specs.py <本板所有页.json>` → `RESULT OK`（无单脚网络、无重复位号、无缺料号）。
3. 建图：`python kit/sch/build_pages.py <页1.json> <页2.json> ...` → 每页 `verify ... 0 problems`。
   - 有脚没连上：`python kit/sch/drv.py <页.json> connect <位号>`，存盘后重跑这一页的核对。
   - 报 `got {''}` 且重连提示 `pin already connected to net "$1N.."`：`python kit/sch/fix_pin.py <页.json> <位号> <脚号>`。
4. **过关**：每页 0 problems；`check_specs` 的网络数和设计一致；多板叠插的连接器逐脚镜像核对过。

## 阶段 4：PCB 导入与布局（docs/04）

```bash
easyeda --project <P> --doc <PCB> pcb import-changes     # 从原理图导入
python kit/pcb/outline.py                                 # 板框 + 安装孔（来自配置）
python kit/pcb/rules.py                                   # 设计规则 + 电源/地线宽规则分配到网络
python kit/pcb/place_table.py placement.csv               # 按坐标表摆放（模板 templates/placement.example.csv）
# 已有布局快照时：python kit/pcb/place_from_dump.py <快照.json>
```

- 先摆机械约束件（连接器、叠板排针、安装位、按键/屏幕），锁定；再按功能分区摆其余件。去耦电容贴着电源脚。
- **核对封装**：库里封装会更新。看焊盘尺寸和间距是否互相重叠（本仓库示例里测试点封装变大后焊盘重叠，导致布不通）。
- 想用 `pcb auto-place` 等自动摆放做种子：`python kit/pcb/guard.py place auto-place [参数]`（新增短路/重叠/出界的件会被挪回，飞线交叉变多则整体撤销）。
- **过关**：`place_table.py` 报告无出界、无压安装孔，重叠项人工确认过；用户看过布局图。

## 阶段 5：布线与铺地（docs/05）

**布线一律用本仓库的成束布线器，不拿 Freerouting 的结果当成品。** 先按板子定一条路，告诉用户：

- **A. 两层板，或电源走线、地两面铺铜的板** → `kit/route/route_bundle.py`（docs/05 第 7 节）。Freerouting 在这里只用来先拿一份电源走法。
- **B. 四层板，内层一层整地、一层按电源分区** → `kit/route/route_plane.py`（docs/05 第 8 节）。先在 `[boards.<板>.planes]` 写好分区、顶层电源铺铜组、宽线、电感禁布。
- 只有 A / B 都布不通、调布局也不行时，才考虑 Freerouting 的成品，**并且先问用户**。

为什么写死：2026-11-07 的下板按旧版本阶段只跑了 Freerouting，被用户否决（直角和斜线一堆、信号线穿过电源铺铜块、内层整层铺一种电源）。成束布线器当时已经做出来、在上板用过，但本阶段的命令里没有它，下板就没用上。

A（两层板）：

```bash
python kit/backup.py before-route
easyeda --project <P> --doc <PCB> pcb export-dsn --name board.dsn   # 文件在 .easyeda/artifacts/
python kit/route/prep_dsn.py <导出的.dsn> prep.dsn
python kit/route/route.py prep.dsn                        # Freerouting 并行几版，只为拿电源走法
python kit/route/route_bundle.py prep.dsn route_runs/<板>/<id>/out.ses bundle.ses   # 信号从引脚重布（~15 min）
python kit/route/ses_smooth.py prep.dsn bundle.ses bundle_s.ses
python kit/route/ses_check.py prep.dsn bundle_s.ses       # 0 间距违规、0 断网
# 导入、直连铺铜、GND 孤岛：按 docs/05 第 7 节（ses2target.py + guard.py restore-copper + cover_check.py + gnd_tie.py）
easyeda --project <P> doc reload <PCB>
easyeda --project <P> --doc <PCB> pcb drc --json --timeout 300 > drc.json
python kit/route/drcsum.py drc.json                       # passed True total 0
python kit/dump.py final_dump.json && python kit/route/gmetrics.py final_dump.json final
python kit/route/render.py --dump final_dump.json --out board.png
```

B（四层板 + 分割电源层）：

```bash
python kit/backup.py before-route
python kit/dump.py live.json
python kit/route/plane_board.py live.json                 # 每个电源区一整块、插件电源脚都在区内；不是就回阶段 4 调布局
bash kit/route/plane_par.sh live.json route_plane_runs "base:" "dir:--layer-dir"   # 每组一个核，~30 min，放后台
python kit/route/plane_fixup.py live.json route_plane_runs/base.json fixed.json     # 补缝合孔、补地线
python kit/route/plane_render.py live.json fixed.json plane.png                    # 给用户看
python kit/route/plane_apply.py live.json fixed.json      # 自动备份 -> 清旧铜 -> 写线/孔/铺铜/禁布区
python kit/route/plane_drc.py                             # reload -> 逐网重铺 -> DRC
python kit/route/plane_check.py --netlist --page <原理图页>   # RESULT: clean
```

- 布线器报 `STALL`（同一根线被拆 12 次）或 `no path`：**先看位置、改布局**（芯片方向、引脚顺序、挡出线口的小件），不要先调参。见 docs/05 第 8 节「布局决定能不能布通」。动原理图引脚（对调网名）要先问用户，并提醒固件跟着改。
- 缝合孔、GND 补线、加宽间距**不要塞进布线器**（会不收敛），布通后由 `plane_fixup.py` 补。
- 不要直接跑 `pcb route-short` / `pcb beautify`：前者会画出贴着异网焊盘的线（0 mil），后者大板会超时、只改一半。确实要用时走 `kit/pcb/guard.py`（见 docs/05 第 5 节）。
- `route.py` 报 `pre-existing violations` 时**先别调参**：输入里有焊盘重叠，回阶段 4 修。
- Freerouting 全部版本都剩几条布不通时，先对比 DSN 焊盘尺寸是否异常（`prep_dsn.py` 只该放大安装孔和无网络的定位柱）。
- 调参一次改一个维度，每批几个版本并行；把前几名的图给用户看，让用户在 2–3 个方向里选。
- **过关**：DRC 0（只剩 Netlist Error 时 `plane_check.py --netlist` 逐脚一致）；A：`ses_check.py` 0 违规 0 断网；B：`plane_check.py` 输出 `RESULT: clean`（含实际灌铜的连通、0 条非 45°、0 个直角、内层无走线、电源铺铜块里无信号线、禁布区下无铜）；用户看过成品图。

## 阶段 6：出板（docs/06）

```bash
python kit/backup.py before-release
python kit/fab/export.py                 # Gerber(含钻孔)、坐标、BOM、IPC-356、工程备份 -> release/<板>_<时间>/
python kit/fab/check.py release/<...>    # 写 release_check.md；FAIL 必须修，WARN 交给用户判断
```

- 把 `release_check.md` 和成品图交给用户。下单表单（板厚、铜厚、颜色、表面处理、是否贴片）由用户在嘉立创填写和下单。
- **过关**：`check.py` 无 FAIL，用户确认 WARN 项。

## 出问题时

- 先看 `docs/07_踩坑清单.md`，大部分症状都在里面。
- 工作区丢了：`docs/08_备份与恢复.md`（`.epro2` 备份 → 会话记录恢复 → 按本流程重建）。
