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
    # 1) 生成映射表（targets 文件：每行 "引脚号<空白>目标功能名"）
    pin_mux_table.py --pinlist pinlist.tsv --targets targets.txt --out mux.tsv
    #    ★ targets 里没给、或给了但对不上功能列表的脚，一律输出「未判定（需裁决）」——
    #      **绝不默认成"无需复用"**。这个默认值曾经直接进过交付物：原理图网络名明明写着
    #      SOC_UART1_RXD，同一行的「底板所需功能」却印成 MCASP0_AFSR"无需复用"（板级 dts 是 mux 2）。
    #      "未给目标"是**没判定**，不是"不需要复用"；只有显式给了目标且目标 == Function0 才算无需复用。

    # 2) ★ 查漏体检：把「引出名」归一化后去该脚 Function 列表里认亲
    #    · same      引出名就是 Function0            → 按默认功能用即可
    #    · elsewhere 引出名落在 Function{i}（i≠0）    → 底板若按引出名用，需 muxmode i
    #    · unknown   引出名不在功能列表里             → 引出名是核心板印字，实际功能要查
    #    ★★ 三类都只是**候选**，不是结论 —— 见下面"候选 ≠ 结论"一段。
    pin_mux_table.py --pinlist pinlist.tsv --audit

参数（不同厂商引脚表列位不同，按需覆盖）:
    --func-col-start 3   Function0 所在列（0 起）
    --func-count     10  Function0..Function9 共 10 列
    --name-col       1   信号名列（"引出名"）
    --ball-col       2   ball 号列（没有就传 -1）

退出码: 有 target 在功能列表里查不到时返回 2（用于 CI/脚本卡住错误，别静默放过）。
"""

# ---------------------------------------------------------------------------
# ★ 强制 UTF-8 输出（Windows 中文环境必需）
#   Windows 中文环境默认编码是 GBK(cp936)：脚本里只要有箭头/数学符号（`↔` `→` `≤`）就会
#   UnicodeEncodeError 直接崩 —— **而且是跑到打印那一步才崩，前面算完的结果一条都出不来**，
#   看着像"检查没通过"，实际是"检查没执行"。实测：`check_deliverable_consistency.py` 崩在
#   `print(report)`、`verify_pinmap.py` 崩在打印 `[A] 引脚↔信号名` 那行。
#   ★ 被 `-I` 拉起时（`-I` 蕴含 `-E`）PYTHONIOENCODING / PYTHONUTF8 都无效，只能在进程内改。
#   ★ **不要**改成 import 一个共享小模块：`-I` 蕴含 `-P`，脚本目录不进 sys.path，只会静默失效；
#     所以这段在每个脚本里内联一份（自带 import，放在 docstring 之后、其余 import 之前也能跑）。
# ---------------------------------------------------------------------------
import sys as _sys

for _s in (_sys.stdout, _sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):        # 被重定向成非文本流时忽略
        pass
if _sys.platform == 'win32':
    try:                                        # 控制台代码页也切到 UTF-8，原生 cmd 窗口才不会显示成乱码
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    except Exception:
        pass


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


def norm(s):
    """归一化功能名，用来回答"引出名与该脚 Function 列表里的是不是同一个功能"。

    只抹掉**同一个功能的两种写法**，不抹掉"不同的功能"：
      SOC_UART1_CTSN           ↔ UART1_CTSn        （前缀 + 大小写 + 下划线）
      SOC_OSPI0_DQ5            ↔ OSPI0_D5          （DQ 写法）
      SOC_GPMC0_AD7/BOOTMODE07 ↔ GPMC0_AD7         （引出名带启动模式后缀）
    """
    s = re.sub(r'^(SOC_|SoC_)', '', s.strip(), flags=re.I).split('/')[0]
    s = re.sub(r'[\s_\-]', '', s).upper()
    return re.sub(r'DQ([0-9])', r'D\1', s)


def selfcheck(p):
    """只看引脚表自身：引出名对应的功能落在该脚 Function 列表的第几位。

    返回 (类别, 下标, 功能名)：
      ('nofunc',   -1, '')  不该有功能的脚（GND / 电源）—— 不参与判断
      ('same',      0, fn)  引出名就是 Function0 —— 按默认功能用即可
      ('elsewhere', i, fn)  引出名落在 Function{i}（i≠0）—— 底板若按引出名用，需 muxmode i
      ('unknown',  -1, '')  引出名不在该脚功能列表里 —— 引出名是核心板印字，实际功能要另找依据

    ★ 这只是**候选**，不是结论。引出名可能是核心板自己起的印字：
      某只脚的引出名叫 SoC_UART1_DCDn，而它的 Function0 是 I2C0_SCL，底板真的就按 I2C0 用。
      这类只有查板级 dts 的 pinctrl、或读图，才能定案。
    """
    if not p['funcs'][0]:
        return ('nofunc', -1, '')
    n = norm(p['name'])
    if not n:
        return ('unknown', -1, '')
    for i, fn in enumerate(p['funcs']):        # 保留下标，空单元格也要占位
        if fn and norm(fn) == n:
            return ('same' if i == 0 else 'elsewhere', i, fn)
    return ('unknown', -1, '')


def resolve(p, target):
    """返回 (显示字符串, 是否解析成功, muxmode)。

    muxmode 为 None 表示"没有真的复用"（按 Function0 用 / 启动 strap / **未判定**）；
    是 int（且 != 0）表示底板把这脚换成了别的功能 —— **这才是"复用"的权威判据**。
    """
    funcs = p['funcs']
    if not funcs[0]:
        return '—', True, None                 # 电源/GND 脚：没有功能可言
    if not target:
        # ★ 不给目标 = 没判定，**不是**"无需复用"。别把未知写成结论。
        return '未判定（需裁决）', True, None
    if target.upper() == funcs[0].upper():
        return (f"{funcs[0]}（Function0，无需复用）" if funcs[0] else '—'), True, None
    if target.startswith('(strap)'):
        g = p['gpio']
        tail = f'；软件读该拨码需 {g} mode {funcs.index(g)}' if g in funcs else ''
        return f"启动 strap {target[7:]}（复位采样，不占 mux{tail}）", True, None
    for i, fn in enumerate(funcs):
        if fn.upper() == target.upper():
            return f'{target} (muxmode {i})', True, i
    return f'{target} (muxmode 待确认)', False, None


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
        groups = {'same': [], 'elsewhere': [], 'unknown': [], 'nofunc': []}
        for pin, p in pins.items():
            kind, i, fn = selfcheck(p)
            groups[kind].append((pin, p, i, fn))

        for key, title, hint_of in (
            ('elsewhere', '引出名落在别的 Function —— 底板若按引出名用，就必须换 muxmode',
             lambda p, i, fn: f'Function{i} = {fn} → 可能 muxmode {i}'),
            ('unknown', '引出名不在该脚功能列表里 —— 引出名是核心板印字，实际功能要另找依据',
             lambda p, i, fn: f'该脚 Function0 = {p["funcs"][0] or "—"}'),
        ):
            items = groups[key]
            print(f'\n=== {title}：{len(items)} 只 ===')
            for pin, p, i, fn in items[:40]:
                print(f'{pin:>5} | {p["name"]:<30} | {hint_of(p, i, fn)}')
            if len(items) > 40:
                print(f'  …… 其余 {len(items) - 40} 只同理（整片同前缀的可以整批裁决）')

        print(f'\n共 {len(pins)} 只脚：same {len(groups["same"])} / '
              f'elsewhere {len(groups["elsewhere"])} / unknown {len(groups["unknown"])} / '
              f'电源·GND {len(groups["nofunc"])}')
        print('★ 上面两组都只是**候选**，不是结论，必须逐条给裁决：')
        print('   · 引出名可能是核心板印字 —— 如某脚引出名 SoC_UART1_DCDn，其 Function0 是 I2C0_SCL，')
        print('     底板真的就按 I2C0 用，"elsewhere" 是虚警；')
        print('   · 也可能引出名与 Function0 同名、底板却仍然换了功能（如 GPMC0_WAIT1 → UART6_RXD），')
        print('     这类本命令看不见，只能靠对拍板级 dts 或读图。')
        print('  结论的**三个依据缺一不可**：① 引脚表自洽反查（本命令）')
        print('  ② 板级 dts 的 pinctrl 逐条对拍（原理图是厂商自家 EVK 时它就是标准答案） ③ 读图。')
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
    out.write('pin\tball\t引出名(Function0)\t底板所需功能(muxmode)\t电平\tGPIO\t自检\taudit\n')
    bad, blind, undecided = [], [], []
    for pin, p in pins.items():
        target = targets.get(pin, '')
        txt, ok, mm = resolve(p, target)
        if not ok:
            bad.append(pin)
        if not target and p['funcs'][0]:
            undecided.append((pin, p, txt))
        kind, idx, fn = selfcheck(p)
        # ★ audit 盲区：引出名与 Function0 是同一个功能，但底板真的换了功能 ——
        #   `--audit` 的判据是"引出名 ≠ Function0"，这类会被它直接跳过。
        if mm is None:
            flag = '-'
        elif kind == 'same':
            flag = '★盲区'; blind.append(pin)
        else:
            flag = '可见'
        self_txt = '-' if kind in ('same', 'nofunc') else (
            f'Function{idx}' if kind == 'elsewhere' else '引出名不在功能表')
        out.write(f'{pin}\t{p["ball"]}\t{p["funcs"][0]}\t{txt}\t{p["volt"]}\t{p["gpio"]}'
                  f'\t{self_txt}\t{flag}\n')
    if out is not sys.stdout:
        out.close()
        n_remux = sum(1 for pin, p in pins.items() if (resolve(p, targets.get(pin, ''))[2] or 0))
        print(f'{a.out}: {len(pins)} 只脚；其中 **{n_remux} 只真的换了功能**（muxmode≠0）')
        if blind:
            print(f'  ★ 但这 {len(blind)} 只 `--audit` 查不到（引出名与 Function0 同名，被它跳过）:')
            for pin in blind:
                t = targets.get(pin, '')
                print(f'      pin{pin:<5} {pins[pin]["name"]:<24} → {t}')
            print('  → 这类只能靠对拍板级 dts 的 pinctrl、或**读图**发现；别只用 --audit 下结论。'
                  '详见 SKILL.md「查漏法」一节。')
        if undecided:
            print(f'  ★ 有 {len(undecided)} 只 targets 里没给目标，输出为「未判定（需裁决）」——')
            print('    交付前必须逐条裁决（引脚表自洽反查 / 板级 dts 对拍 / 读图），'
                  '别把这个状态留在交付物里。自检提示（**多数属于前两类，可整批写进 targets**）：')
            for pin, p, _ in undecided[:30]:
                kind, idx, fn = selfcheck(p)
                hint = {'same': 'Function0 一致，多半可直接判"无需复用"',
                        'nofunc': '该脚无功能定义，跳过',
                        'elsewhere': f'引出名落在 Function{idx} = {fn}',
                        'unknown': f'引出名不在功能表（Function0 = {p["funcs"][0] or "—"}）'}[kind]
                print(f'      pin{pin:<5} {p["name"]:<26} {hint}')
            if len(undecided) > 30:
                print(f'      …… 其余 {len(undecided) - 30} 只同理。'
                      f'\n    ★ 提示：把"引出名就是 Function0"的整批写成 `<脚> <Function0>` 丢进 targets.txt，'
                      f'就能把「无需复用」变成**有依据的结论**而不是默认值。')
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
