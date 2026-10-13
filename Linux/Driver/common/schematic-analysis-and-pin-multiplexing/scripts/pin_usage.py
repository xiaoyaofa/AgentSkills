#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pin_usage.py — 判定"核心板引出的每个信号，底板到底用了没有"。

思路：原理图的**连接器页**（画核心板连接器的那一页）会把每个引出信号都列出来，
凡是被底板用到的，旁边会有一个指向其他页的交叉引用（页号）。没用到的什么也没有。
于是"这个信号有没有被用到" = "它在别的页面上有没有出现"。

为什么给两种判据：
  A) 该信号名是否出现在**非连接器页**上    —— 最可靠，但依赖网络名在别页被完整写出；
  B) 连接器页上该信号名附近是否有**页号**  —— 依赖 PDF 文字层把页号提取出来。
两法一致 → 高置信；不一致 → 逐条人工看原图（这类脚通常就是几个）。
电源/地脚（VDD_*/VSS_*/VCC_*）单独归为 PWR，不参与 用/未用 统计。

用法:
    pin_usage.py --pinlist pinlist.tsv --sch sch.compact.txt --out usage.tsv \
                 [--name-col 1] [--ball-col 2] [--conn-page 3] [--pages 12]

输入说明:
  --pinlist  由 xlsx_dump.py 从厂商引脚表导出的 TSV（一行一只脚）
  --sch      由 pdf_schematic_extract.sh 产出的 *.compact.txt
  --name-col 引脚表里"信号名"列（0 起）
  --conn-page 连接器页页码（默认 3，多数原理图把连接器放在第 3 页）
"""

import argparse
import re
import sys
from collections import OrderedDict


def split_pages(lines, npages, page_re=r'Sheet\s+(\d+)\s+of\s+(\d+)'):
    """按页脚 'Sheet N of M' 切页；返回 {页号: [行...]}"""
    ends = OrderedDict()
    rx = re.compile(page_re, re.I)
    for i, l in enumerate(lines):
        m = rx.search(l)
        if m:
            ends[int(m.group(1))] = i
    pages, prev = {}, 0
    for p in sorted(ends):
        pages[p] = lines[prev:ends[p] + 1]
        prev = ends[p] + 1
    if prev < len(lines):                      # 尾页无页脚时兜底
        pages.setdefault(len(pages) + 1, lines[prev:])
    return pages


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pinlist', required=True)
    ap.add_argument('--sch', required=True)
    ap.add_argument('--out', default='-')
    ap.add_argument('--name-col', type=int, default=1)
    ap.add_argument('--ball-col', type=int, default=2)
    ap.add_argument('--conn-page', type=int, default=3)
    ap.add_argument('--pages', type=int, default=12)
    ap.add_argument('--sku', default='SOC_,SoC_',
                    help='信号名在原理图里的前缀（逗号分隔，剥离后再匹配）')
    ap.add_argument('--skip', default='GND',
                    help='跳过的信号名（逗号分隔）')
    ap.add_argument('--header-rows', type=int, default=1,
                    help='引脚表开头要跳过的表头行数（还会自动识别含 FunctionN 的行）')
    ap.add_argument('--power-rx', default=r'^(VDD|VSS|VCC|GND|VBAT)',
                    help='电源/地脚命名正则：这类脚归为 PWR，不计入 用/未用 统计')
    a = ap.parse_args()

    lines = open(a.sch, encoding='utf-8', errors='replace').read().splitlines()
    pages = split_pages(lines, a.pages)
    if not pages:
        sys.exit('没能按页脚切页：确认 compact 文本里保留着 "Sheet N of M"（不要在提取时过滤掉）')
    others = '\n'.join(l for p, ls in pages.items() if p != a.conn_page for l in ls)
    conn = pages.get(a.conn_page, [])
    skip = {s.strip().upper() for s in a.skip.split(',') if s.strip()}
    power_rx = re.compile(a.power_rx, re.I)
    prefixes = [p.strip() for p in a.sku.split(',') if p.strip()]

    rows = []
    hdr_rx = re.compile(r'#?Function\s*\d', re.I)
    for li, line in enumerate(open(a.pinlist, encoding='utf-8')):
        f = line.rstrip('\n').split('\t')
        if len(f) <= max(a.name_col, a.ball_col):
            continue
        # 跳表头：开头 N 行，或任何单元格含 "FunctionN"（厂商引脚表的通用特征）
        if li < a.header_rows or any(hdr_rx.search(c) for c in f):
            continue
        pin, name = f[0].strip(), f[a.name_col].strip()
        ball = f[a.ball_col].strip()
        if not name or name.upper() in skip:
            continue

        # 原理图文本里可能是 "SoC_XXX"，引脚表里是 "SOC_XXX"；两者都试
        cands = [name]
        for pre in prefixes:
            if name.upper().startswith(pre.upper()):
                cands = [name[len(pre):], name]
                break
        pat = '|'.join(re.escape(c) for c in cands)
        rx = re.compile(r'(?<![A-Za-z0-9_])(' + pat + r')(?![A-Za-z0-9_])', re.I)

        hit_pages = sorted({p for p, ls in pages.items()
                            if p != a.conn_page and any(rx.search(l) for l in ls)})
        # 判据 B：连接器页上该名字附近的独立页号
        refs = set()
        for l in conn:
            for m in rx.finditer(l):
                # 左栏网络的页号写在名字前面，右栏写在后面 → 两侧都扫
                seg = l[max(0, m.start() - 12):m.end() + 12]
                pre, post = seg[:len(seg) - (m.end() - max(0, m.start() - 12))], seg[len(seg) - 12:]
                for part in (pre[-8:], post):
                    for n in re.findall(r'(?<![A-Za-z0-9_.])(\d{1,2})(?![A-Za-z0-9_.])', part):
                        if 1 <= int(n) <= len(pages) and int(n) != a.conn_page:
                            refs.add(int(n))
        if power_rx.match(name) or power_rx.match(f[a.name_col].strip()) \
                or power_rx.match(name.split('/')[0]):
            rows.append((pin, name, ball, [], [], 'PWR'))
            continue
        rows.append((pin, name, ball, hit_pages, sorted(refs), ''))

    out = sys.stdout if a.out == '-' else open(a.out, 'w', encoding='utf-8')
    used = 0
    n_sig = 0
    out.write('pin\tsignal\tball\tused\tby_A_pages\tby_B_refs\tconfidence\n')
    for pin, name, ball, hp, refs, forced in rows:
        if forced == 'PWR':
            out.write(f'{pin}\t{name}\t{ball}\tPWR\t\t\t电源/地脚，不计入统计\n')
            continue
        n_sig += 1
        u = bool(hp) or bool(refs)
        used += u
        if hp and refs:
            conf = 'A+B 一致'
        elif hp:
            conf = '仅A（B 未提取到页号，多为文字层排版）'
        elif refs:
            conf = '仅B（★ 别页没出现网络名，需看图确认）'
        else:
            conf = '-'
        out.write(f'{pin}\t{name}\t{ball}\t{"Y" if u else "N"}\t'
                  f'{",".join(map(str, hp))}\t{",".join(map(str, refs))}\t{conf}\n')
    if out is not sys.stdout:
        out.close()
        print(f'{a.out}: 信号 {n_sig} 个 → 判定"用到" {used} 个，未用 {n_sig - used} 个；'
              f'另电源/地脚 {len(rows) - n_sig} 个')
        print('提示：把 confidence 列不等于 "A+B 一致" 的挑出来，用 img_crop.py 看原图复核。')


if __name__ == '__main__':
    try:
        main()
    except BrokenPipeError:      # 接 head/less 时提前关管道，别打印一堆栈
        import os
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
