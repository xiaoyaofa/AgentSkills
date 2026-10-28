# 模式 B：仅原理图分析（Schematic-Only）

> **什么时候走这条**：手里只有原理图 PDF；或用户明确说「不做引脚复用/不看引脚表/
> 只分析原理图/给软件看控制关系」。**有引脚表且要判定用/未用与 muxmode 时走模式 A。**
>
> 产出：`<板名>-原理图分析.md` + `<板名>-控制关系图.svg`。
> 不产出 xlsx，不读引脚表，不做引脚复用。
>
> 首次验证：2 页 Altium 充电器板（Type-C PD + 充电器 + 电池 + 电源路径开关）。
> 下文所有通用规则都来自这次实测。

---

## B0. 提取（Linux/macOS 与 Windows 二选一）

```bash
BIN=<本 skill>/scripts
OUT=<工作目录>/sch

# Linux/macOS：poppler，pdftotext -layout 是严格版面序
$BIN/pdf_schematic_extract.sh <原理图.pdf> "$OUT" 160

# Windows / 无 poppler：pypdfium2，阅读序
python3 $BIN/pdf_extract.py <原理图.pdf> "$OUT" 160
```

**本次走哪条路线要写进 md 头部**（`check_schematic_report.py` 的 `[S2]` 会查
`poppler` / `pypdfium2` 是否出现）。

> ★ 两条路的文字层行结构不同：poppler 严格版面序，PDFium 阅读序。
> 模式 B 不依赖「页号相邻性」判据，所以两条路都能用；但弱判据（器件号邻近性）
> 在 PDFium 下更差，读图核对要更仔细。

## B1. 按物理页切分（★ 不信页脚）

```bash
python3 $BIN/sch_page_split.py "$OUT/text/<name>.txt" --out "$OUT/text"
# 或显式给页数
python3 $BIN/sch_page_split.py "$OUT/text/<name>.txt" --out "$OUT/text" --pages 2
```

产出：

```
$OUT/text/pg-01.txt / pg-01.compact.txt …
$OUT/text/page_index.tsv      # 物理页/页脚标注/行数/单字符行占比/文字层状态
```

**为什么不能按页脚切**：Altium 等 EDA 导出的 PDF 页脚可能只印 `Sheet     of`，
页码值不在文字层里；按页脚切会把多页并成一页，或切出 0 页。
本脚本按 `\f`（poppler 与 PDFium 都保留）切物理页，页脚只写进 `page_index.tsv` 作辅助。

若输出「文字层退化」：该页 >50% 的行是单字符（竖排/旋转标签常见），
**该页的 grep 结果不可信**，必须整页读图；md 里要写明「该页文字层不可用」。

## B2. 逐页读图（★ 这一步决定结论质量）

1. 先用 Read 读 `$OUT/pages/pg-NN.png` 看整页功能块划分；
2. 对每个功能块，300dpi 重渲染该页后局部裁图：

```bash
# Linux：单页 300dpi
pdftoppm -r 300 -f 2 -l 2 -png <原理图.pdf> "$OUT/hi2"
# Windows：--render-only 2
python3 $BIN/pdf_extract.py <原理图.pdf> "$OUT/hi300" 300 --render-only 2

python3 $BIN/img_crop.py "$OUT/hi2-02.png" "$OUT/crop.png" <x1> <y1> <x2> <y2> 1.1
```

3. **对着裁图确认**：
   - 位号 ↔ 型号（两个标签都可能与相邻方框的型号混在一起）；
   - 控制极性（P/N 沟道、NPN/PNP、栅极上拉/下拉、高/低有效）；
   - 0R / DNP / NC（`@` 前缀、`DNP_` 前缀、`NC` 标注）；
   - 网络名拼写（文字层只用来核对拼写，不用来定拓扑）。

> 实测：同一块板上文字层读到 `MP2703`，300dpi 图上是 `MP2770GL-0000-Z`；
> 文字层还把 `Q3` 读成 `Q8`。**型号与位号以图为准。**

## B3. 器件清单 `devices.tsv`

```bash
cp <本 skill>/assets/devices-template.tsv <板名>-过程记录/devices.tsv
```

只收功能性器件（芯片/模块/连接器/保护/电源通路器件），无源小件不逐颗收。
每行必须有 `确认状态`：

- `已看图`：位号与型号都经 300dpi 裁图确认；
- `待确认 Qn`：图上读不出、或需查手册/实测。

**控制路径上出现的位号必须在 `devices.tsv` 里**（`[S7]` 会查引用完整性）。

## B4. 控制关系 `control-map.tsv`

```bash
cp <本 skill>/assets/control-map-template.tsv <板名>-过程记录/control-map.tsv
```

一行 = 一条控制/信号关系。写法要点：

- **同一信号的不同状态分行写**（`状态` 列 `OTG=1` / `OTG=0`），这样 SVG 能画出
  「切到哪」的两条路径，md 也能直接生成真值表；
- `控制器件` 填真正响应控制的那一级（MOSFET 栅极、充电器 EN/SCL/SDA、PD 控制器），
  `开关/通道` 填被它驱动的功率级（`Q16/Q1 背靠背`）；
- `I2C地址` 图上没写就写 `待确认 Qn`（例如充电器的地址常要查手册）；
- `中断` 写网络名与极性（`INT_N 低有效`）；
- `原理图页` 写**物理页号**，不要写页脚里的 `Sheet N`；
- `备注` 写依据：`R311 1K / R313 10K 下拉`、`R302/R304 上拉`、`R312 NC`、
  `0R 跳线`、`数据手册推断`。

**依据必须能落到图上**：直连、0R 跳线、网络名、数据手册。只看网络名就写
「高有效」是常见的错源；栅极上拉/下拉、P/N 沟道决定极性，必须读图。

## B5. 电源树 `power-tree.tsv`

```bash
cp <本 skill>/assets/power-tree-template.tsv <板名>-过程记录/power-tree.tsv
```

- 一行 = 一条电源路径（上游 → 通道/器件 → 下游）；
- `状态` 用来分泳道（`常开` / `OTG=1` / `OTG=0`），避免 OTG 输出与充电输入
  形成的环把拓扑排序搞乱；
- `条件/使能` 写导通条件；`—` 表示无条件；
- 控制行里 `目标类型=电源轨` 的目标，必须在这张表里出现（`[S6]` 会查）。

## B6. 生成 SVG 与 md 表格

### B6.1 推荐：`--style block` 功能块框图

先建三个布局文件（照 `assets/block-*-template.tsv`）：

```bash
cp $ASSETS/block-zones-template.tsv  <板名>-过程记录/zones.tsv
cp $ASSETS/block-layout-template.tsv <板名>-过程记录/layout.tsv
cp $ASSETS/block-edges-template.tsv  <板名>-过程记录/edges.tsv
```

- `zones.tsv`：分区容器（输入 / 充电 / 系统电源 / OTG / 控制），坐标是画布像素；
  **左/上边保持声明值（并排的分区要对齐左上），右/下边按成员块收紧**，`宽/高` 只是上限；
  并排分区在 40px 内的上/下边会自动对齐；不要把框画得很大再让块缩在角落；
  块必须完全在所属分区内，越界会直接报错；**没有成员块的分区会被省略**，`[S8]` 要求删除；
- `layout.tsv`：每个功能块**只出现一次**，写 `x/y/宽/高/样式/标题/副标题/端口/位号`；
  端口格式 `name:side:offset`（side = L/R/T/B），offset 可写 `50%`；
  **rail（网络标签）和 soc（SoC 接口）块建议 `宽=auto 高=auto`**，按文字收缩，
  别手工画 100×45 / 200×45 的大框；块右上角的 `p.N` 是源原理图页码，图例会注明；
- `edges.tsv`：正交连线，`row` 填对应的 `control-map.tsv`/`power-tree.tsv` id；
  `折点` 一般留空，由程序按**最短路径优先**自动避障布线；只有结果确实不理想时才手写
  （`x,y;x,y`），并保证每段水平/垂直。

生成：

```bash
python3 $BIN/control_map_svg.py --style block \
    --map  <板名>-过程记录/control-map.tsv \
    --power <板名>-过程记录/power-tree.tsv \
    --devices <板名>-过程记录/devices.tsv \
    --layout <板名>-过程记录/layout.tsv \
    --zones  <板名>-过程记录/zones.tsv \
    --edges  <板名>-过程记录/edges.tsv \
    --svg  <板名>-控制关系图.svg \
    --md   <板名>-原理图分析.md \
    --title "<板名> 系统框图" --date <YYYY-MM-DD> \
    --source "<原理图文件名>.pdf"
```

**PNG 预览**（可选，推荐放同目录方便人直接看）：

```bash
python3 scripts/svg_to_png.py <板名>-控制关系图.svg --dpi 120
# 引擎顺序：Edge/Chrome（headless 截图）→ Inkscape → LibreOffice → cairosvg
# Windows 一般自带 Edge，无需 poppler/LibreOffice；输出像素以脚本打印为准
```

> SVG 不能直接当图片看；用上面的方式转 PNG 后用 Read 目检。**每一轮改完布局都要重新转一次**
> —— 线是否穿块、文字是否被裁、核心芯片是否对齐，只有看 PNG 才发现。
> LibreOffice 兜底时导出尺寸可能由它自己决定（常见 2048 宽），脚本会打印实际像素；
> 要严格 120dpi 优先用浏览器/Inkscape。

### B6.2 保底：`--style table` 行式关系表

行数很多、不适合画成一张框图时用；命令与 B6.1 相同，去掉 `--layout/--zones/--edges`，
`--style table`（默认）。它不是推荐的交付形态。

### B6.3 框图设计规范（为什么这样画）

参考：IEC 61082-1（功能分组 / 信号流 / 参考代号 / 标题栏 / 网格）、
IEEE 315（图形符号）、MIL-DTL-24784D（信号左→右、上→下；主线加粗；
箭头表示流向），以及微软 Azure 架构图与 Xilinx Zynq 框图的通行做法
（分区容器、产品名+图标、编号步骤、图例、PS/PL 双色分区）。

落地规则：

1. **一个功能块只画一次**，不要按 TSV 行重复；块内写块名 + 位号 + 关键端口；
2. **功率流左→右/上→下**，电源粗线（3–4px），控制细线（1.5–2px），
   中断/状态虚线，I2C 点线；同一条线只表达一种关系；
3. **正交连线**：不画斜线；线不能穿过非端点功能块（`[S4]` 查）。留空 `折点`
   时程序先找同轴直连，再用 A* 找最短的避障路径；线标签自动缩到文字大小并避开
   功能块/端口名，找不到空位就不标（信息仍在 md 表里）；分支处画 junction dot；
4. **分区容器**：3–5 个功能区，虚线圆角框 + 左上角标题；框按内容自动收紧，
   块必须落在框内；不要超过 6 个块/区；
5. **端口标在块边**：`name:side:offset` 生成小圆点 + 端口名；线只接到端口。
   端口名会自动找不压块、不被线穿过的一侧；**端口间距至少 20px**，否则箭头太短，`[S8]` 会报错；
   **SoC 接口块一律以 `SoC` 开头**，箭头表达方向：`SoC → I2C4`、`SoC ← INT_N`、
   `SoC → OTG_PWR_CTRL`；不要写 `I2C4 ← SoC` 或 `INT_N → SoC`；
6. **状态/真值表单独放 note 块**，不要把长段动作文字塞进功能块；
7. **图例 + 标题栏 + 网格坐标**：图例放左下、标题栏放右下，写图名/日期/依据 PDF/Rev；
8. **颜色语义**：接口=蓝、PD/控制=紫、充电=绿、电池=琥珀、开关=橙、稳压=青；
   颜色只做辅助，线型 + 文字必须能独立区分；
9. **字体**：`Noto Sans CJK SC` / `Source Han Sans SC` / `Microsoft YaHei` 等本机字体，
   字号 ≥ 10；不嵌字体、不引外部文件；
10. **同一个 word 只写一次**：能用一个端口名/网络名说清的，不要重复长句。

**排版顺序（按微软 MB 框图的做法）**：

1. 先放**核心芯片**（`core`，统一蓝色），按行/列对齐；
2. 再放**外围块**（`peripheral`，白底细灰边），沿核心芯片的行/列排列，不要东歪西倒；
3. 最后放**SoC 接口**（`soc`，统一紫色），**紧贴对应核心芯片**，短箭头连接；
4. **远距离连接用同名网络标签**（两个 `VSYS`、两个 `5Vbat`），不画跨半张图的长线；
5. 图例按块类别：核心 / 外围 / SoC / 电源网络 / 说明；线型只保留
   电源 / 控制 / I2C / 中断 / 模拟，不要按电源轨上彩虹色；
6. **空间不够就整体放大**（默认画布 2250×1250，坐标按 20px 网格）；块内文字
   离边框至少 6px，`[S4]` 会查“文字越出块”；块外文字也不能压到别的块；连线只允许
   水平/垂直，`[S4]` 会查“非正交线段”；能直连的端口直接拉直，自动布线会优先选
   拐点少、长度短的路径；
7. **并行连线必须分道**：两条边不能在同一 x/y 上重叠超过 5px（`[S4]` 查）；
   从同一个连接器/芯片出来的多根线（如 VBUS 与 CC）用不同 x 或 y 的通道；
8. **短于 50px 的连线不标文字**（标签会压在端口/边框上）；信息留在 md 表里。

> 知乎那篇参考文章（`zhuanlan.zhihu.com/p/406156445`）本机访问返回 403，
> 但文中提到的微软/Xilinx 框图风格与上面的规则一致；实际落地以 IEC 61082-1 /
> IEEE 315 / MIL-DTL-24784D 的公开条款为准。

### B6.4 接口详图（SoC/MCU 板推荐）

系统级框图只画到「SoC ↔ 器件」这一层；带 SoC/MCU 的板子还要对关键接口
（WiFi/BLE、USB、以太网、音频、存储、显示、工业收发……）各生成一张：
`<板名>-<接口>-连接详图.svg`。

- **左侧 SoC/MCU，右侧器件/连接器**；一张图只画一个接口，不混别的外设；
- **每条信号（组）一条边**，标签写 `信号名 → 器件脚号`（如 `MMC2_CLK → p18`、
  `UART1_TXD → p41`）；串联 0R、上拉、DNP、NC 放 note 块，不塞进边标签；
- **模式/上下拉不能省**：PHY 的 `PHYAD/CFG_MODE/LED` strap、SoC 启动 strap、I2C 上拉、
  `0R/DNP/NC` 都要在 note 里列出，不能只画数据线；
- **多通道/多颗器件按通道拆块**：CAN 的 U20/U22、RS-485 的 U23/U25、LVDS 的 J5/J6 与
  J7/J8 分别画 CAN1/CAN2、RS485-1/2、LVDS0/LVDS1；不要用一个 `U20/U22 ... ×2` 块合并。
  每通道的电源也画成独立块（`VDD_3V3` / `ISO_5V`），与其它接口一致，不要只写在端口名里；
- **模式/上下拉用 `strap` 块画成电阻网络**：上 VCC、下 GND，竖电阻列；`sub` 每行
  `net;上拉位号;阻值;UP|NC;下拉位号;阻值;DOWN|NC`（如 `PHYAD2;R368;4.7K;UP;;;NC`、
  `NET;;;NC;R55;1K;DOWN`），生成器画上/下拉位置；`NC/DNP` 灰色虚线并标 `DNP / NC`，
  一眼看出有没有上拉/下拉。模式表用 `table` 块（`sub` 用 `|` 分行、`;` 分列，首行表头），
  `CFG_LDO[1:0]` 和 `RXD[3:1]` **各画一个表**，不要合并成一段文字；
- **电源/时钟/天线单独放侧边块**（`VDD_BLE_3V3`、`32.7K_CLK`、`ANT J28`……）；
- 仍用 `control_map_svg.py --style block`，layout/zones/edges 各一套，放在
  `<板名>-过程记录/detail/<接口>-{layout,zones,edges}.tsv`；数据用独立的
  `<接口>-{control-map,power-tree,devices}.tsv`，避免 manifest 与系统图混在一起；
- 详图**不参与**主交付物的 `[S1]~[S8]`，但生成器自带的块越界/重叠/端口/文字检查
  仍会执行；出图后同样用 `svg_to_png.py` 转 PNG 目检；
- md 的「系统框图」章节加一张**接口详图索引**，链接到各 SVG。

模板：`assets/block-detail-{zones,layout,edges}-template.tsv`。生成命令与 B6.1 相同，
只把 `--layout/--zones/--edges` 换成 detail 目录下的一套，`--svg` 换成
`<板名>-<接口>-连接详图.svg`，不要加 `--md`（详图不回填主表）。

**块高按端口数写，不要一律 750**：Audio/CAN/CSI 这类只有 3–6 根线的接口，
块高 340–450 就够；块缩小后画布底部会空出一大截，生成时加 `--min-height 900`
把画布最小高度从 1250 降到 900。主控制关系图不要传这个参数，仍用默认 1250。

**详图加 `--diagonal`**：端口先**水平出线**，中段用**斜线交叉**，再**水平进端口**。
TX/RX 这类交叉信号（SoC TX→器件 RX、SoC RX→器件 TX）正交布线会被迫共用竖直通道而
重合；三段式既保留端口处的水平出线，又让交叉发生在中间，映射关系一眼可见。
主控制关系图**不要**加这个参数，仍保持正交（`[S4]` 会查非正交线段）。

## B7. 写 md

照 [`assets/schematic-only-report-template.md`](../assets/schematic-only-report-template.md)
复制后逐节填。两份读者：

- **硬件工程师**：第 4/5/7/8/9 节（接口、器件、电源路径、上电复位、保护）；
- **嵌入式软件工程师**：第 2/6/10 节（快速结论、控制接口、软件注意事项）。

第 6 节的控制表与第 7 节的电源表**不要手写**——它们由 TSV 生成。
叙述部分写「为什么」：为什么互锁、为什么默认关断、软件先做什么后做什么。

## B8. 校验（交付前必跑）

```bash
python3 $BIN/check_schematic_report.py \
    --md <板名>-原理图分析.md --svg <板名>-控制关系图.svg \
    --map <板名>-过程记录/control-map.tsv \
    --power <板名>-过程记录/power-tree.tsv \
    --devices <板名>-过程记录/devices.tsv \
    --layout <板名>-过程记录/layout.tsv \
    --zones <板名>-过程记录/zones.tsv \
    --edges <板名>-过程记录/edges.tsv
```

`[S1]~[S8]` 必须全 0。`[S8]` 查框图布局：块 id 唯一、区域/端口存在、边不悬空、
**每个 control/power 行至少一条边**、块位号在 devices.tsv、manifest 与 layout+edges 一致；
还查块越出分区、空分区、端口偏移越界、rail 块过大、端口间距 <20px（箭头太短）。
`[S4]` 还会查**连线不能穿过非端点功能块**、块不重叠、文字不越界。

检查器自测：

```bash
python3 $BIN/check_schematic_report.py --selftest
```

**反向验证**：故意把 md 的目标、SVG 的边、TSV 的确认状态、layout 的块位号各改错一处，
检查器必须分别报出对应 `[Sx]`。一个「永远说没问题」的检查器比没有更危险。

---

## 模式 B 的坑（实测）

1. **页脚不可信**：`Sheet     of` 没有页码值 → 按 `\f` 物理分页，`page_index.tsv` 只作辅助。
2. **Altium 的 `NL...` 记号把 `_` 写成 `0`**：`NLOTG0PWR0CTRL` = `OTG_PWR_CTRL`，
   `NLPC60I2C40SCL` = `PC6_I2C4_SCL`。grep 时两种写法都试；以图上网络名为准。
3. **型号不是文字层里那个**：文字层可能把 `MP2770GL-0000-Z` 读成 `MP2703`、
   把 `Q3` 读成 `Q8`。位号↔型号必须 300dpi 读图确认（沿用模式 A 踩坑 16）。
4. **控制极性不能猜**：P-MOS 栅极上拉默认关断、N-MOS 栅极下拉默认关断、
   NPN 集电极反相……必须看栅极/基极接法。不能确认就 `待确认 Qn`。
5. **0R / DNP / NC 是三种不同的东西**：`@` 前缀 = DNP，`DNP_` = 不贴，`NC` = 不装。
   只看到「有连线」就写导通，会把没贴的 0R 当成生效。
6. **背靠背 PMOS 的体二极管方向**：即使关断，体二极管也可能让电压倒灌；
   电源路径要写清「关断时是否仍有体二极管通路」。
7. **共享 I2C 总线**：同一总线上的 PD 控制器与充电器地址可能冲突；图上没写地址就
   进待确认，不要从别的板/别的型号抄。
8. **电源树有环**：OTG 输出与充电输入互斥时，按 `状态` 分泳道画，不要硬拓扑排序。
9. **单页信息密集**：一页里可能同时有充电器、升压、两组背靠背开关；
   控制图只画控制/电源路径，完整网络留给 md 与原理图原图。
10. **中文字体**：SVG 不嵌字体；无 CJK 字体的最小系统会显示方框，
    交付时说明「需要 Noto Sans CJK / 思源黑体 / 微软雅黑之一」。
11. **模式 B 不要滑回模式 A**：不读引脚表、不写 `muxmode`、不产出 xlsx；
    `[S5]` 会把 `muxmode` 当禁用词抓出来。
