#!/usr/bin/env bash
# pdf_schematic_extract.sh — 原理图 PDF 双层提取【Linux/macOS 入口，走 poppler】
#
# 用法:
#   pdf_schematic_extract.sh <原理图.pdf> <输出目录> [渲染DPI=160]
#
# 产出:
#   <输出目录>/text/<name>.txt            pdftotext -layout 原始文本
#   <输出目录>/text/<name>.compact.txt    压缩空白后的文本（★ 分析主要看这个，体积可降到 1/20）
#   <输出目录>/pages/pg-NN.png            每页整图（用于通读/裁图）
#   <输出目录>/info.txt                   PDF 元信息（页数/尺寸/旋转）
#
# 依赖: poppler-utils (pdftotext / pdftoppm / pdfinfo)
#
# ★ 平台分工（本 skill 的 PDF 提取有两个入口，按平台挑一个，不要混用）:
#     本脚本 .sh  → Linux/macOS，走 poppler，零 pip 依赖；pdftotext -layout 是**严格版面序**
#     pdf_extract.py → Windows（无系统包），走 pypdfium2；取字是 PDFium「阅读序」，
#                      判据 B（连接器页页号相邻性）会略弱 → 结论务必用图复核。
#   本脚本**不会**在缺 poppler 时静默降级到 Python 入口 —— 那会把"用了弱文字层"藏起来。
#   缺依赖时直接报错并给出替代方案（见下），由调用方显式决定换哪条路。
#
# 为什么要两层:
#   - 文字层给出 **网络名/器件位号的准确拼写**（grep/脚本用），且能拿到交叉引用页号；
#   - 图像层给出 **拓扑关系**（谁接谁、上下拉、门电路类型）——文字层的排版顺序不可靠。
#   只靠文字层会读错方向（如把"调试器→SoC"的总线方向搞反），只靠图像会拼错名字。

set -euo pipefail

PDF="${1:?用法: $(basename "$0") <原理图.pdf> <输出目录> [DPI]}"
OUT="${2:?缺少输出目录}"
DPI="${3:-160}"

[ -f "$PDF" ] || { echo "找不到文件: $PDF" >&2; exit 1; }

if ! { command -v pdftotext >/dev/null 2>&1 && command -v pdftoppm >/dev/null 2>&1; }; then
    cat >&2 <<'EOF'
缺 poppler-utils（pdftotext / pdftoppm）。两条路选一条：

  · 装 poppler 后重跑本脚本（Linux/macOS 首选，零 pip 依赖，版面序最严格）：
      Debian/Ubuntu : sudo apt install poppler-utils
      RHEL/Fedora   : sudo dnf install poppler-utils
      macOS         : brew install poppler
      Windows(scoop): scoop install poppler

  · 或改用同目录的 Python 入口（Windows 上就用这个；纯 pip，不需要系统包）：
      pip install pypdfium2 pillow
      python3 <本目录>/pdf_extract.py "<原理图.pdf>" "<输出目录>" 160
    注意：它用 PDFium 取字，是「阅读序」而非 -layout 严格版面序 → 判据 B 会略弱，
          结论需要用图复核，并在交付文档里注明本次走的是哪条路。
EOF
    exit 1
fi

NAME="$(basename "${PDF%.*}")"
mkdir -p "$OUT/text" "$OUT/pages"

pdfinfo "$PDF" > "$OUT/info.txt" 2>&1 || true
grep -E '^(Title|Pages|Page size|Page rot)' "$OUT/info.txt" || true

pdftotext -layout "$PDF" "$OUT/text/$NAME.txt"
# 压缩连续空白：行数通常减半、字符数降到 5%，且不丢任何 token
sed -E 's/[[:space:]]+/ /g; s/^ //; /^$/d' \
    "$OUT/text/$NAME.txt" > "$OUT/text/$NAME.compact.txt"

pdftoppm -r "$DPI" -png "$PDF" "$OUT/pages/pg"

echo "---"
echo "[路线] poppler（pdftotext -layout，严格版面序）"
echo "文本 : $OUT/text/$NAME.compact.txt  ($(wc -l < "$OUT/text/$NAME.compact.txt") 行)"
echo "整图 : $OUT/pages/pg-*.png  @${DPI}dpi"
echo "页边界（compact 文本里的行号，便于按页定位）:"
grep -n 'Sheet .* of ' "$OUT/text/$NAME.compact.txt" | tail -20 || true
echo
echo "下一步建议: 先用 Read 工具通读 '整图'（框图页最重要），"
echo "再用 img_crop.py 对关键区块裁 300dpi 局部图确认拓扑。"
