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
                                     [--sheet 4] [--used-sheet 2] [--load-col 8]
                                     [--no-used-sheet] [--out report.txt]

四项检查:
  [1] md 里的「位号 型号」配对，与 xlsx 器件清单是否一致
        [硬] 位号在清单里、却绑了别的型号  → 真矛盾
        [待核] md 用了清单里没有的位号      → 补清单或改 md
        ↑ 两类都会让退出码 = 1
  [2] md 提到、但 xlsx 器件清单里没有的位号                    ← 提示（可能是用户自己加的）
  [3] xlsx 器件清单里有、但 md 完全没提到的位号                ← 提示（可能是漏写）
  [4] xlsx 自己「已用表 → 负载/目标列」里的「位号 型号」是否与器件清单一致
        —— 与 [1] 同性质，只是矛盾出在同一份 xlsx 内部（已用表 vs 器件清单）
        ↑ 冲突同样让退出码 = 1；不适用时用 --no-used-sheet 关掉

已知边界与处理:
  · 只比对 xlsx 里出现过的**型号字符串** —— 所以替代不了"逐页读图校订器件清单"；
  · **以 + 结尾的型号会被归一化**（清单里 "ABC123+" 与文本里 "ABC123"
    视为同一型号）。早期版本用 `\b` 收尾，导致这类型号**永远匹配不上、等于没查**；
  · 带 "×4" 后缀、"U3/U4"、"SW1-SW4"、"L6813-L6820" 等写法都会自动展开比对；
  · 箭头（→）视为"非绑定关系"不参与配对（例："RXD=D14 → <型号>" 里的 D14 是 ball 号、
    不是位号，不该和后面的型号配成对）；
  · 走 pypdfium2 路线时，[1]/[4] 的结论不受影响（矛盾是文本层面的，与引擎无关）。
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
# 型号后面"跟着的必须不是字母数字" —— 用这个而不是 \b：\b 要求两侧一个是词字符，
# 于是 "ABC123+" 这类**以 + 结尾的型号永远匹配不上**（+ 与后面的空格/括号之间
# 没有词边界），等于这类型号从没被查过。改成断言"下一个字符不是词字符"即可。
TAIL = r'(?![A-Za-z0-9_])'


def norm_model(model):
    """归一化型号，供跨文件比对：去掉尾部的 + / - / 空格，去掉 '×4' 这类后缀。
    'ABC123+' 与文本里的 'ABC123' 应视为同一型号。"""
    m = re.split(r'\s*[×x]\s*\d+\s*$', model)[0].strip()
    return m.rstrip('+－- ').strip()


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
        model = norm_model(f[1])
        if not model:
            continue
        refs = expand_refs(f[0])
        model2refs.setdefault(model, set()).update(refs)
        for k in refs:
            ref2model.setdefault(k, set()).add(model)
    return model2refs, ref2model


def scan_bindings(text, model2refs, ref2model):
    """在 text 里找「位号 型号」绑定，返回 (hard, soft) 两组冲突。

    hard：位号在器件清单里，却绑了别的型号  → 真矛盾，必须读完图定案
    soft：位号不在清单里                    → 清单漏收录 or 位号写错
    """
    hard, soft = [], []
    for model, refs in sorted(model2refs.items()):
        if len(model) < 5:
            continue
        # 位号紧跟型号，中间只允许空白/=/:（箭头 → 不算绑定）
        pat = re.compile(rf'(?<![\w/(（、,])({REF})\b{BIND}{re.escape(model)}{TAIL}')
        for m in pat.finditer(text):
            ref = m.group(1)
            if ref in refs:
                continue
            if ref in ref2model:
                hard.append((ref, model, sorted(refs), sorted(ref2model[ref])))
            else:
                soft.append((ref, model, sorted(refs)))
    return hard, soft


def load_used_column(path, sheet, col):
    """取「1-底板已用信号」表的某一列（默认负载/目标列），拼成一段文本供 scan_bindings 扫。"""
    dump = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'xlsx_dump.py')
    r = subprocess.run([sys.executable, '-I', dump, path, '--sheet', str(sheet)],
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    rows = [l.split('\t') for l in r.stdout.splitlines() if l.strip()][1:]
    return '\n'.join(x[col] for x in rows if len(x) > col)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--xlsx', required=True, help='引脚映射表.xlsx')
    ap.add_argument('--md', required=True, help='硬件资源分析.md')
    ap.add_argument('--sheet', default='4', help='器件清单所在 sheet（默认 4，也可以是名字）')
    ap.add_argument('--used-sheet', default='2', help='已用信号表所在 sheet（默认 2）')
    ap.add_argument('--load-col', type=int, default=8,
                    help='已用表里「负载/目标」列的下标（默认 8，与本 skill 的列定义一致）')
    ap.add_argument('--no-used-sheet', action='store_true',
                    help='跳过 [4]（已用表负载列）检查')
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

    all_hard, all_soft = [], []

    # ---- [1] md 里的「位号 型号」配对是否与清单一致 ----
    hard, soft = scan_bindings(txt, model2refs, ref2model)
    all_hard += hard
    all_soft += soft
    say(f'[1] md「位号↔型号」冲突: 硬冲突 {len(hard)} + 待核 {len(soft)}')
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

    # ---- [4] xlsx 自己的「已用表 → 负载/目标列」是否与器件清单一致 ----
    # 这是 [1] 的同胞检查：交付物内部也可能自相矛盾（同一份 xlsx 里，
    # 已用表的负载列写 "U4 <某型号>"，而器件清单写该型号挂在 U3）。
    if not a.no_used_sheet:
        used_txt = load_used_column(a.xlsx, a.used_sheet, a.load_col)
        if used_txt is None:
            say(f'[4] 跳过：读不到 sheet {a.used_sheet}（用 --used-sheet 指定，'
                f'或 --no-used-sheet 显式关掉）')
            say('')
        else:
            h4, s4 = scan_bindings(used_txt, model2refs, ref2model)
            all_hard += h4
            all_soft += s4
            say(f'[4] 已用表负载列(sheet {a.used_sheet}, 第 {a.load_col} 列)「位号↔型号」冲突: '
                f'硬冲突 {len(h4)} + 待核 {len(s4)}')
            for ref, model, exp, got in h4:
                say(f'    ✗[硬] 负载列写 "{ref} {model}"，但清单里 {ref} = {got}，{model} 挂在 {exp}')
            for ref, model, exp in s4:
                say(f'    ?[待核] 负载列写 "{ref} {model}"，但清单里没有位号 {ref}'
                    f'（{model} 挂在 {exp}）')
            say('')

    report = '\n'.join(lines)
    print(report, end='')
    if a.out:
        open(a.out, 'w', encoding='utf-8').write(report + '\n')
        print(f'\n（报告已写入 {a.out}）')

    if all_hard:
        print(f'\n★ 硬冲突 {len(all_hard)} 处：同一批交付物里对同一位号给了不同型号，'
              f'必须读完图定案。', file=sys.stderr)
    if all_soft:
        print(f'\n? 待核 {len(all_soft)} 处：用了清单里没有的位号 —— 补清单或改文本，'
              f'别让交付物内部不一致地交出去。', file=sys.stderr)
    sys.exit(1 if (all_hard or all_soft) else 0)


if __name__ == '__main__':
    try:
        main()
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
