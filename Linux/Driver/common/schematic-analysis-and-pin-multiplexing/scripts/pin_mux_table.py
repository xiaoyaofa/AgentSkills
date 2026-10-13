#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pin_mux_table.py — 从厂商引脚表反查"引脚原名 / 底板所需功能 / muxmode"。

这是整份分析里最容易出错、也最容易自动化的一步：
  · 「引脚原名 (Function0)」 = 引脚表 Function0 列，即该 ball 的默认复用功能；
  · 「底板所需功能」        = 原理图告诉我们要把这只脚用成什么；
  · muxmode                = 在 Function0..Function9 里查目标功能所在的下标，**绝不要手填**。
    下标 N 直接就是 pinctrl 里 AM62X_IOPAD(offset, 属性, N) 的第 3 个参数
    （Qualcomm/i.MX/瑞芯微等平台同理，各自宏名不同）。

用法:
    # 1) 生成映射表（targets 文件：每行 "引脚号<空白>目标功能名"，不给则该脚按 Function0 用）
    pin_mux_table.py --pinlist pinlist.tsv --targets targets.txt --out mux.tsv

    # 2) ★ 查漏体检：列出「引脚表引出名 ≠ Function0」的脚
    #    这些脚底板用的很可能不是默认功能。本次实战中正是这一步抓出 2 处真错：
    #    引出名 RESETOUT1 的脚 Function0 其实是 OSPI0_CSn2，真功能在 Function2。
    pin_mux_table.py --pinlist pinlist.tsv --audit

参数（不同厂商引脚表列位不同，按需覆盖）:
    --func-col-start 3   Function0 所在列（0 起）
    --func-count     10  Function0..Function9 共 10 列
    --name-col       1   信号名列（"引出名"）
    --ball-col       2   ball 号列（没有就传 -1）

退出码: 有 target 在功能列表里查不到时返回 2（用于 CI/脚本卡住错误，别静默放过）。
"""

import argparse
import re
import sys

GPIO_RX = re.compile(r'(MCU_)?GPIO\d+_\d+', re.I)
VOLT_RX = re.compile(r'^(0V|1V8|3V3|5V|1V2|1V1|1V35)$', re.I)


def load(path, nc, bc, fc, nf):
    pins = {}
    for line in open(path, encoding='utf-8', errors='replace'):
        f = [x.strip() for x in line.rstrip('\n').split('\t')]
        if not f or not f[0] or f[0].lower().startswith('pin'):
            continue
        if max(nc, fc) >= len(f):
            continue
        funcs = (f[fc:fc + nf] + [''] * nf)[:nf]
        # 尾部列（Bootstrap/Voltage/Comment）常被整格省略 → 不要按列号取，按内容从后往前认
        volt = next((x for x in reversed(f) if VOLT_RX.match(x)), '')
        pins[f[0]] = {
            'name': f[nc] if nc < len(f) else '',
            'ball': f[bc] if 0 <= bc < len(f) else '',
            'funcs': funcs,
            'gpio': next((x for x in funcs if GPIO_RX.fullmatch(x)), ''),
            'volt': volt,
        }
    return pins


def resolve(p, target):
    """返回 (显示字符串, 是否解析成功)"""
    funcs = p['funcs']
    if not target or target.upper() == funcs[0].upper():
        return (f"{funcs[0]}（Function0，无需复用）" if funcs[0] else '—'), True
    if target.startswith('(strap)'):
        g = p['gpio']
        tail = f'；软件读该拨码需 {g} mode {funcs.index(g)}' if g in funcs else ''
        return f"启动 strap {target[7:]}（复位采样，不占 mux{tail}）", True
    for i, fn in enumerate(funcs):
        if fn.upper() == target.upper():
            return f'{target} (muxmode {i})', True
    return f'{target} (muxmode 待确认)', False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pinlist', required=True)
    ap.add_argument('--targets')
    ap.add_argument('--out', default='-')
    ap.add_argument('--audit', action='store_true')
    ap.add_argument('--name-col', type=int, default=1)
    ap.add_argument('--ball-col', type=int, default=2)
    ap.add_argument('--func-col-start', type=int, default=3)
    ap.add_argument('--func-count', type=int, default=10)
    a = ap.parse_args()

    pins = load(a.pinlist, a.name_col, a.ball_col, a.func_col_start, a.func_count)
    if not pins:
        sys.exit('引脚表解析为空：检查 --name-col/--func-col-start 是否符合你的表结构')

    # ---------- 体检模式 ----------
    if a.audit:
        print(f'{"pin":>5} | {"引出名(=引脚表)":<30} | {"Function0":<20} | 结论')
        n = 0
        for pin, p in pins.items():
            core = re.sub(r'^(SOC_|SoC_)', '', p['name'], flags=re.I)
            if not p['funcs'][0] or core.upper() == p['funcs'][0].upper():
                continue
            n += 1
            print(f'{pin:>5} | {p["name"]:<30} | {p["funcs"][0]:<20} | '
                  f'两者不同 —— 底板若用的不是 Function0，必须查 muxmode')
        print(f'\n共 {n} 只脚"引出名 ≠ Function0"。'
              f'\n其中多数只是写法差异（如 SOC_OSPI0_DQ5 ↔ OSPI0_D5）；'
              f'\n真正要换 muxmode 的，用 --targets 显式指定目标功能后再看输出。')
        return

    targets = {}
    if a.targets:
        for line in open(a.targets, encoding='utf-8'):
            line = line.split('#')[0].strip()
            if not line:
                continue
            parts = re.split(r'[\s=,\t]+', line, maxsplit=1)
            if len(parts) == 2:
                targets[parts[0]] = parts[1].strip()

    out = sys.stdout if a.out == '-' else open(a.out, 'w', encoding='utf-8')
    out.write('pin\tball\t引出名(Function0)\t底板所需功能(muxmode)\t电平\tGPIO\n')
    bad = []
    for pin, p in pins.items():
        txt, ok = resolve(p, targets.get(pin, ''))
        if not ok:
            bad.append(pin)
        out.write(f'{pin}\t{p["ball"]}\t{p["funcs"][0]}\t{txt}\t{p["volt"]}\t{p["gpio"]}\n')
    if out is not sys.stdout:
        out.close()
        print(f'{a.out}: {len(pins)} 只脚')
    if bad:
        print(f'★ 有 {len(bad)} 个 target 在功能列表里查不到: {bad}',
              file=sys.stderr)
        sys.exit(2)


if __name__ == '__main__':
    try:
        main()
    except BrokenPipeError:      # 接 head/less 时提前关管道，别打印一堆栈
        import os
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
