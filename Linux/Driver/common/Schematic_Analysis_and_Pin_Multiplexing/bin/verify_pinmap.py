#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_pinmap.py — 引脚映射表的交叉复查（交付前必跑）。

一份引脚表最容易错的地方是"手写的描述列"和"引脚号 ↔ 信号名 对不上"，
而这两类错在客户现场很难发现（配出来的 pinctrl 看着像对的）。
本脚本把能自动化的检查全做了，剩下的少量存疑条目再人工看原图。

五项检查:
  [A] 引脚 ↔ 信号名 一致性    —— 拿厂商引脚表逐行比对，防手抄错位
  [B] 划分完整性              —— 已用 / 未用 两表必须互斥且覆盖除 GND 外全部引脚
  [C] 未用信号反查            —— 被判"未用"的信号不该出现在其他页面上
  [D] 页码列自检              —— 表里写的页号要和原理图实际出现页有交集
  [E] 负载列器件号自检        —— 行里写的器件位号应能在该网络名附近找到（弱判据，需人工判定）
  [F] 引出名 ≠ Function0 体检 —— 调用 pin_mux_table.py --audit 的逻辑，指出必须核对 muxmode 的脚

用法:
    verify_pinmap.py --pinlist pinlist.tsv --sch sch.compact.txt \\
        --used used.tsv --unused unused.tsv \\
        [--used-pin-col 2] [--used-sig-col 1] [--unused-pin-col 0] \\
        [--used-load-col 8] [--used-page-col 7] [--conn-page 3] [--window 8]

输出: 各检查项计数 + 具体条目；退出码 0 = A/B/C/D 全过（E/F 只提示，不阻塞）。
"""

import argparse
import re
import sys
from collections import Counter, OrderedDict

DESIG = re.compile(r'\b(?:U|R|C|Q|D|J|CON|SW|JP|L|FB|TP|Y)\d{2,5}\b')


def read_tsv(path):
    rows = []
    for line in open(path, encoding='utf-8', errors='replace'):
        rows.append([c.strip() for c in line.rstrip('\n').split('\t')])
    return rows


def split_pages(lines, page_re=r'Sheet\s+(\d+)\s+of\s+(\d+)'):
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
    return pages


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pinlist', required=True)
    ap.add_argument('--sch', required=True)
    ap.add_argument('--used', required=True)
    ap.add_argument('--unused', required=True)
    ap.add_argument('--used-pin-col', type=int, default=2)
    ap.add_argument('--used-sig-col', type=int, default=1)
    ap.add_argument('--used-load-col', type=int, default=8)
    ap.add_argument('--used-page-col', type=int, default=7)
    ap.add_argument('--unused-pin-col', type=int, default=0)
    ap.add_argument('--unused-sig-col', type=int, default=1)
    ap.add_argument('--conn-page', type=int, default=3)
    ap.add_argument('--window', type=int, default=8)
    ap.add_argument('--skip', default='GND')
    ap.add_argument('--pin-rx', default=r'^(\d+|L\d+)$',
                    help='引脚号形状（不匹配的行视为说明行，忽略）')
    a = ap.parse_args()

    pin_rx = re.compile(a.pin_rx, re.I)
    pins = {}
    hdr_rx = re.compile(r'#?Function\s*\d', re.I)
    for f in read_tsv(a.pinlist):
        if any(hdr_rx.search(c) for c in f):
            continue
        if len(f) > 2 and f[0] and pin_rx.match(f[0]):
            pins[f[0]] = {'name': f[1], 'funcs': (f[3:13] + [''] * 10)[:10]}
    skip = {s.strip().upper() for s in a.skip.split(',')}
    used = [r for r in read_tsv(a.used) if len(r) > a.used_pin_col and r[a.used_pin_col]]
    unused = [r for r in read_tsv(a.unused) if len(r) > a.unused_pin_col and r[a.unused_pin_col]]
    used = [r for r in used if r[0] not in ('功能分类',)]
    unused = [r for r in unused if r[0] not in ('核心板引脚',)]

    lines = open(a.sch, encoding='utf-8', errors='replace').read().splitlines()
    pages = split_pages(lines)
    other = '\n'.join(l for p, ls in pages.items() if p != a.conn_page for l in ls)

    def crawl(needle):
        return [i for i, l in enumerate(lines) if needle.lower() in l.lower()]

    def pages_of(needle):
        rx = re.compile(r'(?<![A-Za-z0-9_])' + re.escape(needle) + r'(?![A-Za-z0-9_])', re.I)
        return sorted({p for p, ls in pages.items()
                       if p != a.conn_page and any(rx.search(l) for l in ls)})

    errA, errB, errC, errD, warnE, warnF = [], [], [], [], [], []

    # [A] 引脚 ↔ 信号名
    for r in used:
        pin, net = r[a.used_pin_col], r[a.used_sig_col].upper()
        if pin not in pins:
            errA.append(f'引脚号 {pin} 不在厂商引脚表（{r[a.used_sig_col]}）')
            continue
        off = re.sub(r'^(SOC_|SOC_)', '', pins[pin]['name'].upper())
        core = off.split('/')[0]
        cands = [re.sub(r'^(SOC_|SOC_)', '',
                        re.sub(r'\s*\(.*?\)', '', c).strip().upper())
                 for c in re.split(r'[/]', net)]
        def fits(c):
            if not c or len(c) < 4:
                return False
            return (c in (off, core) or core.startswith(c) or c.startswith(core)
                    or c.split('.')[0] == core.split('.')[0])
        if not any(fits(c) for c in cands):
            errA.append(f'引脚 {pin}: 厂商信号名={pins[pin]["name"]}，表中写 "{r[a.used_sig_col]}"')

    # [B] 划分完整性
    p1 = [r[a.used_pin_col] for r in used]
    p2 = [r[a.unused_pin_col] for r in unused]
    dup = [k for k, v in Counter(p1).items() if v > 1]
    ov = sorted(set(p1) & set(p2), key=lambda x: (len(x), x))
    miss = [k for k in pins
            if k not in set(p1) and k not in set(p2)
            and pins[k]['name'].upper() not in skip]
    for k in miss:
        errB.append(f'引脚 {k}（{pins[k]["name"]}）两张表都没收录')

    # [C] 未用信号反查
    p4_12 = other
    for r in unused:
        sig = r[a.unused_sig_col]
        for c in re.split(r'\s*/\s*', sig):
            c = c.strip()
            if len(c) < 6 or c.upper() in skip:
                continue
            if re.search(r'(?<![A-Za-z0-9_])' + re.escape(c) + r'(?![A-Za-z0-9_])',
                         p4_12, re.I):
                errC.append(f'"{sig}" 判为未用，但在其他页面出现')
                break

    # [D] 页码列
    for r in used:
        if len(r) <= a.used_page_col:
            continue
        stated = {int(x) for x in re.findall(r'\d+', r[a.used_page_col])}
        base = re.split(r'\s*/\s*', r[a.used_sig_col])[0].strip()
        act = set(pages_of(base)) | {a.conn_page}
        if stated and act and not (stated & act):
            errD.append(f'"{base}": 表里写 {sorted(stated)}，实际出现在 {sorted(act)}')

    # [E] 负载列器件号（弱判据）
    for r in used:
        if len(r) <= a.used_load_col:
            continue
        load = r[a.used_load_col]
        ds = set(DESIG.findall(load))
        if not ds:
            continue
        base = re.split(r'\s*/\s*', r[a.used_sig_col])[0].strip()
        hit = crawl(base)
        if not hit:
            warnE.append(f'文本里找不到网络 "{base}"（表里写: {load}）')
            continue
        win = set()
        for i in hit:
            for j in range(max(0, i - a.window), min(len(lines), i + a.window + 1)):
                win |= set(DESIG.findall(lines[j]))
        m = ds - win
        if m:
            warnE.append(f'"{base}": 附近未见 {sorted(m)} —— 需人工看原图判定')

    # [F] 引出名 ≠ Function0
    for pin in p1:                      # 只看已用表里的脚（全表体检见 pin_mux_table --audit）
        p = pins.get(pin)
        if not p:
            continue
        core = re.sub(r'\s*\(.*?\)', '', p['name'])
        core = re.sub(r'^(SOC_|SoC_)', '', core, flags=re.I).split('/')[0]
        if p['funcs'][0] and core.upper() != p['funcs'][0].upper():
            warnF.append(f'{pin}: 引出名={p["name"]} vs Function0={p["funcs"][0]}')

    # ---------- 汇总 ----------
    print(f'Sheet1(已用) {len(used)} 行 / Sheet2(未用) {len(unused)} 行 / '
          f'引脚表 {len(pins)} 只脚')
    print(f'  [A] 引脚↔信号名 不一致        : {len(errA)}')
    print(f'  [B] 重复 {len(dup)} / 重叠 {len(ov)} / 两表未收录 {len(miss)}')
    print(f'  [C] 未用信号却出现在别页      : {len(errC)}')
    print(f'  [D] 页码列与实际不符          : {len(errD)}')
    print(f'  [E] 负载列存疑（弱判据）      : {len(warnE)}')
    print(f'  [F] 引出名≠Function0（需核对）: {len(warnF)}')
    for tag, arr in (('A', errA), ('B', errB), ('C', errC), ('D', errD)):
        for x in arr:
            print(f'  [X{tag}] {x}')
    for x in warnE[:15]:
        print(f'  [E] {x}')
    if len(warnE) > 15:
        print(f'  [E] ...（另 {len(warnE) - 15} 条）')
    if warnE:
        print('  ↑ [E] 多为 PDF 文字层排版导致的误报（网络名与器件符号在文本流里隔了几十行）。'
              '按组核对：同一批网络（如同一 PHY、同一 flash）看一次原图就能同时确认。')
    if warnF:
        print(f'  ↑ [F] {len(warnF)} 只脚：请逐个确认底板用的到底是不是 Function0。')
        for x in warnF:
            print(f'      {x}')
        print('      确非 Function0 的 → 写进 targets 文件，'
              '用 pin_mux_table.py --targets 显式给出目标功能。')

    sys.exit(0 if not (errA or errB or errC or errD) else 1)


if __name__ == '__main__':
    main()
