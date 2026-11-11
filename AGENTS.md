# AGENTS.md — 给 AI agent 的入口

你要用这个仓库，在嘉立创EDA 专业版里替用户从零做出一块能下单的板子。
操作手册已经拆成技能（`skills/`）。支持技能的 agent（Claude Code、Codex 等）会按用户的话自动加载；不支持的，按下面的顺序自己读。

## 怎么读

1. **先读 [skills/pcb-workflow/SKILL.md](skills/pcb-workflow/SKILL.md)**：开工步骤、怎么判断项目在哪个阶段、硬规则、和用户协作的规矩、交接文档。
2. 再读当前阶段的技能：

| 阶段 | 技能 | 详细方法 |
|---|---|---|
| 1–2 需求、方案、选型、引脚 | [skills/pcb-requirements](skills/pcb-requirements/SKILL.md) | docs/01、docs/02 |
| 3 原理图 | [skills/pcb-schematic](skills/pcb-schematic/SKILL.md) | docs/03 |
| 4 导入、板框、规则、布局、飞线优化 | [skills/pcb-placement](skills/pcb-placement/SKILL.md) | docs/04 |
| 5 布线、铺铜、DRC | [skills/pcb-routing](skills/pcb-routing/SKILL.md) | docs/05 |
| 6 出板 | [skills/pcb-release](skills/pcb-release/SKILL.md) | docs/06 |

3. 出问题查 `docs/07_踩坑清单.md`；改技能前看 `docs/09_规矩的来由.md`；工作区丢了看 `docs/08_备份与恢复.md`。

## 三条最要紧的

- **用户不该需要引导你。** 用户说一句「做板子」或「继续」，你自己判断阶段、自己找下一步、自己跑检查；只在需要用户拍板和交付阶段成果时开口，开口就给 2–3 个选项和推荐。
- **读回为准，大改前备份，不覆盖用户在 EDA 里的手工修改。**
- **永远不下单、不发布。** 下单、付款、公开发布只能由用户本人做。

## 改这个仓库时

- 技能里只写**规矩、步骤、过关标准**；命令的细节、参数来历、实测数据放 `docs/`；可执行的东西放 `kit/`。
- 用户纠正过 agent 的地方，要落到技能里（哪个阶段的就写进哪个技能），并尽量落成 `kit/` 里的检查，而不是只写一句提醒。
- 每个阶段技能的命令必须和 `kit/` 里的脚本一致；新增脚本后把它写进对应技能，否则 agent 不会用它（成束布线器就这样被漏过一次）。
