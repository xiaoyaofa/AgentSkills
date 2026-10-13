# Windows 上跑这个 skill

本 skill 原本按 Ubuntu 写，依赖 `poppler-utils` + `LibreOffice` 两个系统软件。**Windows 上没有这些系统包也能跑通**——本目录提供纯 pip 的替代入口，功能等价、产出格式一致。

> 在 Linux/macOS 上照旧用原来的 `.sh` / `table_to_xlsx.py` 即可，**不用改**。

## 一、平台 → 用哪个入口

| 环节 | Linux / macOS（有系统包） | Windows / 无系统包 |
|---|---|---|
| PDF 双层提取 | `pdf_schematic_extract.sh`（poppler） | **`pdf_extract.py`**（pypdfium2） |
| xlsx 导出 | `table_to_xlsx.py`（FODS + LibreOffice） | **`table_to_xlsx_openpyxl.py`**（openpyxl） |
| 其余脚本 | `xlsx_dump.py`、`pin_usage.py`、`pin_mux_table.py`、`verify_pinmap.py`、`img_crop.py` | 同一份，跨平台通用 |

**两条 PDF 路线不等价，别当成同一个东西**：

| | `pdf_schematic_extract.sh`（poppler） | `pdf_extract.py`（pypdfium2） |
|---|---|---|
| 取字顺序 | `pdftotext -layout`，**严格版面序** | PDFium「阅读序」，与 `-layout` 有差异 |
| 判据 A（网络名是否在别页出现） | 强 | 不受影响 |
| 判据 B（连接器页上页号相邻性） | 强 | **略弱** |
| 页脚页码 | 能从标题栏取到 | 取不到 → **逐页合成** `Sheet n of N` 锚点 |

所以走 pypdfium2 时，**结论要重点用图复核**，并在交付文档里注明"本次走的是 pypdfium2 路线"。

## 二、装什么（一条 pip 命令）

```bash
pip install pypdfium2 pillow openpyxl
```

| 库 | 顶替了什么 |
|---|---|
| `pypdfium2` | poppler 的 `pdftotext` + `pdftoppm` + `pdfinfo`（wheel 自带 PDFium 引擎） |
| `pillow` | `img_crop.py` 裁图；pypdfium2 渲染转 PIL 也要它 |
| `openpyxl` | LibreOffice（直接写 .xlsx） |

不想用 pip 的话，Windows 也可以装真 poppler：`scoop install poppler` / `choco install poppler`，或用 [oschwartz10612/poppler-windows](https://github.com/oschwartz10612/poppler-windows) 的 release 解压后把 `Library\bin` 加进 `PATH`，然后照 Linux 的用法走 `.sh`（Git Bash / WSL 下）。

## 三、Windows 标准流程

```bash
pip install pypdfium2 pillow openpyxl
BIN=<skill>\scripts
set OUT=D:\work\项目名

:: ① 引脚表 → TSV（表头常在第 2 行，先 --check 看清）
python %BIN%\xlsx_dump.py 引脚表.xlsx --check
python %BIN%\xlsx_dump.py 引脚表.xlsx --sheet 1 --skip-rows 1 > %OUT%\pinlist.tsv

:: ② 原理图双层提取（不需要 poppler）
python %BIN%\pdf_extract.py 原理图.pdf %OUT%\sch 160

:: ③ 用/未用（★ --conn-page 必须填实际连接器页号）
python %BIN%\pin_usage.py --pinlist %OUT%\pinlist.tsv ^
       --sch %OUT%\sch\text\*.compact.txt --conn-page 3 --out %OUT%\usage.tsv

:: ④ 反查 muxmode（纯文本，跨平台最稳的一步）
python %BIN%\pin_mux_table.py --pinlist %OUT%\pinlist.tsv --audit

:: ⑤ 出 xlsx（免 LibreOffice）
python %BIN%\table_to_xlsx_openpyxl.py %OUT%\引脚映射表.xlsx ^
       --sheet "0-说明:%OUT%\notes.tsv:2.6,11" ^
       --sheet "1-已用信号:%OUT%\sheet1.tsv:2,3.2,2.2,1.6,2.8,4.2"
```

> `--sheet "表名:路径[:列宽]"` 里**路径带盘符是支持的**（`D:\work\used.tsv` 或 `D:/work/used.tsv` 都行）：解析规则是"第一个 `:` 前是表名，末尾那段只有形如 `2,3.5` 才当列宽"。

```bash
:: 交付前必跑（二）：交付物内部一致性（md / 已用表负载列 的位号↔型号 ↔ 器件清单）
python %BIN%\verify_pinmap.py --pinlist %OUT%\pinlist.tsv ^
       --sch %OUT%\sch\text\*.compact.txt --used %OUT%\sheet1.tsv --unused %OUT%\sheet2.tsv
python %BIN%\check_deliverable_consistency.py ^
       --xlsx %OUT%\引脚映射表.xlsx --md %OUT%\硬件资源分析.md
```

## 四、注意事项

1. **编码**：脚本一律按 UTF-8 读写 TSV。Windows 上生成 TSV 时别用记事本另存成 GBK；用 `python ... > x.tsv` 重定向或 VS Code 指定 UTF-8 即可。脚本已兼容 CRLF 行尾。
2. **通配符**：`cmd.exe` 不展开通配符，`--sch ...\text\*.compact.txt` 在 cmd 下可能不展开；改用 **Git Bash / PowerShell**，或直接把文件名写全。
3. **连接器页号**：各家原理图不同（有的在第 3 页，有的在很后面），**必须用 `--conn-page` 指定**。填错会看到 `连接器页 N 不在切出的页号 [...] 中` 的提示。
4. **页脚格式**：默认认 `Sheet N of M` 和 `Sheet: N of M` 两种。别的格式用 `--page-re` 指定；若报"切页失败（仅 1 页）"，就是页脚没匹配上——**不要忽略这个错**，静默回退会让所有信号都被判成"用到"。
   > 用 `pdf_extract.py` 时基本不会遇到：它每页末尾会**合成一行 `Sheet n of N`**，不依赖 PDF 标题栏里的页码值（那个值 PDFium/MuPDF 取不到，只有 poppler 能取）。
5. **扫描件原理图**（无文字层）：PDFium 同样取不到字 → 只能走"渲染 PNG + 多模态读图"，文字层的自动化判据（A/B、引脚表交叉核对）全部失效，务必在交付文档里说明。
6. **多模态读图**：`pdf_extract.py` 依赖 `pillow` 渲染 PNG。整页缩略图上的小字读不准，**必须对关键区块单独 300dpi 渲染 + `img_crop.py` 裁图**再看——这是漏读/误读的主要来源。
