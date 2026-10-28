# Changelog

本文件记录仓库结构变更与各技能版本。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [1.3.0] - 2026-10-22

`schematic-analysis-and-pin-multiplexing` 升级到 `1.3.0`（其余 4 个技能未变）。

### Added

- **模式 B 功能块框图**（`control_map_svg.py --style block`）：
  `layout.tsv`（功能块坐标/样式/端口）+ `zones.tsv`（分区容器）+ `edges.tsv`（正交连线）
  → 确定性 SVG；含块名/位号/端口、电源粗线、控制/中断/I2C 线型、图例、标题栏、网格坐标
- `[S8]` 框图布局/连线完整性检查：块 id 唯一、区域/端口存在、边不悬空、
  每个 control/power 行至少一条边、块位号在 devices.tsv、manifest 与 layout+edges 一致
- `[S4]` 增强：连线不能穿过非端点功能块、块不重叠、文字不越界
- 框图设计规范（`references/schematic-only.md` B6.3）：依据 IEC 61082-1 / IEEE 315 /
  MIL-DTL-24784D 与微软/Xilinx 框图惯例
- **接口详图**（B6.4）：SoC/MCU 板在系统框图之外，对关键接口各生成一张
  `<板名>-<接口>-连接详图.svg`（SoC 左、器件右，逐信号标 `信号名 → 脚号`，
  0R/上拉/DNP/NC 放 note 块）；数据放 `过程记录/detail/`，md 加「接口详图索引」
- `control_map_svg.py --diagonal`：接口详图用“水平出线 → 中段斜线交叉 → 水平进端口”，
  TX/RX 交叉不再共用竖直通道而重合；主控制关系图默认仍正交
- `control_map_svg.py --min-height`：详图画布最小高度可调（默认 1250）；
  块高按端口数收缩后传 900，收掉画布底部空白，主图仍用 1250
- 接口详图的“模式/上下拉” `strap` 块：上 VCC、下 GND、竖电阻列，
  每行 `net;上拉位号;阻值;UP|NC;下拉位号;阻值;DOWN|NC`，DNP/NC 灰色虚线标
  `DNP / NC`，能直接看出有没有上拉/下拉
- `table` 块：模式表画成原理图样式的网格表（`sub` 用 `|` 分行、`;` 分列，首行表头）；
  `CFG_LDO[1:0]` 与 `RXD[3:1]` 各一张表，不合并
- 接口详图的多通道/多颗器件按通道拆块（CAN1/CAN2、RS485-1/2、LVDS0/LVDS1），
  不再用 `U20/U22 ... ×2` 合并；每通道电源也画成独立块（`VDD_3V3` / `ISO_5V`）
- `scripts/svg_to_png.py`：SVG→PNG 预览，优先本机 Edge/Chrome（headless 截图），
  再 Inkscape/LibreOffice/cairosvg；Windows 一般自带 Edge，无需 poppler/LibreOffice；
  PNG 只是预览，不属于交付物，`[S1]~[S8]` 不依赖它
- `assets/block-{zones,layout,edges}-template.tsv` 与
  `assets/block-detail-{zones,layout,edges}-template.tsv`

### Changed

- 模式 B 的 SVG 默认推荐 `--style block`；`--style table` 保留为行数很多时的保底
- **分区容器按成员块自动收紧**（只缩不放），块越出分区直接报错，不再出现“框跑出背景框”
- **布线改为最短路径优先**：先试同轴直连，再用 A*（代价=长度+拐弯+并行重叠）绕开功能块；
  端口先垂直出线 14px，`折点` 可留空由程序布线
- **rail 网络标签块支持 `宽=auto 高=auto`**，按标题文字收缩；端口 offset 支持 `50%`
- **端口名自动避让**功能块与走线；边标签缩到文字大小，并在最长线段上找空位，找不到就不标
- 画布宽度按内容自适应（仍不小于 2250）
- **分区对齐**：左/上边保持 `zones.tsv` 声明值，只收右/下边；并排分区在 40px 内的
  上/下边自动对齐
- **SoC 接口块支持 `宽=auto 高=auto`**（200×45 → 约 140×42），文字**一律以 `SoC` 开头**、
  用箭头表达方向（`SoC → I2C4`、`SoC ← INT_N`、`SoC → OTG_PWR_CTRL`）
- 块右上角页码徽标由 `P2` 改为 `p.2`，图例注明“源原理图页码，不是本图页号”；
  标题栏写“本图 1/1”
- 图例/标题栏上移 30px，与画布底边留出间距；接口/调试区放在左下、与图例左对齐
- `[S4]` 新增“块外文字压住功能块”检查；`[S8]` 新增块越界、端口偏移越界、
  rail 块过大、端口间距 <20px（箭头太短）检查
- 布局模板改为 auto rail/soc + `50%` 端口，并修正示例块越界
- 边标签背景宽度改为整数像素，消除 Windows/Linux 在 `.65` 处的 0.1px 舍入差异，
  `control_map_svg.py --check` 在 Windows 实测字节一致
- 交付物契约、md 模板、Windows 流程、SKILL.md 工具表同步

## [1.2.0] - 2026-10-22

`schematic-analysis-and-pin-multiplexing` 升级到 `1.2.0`（其余 4 个技能未变）。

### Added

- **模式 B：仅原理图分析**（`references/schematic-only.md`）—— 只有原理图 PDF、
  没有引脚表时，产出 `<板名>-原理图分析.md` + `<板名>-控制关系图.svg`：
  控制源 → 控制器件/引脚 → 开关/通道 → 目标/动作，另加电源树；面向硬件与嵌入式软件工程师
- `scripts/sch_page_split.py`：按**物理页**切文字层（页脚 `Sheet  of` 没有页码时也能用），
  产出 `pg-NN.txt` 与 `page_index.tsv`
- `scripts/control_map_svg.py`：控制关系/电源树 TSV → 确定性 SVG + md 三张表；
  `--check` 可复验字节一致
- `scripts/check_schematic_report.py`：模式 B 交付物校验 `[S1]~[S7]`（文件名/章节/
  三方一致/SVG 几何/禁用词/电源引用/待确认闭环）+ `--selftest`
- `assets/schematic-only-output-contract.md`、`assets/schematic-only-report-template.md`、
  `assets/{control-map,power-tree,devices}-template.tsv`

### Changed

- `SKILL.md` 开头新增「先分流」：有引脚表走模式 A，只有原理图走模式 B；
  description 改为两种用法
- 工具清单补 3 个脚本，脚本数 10 → 14（顺手修正原来少算的 1 个）
- 模式 A 的 `[A]~[I]` 流程、交付物与契约**未改动**

## [1.1.0] - 2026-10-20

`schematic-analysis-and-pin-multiplexing` 升级到 `1.1.0`（其余 4 个技能未变）。

### Added

- **Windows 兼容路线**：`pdf_extract.py`（走 pypdfium2，纯 pip，无需 poppler）与
  `table_to_xlsx_openpyxl.py`，配套 `scripts/README-windows.md`
- **交付物格式契约与模板**：`assets/output-contract.md`、`assets/report-template.md`、
  `assets/pinmap-template.xlsx`（5 个 sheet 的骨架），把「产物形态」纳入机器校验
- `check_deliverable_consistency.py`：交付物内部一致性复查（md ↔ xlsx）
- `dts_pinmux_audit.py`：拿板级 dts 的 pinctrl 反查「哪些脚真被换了功能」

### Changed

- 查漏依据从 1 条扩到 4 条：引脚表自洽反查 / 板级 dts 对拍 / 原理图侧自动查 / 读图
- 交付前复查从 `[A]~[F]` 扩到 `[A]~[I]`
- 10 个脚本统一强制 UTF-8 输出（中文 Windows 兼容）

### Fixed

- 中文 Windows 上子进程编码导致的阻塞
- `--audit` 存在盲区，权威判据改为 `muxmode != 0`
- 切页不再相信页脚里印的页码，改为机器核对
- 「未判定」被默认写成「无需复用」
- `table_to_xlsx.py` 转换后残留中间 `.fods`，污染输出目录

## [1.0.0] - 2026-10-13

仓库格式规范化，5 个技能版本统一升级为 `1.0.0`。

### Added

- `.claude-plugin/marketplace.json`：仓库成为可安装的插件市场（市场名 `xiaoyaofa-agentskills`），
  5 个技能各自作为独立插件，可单独安装
- 每个技能目录新增 `.claude-plugin/plugin.json`
- `README.md`、`LICENSE`（MIT）、`CHANGELOG.md`、`.gitignore`
- `SKILL.md` frontmatter 补齐 `license` 与 `metadata`
  （承载 `category`/`platform`/`soc`/`board`/`vendor`/`customer` 等分类信息）

### Changed

- 技能入口文件 `skill-<name>.md` → `SKILL.md`，满足标准 skill 发现机制
- 目录名统一 kebab-case 并与 frontmatter `name` 对齐：
  - `Schematic_Analysis_and_Pin_Multiplexing` → `schematic-analysis-and-pin-multiplexing`
  - `mem_stress` → `mem-stress`
  - `stm32mp257` → `stm32mp257-overlayfs`
- `bin/` 按内容语义拆分：可执行脚本 → `scripts/`，预编译产物 → `assets/`
- `tebiandiangong` 保留为客户分类层，其下新建插件根 `c7z020-driver-dev`
- 统一 `description` 标点风格（半角括号／冒号／引号 → 全角与「」）；
  技术词 `WIC/flashlayout`、`pdftotext`、`300dpi` 保留半角斜杠

### Fixed

- `schematic-analysis-and-pin-multiplexing`：正文写「见 `bin/` —— 6 个」与实际
  7 个脚本不符，已改为 7 个；正文中 10 处 `bin/` 路径引用同步更新，避免断链
- `c7z020-driver-dev`：`description` 补上客户名，避免客户标识只存在于目录路径里

### Removed

- 冗余的 `Linux/Driver/.gitkeep`、`Linux/System/.gitkeep`（父目录已有真实子项）

### 后续约定

- 技能内容变更后必须 bump 对应 `plugin.json` 的 `version`，否则已安装用户拿不到更新
