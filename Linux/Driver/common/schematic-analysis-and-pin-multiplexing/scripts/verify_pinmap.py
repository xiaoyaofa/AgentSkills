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
  [F] 引出名落在别处/不在表里 —— 与 pin_mux_table.py 同一套归一化判定，指出必须核对 muxmode 的脚
  [G] 网络名 ↔「底板所需功能」自相矛盾 —— 网络名写着要换的功能，表里却写"无需复用"（弱判据）

用法:
    verify_pinmap.py --pinlist pinlist.tsv --sch sch.compact.txt \\
        --used used.tsv --unused unused.tsv \\
        [--used-pin-col 2] [--used-sig-col 1] [--unused-pin-col 0] \\
        [--used-load-col 8] [--used-page-col 7] [--conn-page 3] [--window 8]

输出: 各检查项计数 + 具体条目；退出码 0 = A/B/C/D 全过（E/F/G/H 只提示，不阻塞）。

  [H] 原理图上的 GPIO 备用功能标注 ↔「底板所需功能」列（弱判据）
      —— 厂商爱在该网络旁用蓝色小字标"这只 SOM 脚当哪个 GPIO 用"（`MCU_GPIO0_3` 之类）。
      这是**唯一一条从原理图侧自动查**的依据，专治"引出名与 Function0 同名、图上却当 GPIO 用"
      （① 看不见、② 要有 dts 才看得见的那一类）。
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
import os
import re
import sys
from collections import Counter, OrderedDict

# 与 pin_mux_table.py 共用同一套"引出名 ↔ Function"归一化判定，免得两处逻辑各写一遍再走偏
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pin_mux_table import norm, selfcheck        # noqa: E402

DESIG = re.compile(r'\b(?:U|R|C|Q|D|J|CON|SW|JP|L|FB|TP|Y)\d{2,5}\b')


def read_tsv(path):
    rows = []
    for line in open(path, encoding='utf-8', errors='replace'):
        rows.append([c.strip() for c in line.rstrip('\r\n').split('\t')])   # 兼容 CRLF
    return rows


def split_pages(lines, page_re=r'Sheet:?\s*(\d+)\s+of\s+(\d+)'):
    # ★ 按页脚**出现顺序**切，不用页脚里印的号 —— 与 pin_usage.py 同一口径，理由见那边：
    #   页脚本身可能是错的（重复/跳号），按它当 key 会把两个物理页并成一个桶、页号整体偏移。
    rx = re.compile(page_re, re.I)
    cuts = [(i, int(m.group(1))) for i, l in enumerate(lines) for m in [rx.search(l)] if m]
    printed = [p for _i, p in cuts]
    trust = all(b > a for a, b in zip(printed, printed[1:]))   # 严格递增就信印的号
    pages, prev = OrderedDict(), 0
    for n, (i, p) in enumerate(cuts, start=1):
        pages[p if trust else n] = lines[prev:i + 1]
        prev = i + 1
    if prev < len(lines):
        pages[(max(pages) + 1) if pages else 1] = lines[prev:]
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
    ap.add_argument('--used-target-col', type=int, default=5,
                    help='「底板所需功能 (muxmode)」列（0 起），[G] 用它判断是否写了"无需复用"')
    ap.add_argument('--unused-pin-col', type=int, default=0)
    ap.add_argument('--unused-sig-col', type=int, default=1)
    ap.add_argument('--conn-page', type=int, default=3)
    ap.add_argument('--window', type=int, default=8)
    ap.add_argument('--skip', default='GND',
                    help='按**精确名字**跳过的信号（逗号分隔）')
    ap.add_argument('--power-rx', default=r'^(VDD|VSS|VCC|GND|VBAT)',
                    help='电源/地脚正则（与 pin_usage.py 同款）：这类信号不参与 [B] 划分完整性判定。'
                         '★ 不给这个，电源脚会被 [B] 报成"两张表都没收录"（实测 X 只）')
    ap.add_argument('--g-ignore', default='',
                    help='[G]/[I] 里已裁决为"网络名只是核心板印字、实际按 Function0 用"的引脚号，'
                         '逗号分隔（裁决理由要写进交付物的备注列）')
    ap.add_argument('--page-re', default=r'Sheet:?\s*(\d+)\s+of\s+(\d+)',
                    help='页脚正则（默认兼容带/不带冒号）')
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
    skip = {s.strip().upper() for s in a.skip.split(',') if s.strip()}
    power_rx = re.compile(a.power_rx, re.I)

    def is_skipped(name):
        """跳过"不该参与引脚划分统计"的信号：精确名 或 电源/地脚正则。"""
        n = (name or '').strip()
        return n.upper() in skip or bool(power_rx.match(n))
    used = [r for r in read_tsv(a.used) if len(r) > a.used_pin_col and r[a.used_pin_col]]
    unused = [r for r in read_tsv(a.unused) if len(r) > a.unused_pin_col and r[a.unused_pin_col]]
    used = [r for r in used if r[0] not in ('功能分类',)]
    unused = [r for r in unused if r[0] not in ('核心板引脚',)]

    lines = open(a.sch, encoding='utf-8', errors='replace').read().splitlines()
    pages = split_pages(lines, a.page_re)
    if len(pages) <= 1:            # ★ 同 pin_usage.py：静默回退成 1 页会让结论全错
        sys.exit(f'切页失败（<=1 页）：页脚格式可能与 --page-re 不符；请指定正确正则。')
    other = '\n'.join(l for p, ls in pages.items() if p != a.conn_page for l in ls)

    def crawl(needle):
        return [i for i, l in enumerate(lines) if needle.lower() in l.lower()]

    def pages_of(needle):
        rx = re.compile(r'(?<![A-Za-z0-9_])' + re.escape(needle) + r'(?![A-Za-z0-9_])', re.I)
        return sorted({p for p, ls in pages.items()
                       if p != a.conn_page and any(rx.search(l) for l in ls)})

    errA, errB, errC, errD, warnE, warnF, warnG, warnH, warnI = [], [], [], [], [], [], [], [], []

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
            and not is_skipped(pins[k]['name'])]
    for k in miss:
        errB.append(f'引脚 {k}（{pins[k]["name"]}）两张表都没收录')

    # [C] 未用信号反查
    p4_12 = other
    for r in unused:
        sig = r[a.unused_sig_col]
        for c in re.split(r'\s*/\s*', sig):
            c = c.strip()
            if len(c) < 6 or is_skipped(c):
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

    # [F] 引出名 ↔ Function 列表（用 pin_mux_table 的归一化判定，别再按字面比 —— 字面比会把
    #     `SOC_OSPI0_DQ5 ↔ OSPI0_D5` 这种写法差异全报出来，实测 59 条里大半是噪声）
    for pin in p1:                      # 只看已用表里的脚（全表体检见 pin_mux_table --audit）
        p = pins.get(pin)
        if not p:
            continue
        kind, idx, fn = selfcheck(p)
        if kind == 'elsewhere':
            warnF.append(f'{pin}: 引出名={p["name"]} 落在 Function{idx}={fn}'
                         f' —— 底板若按引出名用，就必须 muxmode {idx}')
        elif kind == 'unknown':
            warnF.append(f'{pin}: 引出名={p["name"]} 不在该脚功能列表里'
                         f'（Function0={p["funcs"][0]}）—— 需核对实际功能')

    # [G] 网络名 ↔「底板所需功能」列 自相矛盾（弱判据）
    #   原理图网络名常常就写着这只脚该用什么功能（如 `SoC_UART1_RXD`）。若该功能落在这个脚的
    #   Function 列表的下标 ≠0，而第 6 列却写「（Function0，无需复用）」，两处必有一错。
    #   ★ 实测这一条能抓住"生成脚本把'未判定'兜底成'无需复用'"的事故：某板 17 行网络名写着
    #     UART1_RXD / VOUT0_DATA16，同一行却印 MCASP0_AFSR / GPMC0_AD8"无需复用"。
    #   ★ 弱判据：网络名也可能是核心板印字（如 SoC_UART1_DCDn 实际跑 I2C0），命中要人工裁决。
    g_ignore = {s.strip() for s in a.g_ignore.split(',') if s.strip()}
    for r in used:
        pin, net = r[a.used_pin_col], r[a.used_sig_col]
        if len(r) <= a.used_target_col or pin in g_ignore:
            continue
        tgt = r[a.used_target_col]
        p = pins.get(pin)
        if not p or not p['funcs'][0] or not ('Function0' in tgt or '无需复用' in tgt):
            continue
        n = norm(net)
        idx = next((i for i, fn in enumerate(p['funcs']) if fn and norm(fn) == n), None)
        if idx:
            warnG.append(f'{pin}: 网络名「{net}」对应 Function{idx}={p["funcs"][idx]}，'
                         f'但表里写「{tgt}」')

    # [H] 原理图上的 GPIO 备用功能标注 ↔「底板所需功能」列（弱判据，从**原理图侧**自动查）
    #   厂商画原理图时习惯在该网络旁用蓝色小字标出"这只 SOM 脚当哪个 GPIO 用"
    #   （形如 `MCU_GPIO0_3`、`SoC_GPIO0_12`）。这是**唯一一条从原理图侧自动查**的依据：
    #   ⚠ 依据① 靠"引出名 ≠ Function0"，同名就看不见；依据② 要有 dts。
    #   实测它抓到了 ① ② 都看不见、只有逐页读图才能发现的一只（`MCU_SPI0_D0` 当 `MCU_GPIO0_3` 用）。
    #   做法：从引脚表的 Function 列表建"GPIO 名 → 核心板脚"映射，拿图上出现的标注回查。
    gpio2pin = {}
    for pin, p in pins.items():
        for fn in p['funcs']:
            if fn and re.fullmatch(r'(MCU_)?GPIO\d+_\d+', fn, re.I):
                gpio2pin.setdefault(fn.upper(), pin)
                break
    target_of = {r[a.used_pin_col]: r[a.used_target_col]
                 for r in used if len(r) > a.used_target_col}
    unused_pins = {r[a.unused_pin_col] for r in unused}
    for name, pin in sorted(gpio2pin.items(), key=lambda kv: (len(kv[1]), kv[1])):
        short = re.sub(r'^MCU_', '', name)
        # ★ 前缀不能写成可选的 `(?:MCU_)?` —— 那样 `GPIO0_16` 会匹配到文本里的
        #   `MCU_GPIO0_16`（**另一只脚**），实测凭空多报 7 只假警。边界里带上 `_`，
        #   MCU_ 域的标注就不会被非 MCU 的脚认领。裸名另外允许 `SoC_` 前缀那种画法。
        forms = [name] if name.startswith('MCU_') else [name, 'SoC_' + name]
        rx = re.compile('|'.join(r'(?<![A-Za-z0-9_])' + re.escape(f) + r'(?![A-Za-z0-9_])'
                                 for f in forms), re.I)
        n_hit = len(rx.findall(other))
        if not n_hit:
            continue
        cur = target_of.get(pin)
        if cur is None:
            if pin in unused_pins:
                warnH.append(f'{pin}: 图上标了 {name}，但它在「2-未使用信号」里 —— 应该算已用')
            continue
        if short.upper() in cur.upper().replace('MCU_', ''):
            continue                       # 表里已经写成这个 GPIO 了，一致
        warnH.append(f'{pin}: 图上标了 {name}（出现 {n_hit} 次），'
                     f'但「底板所需功能」写的是「{cur}」')

    # [I] 备用功能名与自有网络名「同页共现」（弱判据）—— **没有 dts 时的主要补偿**
    #   为什么需要它：有一整类复用，判据①看不见（引出名 = Function0），又没有任何 GPIO 标注，
    #   只能靠依据②（dts）发现。实测一块板「UART0_RTSn/CTSn → I2C3_SDA/SCL (mux 2)」就属于这类 ——
    #   一轮"手边没 dts 就跳过依据②"的运行里，这两只被整只漏掉，而它们不通会导致 RTC/USB-C CC 全挂。
    #   原理图上的线索是：**这只脚自己的网络名，和它某个"备用功能"的名字，出现在同一页上**
    #   （双标签 / 0R 交叉跳线画在一起）。这条给了「无 dts」时一个可自动化的抓手。
    #   ★ 仍是弱判据：可能是核心板印字（如 SoC_UART1_DCDn 与 I2C0 同页），要人工裁决。
    page_tokens = {}
    for _p, _ls in pages.items():
        if _p == a.conn_page:
            continue
        toks = set()
        for _l in _ls:
            toks.update(t.upper() for t in re.findall(r'[A-Za-z0-9_]+', _l))
        page_tokens[_p] = toks

    def name_pages(nm):
        """该名字（可以带/不带 SoC_ 前缀）出现在哪些页上。"""
        up = nm.upper()
        forms = (up, 'SOC_' + up)
        return {p for p, toks in page_tokens.items() if toks.intersection(forms)}

    for r in used:
        pin = r[a.used_pin_col]
        if pin in g_ignore or len(r) <= a.used_target_col:
            continue
        cur = r[a.used_target_col]
        if not ('Function0' in cur or '无需复用' in cur):
            continue                        # 表里已经写成别的功能了，不用查
        p = pins.get(pin)
        if not p or not p['funcs'][0]:
            continue
        own_name = re.sub(r'^(SOC_|SoC_)', '', r[a.used_sig_col].split('/')[0])
        own_pages = name_pages(own_name)
        if not own_pages:
            continue
        for i, fn in enumerate(p['funcs']):
            if i == 0 or not fn or fn.upper() == p['funcs'][0].upper():
                continue
            if fn.upper() in cur.upper():
                continue            # 表里已经写明这个备用功能了（如"Function0；装屏时 → EHRPWM0_B"），不算漏
            hit = name_pages(fn) & own_pages
            if hit:
                warnI.append(f'{pin}: 自有网络「{r[a.used_sig_col]}」与备用功能 {fn} '
                             f'同在第 {sorted(hit)} 页 —— 底板可能用的是 {fn}，而表里写「{cur}」')
                break

    # ---------- 汇总 ----------
    print(f'Sheet1(已用) {len(used)} 行 / Sheet2(未用) {len(unused)} 行 / '
          f'引脚表 {len(pins)} 只脚')
    print(f'  [A] 引脚↔信号名 不一致        : {len(errA)}')
    print(f'  [B] 重复 {len(dup)} / 重叠 {len(ov)} / 两表未收录 {len(miss)}')
    print(f'  [C] 未用信号却出现在别页      : {len(errC)}')
    print(f'  [D] 页码列与实际不符          : {len(errD)}')
    print(f'  [E] 负载列存疑（弱判据）      : {len(warnE)}')
    print(f'  [F] 引出名落在别处/不在表里    : {len(warnF)}')
    print(f'  [G] 网络名↔所需功能 自相矛盾  : {len(warnG)}')
    print(f'  [H] 图上 GPIO 标注↔所需功能   : {len(warnH)}')
    print(f'  [I] 备用功能与自有网络同页共现: {len(warnI)}')
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
    if warnG:
        print(f'  ↑ [G] {len(warnG)} 行：网络名与「底板所需功能」列对不上，逐条裁决后再交付。')
        for x in warnG:
            print(f'      {x}')
        print('      两种可能：① 表里的"无需复用"是错的（该换 muxmode）—— **这是事故，必须改**；')
        print('                ② 网络名只是核心板印字、实际真按 Function0 用 —— 在备注里写明即可。')
        print('      另：板级 dts 的 pinctrl 是最硬的第三方依据，用 dts_pinmux_audit.py 对拍一遍。')
    if warnH:
        print(f'  ↑ [H] {len(warnH)} 只：原理图上标了 GPIO 备用功能，表里却没这么写 —— 逐条裁决。')
        for x in warnH:
            print(f'      {x}')
        print('      两种可能：① 底板真的把它当 GPIO 用（**表写错了**，改成该 GPIO + muxmode 7）；')
        print('                ② 那只是原图上的排版残留（在备注里写明）。')
        print('      这一条专治"引出名与 Function0 同名、图上却当 GPIO 用"—— ① ② 都看不见的那类。')
    if warnI:
        print(f'  ↑ [I] {len(warnI)} 只：备用功能名与这只脚自己的网络名出现在同一页 —— 逐条裁决。')
        for x in warnI:
            print(f'      {x}')
        print('      同页共现通常是「双标签 / 0R 交叉跳线」的画法，**多半是真的换了功能**。')
        print('      两种裁决：① 确实换了 → 改成该功能 + muxmode（**这类漏掉会让总线整体不通**）；')
        print('                ② 只是核心板印字（如 SoC_UART1_DCDn 与 I2C0 同页）→ 备注写明 + --g-ignore。')
        print('      ★ 没有 dts 时，这一条是"同名却真复用"那一类唯一能自动发现的抓手；'
              '但它也抓不全（例：USB0_DRVVBUS 当 GPIO 用，图上根本没有该 GPIO 名）→ 仍需读图。')

    sys.exit(0 if not (errA or errB or errC or errD) else 1)


if __name__ == '__main__':
    main()
