#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_deliverable_consistency.py — 交付物「内部一致性」复查：md ↔ xlsx。

为什么需要它：一份交付物是 .md（叙述）+ .xlsx（表格）两份文件，同一个位号可能在
两份里各写了一遍。**两边都是自己写的时侯，看哪一份都像对的** —— 典型事故：
md 把隔离电源的位号写成 U6，而同批 xlsx 器件清单写的是 U5，两份打架。
这类矛盾在交接时才会被客户发现。

根因通常是「位号↔型号」靠 PDF 文字层的邻近关系推出来的 —— 原理图里位号与型号
在文字流中常常不相邻，会错配（实战中连错 U3/U4、U5/U6、U16/Q5 三处）。
本脚本把"两份文件必须自洽"变成机器可查。

用法:
    check_deliverable_consistency.py --xlsx 引脚映射表.xlsx --md 硬件资源分析.md
                                     [--sheet 4] [--out report.txt]

三项检查:
  [1] md 里的「位号 型号」配对，与 xlsx 器件清单是否一致
        [硬] 位号在清单里、却绑了别的型号  → 真矛盾
        [待核] md 用了清单里没有的位号      → 补清单或改 md
        ↑ 两类都会让退出码 = 1
  [2] md 提到、但 xlsx 器件清单里没有的位号                    ← 提示（可能是用户自己加的）
  [3] xlsx 器件清单里有、但 md 完全没提到的位号                ← 提示（可能是漏写）

已知边界: 只比对 xlsx 里出现过的**型号字符串**；型号写法不同（如带 "×4" 后缀）算一致；
箭头（→）视为"非绑定关系"不参与配对（例："RXD=D14 → <型号>" 里的 D14 是 ball 号、
不是位号，不该和后面的型号配成对）。
"""

import argparse
import os
import re
import subprocess
import sys

# 位号形状：U3 / Q10 / L7 / FB1 / Y2 / CON7 / JP1 / SW4 / D14 / LCM1 …
REF = r'[UQD]\d{1,2}|L\d{1,3}|FB\d|Y\d|LCM\d|CON\d|JP\d|SW\d|PWR\d'
# 位号与型号之间的"绑定"分隔符：只允许空白与 = / :，显式排除 → -> | 等
BIND = r'\s*(?:[=＝:：]\s*)?'


def expand_refs(cell):
    """把 'U3/U4'、'SW1-SW4'、'Q1,Q2'、'L6813-L6820' 展开成位号集合。"""
    out = set()
    for part in re.split(r'[,，/、]', cell):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r'([A-Za-z]+)(\d+)\s*[-–~]\s*([A-Za-z]+)?(\d+)', part)
        if m:
            pre, a, pre2, b = m.group(1), int(m.group(2)), m.group(3) or m.group(1), int(m.group(4))
            if pre == pre2 and 0 <= b - a <= 40:
                out |= {f'{pre}{i}' for i in range(a, b + 1)}
                continue
        out.add(part)
    return out


def load_xlsx_devices(path, sheet):
    dump = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'xlsx_dump.py')
    r = subprocess.run([sys.executable, '-I', dump, path, '--sheet', str(sheet)],
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        sys.exit(f'读 xlsx 失败（--sheet {sheet} 对吗？）: {r.stderr.strip()[:200]}')
    model2refs, ref2model = {}, {}
    for line in r.stdout.splitlines()[1:]:
        f = [c.strip() for c in line.split('\t')]
        if len(f) < 2 or not f[0]:
            continue
        model = re.split(r'\s*[×x]\s*\d+\s*$', f[1])[0].strip()   # 去掉 "×4"
        if not model:
            continue
        refs = expand_refs(f[0])
        model2refs.setdefault(model, set()).update(refs)
        for k in refs:
            ref2model.setdefault(k, set()).add(model)
    return model2refs, ref2model


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--xlsx', required=True, help='引脚映射表.xlsx')
    ap.add_argument('--md', required=True, help='硬件资源分析.md')
    ap.add_argument('--sheet', default='4', help='器件清单所在 sheet（默认 4，也可以是名字）')
    ap.add_argument('--out', help='把报告同时写到文件')
    a = ap.parse_args()

    for p in (a.xlsx, a.md):
        if not os.path.isfile(p):
            sys.exit(f'找不到文件: {p}')
    model2refs, ref2model = load_xlsx_devices(a.xlsx, a.sheet)
    txt = open(a.md, encoding='utf-8').read()

    lines = []

    def say(s=''):
        lines.append(s)

    say(f'# 交付物一致性复查: {os.path.basename(a.md)} ↔ {os.path.basename(a.xlsx)}')
    say(f'器件清单: {len(ref2model)} 个位号 / {len(model2refs)} 个型号')
    say('')

    # ---- [1] md 里的「位号 型号」配对是否与清单一致 ----
    hard, soft = [], []
    for model, refs in sorted(model2refs.items()):
        if len(model) < 5:
            continue
        # 位号紧跟型号，且中间只有空白/=/:（箭头等不算绑定）
        pat = re.compile(rf'(?<![\w/(（、,])({REF})\b{BIND}({re.escape(model)})\b')
        for m in pat.finditer(txt):
            ref = m.group(1)
            if ref in refs:
                continue
            if ref in ref2model:          # 位号在清单里，却绑了别的型号 → 真矛盾
                hard.append((ref, model, sorted(refs), sorted(ref2model[ref])))
            else:                          # 位号根本不在清单里 → 漏收录 or md 写错
                soft.append((ref, model, sorted(refs)))
    say(f'[1] 「位号↔型号」冲突: 硬冲突 {len(hard)} + 待核 {len(soft)}')
    for ref, model, exp, got in hard:
        say(f'    ✗[硬] md 写 "{ref} {model}"，但清单里 {ref} = {got}，{model} 挂在 {exp}')
    for ref, model, exp in soft:
        say(f'    ?[待核] md 写 "{ref} {model}"，但清单里没有位号 {ref}'
            f'（{model} 挂在 {exp}）→ 要么清单漏收录 {ref}，要么 md 位号写错')
    say('')

    # ---- [2] md 提到但清单没有的位号 ----
    in_md = {m.group(1) for m in re.finditer(rf'\b({REF})\b', txt)}
    only_md = sorted(in_md - set(ref2model), key=lambda x: (re.sub(r'\d', '', x), len(x), x))
    say(f'[2] md 提到、器件清单里没有的位号（{len(only_md)}）: {only_md or "无"}')
    say('    （注意：D14/D4/D17 这类也可能是 ball 号，不一定是漏收录）')
    say('')

    # ---- [3] 清单里有但 md 没提 ----
    only_list = sorted(set(ref2model) - in_md, key=lambda x: (re.sub(r'\d', '', x), len(x), x))
    say(f'[3] 器件清单里有、md 完全没提到的位号（{len(only_list)}）: {only_list or "无"}')
    say('')

    report = '\n'.join(lines)
    print(report, end='')
    if a.out:
        open(a.out, 'w', encoding='utf-8').write(report + '\n')
        print(f'\n（报告已写入 {a.out}）')

    if hard:
        print(f'\n★ 硬冲突 {len(hard)} 处：同一批交付物里 md 与 xlsx 对同一位号给了不同型号，'
              f'必须读完图定案。', file=sys.stderr)
    if soft:
        print(f'\n? 待核 {len(soft)} 处：md 用了清单里没有的位号 —— 补清单或改 md，'
              f'别让两份文件不一致地交出去。', file=sys.stderr)
    sys.exit(1 if (hard or soft) else 0)


if __name__ == '__main__':
    try:
        main()
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
