# AI 画板技能包（嘉立创EDA 专业版）

> **English summary.** A field-tested set of agent skills (plus the scripts they run) for letting an AI agent
> design a PCB end to end in EasyEDA Pro (嘉立创EDA 专业版): requirements → part selection and pin assignment →
> schematic built from JSON page specs and verified pin by pin → placement improved by ratsnest → routing with
> the kit's own bundling routers (2-layer, and 4-layer with a split power plane) → pours, stitching, DRC →
> Gerber / drill / BOM / pick-and-place export with an automated release check. It drives EasyEDA through the
> `easyeda-agent` CLI. Proven on two real 80 × 87 mm boards: a 2-layer STM32F407 controller board and a 4-layer
> motor / power board. Install it as a Claude Code plugin (see "快速开始"), then just say "make me a board" or
> "continue". Skills and docs are in Chinese.

这是一套**让 AI agent 从零画出一块能下单的板子**的技能和工具。装上以后，你只需要说「帮我做一块板子」或「继续」：agent 自己判断做到了哪一步、自己往下推，只在需要你拍板时给你 2–3 个选项。

它不是概念演示：里面每一步都在真实的板子上跑通过；技能里的每条规矩，都是做板过程中用户纠正 agent 之后留下来的，踩过的坑已经改进成脚本或写进了检查项。

**实际战绩**（示例 [examples/nuedc-car-2026](examples/nuedc-car-2026)）：电赛小车上板，2 层、80 × 87 mm、天空星 STM32F407 核心板插座 + 传感/交互/串口，90 个元件、87 个网络。

- 原理图 11 页（上下两块板），每页读存盘文件逐脚核对，0 问题
- 上板：DRC 0，87/87 网络连通，两面 GND 铺铜 + 缝合孔，地面评分 94.2
- 工作区丢失后，用这套流程**一天内**从空工程重建到上述状态
- 下板（4 层，内层一层整地、一层按 5 路电源分区）：成束布线器 87/87 布通，全 45°，DRC 只剩 1 条逐脚核实过的 Netlist Error

## 流程总览

```
需求 ──► 方案/选型 ──► 引脚分配 ──► 原理图规格(JSON) ──► 建图+逐脚核对
                                                          │
出板文件+自动检查 ◄── DRC+连通+评分 ◄── 铺地+缝合 ◄── 布线(自研成束布线器，多参数并行) ◄── 布局 ◄── 导入PCB+板框+规则
```

| 阶段 | 人做什么 | AI 做什么 | 工具 | 过关标准 |
|---|---|---|---|---|
| 1 需求与方案 | 回答选择题、拍板 | 写需求文档、电源方案、给 2–3 个选项 | [templates/requirements.md](templates/requirements.md) | 需求分级（P0/P1/P2）都已确认 |
| 2 选型与引脚 | 确认关键器件 | 查 LCSC 料号/库存，写引脚表并自检 | `easyeda lib`、[templates/pinmap_template.py](templates/pinmap_template.py) | 0 重脚、0 外设冲突 |
| 3 原理图 | 看图 | 写每页规格 JSON → 建图 → 读存盘文件核对 | `kit/sch/` | 每页 0 problems；0 单脚网络 |
| 4 布局 | 定机械约束、调整 | 按功能分区出坐标表、摆放、查重叠 | `kit/pcb/` | 无重叠、无出界、不压安装孔 |
| 5 布线与铺地 | 看前几名快照选方向 | 成束布线器（两层板 `route_bundle.py`，四层分割电源层 `route_plane.py`）多参数并行、补缝合孔、导入、铺铜、核对 | `kit/route/` | 0 未布、DRC 0、含灌铜的连通 0 断开、全 45° |
| 6 出板 | 下单（只能人做） | 导出 Gerber/钻孔/BOM/坐标/网表 + 自动检查 + 备份 | `kit/fab/` | release_check 无 FAIL |

每个阶段的规矩和过关标准在 [skills/](skills/)，做法、命令和坑写在 [docs/](docs/)。

## 技能

| 技能 | 什么时候用 |
|---|---|
| [pcb-workflow](skills/pcb-workflow/SKILL.md) | 总入口：「做板子」「继续」。判断阶段、硬规则、和用户协作的规矩、交接文档 |
| [pcb-requirements](skills/pcb-requirements/SKILL.md) | 需求分级、电源架构、选型、引脚表 |
| [pcb-schematic](skills/pcb-schematic/SKILL.md) | 规格 JSON → 建图 → 逐脚核对 |
| [pcb-placement](skills/pcb-placement/SKILL.md) | 板框、规则、布局、布线前按飞线优化 |
| [pcb-routing](skills/pcb-routing/SKILL.md) | 成束布线、铺铜、缝合孔、DRC 和连通检查 |
| [pcb-release](skills/pcb-release/SKILL.md) | 出板文件和检查；不下单 |

## 快速开始

1. **准备环境**（Windows）：嘉立创EDA 专业版 V4.1.60+、easyeda-agent CLI v1.8.x、Python 3.11+、Freerouting 2.5.0。详见 [docs/00_准备环境.md](docs/00_准备环境.md)。
   ```bash
   pip install -r requirements.txt
   python tools/check_env.py
   ```
2. **建空工程**：在嘉立创EDA 里新建工程（每块板一个 Board = 原理图 + PCB），保持打开。
3. **装技能**（Claude Code）：
   ```
   /plugin marketplace add Thap678/ai-pcb-workflow-easyeda
   /plugin install ai-pcb@ai-pcb-workflow-easyeda
   ```
   其他 agent（Codex 等）：`git clone` 本仓库，让 agent 读 [AGENTS.md](AGENTS.md)；它会指到 `skills/`。
4. **说一句话**：在你的项目文件夹里打开 agent，说「帮我做一块板子」，或者项目做到一半时说「继续」。配置文件 `board.toml` 由 agent 自己从 [board.example.toml](board.example.toml) 复制并填好。之后你只需要在每个阶段末尾看图、做选择。
5. **自己跑**：每个阶段的命令都能手动执行，见 [docs/](docs/) 或示例 [examples/nuedc-car-2026/README.md](examples/nuedc-car-2026/README.md)。

## 仓库结构

| 路径 | 内容 |
|---|---|
| [skills/](skills/) | 六个技能：总流程 + 五个阶段。规矩、步骤、过关标准 |
| `.claude-plugin/` | Claude Code 插件清单（整个仓库就是一个插件） |
| [AGENTS.md](AGENTS.md) | 不支持技能的 agent 的入口，指向 `skills/` |
| [board.example.toml](board.example.toml) | 板子配置模板（所有脚本都读它，不再写死 UUID 和尺寸） |
| [docs/](docs/) | 分阶段方法说明 + 踩坑清单 + 备份与恢复 |
| [templates/](templates/) | 需求文档、引脚表、原理图页规格、布局坐标表的模板 |
| `kit/common.py` | 读配置、调用 easyeda（重试/超时）、单位换算、PCB 快照 |
| `kit/sch/` | 原理图：规格检查、逐页建图、读存盘文件逐脚核对、删件 |
| `kit/pcb/` | 板框和孔、设计规则 + 网络线宽、按坐标表摆放、按快照复位；`ratsnest.py`：飞线长度和交叉报告，布线前优化布局用；`guard.py`：给 auto-place / route-short / beautify 加快照、读回和自动回滚，按快照恢复走线 |
| `kit/route/` | DSN 预处理、Freerouting 多版本并行、成束布线（`route_bundle.py`，低速板「线成束、地成片」；`route_plane.py`，四层板内层电源分割，配套 `plane_fixup` / `plane_apply` / `plane_drc` / `plane_check`）、SES 检查与拉直、后处理、导入、铺地、缝合、GND 孤岛补接、连通、地面评分、出图 |
| `kit/fab/` | 出板导出（Gerber/钻孔/BOM/坐标/IPC-356 + 工程备份）和出板检查 |
| `kit/backup.py`、`kit/recover/` | 工程备份；工作区丢失后从 AI 会话记录恢复文件 |
| `tools/check_env.py` | 环境自检 |
| [examples/nuedc-car-2026/](examples/nuedc-car-2026/) | 完整示例：配置、11 页规格及其生成脚本、布局快照、成品数据和图 |

## 适用范围和限制

- **目前仍处于开发阶段**，这套workflow仍有需要提升的地方，不过他已经能基本完成一个任务了。
- **只支持嘉立创EDA 专业版**，通过 easyeda-agent CLI 操作，需要 EDA 窗口开着。
- **2 层板全流程已实测**；4 层板实测过「内层一层整地、一层按电源分区」这一种叠层（`route_plane.py`），其他叠层没有试过。
- 自动布线适合中低密度数字/控制板。**射频、高速差分、大电流功率回路要人工布**或至少人工审查（`kit/route/` 只做通用规则）。
- 器件封装以嘉立创库为准；库里的封装会更新（本示例的测试点就变过），**布局前要核对焊盘尺寸**。
- 本工具包**不会下单**：EasyEDA 有下单接口，脚本从不调用，下单必须由人在嘉立创完成。

## 许可

本仓库以 [MIT 许可证](LICENSE) 发布：可以自由使用、修改、分发和商用，保留版权声明即可。

第三方软件不随本仓库分发，各自遵循自己的许可：

- **Freerouting**：GPL-3.0，需自行下载（见 [docs/00](docs/00_准备环境.md)）。本仓库只通过命令行调用它，不包含它的代码。
- **嘉立创EDA 专业版、easyeda-agent CLI**：按各自的用户协议使用。示例里引用的元件库 UUID、LCSC 料号指向嘉立创/立创商城的库，库数据本身不在本仓库里。
