---
name: pcb-schematic
description: 在嘉立创EDA 专业版里建原理图并逐脚核对（阶段 3）：每页写规格 JSON，脚本落图，读存盘文件比对每个引脚的网络。用户说「开始画原理图」「修一下原理图」「原理图里我换了封装/测试点」「删掉这几个元件」「核对一下连接」时用。Build and verify EasyEDA Pro schematics from JSON page specs.
---

# 阶段 3：原理图

先读 `pcb-workflow` 技能里的硬规则和协作规矩（本次会话读过就不用再读）。`ROOT` = 本文件所在目录的上两级。
详细方法和规格字段说明：`ROOT/docs/03_原理图.md`。

原理图不是在画布上「画」出来的：**先写成数据，脚本落到 EDA，再从存盘文件读回来逐脚比对**。

## 做法

1. **每页写一份规格 JSON**（格式见 `ROOT/templates/page_spec.example.json`）：页面 UUID、每个元件的料号、坐标、每个引脚的网络、不接的脚。每个引脚必须出现在 `nets` 或 `nc` 里。
   规格最好由一个生成脚本产出（引脚表 JSON → 页面规格），以后改引脚只改一处。
2. **离线检查**，`RESULT OK` 才去碰 EDA：
   ```bash
   python <ROOT>/kit/sch/check_specs.py <这块板的所有页.json>
   ```
   它报单脚网络（几乎都是笔误）、重复位号、缺料号。
3. **建图**：
   ```bash
   python <ROOT>/kit/backup.py before-sch
   python <ROOT>/kit/sch/build_pages.py <页1.json> <页2.json> ...
   ```
   每页输出 `verify ... N pins checked, 0 problems` 才算完成。
4. **有脚没连上**：
   - `python <ROOT>/kit/sch/drv.py <页.json> connect <位号>`，存盘，重新核对这一页；
   - 核对报 `got {''}`、重连又报 `pin already connected to net "$1N.."`：`python <ROOT>/kit/sch/fix_pin.py <页.json> <位号> <脚号>`。
5. **跨板核对**：两块板通过叠板连接器相连时，写一段检查，逐脚比对上板第 n 脚和下板对应脚的网络（底面排针奇偶对调）。示例见 `ROOT/examples/nuedc-car-2026/inputs/gen_specC.py` 末尾。

## 用户在 EDA 里手改过

用户会自己换封装、换测试点、改位号。这是常态：

- 动手前读存盘文件或 `sch list`，找出和规格不一样的地方；
- 把用户的改动**并回规格 JSON**（或它的生成脚本），再用 `kit/sch/verify_file.py` 确认两者一致；
- 绝不用旧规格重建去冲掉用户的改动。

## 改原理图

- 删元件：`python <ROOT>/kit/sch/del_parts.py <页UUID> R601 C601`。
- 改连接：改规格，对相关元件重新 connect，再核对。
- **对调引脚、改网名会影响固件。** 先问用户，说清影响；同意后在交接文档里记下「固件要跟着改什么」。
- 已布线的板子改了原理图：导入变更前先备份，导入后按 `pcb-routing` 的检查重新过一遍。

## 过关

- 每页 `0 problems`；
- `check_specs.py` 的网络数和设计一致，没有单脚网络；
- 跨板连接器逐脚核对通过。

过关后转到 `pcb-placement`。
