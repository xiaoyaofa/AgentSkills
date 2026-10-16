#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pdf_extract.py — pypdfium2 版原理图"双层提取"，用于【替代 poppler】。

把 pdf_schematic_extract.sh 依赖的三个外部命令
    pdfinfo / pdftotext -layout / pdftoppm -png
用一个纯 pip 库（pypdfium2，wheel 自带 PDFium 引擎）顶掉 —— 装完即用，无需任何系统包。

用法（与 .sh 对齐）:
    pdf_extract.py <原理图.pdf> <输出目录> [DPI=160]
    pdf_extract.py <原理图.pdf> <输出目录> 300 --render-only 8    # 只重渲染第 8 页（对应 poppler 的 -f/-l）

产出:
    <输出目录>/info.txt                    ≈ pdfinfo 关键字段
    <输出目录>/text/<name>.txt             ≈ pdftotext 原文（按页拼接）
    <输出目录>/text/<name>.compact.txt     ≈ 压缩空白（下游脚本主要读这个）
    <输出目录>/pages/pg-NN.png             ≈ pdftoppm -png（供 Read 工具通读/裁图）

依赖:  pip install pypdfium2 pillow
注意:  PDFium 的文字提取顺序按"阅读序"，与 poppler 的 -layout 严格版面序略有差异；
       判据 A（网络名是否出现在别页）不受影响；判据 B（页号相邻性）可能略弱，
       对可靠性要求极高时仍建议装 poppler。页脚 "Sheet N of M" 会被原样保留 → 切页正常。
"""

import argparse
import os
import re
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf')
    ap.add_argument('outdir')
    ap.add_argument('dpi', nargs='?', type=float, default=160.0)
    ap.add_argument('--render-only', default='',
                    help='只渲染这几页的 PNG（逗号分隔，如 8 或 3,8,12）——'
                         '对应 poppler 的 pdftoppm -f/-l；文字层仍逐页全取，不受影响')
    a = ap.parse_args()

    try:
        import pypdfium2 as pdfium
    except ImportError:
        sys.exit('缺 pypdfium2：请先  pip install pypdfium2 pillow')

    if not os.path.isfile(a.pdf):
        sys.exit(f'找不到文件: {a.pdf}')

    pdf = pdfium.PdfDocument(a.pdf)
    name = os.path.splitext(os.path.basename(a.pdf))[0]
    tdir = os.path.join(a.outdir, 'text')
    pdir = os.path.join(a.outdir, 'pages')
    os.makedirs(tdir, exist_ok=True)
    os.makedirs(pdir, exist_ok=True)

    n = len(pdf)
    with open(os.path.join(a.outdir, 'info.txt'), 'w', encoding='utf-8') as f:
        f.write(f'Title: {name}\nPages: {n}\nProducer: pypdfium2\n')

    only = {int(x) for x in re.split(r'[,\s]+', a.render_only.strip()) if x} if a.render_only.strip() else None
    raw_pages = []
    for i in range(n):
        pg = pdf[i]
        raw_pages.append(pg.get_textpage().get_text_range() or '')
        if only is None or (i + 1) in only:
            img = pg.render(scale=a.dpi / 72.0).to_pil()
            img.save(os.path.join(pdir, f'pg-{i + 1:02d}.png'))

    # .txt：页间用 \f 分隔（与 pdftotext 一致，保持原文）
    open(os.path.join(tdir, f'{name}.txt'), 'w', encoding='utf-8').write('\f'.join(raw_pages))

    # .compact.txt：逐页压缩空白，**每页末尾合成一行 "Sheet n of N" 作为切页锚点**。
    # 为什么合成：本 PDF 的标题栏页码值在 PDFium/MuPDF 下取不到（poppler 才有），
    # 会导致默认 page_re 切不出页。此处由"页对象"直接给出可靠页号 → 跨引擎/跨平台稳定。
    comp_lines = []
    for i in range(n):
        for ln in raw_pages[i].splitlines():
            c = re.sub(r'\s+', ' ', ln).strip()
            if c:
                comp_lines.append(c)
        comp_lines.append(f'Sheet {i + 1} of {n}')
    compact = '\n'.join(comp_lines)
    open(os.path.join(tdir, f'{name}.compact.txt'), 'w', encoding='utf-8').write(compact + '\n')

    foot = [l for l in compact.splitlines()
            if re.search(r'Sheet:?\s*\d+\s+of\s+\d+', l, re.I)]
    print(f'文本 : {tdir}/{name}.compact.txt  ({compact.count(chr(10)) + 1} 行)')
    print(f'整图 : {pdir}/pg-*.png  @{a.dpi:g}dpi  ({n} 页)')
    print(f'切页锚点(合成页脚) : {len(foot)} 个（= 页数则切页正常）')

    # ★ 退化页体检：**竖排/旋转标签**的页（连接器页最典型）在 PDFium 下会被拆成"一个字符一行"，
    #   整页文字层等于不可用 —— 工具不会崩，但依赖该页的判据会**静默归零**。
    #   实测某板连接器页 4021 行里 3852 行是单字符行，判据 B 从 poppler 的 39/189 掉到 0/189。
    #   主动报出来，别让下游以为"判据 B 跑了只是没命中"。
    degenerate = []
    for i in range(n):
        ls = [l.strip() for l in raw_pages[i].splitlines() if l.strip()]
        if len(ls) >= 50:
            single = sum(1 for l in ls if len(l) == 1)
            if single / len(ls) > 0.5:
                degenerate.append((i + 1, len(ls), single))
    if degenerate:
        print(f'★ 文字层退化页 {len(degenerate)} 个（竖排/旋转标签，PDFium 会逐字符换行）：')
        for p, tot, s in degenerate[:6]:
            print(f'    第 {p} 页：{tot} 行里 {s} 行是单字符（{s * 100 // tot}%）')
        if len(degenerate) > 6:
            print(f'    …… 另 {len(degenerate) - 6} 页')
        print('  → 这些页的**文字层不可用**（依赖该页的判据，如判据 B，会归零）。')
        print('     用 Read 读 pages/pg-NN.png 人工判读，或改用 pdf_schematic_extract.sh（poppler -layout）。')
        print('     交付物里要写明"判据 B 无产出 —— 不是未使用，是文字层取不到"。')

    print('下一步建议: 用 Read 通读 <outdir>/pages/pg-NN.png（框图页最重要）。')


if __name__ == '__main__':
    main()
