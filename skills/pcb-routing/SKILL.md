---
name: pcb-routing
description: 嘉立创EDA 专业版 PCB 的布线、铺铜、缝合孔和 DRC（阶段 5），用本仓库的成束布线器：两层板 route_bundle.py，四层板内层电源分割 route_plane.py。用户说「帮我把线连上」「走线，铺铜」「开始布线」「优化走线和过孔」「提升地面完整度」「扩大铺铜面积」「电源层怎么分」「跑 DRC」，或者否决了一版布线时用。Routing, copper pours, stitching and DRC for an EasyEDA Pro board with the bundling routers of this kit.
---

# 阶段 5：布线与铺铜

先读 `pcb-workflow` 技能里的硬规则和协作规矩（本次会话读过就不用再读）。`ROOT` = 本文件所在目录的上两级。
详细方法、参数来历、实测数据：`ROOT/docs/05_布线与铺地.md`（两层板看第 7 节，四层分割电源层看第 8 节）。

## 开始之前

1. 布局过关了吗？没做过飞线优化（`kit/pcb/ratsnest.py`）就先回 `pcb-placement` 做。
2. `python <ROOT>/kit/backup.py before-route`。
3. 定一条路，用一句话告诉用户：
   - **A. 两层板**，或电源走线、地两面铺铜的板 → `kit/route/route_bundle.py`；
   - **B. 四层板**，内层一层整地、一层按电源分区 → `kit/route/route_plane.py`。

**布线一律用成束布线器。** Freerouting 在 A 里只用来拿一份电源走法，它的结果不当成品。只有 A、B 都布不通、调布局也不行时才考虑 Freerouting 的成品，并且先问用户。

## 用户要的是什么样的板子

这些是验收标准。布出来不符合，用户会否决整版：

- **低速板不追求最短线。** 线长不重要；要的是**线成束、等距并行，地成片**。孤零零的线把铺铜切碎是最难看的。
- **铺铜面积和地面完整度是目标**，不是顺带的事：大块、连续、少被切开。
- **过孔要少。** 过孔多的根因是短跳线（每遇一次交叉钻一对孔），不是底层走线本身。
- **全部 45°。** 不许有直角，不许有任意角度的斜线。
- **电源层不整层铺一种电源。** 四层板的电源层按电源分区：每路一整块，区间留缝。整层铺一种会把电机电源的噪声带到逻辑区下面。
- **电源走电源的地方。** 大电流用铺铜块（灌注），不用细线拉；电源脚打孔进自己的分区。
- **信号线不穿过电源铺铜块。**
- **电感下方禁布**（地层保留作屏蔽）。
- **散热孔是地孔**：网络设成 GND。
- **铺铜必须真连通。** 给孤立的碎铜打一两个孔挂到地上没有意义：最大整块一点没变，只是评分好看。孤岛要么有实在的连接，要么去掉。

## A. 两层板

```bash
easyeda --project <P> --doc <PCB> pcb export-dsn --name board.dsn      # 文件在 .easyeda/artifacts/
python <ROOT>/kit/route/prep_dsn.py <导出的.dsn> prep.dsn
python <ROOT>/kit/route/route.py prep.dsn                              # Freerouting 并行几版，只为电源走法（后台）
python <ROOT>/kit/route/route_bundle.py prep.dsn route_runs/<板>/<id>/out.ses bundle.ses   # 信号从引脚重布，约 15 分钟（后台）
python <ROOT>/kit/route/ses_smooth.py prep.dsn bundle.ses bundle_s.ses
python <ROOT>/kit/route/ses_check.py prep.dsn bundle_s.ses             # 0 间距违规、0 断网
```

导入、直连铺铜、GND 孤岛补接、DRC、评分：按 `docs/05` 第 7 节和第 4 节的命令做（`ses2target.py` → `guard.py restore-copper` → `cover_check.py` → `pour_gnd.py` → `gnd_tie.py` → reload → DRC → `gmetrics.py` → `render.py`）。

## B. 四层板 + 内层电源分割

先在 `[boards.<板>.planes]` 写好分区、顶层电源铺铜组、宽线、电感禁布（例子见 `ROOT/board.example.toml`）。

```bash
python <ROOT>/kit/dump.py live.json
python <ROOT>/kit/route/plane_board.py live.json            # 每个区一整块、插件电源脚都在区内；不是就回布局
bash <ROOT>/kit/route/plane_par.sh live.json route_plane_runs "base:" "dir:--layer-dir"   # 每组一个核，约 30–45 分钟（后台）
python <ROOT>/kit/route/plane_fixup.py live.json route_plane_runs/<先布通的那组>.json fixed.json   # 补缝合孔、补地线
python <ROOT>/kit/route/plane_render.py live.json fixed.json plane.png     # 给用户看
python <ROOT>/kit/route/plane_apply.py live.json fixed.json               # 自动备份 -> 清旧铜 -> 写线/孔/铺铜/禁布区
python <ROOT>/kit/route/plane_drc.py                                      # reload -> 逐网重铺 -> DRC
python <ROOT>/kit/route/plane_check.py --netlist --page <原理图页 UUID>     # RESULT: clean
```

配置里的 `router_clearance_mm` 如果是给 Freerouting 留了余量的值（例如 0.22），给 `route_plane.py` 加 `--clearance 0.2`。

## 跑的时候

- **放后台，并行。** A* 是单线程，一组参数占一个核。几组一起跑，谁先布通用谁。
- **调参一次只改一个维度。**
- **布不通先看位置，再改布局，最后才调参。** `STALL`（同一根线被拆 12 次）、`no path`、40 分钟不收敛，都先去看卡住的网被什么挡着，回 `pcb-placement`。告诉用户时说人话：哪个件、哪根线、为什么过不去、你建议怎么改、需不需要用户决定。
- **缝合孔、GND 补线、加宽间距不放进布线器**（会不收敛）。布通之后由 `plane_fixup.py` 补。
- 不用加权 A*（用户否决过）。
- 不直接跑 `pcb route-short` / `pcb beautify`；确实要用时走 `kit/pcb/guard.py`。
- `route.py` 报 `pre-existing violations`：输入里有焊盘重叠，回布局修，别调参。

## 给用户看

- 写进 EDA 之前先出图（`plane_render.py` / `render.py`），停下来等用户选（停点 S5）。有几版可选时，把前几名的图并排给用户，每版一句话说明差别，让用户选。
- 布通之后想再优化走线、过孔、铺铜：先只出办法（停点 S6）。同一办法两轮没改善、或要写新算法：停下来给方向，新办法先做小实验（停点 S7）。
- 用户否决一版时，把否决的理由逐条记进交接文档，下一版逐条对照。

## 写进 EDA 之后

顺序固定：**`doc reload` → 逐个网络重铺 → DRC → 检查**。批量写入后不 reload 就跑 DRC，会虚报几百条「走线未连接」。
跑 DRC 前把 EDA 窗口放到前台，只跑一次。
DRC 剩下的零星间距问题改走法，不要改线宽（电源网的线宽规则会再报一次）。

## 过关

- DRC 0。只剩 Netlist Error 时，`plane_check.py --netlist` 逐脚一致才算过；
- A：`ses_check.py` 0 违规、0 断网；
- B：`plane_check.py` 输出 `RESULT: clean`。它查的是 EDA 的 DRC 不查或查不准的东西：含实际灌铜的连通、0 条非 45°、0 个直角、内层无走线、电源铺铜块里无信号线、禁布区下无铜；
- 上面「用户要的是什么样的板子」逐条对照过（能由 `plane_check.py` / `ses_check.py` 查的以脚本结论为准，只人工看脚本不查的几条）；
- 用户看过成品图；
- `python <ROOT>/kit/backup.py routed`。

过关后转到 `pcb-release`。
