---
name: schematic-analysis-and-pin-multiplexing
description: 从客户底板原理图 PDF + 厂商核心板引脚表（xlsx）出发，产出《硬件资源分析.md》与《引脚映射表.xlsx》的完整方法与工具链。覆盖：原理图 PDF 双层提取（pdftotext 文字层 + 300dpi 局部裁图）、xlsx 免依赖解析（空单元格省略／表头不在首行／尾部列串位等坑）、「底板到底用了哪些核心板信号」的双判据对撞、ball↔GPIO↔muxmode 反查、★「引脚表引出名≠Function0」查漏法、交付前的五项交叉复查、以及「表格用 xlsx、叙述用 markdown」的输出取舍。★ 需多模态视觉：拓扑判读（总线方向／门电路类型／DNP 归属／器件归属）只能读图，纯文本模型须走文中「模型能力要求」一节的降级路径。
license: MIT
metadata:
  category: Linux/Driver
  platform: common
  soc: AM62x
  board: MYC-YM62X（首次验证）
  vendor: MYIR
---

# 原理图分析与引脚复用（Schematic Analysis and Pin Multiplexing）

- **脚本**: 见 [`scripts/`](scripts/) —— 7 个，全部免第三方 Python 包（除裁图用 Pillow、出 xlsx 用 LibreOffice）
- **依赖**: `poppler-utils`(pdftotext/pdftoppm/pdfinfo)、`python3`(仅标准库)、`Pillow`(裁图)、`LibreOffice`(可选，生成 xlsx)
- **适用**: 任意 SoC/厂商 —— 只要手里有"① 底板原理图 PDF ② 厂商核心板引脚复用表(xlsx/表格)"
- **首次验证平台**: MYIR MYC-YM62X（AM62x，ALW/BGA425）

## 用途

客户拿核心板做自研底板时，BSP 动手前必须先回答三个问题：

1. **底板上有哪些芯片/外设？**（决定要开哪些驱动、加哪些 dts 节点）
2. **核心板的每只脚，底板到底接没接？接去了哪？**（决定要不要裁剪现有设备树）
3. **每只被用到的脚，SoC 上原本是什么功能、底板要把它复用成什么功能（mux mode 是几）？**（决定 pinctrl 怎么写）

这份 skill 就是把这三件事做成可复现的流程，产出两份交付物：**《硬件资源分析.md》**（叙述）+ **《引脚映射表.xlsx》**（表格）。

## 工具清单

| 脚本 | 一句话 |
|---|---|
| `scripts/xlsx_dump.py` | 免依赖读 .xlsx（按单元格 `r=` 引用解析），导 TSV / `--check` 体检表结构 |
| `scripts/pdf_schematic_extract.sh` | 原理图 PDF 双层提取：文字层(原版+压缩空白版) + 每页 PNG + 页边界行号 |
| `scripts/img_crop.py` | 局部裁剪放大，供 Read 工具精读小字（网络名/位号/上下拉） |
| `scripts/pin_usage.py` | 判定"底板用了哪些核心板信号"，A/B 双判据 + 置信度列 |
| `scripts/pin_mux_table.py` | 反查 引脚原名(Function0) / 底板所需功能 / muxmode；`--audit` 查漏 |
| `scripts/table_to_xlsx.py` | 多个 TSV → 多 sheet xlsx（FODS + LibreOffice，免 openpyxl） |
| `scripts/verify_pinmap.py` | 交付前五项交叉复查 |

## 模型能力要求（★ 用之前先看）

**这套流程只有一部分依赖多模态视觉，但那一部分恰恰是抓真错的关键。** 先确认手上的模型属于哪种情况，再决定按哪条路子走。

### 哪些步骤纯文本、哪些必须要视觉

| 步骤 | 依赖 | 说明 |
|---|---|---|
| `xlsx_dump.py` / `table_to_xlsx.py` | 纯文本 | xlsx 本身就是 zip+XML |
| `pin_usage.py` | 纯文本 | 判据 A/B 读的都是 `pdftotext` 出来的文字层 |
| `pin_mux_table.py`（含 `--audit` 查漏） | 纯文本 | **本次最有价值的发现（`RESETOUT1` 的 Function0 其实是 `CSn2`）就是纯文本抓到的** |
| `verify_pinmap.py` | 纯文本 | A–F 六项检查全部基于文字层 |
| `pdf_schematic_extract.sh` + `img_crop.py` | 纯文本（但产出是给视觉用的图） | 这两个脚本本身不需要视觉，它们的**唯一用途**是喂给模型看 |
| **拓扑判读** | **必须视觉** | 谁驱动谁、总线方向、上下拉是否贴、门电路是"或门"还是"缓冲" —— 文字层按排版顺序输出，这些关系在里面是**并列出现的，看不出方向** |

### 实测来源分布（一次真实项目，10 个非平凡结论）

| 只能靠图（6） | 纯文本够用（4） |
|---|---|
| 看门狗 WDI 接在哪只脚（文本层 `MR WDI SoC_GPMC0_AD0/BOOTMODE00` 顺序可两解） | `RESETOUT1`/`ECC_FAIL` 的 Function0 陷阱（`--audit` 抓） |
| U13/U14 是**或门**而非缓冲（文本只有 `GND Vcc / B / Y 4 R78`） | OSPI 的 LBCLK/DQS 连线 |
| 三个 LC0504F 谁属 SD、谁属 JTAG（文本里交错排列） | 双千兆 PHY 型号（文本层也有 `YT8531H-CA`） |
| 四路 LED 分别由哪个信号驱动 | 189 个信号的"用/未用"统计 |
| 16 位拨码挂在 BOOTMODE00–15 | |
| 看门狗支路 D6/R72 是 DNP → 实际不生效（靠 `@`/`DNP_` 区分） | |

**修的 4 处真错里，3 处是靠读图发现的。** 所以图这一层不是锦上添花。

### 没有多模态时怎么降级（不是做不了，是要诚实标注）

原则一句话：**"看图才能定的事情，标成待人工确认，不要硬猜"。**

1. 流程照跑 ①–⑤，`verify_pinmap.py` 照跑 —— 引脚/ball/muxmode 反查、用/不用统计、`--audit` 查漏**全都不受影响**；
2. 把所有 `confidence = 仅B` 的条目、`verify_pinmap` 的 `[E]` 项、"总线方向/门电路类型/DNP 归属"这类**只列现象、不给结论**，汇总成一张《需人工看图确认》清单交给客户/硬件工程师（一次真实项目这类条目约 8 组，人看图十来分钟能判完）；
3. 交付文档里必须写明**哪些结论是文本层推导、哪些经过图像确认**，不要让下一手误以为全都核过。

### 视觉能力的门槛

- **整页缩略图（160dpi）上的 5–6px 小字读不准** —— 必须对该页 300dpi 重渲染后用小区域裁图。这就是 `img_crop.py` 是必备件而不是可选项的原因。
- 视觉分辨率或小字 OCR 弱的模型，在这里会出**"看似合理的错读"**（比"读不出来"更危险）：比如把 `OSPI0_CSn2` 看成 `OSPI0_RESET_OUT1`、把或门看成缓冲、把 DNP 电阻当成贴了。
- 判断模型是否胜任的土办法：拿一张 300dpi 的裁图问它"这个电阻的阻值/位号是多少、它贴在哪个网络上"，答案能对得上再往下走。

## 标准流程

```bash
BIN=<本skill>/scripts
OUT=~/work/<客户>_<板名>; mkdir -p $OUT

# ① 引脚表 -> TSV（先体检，确认表头在第几行）
python3 $BIN/xlsx_dump.py 核心板引脚表.xlsx --check
python3 $BIN/xlsx_dump.py 核心板引脚表.xlsx --sheet 1 --skip-rows 1 > $OUT/pinlist.tsv

# ② 原理图 -> 文字层 + 整图
$BIN/pdf_schematic_extract.sh 原理图.pdf $OUT/sch 160

# ③ 通读：先看框图页，再逐页看功能页（用 Read 读 $OUT/sch/pages/pg-NN.png）
#    看不清的区块 -> 300dpi 重渲染该页后局部裁图
#    ★ 模型没有视觉能力时，跳过本步的"判读"，改为记录待人工确认项（见「模型能力要求」）
pdftoppm -r 300 -f 8 -l 8 -png 原理图.pdf $OUT/sch/hi8
python3 $BIN/img_crop.py $OUT/sch/hi8-08.png $OUT/sch/crop.png 950 1150 2600 2481 1.1
#    ★ 对照文字层确认网络名拼写：grep -n "SoC_XXX" $OUT/sch/text/原理图.compact.txt

# ④ 判定"底板用了哪些信号"
python3 $BIN/pin_usage.py --pinlist $OUT/pinlist.tsv --sch $OUT/sch/text/*.compact.txt \
        --out $OUT/usage.tsv
#    confidence 列 != "A+B 一致" 的，逐条裁图复核

# ⑤ 反查 muxmode（targets 文件：每行 "引脚号 目标功能名"）
python3 $BIN/pin_mux_table.py --pinlist $OUT/pinlist.tsv --audit          # ★ 先查漏
python3 $BIN/pin_mux_table.py --pinlist $OUT/pinlist.tsv \
        --targets $OUT/targets.txt --out $OUT/mux.tsv

# ⑥ 出交付物（表格用 xlsx，叙述用 markdown）
python3 $BIN/table_to_xlsx.py $OUT/引脚映射表.xlsx \
        --sheet "0-说明:$OUT/notes.tsv" \
        --sheet "1-底板已用信号:$OUT/sheet1.tsv:2,3.2,2.2,1.6,2.8,4.2,1.2,1.6,6,6" \
        --sheet "2-未使用信号:$OUT/sheet2.tsv:2.2,4,1.6,2.8,2,1.2,7"

# ⑦ 交付前必跑
python3 $BIN/verify_pinmap.py --pinlist $OUT/pinlist.tsv \
        --sch $OUT/sch/text/*.compact.txt \
        --used $OUT/sheet1.tsv --unused $OUT/sheet2.tsv
```

## 关键方法（这一节是精华）

### 1. 原理图必须"双层读"，任一层单独用都会错

| 层 | 拿到什么 | 不能拿什么 |
|---|---|---|
| 文字层（`pdftotext -layout`） | 网络名/位号的**准确拼写**、跨页交叉引用页号 → 可以 grep、可以脚本化 | 拓扑关系。PDF 文字流按排版顺序输出，**网络名和它接的器件经常在文本里隔几十行** |
| 图像层（`pdftoppm` + Read） | **谁接谁**、上下拉、门电路类型、总线方向 | 小字容易看错 → 所以关键处要 300dpi 裁图放大 |

实战教训：只看文字层，会把"调试器→SoC"的 TCK 方向写反（文字层里两端的名字是并列出现的，看不出驱动方向）；只看图像，会把 `OSPI0_RESET_OUT1` 记成 `OSPI0_RESETOUT1`（原理图网络名与 SoC 功能名本来就不同）。**结论性的东西，两层都确认过再写。**

### 2. "用/不用"要用两种判据对撞

多页原理图里，**连接器页**会把核心板每只脚都画出来，凡被底板用到的，旁边有指向其他页的交叉引用。于是：

- **判据 A**：该网络名是否出现在**非连接器页**上
- **判据 B**：连接器页上该网络名两侧是否有**页号**（左栏网络的页号在名字前面、右栏在后面，两侧都要扫）

`pin_usage.py` 同时给 A、B 并标 `confidence`：`A+B 一致` 可以直接信；`仅A` 通常是文字层没把页号提出来（正常）；**`仅B` 最可疑**（别页没出现网络名），必须裁图看。要单纯靠一种判据，就别下结论。

### 3. ball / GPIO / muxmode 一律反查，绝不手抄

引脚表里每只脚有 `Function0..Function9` 十列。**目标功能在第几列，muxmode 就是几** —— 这个下标直接就是 pinctrl 宏的第 3 个参数（`AM62X_IOPAD(offset, 属性, muxmode)`；其他平台宏名不同但语义相同）。

所以：只用写明"这只脚底板要当什么用"（targets 文件），muxmode 由脚本查表得出。手抄 muxmode 是纯粹的自找麻烦。

**交叉验证技巧**：如果手上有厂商现成的同平台设备树，拿它的 pinctrl 对一遍。本次 A15 反查得 `I2C3_SCL → muxmode 2`，与厂商 BSP 里 `AM62X_IOPAD(0x1d0, PIN_INPUT_PULLUP, 2) /* (A15) UART0_CTSn.I2C3_SCL */` 完全一致，说明表和推导都对。

### 4. ★ 查漏法：凡"引脚表引出名 ≠ Function0"的脚，都要单独确认

厂商引脚表的「引脚名」是**引出命名**，不等于 SoC 的 Function0。多数只是写法差异（`SOC_OSPI0_DQ5` ↔ `OSPI0_D5`），但**有少数脚引出的确实不是 Function0**，按 Function0 配 pinctrl 就会配成别的功能。

```bash
python3 scripts/pin_mux_table.py --pinlist pinlist.tsv --audit   # 全部列出，逐条判
```

实战抓出来的真错（都是"看起来像对的"）：

| 引脚表引出名 | Function0 | 底板真要用的功能 | 如果按 Function0 配会怎样 |
|---|---|---|---|
| `SOC_OSPI0_RESETOUT1` | `OSPI0_CSn2` | `OSPI0_RESET_OUT1` **(muxmode 2)** | flash 的复位脚变成一个片选 |
| `SOC_OSPI0_ECC_FAIL` | `OSPI0_CSn3` | `OSPI0_ECC_FAIL` **(muxmode 2)** | flash 的 INT 脚变成另一个片选 |
| `SOC_UART0_CTSN` | `UART0_CTSn` | `I2C3_SCL` **(muxmode 2)** | RTC 的 I²C 完全不通 |
| `SOC_OSPI0_CSN1` | `OSPI0_CSn1` | `GPIO0_12` **(muxmode 7)** | 指示灯不亮 |

这类错误的共同特征：**引脚表名字里带的是"这只脚在核心板上叫什么"，不是"SoC 内部这个焊盘默认是什么"**。凡是带 `/` 的（`SOC_GPMC0_AD0/BOOTMODE00`）就是"默认功能 + 复用/strap 用途"两层含义，要分开看。

### 5. 输出格式：表格用 xlsx，叙述用 markdown

| 内容 | 格式 | 理由 |
|---|---|---|
| 信号↔引脚↔ball↔GPIO↔负载 对照表 | **xlsx** | 100+ 行 × 10 列，要能筛选/排序/加批注；且与厂商给的引脚表格式一致，可直接发客户 |
| 框图、电源树、器件事项、BSP 结论、待确认问题 | **markdown** | 叙述性内容，diff 友好、好维护、好引用 |

xlsx 里务必留一个 **「0-说明」sheet**，写清每列的语义、muxmode 是怎么来的、以及本文这几条判读规则 —— 表格会流转到别人手里，语义不写下来等于没有。

## 交付前五项复查（`verify_pinmap.py`）

| 检查 | 抓什么 |
|---|---|
| **A** 引脚↔信号名一致性 | 表里写的"引脚 2xx / ball …"与厂商表逐行比对，防手抄错位 |
| **B** 划分完整性 | 已用表 + 未用表必须**互斥且覆盖除 GND/电源外的全部引脚**（本次 222 脚 = 106+88+28GND，一个不多一个不少） |
| **C** 未用信号反查 | 被判"未用"的信号不该出现在其他页面上 |
| **D** 页码列自检 | 表里写的页号要与原理图实际出现页有交集 |
| **E** 负载列器件号（弱判据） | 行里写的位号应能在该网络名附近找到。**大批量报错基本是 PDF 文字层排版导致的误报**，按组核对（同一 PHY / 同一 flash 看一次图就够），别逐条抠 |
| **F** 引出名≠Function0 | 见上节第 4 条 |

A/B/C/D 必须为 0 才能交付（脚本用退出码体现）；E 需按组人工裁决；F 必须逐条给出结论。

## 踩坑记录

### xlsx 侧

1. **表头不一定在第 1 行**。厂商表第 1 行常是标题+版本号（`MYC-YM62X Pin List ... Ver：1.0`），表头在第 2 行 → 导出加 `--skip-rows 1`。先跑 `--check` 看清结构。
2. **必须按单元格 `r="N6"` 引用定位列，不要按出现顺序数**。同一个文件里，有的行把空单元格也写出来、有的整行省略；按顺序数（`split('|')`/`csv.reader`）会串位。`xlsx_dump.py` 按 `r=` 解析，所以可靠。
3. **尾部列（Bootstrap/Voltage/Comment）靠列号取极易串位**。本次实战把 Bootstrap 当成了 Voltage，结果 BOOTMODE 那一行"电压"显示成 `BOOTMODE00`。稳妥做法：按内容特征取值（电压只会是 `0V/1V8/3V3/5V`），或用表头名反查列号。
4. **表里混着非引脚行**（如 `End of Pin List`），用引脚号形状正则过滤（`^(\d+|L\d+)$`）。
5. 引脚表列位各家不同 → 脚本都留了 `--name-col/--ball-col/--func-col-start/--func-count`，别硬改脚本。

### 原理图侧

6. **中文 EDA 原理图的约定**：位号/参数前缀 `@` = DNP（不贴），如 `R97 @0R/4/5%`、`@22`、`@10K-R2`；反过来带 `DNP_` 字样的也是不贴（`DNP_4.7K_1%`）。**判断某条支路是否生效必须看这个**。
7. **`0R` 交叉跳线**：I²C/时钟这类总线上常有一排 0R 电阻做"交叉点"，靠贴/不贴决定信号走哪去（本次 I²C0/I²C3 到 RTC 与 Type-C 控制器之间就是）。别只看到"有连线"就下结论，要看哪个是 DNP。
8. **strap 脚要单独处理**：BOOTMODE 这类复位采样脚"不占 mux"，但如果软件要读拨码状态，就得再 mux 成 GPIO —— 表里要两种都写明。
9. **同一网络可能有多个别名**（如 `SoC_I2C0_SCL` 与 `SoC_UART1_DCDn` 是同一根线），脚本比对时两侧都要做归一化。
10. **别把 SoC 级文件当板级内容**。核对"现有 BSP 已使能哪些外设"时，要区分**板级文件**（`board.dtsi/board.dts`）和 **SoC 级文件**（`k3-am62*.dtsi` 这种）。本次第一版就把 SoC 级内容混进来了，结论偏大。判据：只统计板级文件里的 `status="okay"`，注意**没有 `status` 属性的节点默认就是使能**（本次 `cpsw3g` 就是），不能只看有没有 `okay`。

### 方法层面

11. **别从残留 include/注释里判断硬件**。曾在设备树里看到 `#include <dt-bindings/net/ti-dp83867.h>` 就断定 PHY 是 DP83867，实际板上是 YT8531 —— 那个 include 是从 TI 参考设计带下来的残留，而 PHY 节点根本没有 `compatible`（靠 PHY ID 自动绑定）。**型号要从原理图/BOM 看，代码里的线索只能当辅助。**
12. **抽查不算复查**。第一次交付只抽查了 5 条就发了，后来做全量交叉复查时才发现 4 处真错。**结论性表格必须全量脚本化比对**（这正是 `verify_pinmap.py` 存在的原因）。
13. **交付前留存原文件哈希**（`sha256sum`），并在文档里写明"原文件只读未改"，客户很在意这点。

## 移植边界

| 情况 | 怎么办 |
|---|---|
| 模型**不支持多模态** / 视觉弱 | 见上文「模型能力要求」：文本层能做的照做，**看图才能定的全部标"待人工确认"，不要硬猜**；交付文档里注明哪些结论未经图像确认 |
| 原理图是扫描件（无文字层） | `pdftotext` 出空 → 只能走图像路线，用 Read 逐页读；或先 OCR（tesseract，中文需 chi_sim）再走流程 |
| 引脚表不是 xlsx（PDF/网页/CSV） | `pin_usage.py`/`pin_mux_table.py` 只要 TSV，`--name-col` 等参数指对列即可 |
| 多块底板共用一份核心板引脚表 | 引脚表只解析一次，逐板跑 ④⑤⑥ 步 |
| 原理图页数/连接器页号不同 | `--conn-page` 指定；页脚不是 `Sheet N of M` 时改 `page_re` |
| 需要出 Word/PDF 报告 | markdown 交付物用 pandoc 转，不要为格式牺牲可维护性 |

**不做的事**：不改原理图/引脚表原文件；不替客户判断 strap 电阻该不该贴（那是 BOM 的事，只列出来让他确认）；不在没有硬件的情况下改设备树。
