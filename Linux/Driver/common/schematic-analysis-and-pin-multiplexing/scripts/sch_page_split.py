#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sch_page_split.py — 按【物理页】切分原理图文字层（模式 B 专用）。

为什么需要它：
    模式 A 的 pin_usage.py 依赖 `.compact.txt` 里的 "Sheet N of M" 页脚锚点切页。
    但有些 EDA（实测 Altium）导出的 PDF 页脚只印 "Sheet     of"，**页码值不在文字层里**
    —— 按页脚切会切不出页，或者把多页并成一页，后面的页码列会整体偏移。
    本脚本直接按 pdftotext / PDFium 都保留的 **换页符 \\f** 切物理页，不信任页脚。

用法:
    python3 sch_page_split.py <out>/text/<name>.txt --out <out>/text [--pages N]
    python3 sch_page_split.py <out>/text/<name>.txt --pdf-info <out>/info.txt

产出（都在 --out 目录）:
    pg-01.txt / pg-01.compact.txt …   每页原始文本 + 压缩空白文本
    page_index.tsv                    物理页 / 页脚标注 / 行数 / 单字符行占比 / 文字层状态
                                       （md 的「原理图页索引」可直接照它写）

退出码:
    0  正常；1  页数与 info.txt/--pages 不一致（或文字层为空）

注意：
    · poppler 的 `pdftotext` 与 pypdfium2 路线的 `.txt` 都以 \\f 分隔页，二者通用；
    · 页脚标注只作为**辅助信息**写进 page_index.tsv，绝不用于切页/编号；
    · 单字符行占比 > 50% 视为「文字层退化」（竖排/旋转标签常见），只告警不阻断。
"""

# ---------------------------------------------------------------------------
# ★ 强制 UTF-8 输出（Windows 中文环境必需）
#   Windows 中文环境默认编码是 GBK(cp936)：脚本里只要有箭头/数学符号就会崩，
#   而且往往崩在 print 那一步，前面算完的结果一条都出不来。
#   ★ 被 `-I` 拉起时（`-I` 蕴含 `-E`）PYTHONIOENCODING / PYTHONUTF8 都无效，只能进程内改；
#     `-I` 还蕴含 `-P`，脚本目录不进 sys.path，别改成 import 共享模块。
# ---------------------------------------------------------------------------
import sys as _sys

for _s in (_sys.stdout, _sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass
if _sys.platform == 'win32':
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    except Exception:
        pass


import argparse
import os
import re
import sys


def read_pages(path):
    """按换页符切物理页；丢掉末尾的空段（pdftotext 每页后都补一个 \\f）。"""
    with open(path, encoding='utf-8', errors='replace') as f:
        text = f.read()
    parts = text.split('\f')
    while parts and not parts[-1].strip():
        parts.pop()
    return parts


def compact(text):
    """压缩空白：与模式 A 的 .compact.txt 口径一致，便于 grep。"""
    lines = []
    for ln in text.splitlines():
        c = re.sub(r'\s+', ' ', ln).strip()
        if c:
            lines.append(c)
    return '\n'.join(lines) + ('\n' if lines else '')


def footer_of(text):
    """取该页的页脚标注（只用于 page_index.tsv，不参与切页）。"""
    for ln in text.splitlines():
        s = re.sub(r'\s+', ' ', ln).strip()
        if re.search(r'\bSheet\b', s, re.I) and re.search(r'\bof\b', s, re.I):
            return s[:100]
    return '—'


def page_stats(text):
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    single = sum(1 for ln in lines if len(ln) == 1)
    ratio = (single / len(lines)) if lines else 0.0
    status = '退化' if (len(lines) >= 50 and ratio > 0.5) else '正常'
    return len(lines), single, ratio, status


def expected_pages(a):
    """优先 --pages；否则读 <out>/info.txt 的 Pages:。"""
    if a.pages:
        return a.pages, '--pages'
    info = a.pdf_info
    if not info:
        # 默认：<out>/text/<name>.txt → <out>/info.txt
        info = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(a.src))), 'info.txt')
    if os.path.isfile(info):
        with open(info, encoding='utf-8', errors='replace') as f:
            m = re.search(r'^Pages:\s*(\d+)', f.read(), re.M | re.I)
        if m:
            return int(m.group(1)), info
    return None, ''


def main():
    ap = argparse.ArgumentParser(description='按物理页切分原理图文字层')
    ap.add_argument('src', help='pdftotext / pdf_extract.py 产出的 <name>.txt')
    ap.add_argument('--out', help='输出目录（默认与 src 同目录）')
    ap.add_argument('--pages', type=int, help='期望页数（默认从 info.txt 的 Pages: 读）')
    ap.add_argument('--pdf-info', help='info.txt 路径（默认 <out>/info.txt）')
    ap.add_argument('--allow-mismatch', action='store_true',
                    help='页数与期望不一致时只告警、不退出 1（不建议）')
    a = ap.parse_args()

    if not os.path.isfile(a.src):
        sys.exit(f'找不到文件: {a.src}')

    pages = read_pages(a.src)
    if not any(p.strip() for p in pages):
        sys.exit('文字层为空 —— 可能是扫描件；模式 B 需先 OCR，或只能走图像路线。')

    want, src_info = expected_pages(a)
    out = a.out or os.path.dirname(os.path.abspath(a.src))
    os.makedirs(out, exist_ok=True)

    rows = []
    for i, txt in enumerate(pages, 1):
        base = f'pg-{i:02d}'
        with open(os.path.join(out, base + '.txt'), 'w', encoding='utf-8') as f:
            f.write(txt)
        with open(os.path.join(out, base + '.compact.txt'), 'w', encoding='utf-8') as f:
            f.write(compact(txt))
        n, single, ratio, status = page_stats(txt)
        rows.append((i, footer_of(txt), n, single, f'{ratio:.0%}', status))

    idx = os.path.join(out, 'page_index.tsv')
    with open(idx, 'w', encoding='utf-8') as f:
        f.write('物理页\t页脚标注\t非空行\t单字符行\t单字符行占比\t文字层\n')
        for r in rows:
            f.write('\t'.join(map(str, r)) + '\n')

    print(f'# 物理页切分: {os.path.basename(a.src)}')
    print(f'  页数 = {len(pages)}' + (f'（期望 {want}，来自 {src_info}）' if want else '（未提供期望页数）'))
    print(f'  输出: {out}/pg-NN.txt, pg-NN.compact.txt')
    print(f'  索引: {idx}')
    for i, foot, n, single, ratio, status in rows:
        warn = '  ★ 文字层退化，依赖该页的判据会归零' if status == '退化' else ''
        print(f'    P{i}: 非空行 {n:4d} / 单字符 {single:4d} ({ratio:>4s}) / {status} / 页脚「{foot}」{warn}')

    if want and len(pages) != want:
        msg = (f'页数不一致：切出 {len(pages)} 页，期望 {want} 页'
               f'（{src_info or "--pages"}）。物理页可能含空页/换页符异常，'
               f'请核对 PDF 页数与 .txt 里的换页符。')
        if a.allow_mismatch:
            print('★ ' + msg)
        else:
            sys.exit('✗ ' + msg)
    return 0


if __name__ == '__main__':
    sys.exit(main())
