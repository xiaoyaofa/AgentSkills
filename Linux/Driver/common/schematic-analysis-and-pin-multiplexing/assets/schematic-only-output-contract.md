# B 阶段交付物格式契约（仅原理图分析）

> **适用**：**B 阶段每次调用必跑**（只要提供原理图 PDF）。B 产出
> `<板名>-原理图分析.md` + `<板名>-控制关系图.svg`。
> 同一次调用如果还提供厂商核心板引脚表，并**经用户确认**追加 A，
> 则另产 `<板名>-硬件资源分析.md` + `<板名>-引脚映射表.xlsx`；两套分别校验、
> 同次可同时产出，但**内容不混写**。
> A 的文件名/5 个 sheet/`[A]~[I]` 契约见 [`output-contract.md`](output-contract.md)。
>
> 本契约由 `scripts/check_schematic_report.py` 的 `[S1]~[S8]` 机器校验；
> 模板见 [`schematic-only-report-template.md`](schematic-only-report-template.md)，
> TSV 表头见 `control-map-template.tsv` / `power-tree-template.tsv` / `devices-template.tsv`。
>
> **★ 前置自检：上面这些文件不在 ⇒ 你手上是旧版 skill 的副本，先同步再动手。**

## 一、交付物只有两份

```
<板名>-原理图分析.md
<板名>-控制关系图.svg
```

`<板名>` 取原理图文件名去掉 `_SCH`/`_V1` 之类的后缀，保证同一目录下多块板不混。
两份必须是**同一块板**（`[S1]` 查）。

过程数据不进交付目录的这两份文件，放：

```
<板名>-过程记录/
├── control-map.tsv        # 控制关系（单一事实来源）
├── power-tree.tsv         # 电源树（单一事实来源）
├── devices.tsv            # 器件清单（单一事实来源）
├── layout.tsv             # 框图功能块坐标/样式/端口（block 样式）
├── zones.tsv              # 框图分区容器（block 样式）
├── edges.tsv              # 框图正交连线（block 样式）
├── pages/                 # 物理页索引 + pg-NN.txt（sch_page_split.py 产出）
│   ├── page_index.tsv
│   └── pg-01.txt …
├── A-引脚复用/            # 仅确认 A 后才有；A 的过程文件，避免与 B 的 devices.tsv 冲突
├── control-table.md       # 可选：生成表片段
├── power-table.md
├── device-table.md
├── render/                # 可选：SVG 转 PNG/PDF 的目检预览（不属于交付物）
└── ../sch/                # 共享提取：PDF 文字层 + 整页 PNG + 300dpi 裁图（B/A 共用）
```

## 二、三张 TSV 的列（逐字固定）

### `control-map.tsv`

```
id	分组	状态	控制源	源类型	方向	控制器件	器件型号	器件引脚	开关/通道	目标	目标类型	关系	有效状态/条件	动作与结果	I2C地址	中断	原理图页	确认状态	备注
```

- `id`：稳定主键 `C01…`；md/SVG/TSV 三方靠它对齐，**不要复用/跳号**；
- `源类型`：`GPIO` / `I2C` / `SPI` / `模拟` / `电源` / `连接器` / `器件输出`
- `方向`：`SOC_OUT`（软件输出控制）/ `SOC_IN`（软件输入）/ `BIDIR` / `POWER` / `NA`
- `关系`：`控制` / `数据` / `中断` / `状态反馈` / `电源`（决定 SVG 线型）
- `目标类型`：`电源轨` / `信号` / `器件` / `连接器`
- `有效状态/条件`：`高有效` / `低有效` / `OTG_PWR_CTRL=1` / `—`
- `确认状态`：`已看图` 或 `待确认 Qn`（**必须是后者时带 Q 编号**，且在 md 的待确认章节闭环）
- `原理图页`：物理页号（`1`/`2`），**不要写页脚里的 `Sheet N`**；
- 单元格内禁止 tab / 换行；`|` 只用于 SVG 多行标签（md 表格里会自动转义）。

### `power-tree.tsv`

```
id	状态	上游网络	来源器件	器件型号	器件引脚	通道/开关	下游网络	电压	条件/使能	动作与结果	原理图页	确认状态	备注
```

- `状态`：`常开` / `OTG=1` / `OTG=0` 之类；SVG 按它分泳道；
- `条件/使能`：这条路径在什么条件下导通（`—` = 无条件）；
- `来源器件/通道`：保险丝、MOSFET、充电器、升压等；`通道/开关` 可写 `Q15/Q2 背靠背`；
- 同一对上下游可以有多条（不同状态），靠 `状态` 区分。

### `devices.tsv`

```
位号	型号	类别	原理图页	作用	确认状态	备注
```

只收功能性器件；电阻/电容等无源小件不逐颗收录，但**控制路径上出现的位号必须在表里**
（`[S7]` 会查）。

## 三、md：必备章节与生成表标记

标题关键词（编号可不同，但都要有）：

`概览`、`快速结论`、`电源树`、`接口`、`器件清单`、`软件视角`、`硬件视角`、
`上电`、`待确认`

另外必须有：

- 头部元信息：原理图 PDF 文件名、页数、EDA、分析日期、**提取路线**（poppler / pypdfium2）；
- 范围声明：必须含 **「未使用核心板引脚表」「不做引脚复用」** 两个字面短语（`[S2]` 查），
  并按三种情况之一写清：
  - 无引脚表：`本次未提供引脚表。`
  - 同批确认 A：`同批《硬件资源分析.md》负责引脚复用（引脚表仅用于该报告）。`
  - 有表但跳过 A：`本次提供了引脚表，但按用户要求未做引脚复用。`
- 三个生成表标记各一次：

```markdown
<!-- BEGIN device-table (generated from devices.tsv; do not edit) -->
<!-- END device-table -->

<!-- BEGIN control-table (generated from control-map.tsv; do not edit) -->
<!-- END control-table -->

<!-- BEGIN power-table (generated from power-tree.tsv; do not edit) -->
<!-- END power-table -->
```

标记之间由 `control_map_svg.py --md` 回填；**手改标记内内容会被 `[S3]/[S3P]/[S7]` 抓出来**。

## 四、SVG 的要求

### 4.1 推荐：`--style block` 功能块框图

**为什么推荐**：行式表格 + 箭头看起来像"把表贴到图上"，不是框图；功能块重复、
看不出拓扑、软件工程师读起来费劲。框图应当按 IEC 61082-1 / IEEE 315 /
MIL-DTL-24784D 的惯例：

- **核心芯片先排整齐**：PD 控制器、充电器、稳压器等用**统一的蓝色**（`core`），
  先按行/列对齐，再在其周围放外围块；不要让核心芯片东歪西倒；
- **外围块统一白底细灰边**（`peripheral`）；**SoC 接口统一紫色**（`soc`），
  并且**紧贴对应的核心芯片**，用短箭头连接 —— 不要把 I2C/INT 拉到图底再连回来；
- **远距离连接用网络标签**：同名电源/网络标签成对出现（如两个 `VSYS`、两个 `5Vbat`），
  不画跨半张图的长线；短连接才画箭头；
- **信号/功率流左→右、上→下**；主线（电源）加粗，控制/状态线变细/虚线；
- **功能块只出现一次**，矩形 + 块名 + 位号；块右上角的 `p.N` 是**源原理图页码**
  （不是本图页号，图例会注明）；端口标在块边（`name:side:offset`，
  offset 也可写 `50%`，配合 rail/soc 的 `auto` 尺寸使用）；
- **SoC 接口块的文字一律以 `SoC` 开头**，用箭头表示方向：`SoC → I2C4`、
  `SoC ← INT_N`、`SoC → OTG_PWR_CTRL`；不要写 `I2C4 ← SoC` 或 `INT_N → SoC`；
- **正交连线**：只允许水平/垂直（`[S4]` 会查非正交线段）。空 `折点` 时由程序做
  **最短路径优先的避障布线**：先试同轴直连，再用 A*（代价=长度+拐弯+并行重叠）
  绕开功能块；手写 `折点` 只在程序确实做不到时用。端口间距 <20px 的连线
  箭头太短，`[S8]` 会报错；
- **并行连线必须分道**：两条边不能在同一 x/y 上重叠 >5px（`[S4]` 查），
  同一连接器/芯片出来的 VBUS、CC 等要用不同通道；**短于 50px 的连线不标文字**；
- **画布默认 2250×1250**；空间不够就整体放大，不要硬挤。块内文字离边框 ≥6px，
  `[S4]` 会查“文字越出块”；块外文字（端口名/线标签）也不能压到别的块；
- **分区容器**（虚线圆角框）按功能分组：输入 / 充电 / 系统电源 / OTG / 控制。
  分区**左/上边保持 `zones.tsv` 的声明值（便于并排的框对齐），右/下边按成员块收紧**，
  所以宽高是上限；**并排分区在 40px 内的上/下边会自动对齐**；
  **每个块必须完全落在所属分区内**，否则生成器直接报错。不要把分区画得很大再让块挤在角落；
  **没有成员块的分区会被省略**，`[S8]` 会报错要求删掉它或把块移进去；
- **图例 + 标题栏 + 网格坐标**（IEC 61082-1 的 title block / grid reference）；
- **状态/真值表**放在独立 note 块里，不把长段动作文字塞进功能块；
- 颜色只保留三类块（核心/外围/SoC）+ 电源网络灰 + 说明黄；线色只保留
  电源/控制/I2C/中断/模拟，**不要按电源轨上彩虹色**；
- 字体：`Noto Sans CJK SC` / `Source Han Sans SC` / `Microsoft YaHei` 等本机字体，
  **不嵌字体、不引外部文件**；字号 ≥ 10；
- 不用 `marker-end`（渲染器兼容性差），箭头用显式 `<polygon>`；
- 节点必须带 `id` + `data-node` + `data-label`；边组必须带
  `data-row` / `data-hop` / `data-from` / `data-to` / `data-edge`；
- 必须内嵌 `<metadata id="control-map-manifest">`（JSON，含 schema/layout/zones/edges/
  control/power/device rows），这是三方校验的锚点；
- 同一份 TSV + 同样的参数 → **字节级相同**（`control_map_svg.py --check` 可复验）。

**接口详图（可选，SoC/MCU 板推荐）**：系统框图之外，对关键接口各生成一张
`<板名>-<接口>-连接详图.svg`（如 `ExampleBoard-WiFi-连接详图.svg`），同样由
`control_map_svg.py --style block` 生成。数据放 `<板名>-过程记录/detail/`，
文件名为 `<接口>-{layout,zones,edges}.tsv`，另配独立的
`<接口>-{control-map,power-tree,devices}.tsv`，不要与系统图共用三张表。
主 `[S1]~[S8]` 只校验 `<板名>-控制关系图.svg`；详图靠生成器自检 + PNG 目检。
详图可加 `--diagonal`：端口水平出线 → 中段斜线交叉 → 水平进端口（TX/RX 交叉不会重合）；
主图不要加，仍须正交。块高按端口数收缩（线少就别写 750 高），并加
`--min-height 900` 把画布最小高度从 1250 降到 900，收掉底部空白；主图保持默认 1250。
模式/上下拉用 `strap` 块：`layout.tsv` 样式列写 `strap`，副标题每行
`net;上拉位号;阻值;UP|NC;下拉位号;阻值;DOWN|NC`，块内上 VCC、下 GND、竖电阻，
`NC/DNP` 自动灰色虚线标 `DNP / NC`，能直接看出上拉/下拉。
`CFG_LDO/RXD` 模式表用 `table` 块（`sub` 用 `|` 分行、`;` 分列，首行表头），
两张表**各画一个**，不要合并成一段文字。多通道器件（CAN U20/U22、RS-485 U23/U25、
LVDS J5/J6 与 J7/J8）按通道拆成 CAN1/CAN2、RS485-1/2、LVDS0/LVDS1 多个块，
不要用 `U20/U22 ×2` 合并；每通道电源也画成独立块（`VDD_3V3` / `ISO_5V`），
与其它接口一致，不要只写在端口名里。

**PNG 预览**不属于交付物；需要时用 `scripts/svg_to_png.py`（优先 Edge/Chrome，
其次 Inkscape/LibreOffice/cairosvg）。Windows 一般自带 Edge，无需 poppler/LibreOffice。
SVG 才是交付物，`[S1]~[S8]` 不依赖 PNG。
详图的画法见 `references/schematic-only.md` B6.4。

**输入文件**（都在过程记录目录）：

| 文件 | 作用 | 关键列 |
|---|---|---|
| `zones.tsv` | 分区容器 | `id 标题 x y 宽 高 颜色` |
| `layout.tsv` | 功能块 | `id 区域 x y 宽 高 样式 标题 副标题 端口 位号 页码 备注` |
| `edges.tsv` | 正交连线 | `id row from from_port to to_port 类型 标签 颜色 折点 页码` |

- `宽`/`高`：像素，或写 `auto`（**仅 rail / soc 样式**）。`auto` 按标题/副标题
  文字宽度 + 页码徽标收缩，网络标签块和 SoC 接口块会明显变小；这两类建议一律 `auto`；
- `端口`：`name:side:offset`，side ∈ `L/R/T/B`，用 `;` 分隔；offset 是绝对像素，
  也可写 `50%`（按边长百分比，配合 `auto` 最稳）；边必须引用存在的端口；
- `类型`：`power/control/i2c/int/status/analog/bus`（决定线宽/线型/颜色）；
- `row`：对应的 `control-map.tsv`/`power-tree.tsv` 的 id；`[S8]` 会查**每一行 TSV
  至少有一条框图连线**，且边的 `row` 不能凭空出现；
- `折点`：`x,y;x,y` 正交折点；空/`—` 时自动布线（最短路径 + 避障）。
  只有在自动结果确实不理想时才手写折点，并保证每段水平/垂直。
- `位号`：`Q15,Q2` 这种多颗用逗号分隔；`—` 表示导轨/总线/注释块，不查器件表。

### 4.2 保留：`--style table` 行式关系表

旧样式（每行一条 TSV、5 列箭头）仍保留，适合行数很多、不适合画成一张框图的板子；
但它**不是推荐的交付形态**，新板优先用 4.1。

- 控制关系固定 5 列：`控制源 → 控制器件/引脚 → 开关/通道 → 目标 → 状态/动作与结果`；
  电源树固定 4 列：`上游网络 → 通道/器件 → 下游网络 → 条件/使能/结果`；
- 必须有图例（实线=控制、点线=I2C、虚线=中断/状态、粗线=电源、灰虚线框=直连）；
- 必须有标题、来源说明、生成日期（可选）。

## 五、什么不该进交付物

| 不该出现 | 为什么 | 该放哪 |
|---|---|---|
| `判据A命中页`、`判据B`、`sha256`、`--conn-page`、`--page-re`、`--audit` | 工具内部字段 / 过程参数 | 过程记录 |
| `产物清单`、`Windows 通用性验证`、`建议反馈到 Skill` | skill 自身的验证结论，不是客户要的硬件分析 | 单独的验证报告 |
| `muxmode` | B 交付物不写引脚复用结论；即使同批确认了 A，也只放在 A 的交付物里 | A 的 xlsx/md |
| 裸「未判定」 | 会被读成结论 | 写成 `待确认 Qn` 并进待确认章节 |
| 绝对路径（`/home/...`、`D:\...`） | 交付物要能换机器阅读 | 过程记录 |

## 六、自检

```bash
python3 scripts/check_schematic_report.py \
        --md <板名>-原理图分析.md --svg <板名>-控制关系图.svg \
        --map <板名>-过程记录/control-map.tsv \
        --power <板名>-过程记录/power-tree.tsv \
        --devices <板名>-过程记录/devices.tsv \
        --layout <板名>-过程记录/layout.tsv \
        --zones <板名>-过程记录/zones.tsv \
        --edges <板名>-过程记录/edges.tsv

# 检查器自测（构造已知有错的样本，确认每个 [Sx] 都能报出来）
python3 scripts/check_schematic_report.py --selftest

# 生成器可复现性（同一份 TSV 两次生成应字节一致）
python3 scripts/control_map_svg.py ... --check
```

`[S1]~[S8]` 任何一项不为 0 都退出 1。**交付前必须全 0。**
`[S8]` 只在给了 `--layout/--zones/--edges` 时检查；它查：
块 id 唯一 / 区域存在 / 端口存在 / 边不悬空 / 每个 control/power 行至少一条边 /
块位号在 devices.tsv / manifest 与 layout+edges 一致；另外还查
**块是否越出所属分区**、**分区是否为空**、**端口偏移是否超出块边**、
**rail 块是否过大**、**端口间距是否 <20px（箭头太短）**。
