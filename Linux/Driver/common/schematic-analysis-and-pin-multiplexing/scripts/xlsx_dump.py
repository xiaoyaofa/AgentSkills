#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""xlsx_dump.py — 免依赖读取 .xlsx，导出为 TSV / 文本。

为什么要自己解析：客户机器上常常没装 openpyxl / pandas，而 .xlsx 本身就是
一个 zip + XML，标准库 zipfile + xml.etree 足够读，不联网、不装包。

用法:
    xlsx_dump.py book.xlsx                          # 列出 sheet 名 + 行列数
    xlsx_dump.py book.xlsx --sheet 2                # 导出第 2 个 sheet 为 TSV
    xlsx_dump.py book.xlsx --sheet "Pin List"       # 按名字导出
    xlsx_dump.py book.xlsx --sheet 1 --skip-rows 1  # 丢掉第 1 行标题（表头在第 2 行时用）
    xlsx_dump.py book.xlsx --all --outdir out       # 每个 sheet 各导一个 .tsv
    xlsx_dump.py book.xlsx --sheet 1 --check        # 体检：表头在哪、空单元格是否被省略

★ 关键坑（都是实战踩过的）
  1) **必须按单元格自带的 r="N6" 引用定位列，不要按出现顺序数**。
     同一个文件里，有的行会把空单元格也写出来，有的行整格省略 ——
     按顺序数就会串位。本脚本按 r= 引用解析，所以是可靠的；
     而 `line.split('|')` / `csv.reader` 那种按位置的读法在厂商表上会翻车。
  2) **表头不一定在第 1 行**。厂商表第 1 行常是标题+版本号
     （如 "MYC-YM62X Pin List ... Ver：1.0"），表头在第 2 行 → 导出时加 --skip-rows 1。
  3) **尾部列（Bootstrap / Voltage / Comment）靠列号取极易串位**。
     实战中曾把 Bootstrap 列当成 Voltage 列，结果 BOOTMODE 那一行的"电压"
     显示成 "BOOTMODE00"。稳妥做法：按内容特征取值（电压只会是 0V/1V8/3V3/5V …），
     或用表头名反查列号，不要硬编码列索引。
"""

import argparse
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter

NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'


def letter(i):
    """0 -> 'A', 25 -> 'Z', 26 -> 'AA'"""
    s, i = '', i + 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def col_idx(ref):
    """'BC12' -> 54 (0-based col index)"""
    m = re.match(r'([A-Z]+)', ref or '')
    if not m:
        return 0
    n = 0
    for ch in m.group(1):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def load(path):
    """-> [ {name, cells(稀疏 dict), written(每行写出的 <c> 个数)} ]"""
    if not zipfile.is_zipfile(path):
        sys.exit(f'不是 xlsx/zip 文件: {path}')
    z = zipfile.ZipFile(path)

    shared = []
    try:
        root = ET.fromstring(z.read('xl/sharedStrings.xml'))
        for si in root.findall(NS + 'si'):
            shared.append(''.join(t.text or '' for t in si.iter(NS + 't')))
    except KeyError:
        pass

    wb = ET.fromstring(z.read('xl/workbook.xml'))
    names = [sh.get('name') for sh in wb.iter(NS + 'sheet')]

    out = []
    for i in range(1, len(names) + 1):
        fn = f'xl/worksheets/sheet{i}.xml'
        if fn not in z.namelist():
            continue
        root = ET.fromstring(z.read(fn))
        cells, written = [], []
        for row in root.iter(NS + 'row'):
            d = {}
            cs = row.findall(NS + 'c')
            written.append(len(cs))
            for c in cs:
                t, v, isel = c.get('t'), c.find(NS + 'v'), c.find(NS + 'is')
                if t == 's' and v is not None:
                    val = shared[int(v.text)]
                elif t == 'inlineStr' and isel is not None:
                    val = ''.join(x.text or '' for x in isel.iter(NS + 't'))
                elif v is not None:
                    val = v.text
                else:
                    val = ''
                if val:
                    d[col_idx(c.get('r'))] = val.strip()
            cells.append(d)
        out.append({'name': names[i - 1], 'cells': cells, 'written': written})
    return out


def to_matrix(cells):
    if not cells:
        return []
    width = max((max(r) + 1) for r in cells if r) if any(cells) else 0
    return [[r.get(i, '') for i in range(width)] for r in cells]


def main():
    # ★ 强制 stdout/stderr 走 UTF-8：中文 Windows 下 Python 默认按 GBK(cp936) 写 stdout，
    #   而调用方（如 check_deliverable_consistency.py）多半按 UTF-8 读 —— 于是 sheet 名/列名
    #   读成乱码（[5] 假报"列名不符"），内容里有 GBK 编不出的字符（↔ → ≤）时直接崩。
    #   被 `-I` 拉起时（`-I` 蕴含 `-E`）PYTHONIOENCODING 是无效的，只能这样在进程内改。
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding='utf-8')
        except (AttributeError, ValueError):     # 被重定向成非文本流时忽略
            pass

    ap = argparse.ArgumentParser()
    ap.add_argument('xlsx')
    ap.add_argument('--sheet', help='sheet 名或从 1 开始的序号')
    ap.add_argument('--all', action='store_true', help='导出所有 sheet')
    ap.add_argument('--outdir', default='.', help='--all 时的输出目录')
    ap.add_argument('--check', action='store_true', help='体检（不导出）')
    ap.add_argument('--skip-rows', type=int, default=0,
                    help='导出时丢掉前 N 行（表头在第 2 行 → --skip-rows 1）')
    ap.add_argument('--sep', default='\t')
    a = ap.parse_args()

    sheets = load(a.xlsx)

    # ★ `--check` 不给 --sheet 时也要逐 sheet 体检 —— 否则只打印 sheet 清单，
    #   用户按文档"先体检确认表头在第几行"跑了却看不到表头行号（实测就是这么混进一行表头的）。
    if not a.sheet and not a.all and not a.check:
        print(f'# {os.path.basename(a.xlsx)} 共 {len(sheets)} 个 sheet')
        for i, s in enumerate(sheets, 1):
            m = to_matrix(s['cells'])
            print(f'  {i}. {s["name"]}  ({len(m)} 行 x {(len(m[0]) if m else 0)} 列)')
        return

    todo = sheets if (a.all or not a.sheet) else [
        s for i, s in enumerate(sheets, 1) if a.sheet in (str(i), s['name'])
    ]
    if not todo:
        sys.exit(f'找不到 sheet: {a.sheet}（可选: {[s["name"] for s in sheets]}）')

    for s in todo:
        mat = to_matrix(s['cells'])

        if a.check:
            w = s['written']
            dist = Counter(w)
            print(f'# {s["name"]}: {len(w)} 行')
            print(f'  每行写出的单元格个数分布: {dict(sorted(dist.items()))}')
            hdr = next((i for i, r in enumerate(s['cells'])
                        if sum(1 for k in r if r[k]) >= 3), 0)
            print(f'  表头可能在第 {hdr + 1} 行，列: '
                  f'{" ".join(letter(c) for c in sorted(s["cells"][hdr]))}')
            # ★ 把要用的参数**算好写出来** —— 文档里"表头常在第 2 行"只是经验，实测有表头在
            #   第 3 行的（第 1 行标题、第 2 行日期），照文档抄 --skip-rows 1 就会把表头混进数据。
            print(f'  → 要**纯数据**用 `--skip-rows {hdr + 1}`；'
                  f'要**连表头一起保留**（下游脚本会自动跳表头行）用 `--skip-rows {hdr}`'
                  if hdr else '  → 该 sheet 首行即表头/数据，无需 --skip-rows')
            mode = dist.most_common(1)[0][0] if dist else 0
            if len(dist) > 1:
                odd = [i + 1 for i, c in enumerate(w) if c != mode]
                print(f'  ⚠ 有 {len(odd)} 行的写出单元格数与多数行({mode})不同 '
                      f'→ 存在"整格省略"（前几行: {odd[:8]}）。')
                print('     结论：取值必须按 r= 引用（本工具已如此），'
                      '不要按出现顺序数，也不要硬编码尾部列索引。')
            else:
                print('  ✓ 每行写出的单元格数一致，列位置稳定。')
            print('  首行内容: ' + ' | '.join(
                f'{letter(k)}={v}' for k, v in sorted(s['cells'][hdr].items()))[:200])
            continue

        mat = mat[a.skip_rows:]
        if a.all:
            os.makedirs(a.outdir, exist_ok=True)
            fp = os.path.join(a.outdir, re.sub(r'[^\w.-]+', '_', s['name']) + '.tsv')
            with open(fp, 'w', encoding='utf-8') as f:
                for r in mat:
                    f.write(a.sep.join(r) + '\n')
            print(f'已写出 {fp}  ({len(mat)} 行)')
        else:
            for r in mat:
                print(a.sep.join(r))


if __name__ == '__main__':
    try:
        main()
    except BrokenPipeError:      # 接 head/less 时提前关管道，别打印一堆栈
        import os
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
