#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dts_pinmux_audit.py — 查漏法**判据②**：用板级 dts 的 pinctrl 反查"到底哪些脚被换了功能"。

为什么需要它（实战教训）：
  「底板用了哪些脚、用成什么功能」有三个依据，**缺一不可，任一条单独用都会漏**：
    ① 引脚表自洽反查          → pin_mux_table.py --audit
    ② 板级 dts 的 pinctrl 对拍 → 本脚本   ★ 原理图是厂商自家 EVK 时，这就是标准答案
    ③ 读图                     → 客户自研板只能靠这个
  实测一块厂商 EVK：① 看不出 GPMC0_WAIT1→UART6_RXD（引出名与 Function0 同名），漏 2 只；
  ② 一眼查出 10 只，其中 8 只是 ① 报不出来的。

★ 关键：**不能只 grep "第三个参数 ≠ 0"**。厂商 dts 里到处是
  · 从芯片厂 SK 板抄来、压根没被引用的引脚组（`pruss_uart_pins` 之类）；
  · 可选外设的叠加层 `.dtso`（LVDS 屏 / HDMI 音频），装了才生效；
  · 同一批脚的多套互斥配置（GPIO vs 通信），只有一套被 `pinctrl-N` 引用。
  本次实测：naive grep 得到 38 处 ≠0，其中 20 处是上面这些噪声 —— 直接采信会写出错误的交付物。
  所以本脚本只认「**被 `pinctrl-N` 真正引用到的引脚组**」，并把孤儿组与叠加层分开列出。

用法:
    python3 dts_pinmux_audit.py --dts-dir arch/arm64/boot/dts/myir \\
            --entry myd-am62x-6252.dts --pinlist pinlist.tsv
    # 加 --used sheet1.tsv 就把交付物现状一起对拍（列名按交付物契约第 3/6 列）

输出三块：
    [A] 基础配置里 muxmode≠0 的脚（**这就是答案**）
    [B] 只在叠加层里 ≠0 的脚（装了对应外设才生效，要写进交付物的"可选配置"）
    [C] 被引用了但查不到 ball / 或孤儿组 —— 噪声，别采信
"""

import argparse
import glob
import os
import re
import sys

# ★ 主域是 AM62X_IOPAD，**MCU 域是 AM62X_MCU_IOPAD** —— 只匹配主域会整片漏掉 MCU 脚的复用
#   （实测一块板上 MCU_GPIO0_0/2/15/16 这几只全在 MCU 域，漏了就等于 §7.1 少半张表）
IOPAD = re.compile(
    r'([A-Z0-9_]*IOPAD)\(\s*0x([0-9a-fA-F]+)\s*,\s*([A-Z_]+)\s*,\s*(\d)\s*\)'
    r'\s*/\*\s*(?:\(([^)]*)\))?\s*([^*]*)\*/')
# 组定义：  label: node-name {  ... };
GROUP = re.compile(r'^\s*([A-Za-z0-9_]+)\s*:\s*([A-Za-z0-9_\-]+)\s*\{')
DTS_SUFFIX = ('.dts', '.dtsi', '.dtso')


def norm_ball(b):
    """`(B19/B18)` → B19：ALW/AMC 双封装注释，取第一个（引脚表用 ALW）。"""
    return b.split('/')[0].strip()


INC = re.compile(r'^\s*#\s*include\s+"([^"]+)"')


def follow_includes(path, seen=None):
    """沿 `#include "..."` 递归收集**参与这块板编译**的文件（`<...>` 系统头忽略）。

    ★ 为什么必须按入口收：一个厂商 dts 目录里通常躺着同系列的多个变体
      （`...-6252.dts` / `-6254.dts` / `-6231.dts`）和多个叠加层 `.dtso`，
      它们的引脚组名往往一模一样（复制来的）。**全目录一起扫 = 把别的板的配置
      算到本板上**，结果可能多报也可能盖掉。实测这三个变体恰好内容相同所以没出事，
      但换个厂商就是真错。
    """
    seen = set() if seen is None else seen
    p = os.path.abspath(path)
    if p in seen or not os.path.isfile(p):
        return seen
    seen.add(p)
    for line in open(p, encoding='utf-8', errors='replace'):
        m = INC.match(line)
        if m:
            follow_includes(os.path.join(os.path.dirname(p), m.group(1)), seen)
    return seen


NODE = re.compile(r'^\s*&([A-Za-z0-9_]+)\s*\{')


def enclosing_node(lines, i):
    """找到第 i 行所属的 `&node { ... }` 顶层块，返回 (节点名, 是否 enabled)。

    判定方式：往回找最近一个 `&name {`，再从这里往后扫到配对的 `};`，
    块内出现 `status = "disabled"` 就算没使能（K3 的板级覆盖全是这个形状）。
    """
    start = None
    for k in range(i, -1, -1):
        m = NODE.match(lines[k])
        if m:
            start = k
            break
    if start is None:
        return ('?', True)
    depth = 0
    for k in range(start, len(lines)):
        depth += lines[k].count('{') - lines[k].count('}')
        if k > start and depth <= 0:
            break
    body = '\n'.join(lines[start:k + 1])
    return (NODE.match(lines[start]).group(1),
            not re.search(r'status\s*=\s*"disabled"', body))


def scan(path):
    """返回 {组名: {'node':.., 'pads':[(ball, attr, muxmode, fn)], 'line':..}}"""
    groups, cur, depth = {}, None, 0
    for i, line in enumerate(open(path, encoding='utf-8', errors='replace'), 1):
        m = GROUP.match(line)
        if m and cur is None:
            cur = m.group(1)
            groups[cur] = {'node': m.group(2), 'pads': [], 'line': i, 'file': path}
        if cur is not None:
            for p in IOPAD.finditer(line):
                _, _off, attr, mm, ball, desc = p.groups()
                if not ball:
                    continue
                fn = desc.split('.')[-1].strip() if '.' in desc else desc.strip()
                groups[cur]['pads'].append((norm_ball(ball), attr, int(mm), fn))
            depth += line.count('{') - line.count('}')
            if depth <= 0 and (';' in line or '}' in line) and groups[cur]['pads']:
                cur, depth = None, 0
            elif depth <= 0 and not groups[cur]['pads']:
                cur, depth = None, 0
    return groups


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dts-dir', help='厂商 dts 目录')
    ap.add_argument('--entry', help='板级入口 .dts；给了就只扫它的 #include 链，'
                                   '不给则把目录下所有 .dts/.dtso 一起扫（会合并多块板/变体）')
    ap.add_argument('--pinlist', help='厂商引脚表 TSV（用来 ball → 核心板脚位）')
    ap.add_argument('--used', help='交付物 sheet1 TSV（第 3 列=核心板引脚，第 6 列=底板所需功能）')
    ap.add_argument('--name-col', type=int, default=1)
    ap.add_argument('--ball-col', type=int, default=2)
    a = ap.parse_args()

    if not (a.dts_dir or a.entry):
        ap.error('至少给 --dts-dir 或 --entry 之一')
    if a.entry and not os.path.isfile(a.entry) and a.dts_dir:
        cand = os.path.join(a.dts_dir, a.entry)
        if os.path.isfile(cand):
            a.entry = cand

    if a.entry:
        if not os.path.isfile(a.entry):
            sys.exit(f'找不到入口文件: {a.entry}')
        # 叠加层 .dtso 不是被 #include 的 —— 它们各自编译、运行时叠加，所以单独收不跟 include 链
        ddir = a.dts_dir or os.path.dirname(os.path.abspath(a.entry))
        ovl = sorted(f for f in glob.glob(os.path.join(ddir, '*.dtso')))
        files = sorted(set(follow_includes(a.entry)) | set(ovl))
        print(f'按入口 {os.path.basename(a.entry)} 的 #include 链 + {len(ovl)} 个叠加层，'
              f'共 {len(files)} 个文件')
    else:
        files = sorted(f for f in glob.glob(os.path.join(a.dts_dir, '*')) if f.endswith(DTS_SUFFIX))
        print(f'★ 没给 --entry：整个目录 {len(files)} 个 .dts/.dtso 一起扫 —— '
              f'同系列的多块板/多个变体的引脚组会被合并。能指定入口就指定。')
    if not files:
        sys.exit(f'{(a.dts_dir or a.entry)} 下没有 .dts/.dtsi/.dtso')

    allgroups, overlays = {}, set()
    for f in files:
        if f.endswith('.dtso'):
            overlays.add(os.path.basename(f))
        # 只留真的带 IOPAD 的组（`psci:`/`thermal_zones:` 这类同名样式不含引脚）
        allgroups.update({g: v for g, v in scan(f).items() if v['pads']})
    if not allgroups:
        sys.exit('没解析出任何引脚组：确认这是 TI K3 风格的 dts（AM62X_IOPAD 宏）')

    # ---- 哪些组真的被引用、且引用它的节点是 enabled ----
    # 引用一定带 `&`，定义是 `label:` —— 靠这个区分。
    # ★ 光"被引用"不够：`&mcasp1 { status = "disabled"; }` 也写着 pinctrl-0，
    #   但 disabled 的节点不会去申请引脚，那些脚其实是空的。实测这一条差掉 4 处虚警。
    refs = {g: [] for g in allgroups}
    for f in files:
        lines = open(f, encoding='utf-8', errors='replace').read().splitlines()
        base = os.path.basename(f)
        for i, line in enumerate(lines):
            for g in allgroups:
                if not re.search(r'&\s*' + re.escape(g) + r'\b', line):
                    continue
                node, enabled = enclosing_node(lines, i)
                if enabled:
                    refs[g].append(f'{base}:{node}')
                else:
                    refs[g].append(f'{base}:{node}(disabled)')

    base, opt, off, noise = [], [], [], []
    for g, info in allgroups.items():
        if not refs[g]:
            noise.append((g, info, '没有被任何 pinctrl-N 引用（多半是从芯片厂 SK 板抄来的残留）'))
            continue
        in_overlay = any(r.split(':')[0] in overlays for r in refs[g])
        enabled = any(not r.endswith('(disabled)') for r in refs[g])
        for ball, attr, mm, fn in info['pads']:
            if mm == 0:
                continue
            row = (ball, mm, fn, g, ', '.join(refs[g]))
            if not enabled:
                off.append(row)
            elif in_overlay:
                opt.append(row)
            else:
                base.append(row)

    # ---- ball → 核心板脚位 ----
    pinof = {}
    if a.pinlist:
        for line in open(a.pinlist, encoding='utf-8'):
            f = [x.strip() for x in line.rstrip('\n').split('\t')]
            if len(f) > max(a.name_col, a.ball_col) and f[0] and not f[0].lower().startswith('pin'):
                pinof[f[a.ball_col]] = (f[0], f[a.name_col])

    def show(rows, title):
        print(f'\n=== {title}：{len(rows)} 处 ===')
        for ball, mm, fn, g, src in sorted(rows, key=lambda r: r[0]):
            som, name = pinof.get(ball, ('—', ''))
            print(f'  {ball:>5} | 核心板脚 {som:>5} | {name:<26} | muxmode {mm} → {fn:<18} | {g}')

    print(f'扫了 {len(files)} 个文件：{len(allgroups)} 个引脚组，'
          f'其中 {len(refs) - sum(1 for g in allgroups if not refs[g])} 个被引用')
    show(base, '[A] 基础配置里 muxmode≠0 的脚（**这就是答案**）')
    show(opt, '[B] 只在叠加层 .dtso 里 ≠0（装了对应外设才生效，写进"可选配置"）')
    show(off, '[D] 引用它的节点是 disabled —— **不算数**，这些脚并没被占用（虚警）')
    # ---- 同一只脚被两个 enabled 的节点配成不同功能 → dts 真冲突 ----
    # ★ 这里要连 muxmode == 0 的也算：冲突常常正是"一个按 Function0 用、另一个换走了"。
    claims = {}
    for g, info in allgroups.items():
        if not refs[g] or not any(not r.endswith('(disabled)') for r in refs[g]):
            continue
        for ball, attr, mm, fn in info['pads']:
            claims.setdefault(ball, set()).add((mm, fn or 'Function0', g))
    clash = {b: v for b, v in claims.items() if len({x[0] for x in v}) > 1}
    if clash:
        print(f'\n=== [E] ★ 同一只脚被配成不同功能（两个节点都 enabled，谁后 probe 谁赢）：{len(clash)} 处 ===')
        for b, v in sorted(clash.items()):
            som, name = pinof.get(b, ('—', ''))
            ways = ' / '.join(f'{fn}(mux {mm}) ← {g}' for mm, fn, g in sorted(v))
            print(f'  {b:>5} | 核心板脚 {som:>5} | {name:<24} | {ways}')
        print('  → 这是设备树里的真冲突，交付物里要写成待确认，别默默按某一个写。')


    print(f'\n=== [C] 孤儿组（引用不到，别采信）：{len(noise)} 个 ===')
    for g, info, why in noise:
        print(f'  {g:<34} {len(info["pads"])} 只脚  —— {why}')

    # ---- 与交付物对拍 ----
    if a.used and a.pinlist:
        sheet = {}
        for line in open(a.used, encoding='utf-8'):
            f = line.rstrip('\n').split('\t')
            if len(f) >= 6 and f[0] != '功能分类':
                sheet[f[2].strip()] = f[5]
        print('\n=== 与交付物「底板所需功能」列对拍 ===')
        bad = 0
        for ball, mm, fn, g, src in sorted(base, key=lambda r: r[0]):
            som = pinof.get(ball, ('', ''))[0]
            cur = sheet.get(som)
            if cur is None:
                print(f'  ✗ ball {ball} 核心板脚 {som}：pinctrl 说是 {fn}(mux {mm})，'
                      f'但交付物里查无此脚'); bad += 1
            elif f'{mm}' not in cur and 'Function0' in cur:
                print(f'  ✗ ball {ball} 核心板脚 {som}：pinctrl 说 {fn} (muxmode {mm})，'
                      f'交付物写「{cur}」'); bad += 1
        print(f'  —— 不一致 {bad} 处' + ('（必须逐条裁决后再交付）' if bad else ' ✅'))
        if bad:
            sys.exit(1)


if __name__ == '__main__':
    main()
