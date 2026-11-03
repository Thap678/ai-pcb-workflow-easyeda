# 电赛小车2026 重建工具包

2026-11-03 工作区误删后，用这套工具在一个空的嘉立创EDA 工程里重建了两块板的原理图、上板布局和上板布线：

- **原理图**：11 页，逐脚核对 0 问题
- **上板 PCB**：DRC 0，87 个网络全部连通，地面评分 94.2

以后要再建一遍，或者在新工程里复用这套流程，就按本文从上往下做。

> 目录结构不要改：`sch/` 和 `route/` 里的脚本靠相对路径互相引用（`../pinmap_sky.json`、`../sch/del_parts.py` 等）。
> `route/` 下的脚本要在 `route/` 目录里运行。

## 0. 准备

| 东西 | 说明 |
|---|---|
| Python 3 | 用到 `shapely`、`numpy`、`scipy`、`Pillow` |
| easyeda-agent CLI v1.8.1 | 先在后台运行 `easyeda daemon start`，再打开嘉立创EDA 和工程，然后跑 `easyeda health`。返回的 `windows` 不能是空的，否则重载扩展或重启 EDA |
| Freerouting 2.5.0（Java 版） | 用 `freerouting/freerouting-2.5.0-windows-x64.msi` 解包（只解包、不安装），目标目录必须是纯英文路径：`msiexec /a freerouting-2.5.0-windows-x64.msi /qn TARGETDIR=C:\fr_extract`。得到 `C:\fr_extract\freerouting\freerouting.exe`。MSI 的 SHA-256：`f5144acbeefa53248a0e0f2078a13600be73710bb386c013ee11cf0aa9cc10fb`。**不要用原生的 freerouting-cli** |

### 换到新工程时要改的 UUID

脚本里写死了本次工程的 UUID：

| UUID | 指的是 | 出现的文件 |
|---|---|---|
| `5be68595…d691c` | 工程 | `outline.py`、`sch/del_parts.py`、`sch/gen_specC.py`、`route/place_stitch.py` |
| `55840169c52b8bea` | 上板 PCB | `route/apply_ses.py`、`fix_widths.py`、`pour_gnd.py`、`place_stitch.py` |
| 11 个原理图页 UUID | 各原理图页 | `sch/gen_specC.py` 顶部的 `UP` / `LO` |

页面 UUID 用 `easyeda doc ls --project <工程>` 查。页面和原理图的对应关系看工程文件 `.eprj3` 里的 `sheets`。

`sch/tb2.py`（写标题栏）里还是旧页面 UUID，这次没有用到。

## 1. 搭工程骨架（在 EDA 里新建空工程之后）

```bash
easyeda --project <P> board rename --name Board1 --new "上板（主控、传感）"
easyeda --project <P> board rename --name Board2 --new "下板（电源、驱动）"
easyeda --project <P> sch rename --schematic <上板原理图> --name Schematic6
easyeda --project <P> sch page-new --schematic <上板原理图>      # 上板共 5 页，下板共 6 页
easyeda --project <P> sch page-rename --page <页> --name "1_核心板插座"
```

## 2. 原理图（方案 C）

1. 在根目录运行 `python pinmap_sky.py`，重新生成 `pinmap_sky.json`。应输出 62 路信号，0 重脚、0 EXTI 冲突。
2. 运行 `python gen_pindoc.py <输出路径>/起航小车_引脚映射.md`。这一步顺带在同目录生成 `qh_core_pinmap.h`。
3. 在 `sch/` 目录运行 `python gen_specC.py`，生成 11 份整页规格 `c_u1–5.json`（上板）和 `c_l1–6.json`（下板）。
   - 输入：v2 规格（`n1…n5`、`n1_imu`、`p1…p6`）、`pinmap_sky.json`、`route/swap_c.json`。
   - 脚本自检两项：J603 和 J513 逐脚镜像一致；J513.37 = IMON，J603.38 = NC。
4. 运行 `python build_all.py c_u1 c_u2 … c_l6`。
   - 每页依次：放置（会重试）→ 连线 → 打 NC 标记 → 存盘 → `verify_file.py` 读存盘后的 `.esch2` 逐脚核对。
   - 单个元件没连上时，用 `python drv.py c_xx.json connect <位号>` 补连，再存盘、重新核对。
5. 验收标准：每页「0 problems」；上板 87 个网络、下板 100 个网络，都没有单脚网络。

## 3. 上板 PCB

1. 导入元件：`easyeda --project <P> --doc <PCB> pcb import-changes`，两块板各做一次。
2. 板框和孔：`python outline.py <上板PCB> <下板PCB>`。生成 80×87 mm、3 mm 圆角的板框，以及 4 个 M3 孔：(4,4)、(76,4)、(4,83)、(76,83)。
3. 设计规则：`easyeda … pcb drc-rules-set --from route/rules_pcb6_rebuild.json`。
   - 这份规则由 `make_rules.py` 生成：铜间距 0.15 mm，线宽默认 0.25 / 最小 0.15 mm，PWR_5V 1.2 mm，PWR_3V3 0.5 mm，GND 0.3 mm，铺铜间距 0.25 mm。
4. 网络线宽：`python route/set_netrules.py <P> <PCB>`。
   - 分配：5V_MAIN、5V_LOGIC 用 PWR_5V；3V3_MAIN、3V3_RF、VCC_GRAY 用 PWR_3V3；GND 用 GND。
5. 布局：`python route/place_from_dump.py <P> <PCB> route/dumpF.json`。
   - 按 11-01 的快照把 90 个元件放回原位，读回核对后锁定原来锁着的件。

## 4. 上板布线（r9-02 配方）

以下命令都在 `route/` 目录里运行：

```bash
easyeda --project <P> --doc <PCB> pcb export-dsn --name x.dsn   # 导出的文件在 .easyeda/artifacts/
python prep_dsn_g01.py <导出的.dsn> boardR_prep.dsn
python rebuild_route.py              # 4 个版本并行跑，约 5 分钟；挑 0 未布、0 违规、过孔最少的那版
python ses_polish.py batch_runs/rebuild/r9-02a/out.ses --chamfer 0 --out final.ses
python apply_ses.py final.ses        # 删铺铜 → 拆线 → 导入 SES → 恢复线宽
easyeda … pcb dump --include-copper > d.json
python conn.py d.json                # 必须是 0 未连通；不是的话先修再往下
python pour_gnd.py 0.3               # 两面 GND 铺铜
easyeda … pcb dump --include-copper > p.json
python stitch2.py p.json 4           # 缝合孔计划
python place_stitch.py p.json        # 放置缝合孔
easyeda … pcb pour-rebuild
easyeda … pcb save
easyeda --project <P> doc reload <PCB>
easyeda … pcb drc --json --timeout 300 > drc.json
python drcsum.py drc.json
easyeda … pcb dump --include-copper > final.json
python conn.py final.json
python gmetrics.py final.json label
python fast_render.py --dump final.json --out board.png   # 出成品图
```

r9-02 的参数：两层都偏好水平走线，过孔代价 70，顶层 1.0/2.1，底层 3.5/4.5，plane_via 5，ripup 100，passes 100，关闭优化器。另外设环境变量 `FREEROUTING__ROUTER__COPPER_TO_EDGE_CLEARANCE_UM=450`，让铜离板边至少 0.45 mm。

## 5. 踩过的坑（都已改进脚本或写进流程）

- **预处理会误放大排针焊盘**：`prep_dsn_g01.py` 会把不接网络的孔放大 0.1 mm，但 NC 排针脚和接了网络的脚共用同一种焊盘定义。旧版脚本靠硬编码旧工程的焊盘 ID 跳过排针，换工程后失效，161 个焊盘被放大，整板布不通。现在改为只放大「只被不接网络的脚使用」的焊盘定义。
- **改坐标时带上 `layer` 会写入失败**：`pcb modify` 的补丁如果同时带 `layer` 和 x/y，不会生效，连接器还会报告成功。先单独发 `{"layer":2}` 换面，再写坐标。
- **测试点不要用 RH-5015**：库里 RH-5015（C5199798）的焊盘现在是 2.8×1.5 mm，2.5 mm 间距下会互相重叠。用系统库的 Test-Point-0.5mm（1×1 mm 焊盘）。
- **JY901S 要用器件 UUID**：放置时用 `e5c3754273754e7cbae712375e68caf4`，符号 UUID 放不上去。
- **后处理可能拉断线**：`ses_polish.py` 把平行线归拢成股时，可能把和另一根线 T 型相接的那段挪开，导致断线。所以导入后一定要先跑 `conn.py`。这次断了 EC11_B 一处，补了一段 0.33 mm 的短线。
- **缝合孔不避让焊盘**：`stitch2.py` 不检查焊盘，缝合孔可能贴到排针焊盘上。DRC 报出来以后用 `pcb via-delete --ids …` 删掉。这次删了 3 个。
- **连接器的假失败和假成功**：写操作返回失败时可能其实已经写进去了，返回成功时也可能没写进去。一律以读回的结果为准。
- **随时备份**：`easyeda project export --window <W> --project-uuid <P> --out <路径>.epro2` 可以导出整个工程，备份要放在工作区之外。

## 6. 从会话记录恢复文件（`恢复/`）

工作区再次丢失时，可以从两类会话记录里把写过、读过的文件拼回来：

- Claude Code 的记录：`~/.claude/projects/<目录>/*.jsonl`
- 另一个 agent 的记录：`~/.dsh/sessions/<目录>/*/session.v4.jsonl.zstd`

步骤：

1. `node dsh_dump.js <dsh会话目录> <输出目录>`：把 zstd 压缩的记录解压成 jsonl。用 Node 24 自带的 zstd，按帧拆开解压。
2. `python replay.py <输出目录> <claude jsonl…> -- <dsh jsonl…>`：按时间顺序回放写入、编辑和完整读取，还原 `02_硬件设计` 下的文件，并生成 `_report.csv`。
   - 标为 BROKEN 的文件，中间有过命令行改动，记录里看不到，需要人工补。
3. `~/.claude/file-history/<会话>/` 里还有 Claude 改文件前的备份，可以作为补充。

## 参考数据（`参考数据/`）

- `board6_rebuild_release.json`、`gm_rebuild_release.json`、`rebuild_release.png`：这次重建完成的上板快照、地面评分和成品图。
- 方案 C 的落地清单和落地记录。
- 第 2 轮批量布线调参的记录。

## 发布说明（私有归档）

本仓库是**个人私有归档**，用于备份这套重建流程与它的验收数据，不是公开项目。仓库内脚本由本人编写，未附 LICENSE；`参考数据/` 中的板子快照与成品图属自有产物。请勿转载。

当前入库 **70 个文件、约 3.4 MiB**（源目录全量为 97 个文件、88.26 MiB）。差异全部来自下列排除项，**脚本、11 页原理图规格 JSON、`参考数据/` 与 `恢复/` 均完整保留**。

### Freerouting 需要自行下载（安装包不入库）

`freerouting/freerouting-2.5.0-windows-x64.msi`（84.61 MiB / 88,718,425 字节）是第三方二进制，**已从本仓库移除**，见 `.gitignore` 第 1 节。

- 下载页：https://github.com/freerouting/freerouting/releases
- 期望版本：**2.5.0（Java 版）**，资产名 `freerouting-2.5.0-windows-x64.msi`。本文第 4 节的路由配方对布线器代价模型敏感，**不要**换成别的大版本后直接复用 `route/` 下的参数。
- 校验：解包前先确认 SHA-256 为
  `f5144acbeefa53248a0e0f2078a13600be73710bb386c013ee11cf0aa9cc10fb`
  （与本文第 0 节一致）。不一致说明上游资产变了，请停下确认。
- 解包方式与目标路径（`C:\fr_extract\freerouting\freerouting.exe`）仍按本文第 0 节执行，此处不再重复。

### 未纳入本仓库的缓存与日志

| 类别 | 数量 | 体积 |
|---|---|---|
| `freerouting/*.msi` | 1 | 84.61 MiB |
| `__pycache__/`、`*.pyc`（根 4 / `route/` 19 / `sch/` 6） | 29 | 0.28 MiB |
| `*.log` / `*.ses` / `*.dsn`（运行时生成） | 当前 0 | — |

### 运行环境

- **CPython 3.12**（原 `.pyc` 的 `cpython-312` 标签可证）。依赖见 `requirements.txt`：`shapely`、`numpy`、`scipy`、`Pillow`。
- **`easyeda-agent` CLI v1.8.1**：本仓库不包含该 CLI，也未记录其获取渠道，请按其发布说明自行安装，并先执行 `easyeda daemon start` 再跑任何脚本。
- **`恢复/replay.py` 需要环境变量 `REPLAY_ROOT`**：脚本只回写 `REPLAY_ROOT` 之下的文件（未设置时默认取当前目录下的 `02_硬件设计`）。在资料库根目录运行时：

  ```powershell
  $env:REPLAY_ROOT = (Resolve-Path '.\02_硬件设计').Path
  ```

### 关于路径与引用的两点免责

1. 本仓库的脚本按"在 `route/` 目录内运行"编写，且 `sch/` 与 `route/` 之间存在 `../pinmap_sky.json`、`../route/swap_c.json` 这类相对引用。clone 后**不要只取单个脚本**，也不要改动目录层级。
2. `参考数据/` 下的三份 `.md` 是当时的审计台账，其中引用的 `route/board6.json`、`swap_chosen.json`、`_tmp_analyze/*`、`qh_core_pinmap.h` 等约 63 个文件属原始工作区，**未随本工具包发布**；相关结论请以 `route/dumpF.json` 与 `参考数据/board6_rebuild_release.json` 为准。本文第 118 行提到的 `_report.csv` 是 `恢复/replay.py` 的**输出**，不是仓库内文件。
