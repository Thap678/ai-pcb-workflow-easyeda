# AGENTS.md — 给 AI agent 的操作手册

你要用这个仓库，在嘉立创EDA 专业版里替用户从零做出一块能下单的板子。先读完本文件，再按阶段推进。
每个阶段的详细方法在 `docs/`；本文件只写**必须遵守的规则、每步命令和过关标准**。

## 硬规则（每条都来自真实踩坑）

1. **读回为准，不信回执。** easyeda 连接器会「假失败」（写进去了却报失败）和「假成功」（报成功却没写进去）。每次写操作后用读命令（`sch list`、`pcb list`、`pcb dump`、读存盘的 `.esch2`）确认，再决定重试还是继续。不要盲目重发写命令。
2. **大改前先备份，备份放工作区外。** `python kit/backup.py <标签>` 导出整个工程 `.epro2`。重新导入、重新布线、改规则、批量改原理图之前都要做。工作文件夹被删过一次，工程只能整套重建。
3. **永远不下单、不发布。** EasyEDA 有 `placePcbOrder` 等下单接口，任何情况都不要调用；下单、付款、公开发布只能由用户本人做。
4. **给选项，不问开放题。** 需要用户决定时，给 2–3 个现成选项，标出推荐项和代价（时间、成本、风险），让用户选。用户没意见就按推荐走。
5. **不覆盖用户的手工修改。** 用户会在 EDA 里自己改（换封装、改编号、挪元件）。动手前先 `pcb dump` / 读存盘文件，和上次的快照比，找出用户改过的地方；改动要并进规格文件，而不是被脚本冲掉。
6. **每阶段过关才进下一阶段。** 过关标准见下文。没过关就停下来修，或者把问题和选项交给用户。
7. **长任务放后台。** Freerouting 一版要 5–20 分钟，并行跑；不要前台卡住，也不要轮询日志刷屏。
8. **记录决策。** 用户拍板的方案、参数、例外，写进项目的交接文档（模板见 `templates/requirements.md` 末尾），下一个 agent 接手时先读它。

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
- **过关**：`place_table.py` 报告无出界、无压安装孔，重叠项人工确认过；用户看过布局图。

## 阶段 5：布线与铺地（docs/05）

```bash
python kit/backup.py before-route
easyeda --project <P> --doc <PCB> pcb export-dsn --name board.dsn   # 文件在 .easyeda/artifacts/
python kit/route/prep_dsn.py <导出的.dsn> prep.dsn
python kit/route/route.py prep.dsn                        # 配置里的多个参数版本并行；看排名
python kit/route/ses_conn.py prep.dsn route_runs/<板>/<最好的id>/out.ses   # 0 open nets
python kit/route/ses_polish.py <out.ses> --dsn prep.dsn --chamfer 0 --out final.ses
python kit/route/ses_conn.py prep.dsn final.ses           # 后处理后仍是 0 open nets
python kit/route/apply_ses.py final.ses                   # 导入，恢复线宽，末尾自动跑连通检查
python kit/route/pour_gnd.py                              # 两面铺地
python kit/dump.py poured.json
python kit/route/stitch.py poured.json                    # 缝合孔（避开焊盘），重建铺铜并存盘
easyeda --project <P> doc reload <PCB>
easyeda --project <P> --doc <PCB> pcb drc --json --timeout 300 > drc.json
python kit/route/drcsum.py drc.json                       # passed True total 0
python kit/dump.py final_dump.json
python kit/route/conn.py final_dump.json                  # nets with unrouted connections: 0
python kit/route/gmetrics.py final_dump.json final        # 地面评分（越高越好）
python kit/route/render.py --dump final_dump.json --out board.png
```

- `route.py` 报 `pre-existing violations` 时**先别调参**：输入里有焊盘重叠，回阶段 4 修。
- 全部版本都剩几条布不通时，先对比 DSN 焊盘尺寸是否异常（`prep_dsn.py` 只该放大安装孔和无网络的定位柱）。
- 调参一次改一个维度（过孔代价、底层代价、方向），每批几个版本并行；把前三名的图给用户看，让用户在 2–3 个方向里选。
- **过关**：DRC 0、`conn.py` 0 断开、用户看过成品图。

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
