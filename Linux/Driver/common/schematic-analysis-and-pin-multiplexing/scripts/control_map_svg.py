#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""control_map_svg.py — 模式 B：控制关系 / 电源树 TSV → SVG + md 表格。

设计原则（与 skill 的整体方法论一致）：
  1. **单一事实来源**：控制关系、电源树、器件清单都在 TSV 里；SVG 与 md 表格
     由同一份数据生成，不靠人手抄，避免"表里写 U5、图上画 U6"。
  2. **确定性**：同一份 TSV + 同样的参数 → 字节级相同的 SVG（--check 可复验）。
  3. **不画全网络**：只画"谁控制谁、切到哪条电源路径"，让嵌入式软件工程师一眼看懂；
     完整网络/参数在 md 正文与原理图原图里。
  4. **未知不伪装成结论**：TSV 的「确认状态」只有 `已看图` / `待确认 Qnn` 两种；
     后者必须由检查器在 md 的待确认章节闭环。

用法:
    # 推荐：功能块框图（layout/zones/edges 三个 TSV）
    python3 control_map_svg.py --style block \\
        --map control-map.tsv --power power-tree.tsv --devices devices.tsv \\
        --layout layout.tsv --zones zones.tsv --edges edges.tsv \\
        --svg out.svg [--md report.md] [--title "板名 系统框图"] \\
        [--date 2026-10-22] [--source "原理图.pdf"] [--check]

    # 保底：行式关系表（不需要 layout/zones/edges）
    python3 control_map_svg.py --style table \\
        --map control-map.tsv --power power-tree.tsv --devices devices.tsv \\
        --svg out.svg [--md report.md] ...

输出:
    · <svg>                                  功能块框图 或 行式关系表
    · --md 给定时：只替换 md 里三个 BEGIN/END 标记之间的内容
    · 另给 --md-table/--md-power/--md-devices 时：同时写出独立表格片段
    · stdout：节点/边统计

TSV 表头（必须逐字一致，检查器也会查）:
    control-map.tsv : id 分组 状态 控制源 源类型 方向 控制器件 器件型号 器件引脚
                      开关/通道 目标 目标类型 关系 有效状态/条件 动作与结果
                      I2C地址 中断 原理图页 确认状态 备注
    power-tree.tsv  : id 状态 上游网络 来源器件 器件型号 器件引脚 通道/开关
                      下游网络 电压 条件/使能 动作与结果 原理图页 确认状态 备注
    devices.tsv     : 位号 型号 类别 原理图页 作用 确认状态 备注
"""

# ---------------------------------------------------------------------------
# ★ 强制 UTF-8 输出（Windows 中文环境必需；`-I` 下只能进程内改，且不能 import 共享模块）
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
import csv
import heapq
import json
import math
import os
import re
import sys
from xml.sax.saxutils import escape as xml_escape, quoteattr

CONTROL_COLS = ['id', '分组', '状态', '控制源', '源类型', '方向', '控制器件', '器件型号',
                '器件引脚', '开关/通道', '目标', '目标类型', '关系', '有效状态/条件',
                '动作与结果', 'I2C地址', '中断', '原理图页', '确认状态', '备注']
POWER_COLS = ['id', '状态', '上游网络', '来源器件', '器件型号', '器件引脚', '通道/开关',
              '下游网络', '电压', '条件/使能', '动作与结果', '原理图页', '确认状态', '备注']
DEVICE_COLS = ['位号', '型号', '类别', '原理图页', '作用', '确认状态', '备注']

# md 表格列（顺序固定；检查器按这个顺序逐列比对原始 TSV 值）
CONTROL_MD_COLS = ['id', '控制源', '源类型', '方向', '控制器件', '器件引脚', '开关/通道',
                   '目标', '有效状态/条件', '动作与结果', 'I2C地址', '中断',
                   '原理图页', '确认状态', '备注']
POWER_MD_COLS = ['id', '状态', '上游网络', '来源器件', '器件引脚', '通道/开关',
                 '下游网络', '电压', '条件/使能', '动作与结果', '原理图页', '确认状态']

# 画布参数
W = 1600
M = 40
GAP = 30
LINE_H = 18
FS = 13
FS_SMALL = 11
FS_TITLE = 21
FS_COL = 13
HEADER_H = 30
GROUP_H = 28
LANE_GAP = 8
PANEL_GAP = 34
FONT = ("'Noto Sans CJK SC','Source Han Sans SC','Microsoft YaHei',"
        "'PingFang SC','WenQuanYi Micro Hei',sans-serif")

# 列宽（控制面板合计 1420 + 4*30 = 1540；左右各 30 → 1600）
CONTROL_LAYOUT = [
    ('控制源', 240, 'source'),
    ('控制器件 / 引脚', 300, 'controller'),
    ('开关 / 通道', 260, 'switch'),
    ('目标', 180, 'target'),
    ('状态 / 动作与结果', 440, 'action'),
]
# 电源面板：230+300+230+710 = 1470 + 3*20 = 1530；左右各 35
POWER_LAYOUT = [
    ('上游网络', 230, 'rail'),
    ('通道 / 器件', 300, 'pass'),
    ('下游网络', 230, 'rail'),
    ('条件 / 使能 / 结果', 710, 'action'),
]
POWER_GAP = 20

ROLE_STYLE = {
    'source':     ('#dce9f7', '#3b6ea5'),
    'controller': ('#d9f0ee', '#2a8c82'),
    'switch':     ('#fde9cf', '#c47a1b'),
    'pass':       ('#fde9cf', '#c47a1b'),
    'target':     ('#dff2df', '#2f7d32'),
    'target_sig': ('#e6e6f7', '#4a4a9c'),
    'rail':       ('#dff2df', '#2f7d32'),
    'action':     ('#fff4cc', '#b08a00'),
    'empty':      ('#f7f7f7', '#aaaaaa'),
}

# ---------------------------------------------------------------------------
# 模式 B · 框图（--style block）
# 依据：IEC 61082-1（功能分组/信号流/参考代号/标题栏）、IEEE 315（图形符号）、
#       MIL-DTL-24784D（信号左→右、上→下；主线加粗）、微软/Xilinx 框图惯例
#       （分区容器、块名+位号、端口标在块边、正交连线、图例）。
# ---------------------------------------------------------------------------
ZONE_COLS = ['id', '标题', 'x', 'y', '宽', '高', '颜色']
BLOCK_COLS = ['id', '区域', 'x', 'y', '宽', '高', '样式', '标题', '副标题',
              '端口', '位号', '页码', '备注']
EDGE_COLS = ['id', 'row', 'from', 'from_port', 'to', 'to_port', '类型', '标签',
             '颜色', '折点', '页码']

# 功能块样式：body=填充，header=标题栏，text=标题栏文字，stroke=描边
BLOCK_STYLE = {
    'connector':  {'body': '#E3F2FD', 'header': '#1565C0', 'text': '#FFFFFF', 'stroke': '#0D47A1'},
    'pd':         {'body': '#F3E5F5', 'header': '#6A1B9A', 'text': '#FFFFFF', 'stroke': '#4A148C'},
    'charger':    {'body': '#E8F5E9', 'header': '#2E7D32', 'text': '#FFFFFF', 'stroke': '#1B5E20'},
    'battery':    {'body': '#FFF8E1', 'header': '#F9A825', 'text': '#4E342E', 'stroke': '#E65100'},
    'switch':     {'body': '#FFF3E0', 'header': '#E65100', 'text': '#FFFFFF', 'stroke': '#BF360C'},
    'regulator':  {'body': '#E0F2F1', 'header': '#00695C', 'text': '#FFFFFF', 'stroke': '#004D40'},
    'protection': {'body': '#FCE4EC', 'header': '#AD1457', 'text': '#FFFFFF', 'stroke': '#880E4F'},
    'rail':       {'body': '#ECEFF1', 'header': '#546E7A', 'text': '#263238', 'stroke': '#78909C', 'flat': True, 'show_ports': False, 'fs': 13},
    'control':    {'body': '#E8EAF6', 'header': '#283593', 'text': '#FFFFFF', 'stroke': '#1A237E'},
    'signal':     {'body': '#F5F5F5', 'header': '#616161', 'text': '#FFFFFF', 'stroke': '#212121'},
    'note':       {'body': '#FFFDE7', 'header': '#FBC02D', 'text': '#4E342E', 'stroke': '#F57F17', 'flat': True, 'fs': 14, 'show_ports': False},
    # 模式/上下拉：上 VCC、下 GND，竖电阻；DNP 灰色虚线标 NC
    'strap':      {'body': '#FFF8E1', 'header': '#F9A825', 'text': '#4E342E', 'stroke': '#F57F17', 'flat': True, 'fs': 14, 'show_ports': False},
    # 原理图样式的模式表：sub 用 | 分行、; 分列，第一行表头
    'table':      {'body': '#FFFFFF', 'header': '#455A64', 'text': '#263238', 'stroke': '#607D8B', 'flat': True, 'fs': 12, 'show_ports': False},
    # 微软 MB 框图风格：核心芯片统一蓝色，外围白底细灰边，SoC 接口统一紫色
    'core':       {'body': '#E3F2FD', 'header': '#1565C0', 'text': '#0D47A1', 'stroke': '#1565C0', 'flat': True, 'fs': 15},
    'peripheral': {'body': '#FFFFFF', 'header': '#455A64', 'text': '#263238', 'stroke': '#455A64', 'flat': True, 'fs': 14},
    'soc':        {'body': '#F3E5F5', 'header': '#6A1B9A', 'text': '#4A148C', 'stroke': '#6A1B9A', 'flat': True, 'fs': 13, 'show_ports': False},
}

EDGE_STYLE = {
    # 统一成两色：电源=深蓝，控制/状态=深灰，SoC/总线=紫（不再按轨上彩虹色）
    'power':   {'color': '#0D47A1', 'width': 3.0, 'dash': ''},
    'control': {'color': '#455A64', 'width': 1.7, 'dash': ''},
    'i2c':     {'color': '#6A1B9A', 'width': 1.7, 'dash': '2 3'},
    'int':     {'color': '#455A64', 'width': 1.7, 'dash': '7 4'},
    'status':  {'color': '#455A64', 'width': 1.5, 'dash': '7 4'},
    'analog':  {'color': '#455A64', 'width': 1.5, 'dash': '7 4 2 4'},
    'bus':     {'color': '#6A1B9A', 'width': 2.0, 'dash': ''},
}

BLOCK_HEADER_H = 26
BLOCK_RX = 8
ZONE_TITLE_H = 22

# 分区自动收缩 / 正交布线 / 端口标签参数
ZONE_PAD = 14          # 分区右/下边收缩时保留的留白
PORT_STUB = 14         # 端口出线必须先垂直离开块边
ROUTE_CLEAR = 6        # 走线离块边的最小间距
ROUTE_LANE_EXTRA = 10  # 追加一条更外侧的并行通道
TURN_COST = 24         # A* 里一次 90° 拐弯折算的像素代价
MIN_EDGE_LEN = 20      # 端口间距小于此值 → 箭头太短，报错
RAIL_H = 30            # rail 自动高度的默认值
EDGE_LABEL_FS = 10.5
EDGE_LABEL_WRAP = 150


# ---------------------------------------------------------------------------
# TSV
# ---------------------------------------------------------------------------
def read_tsv(path, cols, label, key='id'):
    if not os.path.isfile(path):
        sys.exit(f'找不到{label}: {path}')
    with open(path, encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f, delimiter='\t')
        header = reader.fieldnames or []
        if header != cols:
            sys.exit(f'{label} 表头不符。\n  实际: {header}\n  契约: {cols}\n'
                     f'  请照 assets/*-template.tsv 的表头建表。')
        rows = []
        for i, r in enumerate(reader, 1):
            if not any((v or '').strip() for v in r.values()):
                continue
            r = {k: (v or '').strip() for k, v in r.items()}
            if key and not r.get(key):
                sys.exit(f'{label} 第 {i} 行缺 {key}（每行必须有稳定主键，如 C01/P01）')
            rows.append(r)
    if not rows:
        sys.exit(f'{label} 没有数据行: {path}')
    return rows


# ---------------------------------------------------------------------------
# 文本/几何
# ---------------------------------------------------------------------------
def units(s):
    """估算文本宽度：CJK 记 1.0em，ASCII 记 0.55em。"""
    return sum(1.0 if ord(ch) > 0x2E7F else 0.55 for ch in s)


def wrap(text, width_px, size=FS):
    """按列宽折行；`|` 是作者显式指定的换行。"""
    text = (text or '').replace('\t', ' ').strip()
    if text in ('', '—', '-', 'NA', 'N/A'):
        return ['—']
    max_units = max(6.0, (width_px - 22) / float(size))
    out = []
    for part in text.split('|'):
        part = part.strip()
        if not part:
            continue
        cur, cu = '', 0.0
        for ch in part:
            u = 1.0 if ord(ch) > 0x2E7F else 0.55
            if cu + u > max_units and cur:
                out.append(cur.strip())
                cur, cu = '', 0.0
            cur += ch
            cu += u
        if cur.strip():
            out.append(cur.strip())
    return out or ['—']


def box_h(lines):
    return max(44, len(lines) * LINE_H + 16)


def rect(x, y, w, h, role, extra='', fill=None, stroke=None):
    f0, s0 = ROLE_STYLE[role]
    fill = fill or f0
    stroke = stroke or s0
    dash = ' stroke-dasharray="5 4"' if role == 'empty' else ''
    return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="6" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.4"{dash} {extra}/>')


def text_block(x, y, w, h, lines, role, size=FS, center=False, bold_first=True):
    fill = '#1a1a1a' if role != 'empty' else '#777777'
    total = len(lines) * LINE_H
    first_y = y + (h - total) / 2.0 + size
    tx = x + w / 2.0 if center else x + 10
    anchor = 'middle' if center else 'start'
    out = [f'<text x="{tx:.1f}" y="{first_y:.1f}" font-family={quoteattr(FONT)} '
           f'font-size="{size}" fill="{fill}" text-anchor="{anchor}">']
    for i, ln in enumerate(lines):
        wt = ' font-weight="bold"' if (i == 0 and bold_first) else ''
        dy = 0 if i == 0 else LINE_H
        out.append(f'<tspan x="{tx:.1f}" dy="{dy}"{wt}>{xml_escape(ln)}</tspan>')
    out.append('</text>')
    return ''.join(out)


def arrow(x1, y, x2, kind='control', color='#44546a'):
    """正交直线 + 显式箭头；不用 marker-end（渲染器兼容性差）。"""
    dash = ''
    width = 1.6
    if kind == 'data':
        dash = ' stroke-dasharray="2 3"'
    elif kind in ('irq', 'feedback', 'result'):
        dash = ' stroke-dasharray="6 4"'
    elif kind == 'power':
        width = 2.8
    x_end = x2 - 2
    x_line = x_end - 9
    return (f'<line x1="{x1 + 2:.1f}" y1="{y:.1f}" x2="{x_line:.1f}" y2="{y:.1f}" '
            f'stroke="{color}" stroke-width="{width}"{dash}/>'
            f'<polygon points="{x_end:.1f},{y:.1f} {x_line:.1f},{y - 4.5:.1f} '
            f'{x_line:.1f},{y + 4.5:.1f}" fill="{color}"/>')


# ---------------------------------------------------------------------------
# 控制关系面板
# ---------------------------------------------------------------------------
def control_cells(r):
    """一行 TSV → 5 个格子的显示文本与样式角色。"""
    src = r['控制源']
    st = r['源类型']
    dr = r['方向']
    src_label = src + ('|' + st + (('·' + dr) if dr not in ('', '—', 'NA') else '')
                       if (st or dr) else '')

    ctrl = r['控制器件']
    part = r['器件型号']
    pin = r['器件引脚']
    ctrl_parts = [x for x in (ctrl, part, pin) if x not in ('', '—')]
    ctrl_label = '|'.join(ctrl_parts) if ctrl_parts else '—'

    sw = r['开关/通道']
    sw_label = sw if sw not in ('', '—') else '—'

    tgt = r['目标']
    ttype = r['目标类型']
    tgt_label = tgt + ('|' + ttype if ttype not in ('', '—') else '')

    state = r['状态']
    cond = r['有效状态/条件']
    prefix = ' / '.join(x for x in (state, cond) if x not in ('', '—'))
    act = r['动作与结果']
    act_label = (prefix + '：' if prefix else '') + act
    extra = []
    if r.get('I2C地址') not in ('', '—'):
        extra.append('I2C ' + r['I2C地址'])
    if r.get('中断') not in ('', '—'):
        extra.append('IRQ ' + r['中断'])
    if extra:
        act_label += '|' + ' / '.join(extra)

    tgt_role = 'target_sig' if ttype == '信号' else 'target'
    return [
        ('source', src_label),
        ('controller' if ctrl_parts else 'empty', ctrl_label),
        ('switch' if sw_label != '—' else 'empty', sw_label),
        (tgt_role if tgt not in ('', '—') else 'empty', tgt_label),
        ('action', act_label),
    ]


def edge_kind(r):
    rel = r['关系']
    if rel == '数据':
        return 'data'
    if rel in ('中断', '状态反馈'):
        return 'irq'
    if rel == '电源':
        return 'power'
    return 'control'


def render_control_panel(rows, y, title):
    out = []
    nodes, edges = [], []

    out.append(f'<text x="{M}" y="{y}" font-family={quoteattr(FONT)} font-size="{FS_TITLE}" '
               f'font-weight="bold" fill="#1f3a5f">{xml_escape(title)}</text>')
    y += 16
    out.append(f'<text x="{M}" y="{y}" font-family={quoteattr(FONT)} font-size="{FS_SMALL}" '
               f'fill="#5b6b7f">共 {len(rows)} 条控制关系；箭头方向 = 控制/信号方向，'
               f'虚线 = 中断/状态反馈，点线 = I2C/数据</text>')
    y += 14

    xs = []
    x = M
    for _, w, _ in CONTROL_LAYOUT:
        xs.append((x, w))
        x += w + GAP

    # 列头
    for (name, w, _), (cx, _) in zip(CONTROL_LAYOUT, xs):
        out.append(rect(cx, y, w, HEADER_H, 'empty', fill='#eef1f5', stroke='#c9d2dd'))
        out.append(text_block(cx, y, w, HEADER_H, [name], 'empty', FS_COL, center=True,
                              bold_first=True))
    y += HEADER_H + LANE_GAP

    last_group = None
    for idx, r in enumerate(rows):
        if r['分组'] != last_group:
            if last_group is not None:
                y += LANE_GAP
            out.append(f'<rect x="{M}" y="{y}" width="{W - 2 * M}" height="{GROUP_H}" rx="4" '
                       f'fill="#e8eef7" stroke="#c9d2dd" stroke-width="1"/>')
            out.append(f'<text x="{M + 12}" y="{y + 19}" font-family={quoteattr(FONT)} '
                       f'font-size="{FS_COL}" font-weight="bold" fill="#1f3a5f">'
                       f'{xml_escape(r["分组"])}</text>')
            y += GROUP_H
            last_group = r['分组']

        cells = control_cells(r)
        wrapped = [wrap(t, w, FS) for (_, t), (_, w, _) in zip(cells, CONTROL_LAYOUT)]
        lane_h = max(box_h(lines) for lines in wrapped) + 8

        # 页码小徽标（放左页边，不占列宽）
        pg = r['原理图页']
        out.append(f'<rect x="{M - 30}" y="{y + lane_h / 2 - 11:.1f}" width="26" height="22" '
                   f'rx="4" fill="#eef1f5" stroke="#b8c4d2"/>')
        out.append(f'<text x="{M - 17}" y="{y + lane_h / 2 + 4:.1f}" font-family={quoteattr(FONT)} '
                   f'font-size="11" fill="#44546a" text-anchor="middle">P{xml_escape(pg)}</text>')

        # 先画边，再画节点（节点盖住线头）
        hop = 0
        prev = None
        for i, (role, _) in enumerate(cells):
            nid = f'n-{r["id"]}-{i + 1}-{role}'
            cx, cw = xs[i]
            if prev is not None:
                hop += 1
                kind = edge_kind(r) if hop < len(cells) - 1 else 'result'
                px, pw = xs[i - 1]
                out.append(f'<g data-row="{xml_escape(r["id"])}" data-hop="{hop}" '
                           f'data-from="{xml_escape(prev)}" data-to="{xml_escape(nid)}" '
                           f'data-kind="{kind}">'
                           f'{arrow(px + pw, y + lane_h / 2, cx, kind)}</g>')
                edges.append({'row': r['id'], 'hop': hop, 'from': prev, 'to': nid, 'kind': kind})
            prev = nid

        for i, ((role, _), lines, (cx, cw)) in enumerate(zip(cells, wrapped, xs)):
            nid = f'n-{r["id"]}-{i + 1}-{role}'
            bh = lane_h - 8
            out.append(rect(cx, y, cw, bh, role,
                            f'id="{nid}" data-node="{role}" '
                            f'data-label={quoteattr("|".join(lines))}'))
            out.append(text_block(cx, y, cw, bh, lines, role))
            nodes.append({'id': nid, 'role': role, 'label': '|'.join(lines), 'row': r['id'],
                          'col': i + 1})

        y += lane_h + LANE_GAP

    return ''.join(out), y, nodes, edges


# ---------------------------------------------------------------------------
# 电源树面板
# ---------------------------------------------------------------------------
def power_cells(r):
    up = r['上游网络']
    volt = r['电压']
    up_label = up + ('|' + volt if volt not in ('', '—') else '')

    dev = r['来源器件']
    part = r['器件型号']
    pin = r['器件引脚']
    pass_el = r['通道/开关']
    parts = [x for x in (dev, part, pin) if x not in ('', '—')]
    if pass_el not in ('', '—'):
        parts.append(pass_el)
    pass_label = '|'.join(parts) if parts else '—'

    down = r['下游网络']
    down_label = down + ('|' + volt if volt not in ('', '—') else '')
    if r['下游网络'] != up and volt not in ('', '—'):
        down_label = down + '|' + volt

    cond = r['条件/使能']
    act = r['动作与结果']
    prefix = cond if cond not in ('', '—') else ''
    act_label = (prefix + '：' if prefix else '') + act

    return [
        ('rail' if up not in ('', '—') else 'empty', up_label),
        ('pass' if parts else 'empty', pass_label),
        ('rail' if down not in ('', '—') else 'empty', down_label),
        ('action', act_label),
    ]


def render_power_panel(rows, y, title):
    out = []
    nodes, edges = [], []

    out.append(f'<text x="{M}" y="{y}" font-family={quoteattr(FONT)} font-size="{FS_TITLE}" '
               f'font-weight="bold" fill="#1f3a5f">{xml_escape(title)}</text>')
    y += 16
    out.append(f'<text x="{M}" y="{y}" font-family={quoteattr(FONT)} font-size="{FS_SMALL}" '
               f'fill="#5b6b7f">共 {len(rows)} 条电源路径；粗箭头 = 电源，'
               f'条件写在最右列（如 OTG=1 / 常开）</text>')
    y += 14

    xs = []
    x = M
    for _, w, _ in POWER_LAYOUT:
        xs.append((x, w))
        x += w + POWER_GAP

    for (name, w, _), (cx, _) in zip(POWER_LAYOUT, xs):
        out.append(rect(cx, y, w, HEADER_H, 'empty', fill='#eef1f5', stroke='#c9d2dd'))
        out.append(text_block(cx, y, w, HEADER_H, [name], 'empty', FS_COL, center=True))
    y += HEADER_H + LANE_GAP

    last_state = None
    for r in rows:
        if r['状态'] != last_state:
            if last_state is not None:
                y += LANE_GAP
            out.append(f'<rect x="{M}" y="{y}" width="{W - 2 * M}" height="{GROUP_H}" rx="4" '
                       f'fill="#eaf3ea" stroke="#c9d2dd" stroke-width="1"/>')
            out.append(f'<text x="{M + 12}" y="{y + 19}" font-family={quoteattr(FONT)} '
                       f'font-size="{FS_COL}" font-weight="bold" fill="#2f5f32">'
                       f'{xml_escape(r["状态"] or "状态未标注")}</text>')
            y += GROUP_H
            last_state = r['状态']

        cells = power_cells(r)
        wrapped = [wrap(t, w, FS) for (_, t), (_, w, _) in zip(cells, POWER_LAYOUT)]
        lane_h = max(box_h(lines) for lines in wrapped) + 8

        pg = r['原理图页']
        out.append(f'<rect x="{M - 30}" y="{y + lane_h / 2 - 11:.1f}" width="26" height="22" '
                   f'rx="4" fill="#eef1f5" stroke="#b8c4d2"/>')
        out.append(f'<text x="{M - 17}" y="{y + lane_h / 2 + 4:.1f}" font-family={quoteattr(FONT)} '
                   f'font-size="11" fill="#44546a" text-anchor="middle">P{xml_escape(pg)}</text>')

        prev = None
        for i, (role, _) in enumerate(cells):
            nid = f'p-{r["id"]}-{i + 1}-{role}'
            cx, cw = xs[i]
            if prev is not None:
                px, pw = xs[i - 1]
                out.append(f'<g data-row="{xml_escape(r["id"])}" data-hop="{i}" '
                           f'data-from="{xml_escape(prev)}" data-to="{xml_escape(nid)}" '
                           f'data-kind="power">'
                           f'{arrow(px + pw, y + lane_h / 2, cx, "power", "#2f7d32")}</g>')
                edges.append({'row': r['id'], 'hop': i, 'from': prev, 'to': nid, 'kind': 'power'})
            prev = nid

        for i, ((role, _), lines, (cx, cw)) in enumerate(zip(cells, wrapped, xs)):
            nid = f'p-{r["id"]}-{i + 1}-{role}'
            bh = lane_h - 8
            out.append(rect(cx, y, cw, bh, role,
                            f'id="{nid}" data-node="{role}" '
                            f'data-label={quoteattr("|".join(lines))}'))
            out.append(text_block(cx, y, cw, bh, lines, role))
            nodes.append({'id': nid, 'role': role, 'label': '|'.join(lines), 'row': r['id'],
                          'col': i + 1})

        y += lane_h + LANE_GAP

    return ''.join(out), y, nodes, edges


# ---------------------------------------------------------------------------
# 框图（B 模式推荐样式）：布局/分区/连线都来自显式 TSV，确定性生成
# ---------------------------------------------------------------------------
def parse_ports(s):
    """name:side:offset；offset 可写 50% 表示按边长百分比定位（配合 auto 尺寸）。"""
    ports = {}
    for item in (s or '').split(';'):
        item = item.strip()
        if not item:
            continue
        parts = item.split(':')
        if len(parts) != 3:
            sys.exit(f'端口格式应为 name:side:offset（用 ; 分隔），实际: {item!r}')
        name, side, off = parts
        side = side.strip().upper()
        if side not in ('L', 'R', 'T', 'B'):
            sys.exit(f'端口 {name!r} 的 side 必须是 L/R/T/B，实际: {side!r}')
        off = off.strip()
        if off.endswith('%'):
            try:
                pct = float(off[:-1])
            except ValueError:
                sys.exit(f'端口 {name!r} 的百分比偏移非法: {off!r}')
            if not 0 <= pct <= 100:
                sys.exit(f'端口 {name!r} 的百分比偏移必须在 0~100: {off!r}')
            ports[name.strip()] = (side, ('%', pct))
        else:
            ports[name.strip()] = (side, float(off))
    return ports


def _auto_text_width(text, size):
    return units(text) * size


def resolve_auto_size(b):
    """rail/note 的 宽/高 可写 auto：按标题文字宽度收缩，避免网络标签框占地过大。"""
    st = BLOCK_STYLE[b['style']]
    auto_w = b['w'] in ('auto', 'AUTO', 'Auto')
    auto_h = b['h'] in ('auto', 'AUTO', 'Auto')
    if not (auto_w or auto_h):
        return
    if b['style'] not in ('rail', 'soc'):
        sys.exit(f'block {b["id"]}: 只有 rail/soc 样式支持 宽/高=auto'
                 f'（当前样式 {b["style"]}）')
    fs = st.get('fs', 13)
    tw = _auto_text_width(b['title'], fs)
    sw = max((_auto_text_width(x, 11) for x in wrap(b['subtitle'], 220, 11)), default=0.0)
    if auto_w:
        # 右侧要给 p.N 页码徽标留位，否则标题会和页码粘在一起
        extra = 62 if b['page'] not in ('', '—', '-') else 42
        b['w'] = float(max(60, math.ceil((max(tw, sw) + extra) / 2.0) * 2))
    if auto_h:
        if b['style'] == 'soc':
            b['h'] = 42.0
        else:
            b['h'] = float(RAIL_H if not b['subtitle'] else 46)


def parse_blocks(rows):
    out, ids = [], set()
    for r in rows:
        if r['id'] in ids:
            sys.exit(f'block id 重复: {r["id"]}')
        ids.add(r['id'])
        st = r['样式']
        if st not in BLOCK_STYLE:
            sys.exit(f'block {r["id"]} 的样式 {st!r} 不在允许列表: {list(BLOCK_STYLE)}')
        out.append({
            'id': r['id'], 'zone': r['区域'],
            'x': float(r['x']), 'y': float(r['y']),
            'w': r['宽'].strip(), 'h': r['高'].strip(),
            'style': st, 'title': r['标题'], 'subtitle': r['副标题'],
            'ports': parse_ports(r['端口']), 'refdes': r['位号'],
            'page': r['页码'], 'note': r['备注'],
        })
    for b in out:
        resolve_auto_size(b)
        b['w'], b['h'] = float(b['w']), float(b['h'])
        for name, (side, off) in b['ports'].items():
            if isinstance(off, tuple):
                continue
            limit = b['w'] if side in ('T', 'B') else b['h']
            if off < 0 or off > limit + 0.5:
                sys.exit(f'block {b["id"]} 端口 {name} 的偏移 {off} 超出块边长度 {limit}'
                         f'（side={side}）；请修正 layout.tsv 或改用 50% 百分比')
    return out


def parse_zones(rows):
    return [{
        'id': r['id'], 'title': r['标题'],
        'x': float(r['x']), 'y': float(r['y']),
        'w': float(r['宽']), 'h': float(r['高']), 'color': r['颜色'],
    } for r in rows]


def parse_edges(rows):
    out = []
    for r in rows:
        kind = r['类型']
        if kind not in EDGE_STYLE:
            sys.exit(f'edge {r["id"]} 的类型 {kind!r} 不在允许列表: {list(EDGE_STYLE)}')
        pts = []
        if r['折点'] not in ('', '—', '-'):
            for p in r['折点'].split(';'):
                p = p.strip()
                if not p:
                    continue
                xy = p.split(',')
                if len(xy) != 2:
                    sys.exit(f'edge {r["id"]} 折点格式应为 x,y;x,y，实际: {r["折点"]!r}')
                pts.append((float(xy[0]), float(xy[1])))
        out.append({
            'id': r['id'], 'row': r['row'], 'from': r['from'],
            'from_port': r['from_port'], 'to': r['to'], 'to_port': r['to_port'],
            'type': kind, 'label': r['标签'], 'color': r['颜色'],
            'points': pts, 'page': r['页码'],
        })
    return out


def block_port(b, name):
    if name not in b['ports']:
        sys.exit(f"块 {b['id']} 没有端口 {name!r}（已有: {list(b['ports'])}）")
    side, off = b['ports'][name]
    x, y, w, h = b['x'], b['y'], b['w'], b['h']
    if isinstance(off, tuple):
        off = (w if side in ('T', 'B') else h) * off[1] / 100.0
    if side == 'L':
        return (x, y + off), 'L'
    if side == 'R':
        return (x + w, y + off), 'R'
    if side == 'T':
        return (x + off, y), 'T'
    return (x + off, y + h), 'B'


def header_path(x, y, w, h=BLOCK_HEADER_H):
    r = BLOCK_RX
    return (f'M {x + r:.1f},{y:.1f} H {x + w - r:.1f} Q {x + w:.1f},{y:.1f} '
            f'{x + w:.1f},{y + r:.1f} V {y + h:.1f} H {x:.1f} V {y + r:.1f} '
            f'Q {x:.1f},{y:.1f} {x + r:.1f},{y:.1f} Z')


def page_label(page):
    """块右上角的页码徽标：p.N = 源原理图第 N 页（不是本图页号）。"""
    return f'p.{page}'


def subtitle_geom(b):
    """副标题的位置/字号/折行；draw_block 与端口标签避让共用。"""
    st = BLOCK_STYLE[b['style']]
    flat = st.get('flat', False)
    x, y, w, h = b['x'], b['y'], b['w'], b['h']
    if flat:
        small = h < 56
        yy = y + (33 if small else 40)
        fs = 10.5 if small else 12
        max_lines = 1 if small else max(1, int((h - 46) // 15))
        fill = '#37474F'
    else:
        yy = y + BLOCK_HEADER_H + 15
        fs = 11
        max_lines = max(1, int((h - BLOCK_HEADER_H - 8) // 14))
        fill = '#263238'
    lines = wrap(b['subtitle'], w - 20, fs)[:max_lines]
    return yy, fs, lines, fill


def text_bbox(tx, ty, text, size, anchor):
    w = units(text) * size + 2
    if anchor == 'middle':
        left = tx - w / 2.0
    elif anchor == 'end':
        left = tx - w
    else:
        left = tx
    return (left, ty - size + 1, left + w, ty + 3)


def box_hits(a, b):
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def block_text_boxes(b):
    st = BLOCK_STYLE[b['style']]
    flat = st.get('flat', False)
    x, y, w, h = b['x'], b['y'], b['w'], b['h']
    ty = y + (21 if flat else 18)
    fs = st.get('fs', 13)
    out = [text_bbox(x + 10, ty, b['title'], fs, 'start')]
    if b['page'] not in ('', '—', '-'):
        out.append(text_bbox(x + w - 8, ty, page_label(b['page']), 10, 'end'))
    if b['subtitle']:
        yy, sub_fs, lines, _ = subtitle_geom(b)
        for i, ln in enumerate(lines):
            out.append(text_bbox(x + 10, yy + i * 15, ln, sub_fs, 'start'))
    return out


def port_label_candidates(b, name, side):
    x, y, w, h = b['x'], b['y'], b['w'], b['h']
    (px, py), _ = block_port(b, name)
    if side == 'L':
        # 左右端口：标签放在线的上方/下方，避免被水平出线穿字
        return [(x - 6, py - 5, 'end', False), (x - 6, py + 15, 'end', False),
                (x + 10, py + 4, 'start', True)]
    if side == 'R':
        return [(x + w + 6, py - 5, 'start', False), (x + w + 6, py + 15, 'start', False),
                (x + w - 10, py + 4, 'end', True)]
    if side == 'T':
        return [(px + 6, y - 8, 'start', False), (px - 6, y - 8, 'end', False),
                (px, y - 9, 'middle', False),
                (px + 6, y + 16, 'start', True), (px - 6, y + 16, 'end', True),
                (px + 6, y + 26, 'start', True), (px - 6, y + 26, 'end', True)]
    return [(px + 6, y + h + 14, 'start', False), (px - 6, y + h + 14, 'end', False),
            (px, y + h + 16, 'middle', False),
            (px + 6, y + h - 6, 'start', True), (px - 6, y + h - 6, 'end', True),
            (px + 6, y + h - 24, 'start', True), (px - 6, y + h - 24, 'end', True)]


def layout_port_labels(blocks, extra_obstacles=()):
    """给端口名找不压块/不压线/不压字的位置：先块外，再块内；都冲突则记录警告。"""
    rects = {b['id']: (b['x'], b['y'], b['w'], b['h']) for b in blocks}
    own_text = {b['id']: block_text_boxes(b) for b in blocks}
    placed, out, warns = [], {}, []
    by_block = {}
    for b in blocks:
        st = BLOCK_STYLE[b['style']]
        if not st.get('show_ports', True):
            continue
        for name, (side, _) in b['ports'].items():
            chosen = None
            for tx, ty, anchor, inside in port_label_candidates(b, name, side):
                box = text_bbox(tx, ty, name, 11, anchor)
                if inside and (box[0] < b['x'] + 2 or box[2] > b['x'] + b['w'] - 2
                               or box[1] < b['y'] + 2 or box[3] > b['y'] + b['h'] - 2):
                    continue
                obs = list(own_text[b['id']]) if inside else []
                for oid, r in rects.items():
                    if inside and oid == b['id']:
                        continue
                    if box_hits(box, (r[0] - 1, r[1] - 1, r[0] + r[2] + 1, r[1] + r[3] + 1)):
                        break
                else:
                    if any(box_hits(box, q) for q in placed):
                        continue
                    if any(box_hits(box, q) for q in extra_obstacles):
                        continue
                    chosen = (tx, ty, anchor)
                    break
            if chosen is None:
                tx, ty, anchor, _ = port_label_candidates(b, name, side)[0]
                chosen = (tx, ty, anchor)
                warns.append(f'端口 {b["id"]}.{name} 的标签四周都放不下，已回退到默认位置；'
                             f'请拉开块间距')
            out[(b['id'], name)] = chosen
            box = text_bbox(chosen[0], chosen[1], name, 11, chosen[2])
            placed.append(box)
            by_block.setdefault(b['id'], []).append(box)
    return out, placed, by_block, warns


def draw_table(b, st):
    """表格块：sub 用 | 分行、; 分列，第一行是表头；画成原理图样式的网格表。"""
    x, y, w, h = b['x'], b['y'], b['w'], b['h']
    rows = [ln for ln in b['subtitle'].split('|') if ln.strip()]
    if not rows:
        return ''
    cols = max(len([c for c in r.split(';')]) for r in rows)
    x0, y0 = x + 10, y + 40
    tw, rh = w - 20, 20
    c0 = tw * 0.42
    cw = [c0] + ([(tw - c0) / (cols - 1)] * (cols - 1) if cols > 1 else [])
    out = []
    for i, row in enumerate(rows):
        cells = [c.strip() for c in row.split(';')]
        yy = y0 + i * rh
        for j in range(cols):
            cx = x0 + sum(cw[:j])
            fill = '#ECEFF1' if i == 0 else '#FFFFFF'
            out.append(f'<rect x="{cx:.1f}" y="{yy:.1f}" width="{cw[j]:.1f}" height="{rh}" '
                       f'fill="{fill}" stroke="#90A4AE" stroke-width="1"/>')
            if j < len(cells):
                weight = ' font-weight="bold"' if i == 0 else ''
                out.append(f'<text x="{cx + 5:.1f}" y="{yy + 14:.1f}" '
                           f'font-family={quoteattr(FONT)} font-size="10" fill="#263238"{weight}>'
                           f'{xml_escape(cells[j])}</text>')
    return ''.join(out)


def _draw_res_v(cx, ytop, ybot, ref, val, nc, out, pos='up'):
    """竖电阻：本体靠 VCC/GND 端，信号线只跨引线；nc=灰色虚线并标 DNP/NC。"""
    fg = '#9E9E9E' if nc else '#37474F'
    body_y = ytop + 10 if pos == 'up' else ybot - 32
    dash = ' stroke-dasharray="3 3"' if nc else ''
    out.append(f'<line x1="{cx:.1f}" y1="{ytop:.1f}" x2="{cx:.1f}" y2="{body_y:.1f}" '
               f'stroke="{fg}" stroke-width="1.2"/>')
    out.append(f'<rect x="{cx - 5:.1f}" y="{body_y:.1f}" width="10" height="22" rx="2" '
               f'fill="{"#FAFAFA" if nc else "#FFFFFF"}" stroke="{fg}" stroke-width="1.2"{dash}/>')
    out.append(f'<line x1="{cx:.1f}" y1="{body_y + 22:.1f}" x2="{cx:.1f}" y2="{ybot:.1f}" '
               f'stroke="{fg}" stroke-width="1.2"/>')
    if nc:
        label = f'{ref} {val}/NC' if ref else 'NC'
    else:
        label = f'{ref} {val}'.strip()
    if label:
        out.append(f'<text x="{cx + 8:.1f}" y="{body_y + 15:.1f}" font-family={quoteattr(FONT)} '
                   f'font-size="9.0" fill="{fg}">{xml_escape(label)}</text>')


def draw_strap_rows(b, st):
    """模式/上下拉块：上 VCC、下 GND，竖电阻列；实线=贴装，灰色虚线=NC/DNP。"""
    x, y, w, h = b['x'], b['y'], b['w'], b['h']
    out = []
    rows = [ln for ln in b['subtitle'].split('|') if ln.strip()]
    n = max(len(rows), 1)
    y_vcc, y_gnd = y + 42, y + h - 40
    out.append(f'<line x1="{x + 95:.1f}" y1="{y_vcc:.1f}" x2="{x + w - 10:.1f}" '
               f'y2="{y_vcc:.1f}" stroke="#455A64" stroke-width="1.2"/>')
    out.append(f'<text x="{x + 10:.1f}" y="{y_vcc - 6:.1f}" font-family={quoteattr(FONT)} '
               f'font-size="9" fill="#546E7A">VCC</text>')
    out.append(f'<line x1="{x + 95:.1f}" y1="{y_gnd:.1f}" x2="{x + w - 10:.1f}" '
               f'y2="{y_gnd:.1f}" stroke="#455A64" stroke-width="1.2"/>')
    out.append(f'<text x="{x + 10:.1f}" y="{y_gnd + 12:.1f}" font-family={quoteattr(FONT)} '
               f'font-size="9" fill="#546E7A">GND</text>')
    step = (w - 175.0) / max(n - 1, 1)
    for i, row in enumerate(rows):
        p = [v.strip() for v in row.split(';')]
        if len(p) < 4:
            continue
        net = p[0]
        up_ref, up_val, up_state = p[1], p[2], p[3]
        down_ref = p[4] if len(p) > 4 else ''
        down_val = p[5] if len(p) > 5 else ''
        down_state = p[6] if len(p) > 6 else 'NC'
        up_nc = up_state.upper() in ('NC', 'DNP', 'DNF') or not up_ref
        down_nc = down_state.upper() in ('NC', 'DNP', 'DNF') or not down_ref
        y_sig = y + 82 + i * 45
        cx = x + 105 + i * step
        out.append(f'<text x="{x + 10:.1f}" y="{y_sig + 4:.1f}" font-family={quoteattr(FONT)} '
                   f'font-size="9.5" fill="#37474F">{xml_escape(net)}</text>')
        out.append(f'<line x1="{x + 92:.1f}" y1="{y_sig:.1f}" x2="{cx:.1f}" y2="{y_sig:.1f}" '
                   f'stroke="#455A64" stroke-width="1.2"/>')
        out.append(f'<circle cx="{cx:.1f}" cy="{y_sig:.1f}" r="2.6" fill="#455A64"/>')
        _draw_res_v(cx, y_vcc, y_sig, up_ref, up_val, up_nc, out, 'up')
        _draw_res_v(cx, y_sig, y_gnd, down_ref, down_val, down_nc, out, 'down')
    return ''.join(out)


def draw_block(b, port_labels):
    st = BLOCK_STYLE[b['style']]
    x, y, w, h = b['x'], b['y'], b['w'], b['h']
    flat = st.get('flat', False)
    out = [f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
           f'rx="{BLOCK_RX}" fill="{st["body"]}" stroke="{st["stroke"]}" stroke-width="1.8" '
           f'id="blk-{xml_escape(b["id"])}" data-node="block" '
           f'data-label={quoteattr(b["title"] + ("|" + b["subtitle"] if b["subtitle"] else ""))}/>']
    if not flat:
        out.append(f'<path d="{header_path(x, y, w)}" fill="{st["header"]}"/>')
    title_y = y + (21 if flat else 18)
    title_fs = st.get('fs', 13)
    out.append(f'<text x="{x + 10:.1f}" y="{title_y:.1f}" font-family={quoteattr(FONT)} '
               f'font-size="{title_fs}" font-weight="bold" fill="{st["text"]}">'
               f'{xml_escape(b["title"])}</text>')
    if b['page'] not in ('', '—', '-'):
        page_fill = '#78909C' if flat else st['text']
        out.append(f'<text x="{x + w - 8:.1f}" y="{title_y:.1f}" text-anchor="end" '
                   f'font-family={quoteattr(FONT)} font-size="10" fill="{page_fill}">'
                   f'{xml_escape(page_label(b["page"]))}</text>')
    if b['style'] == 'strap':
        out.append(draw_strap_rows(b, st))
    elif b['style'] == 'table':
        out.append(draw_table(b, st))
    elif b['subtitle']:
        yy, sub_fs, lines, sub_fill = subtitle_geom(b)
        out.append(f'<text x="{x + 10:.1f}" y="{yy:.1f}" font-family={quoteattr(FONT)} '
                   f'font-size="{sub_fs}" fill="{sub_fill}">')
        for i, ln in enumerate(lines):
            dy = 0 if i == 0 else 15
            out.append(f'<tspan x="{x + 10:.1f}" dy="{dy}">{xml_escape(ln)}</tspan>')
        out.append('</text>')
    for name, (side, _) in b['ports'].items():
        (px, py), side = block_port(b, name)
        out.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="3.6" fill="#ffffff" '
                   f'stroke="{st["stroke"]}" stroke-width="1.5"/>')
        if not st.get('show_ports', True):
            continue
        tx, ty, anchor = port_labels.get((b['id'], name),
                                         (px + 6, py + 4, 'start'))
        out.append(f'<text x="{tx:.1f}" y="{ty:.1f}" text-anchor="{anchor}" '
                   f'font-family={quoteattr(FONT)} font-size="11" fill="#37474F">'
                   f'{xml_escape(name)}</text>')
    return ''.join(out)


def simplify_path(pts):
    """去掉重复点与共线点：同一 x/y 上的中间点直接删掉，能拉直就拉直。"""
    out = []
    for p in pts:
        if not out or abs(p[0] - out[-1][0]) > 0.5 or abs(p[1] - out[-1][1]) > 0.5:
            out.append(p)
    if len(out) <= 2:
        return out
    res = [out[0]]
    for i in range(1, len(out) - 1):
        a, b, c = res[-1], out[i], out[i + 1]
        same_x = abs(a[0] - b[0]) < 0.5 and abs(b[0] - c[0]) < 0.5
        same_y = abs(a[1] - b[1]) < 0.5 and abs(b[1] - c[1]) < 0.5
        if same_x or same_y:
            continue
        res.append(b)
    res.append(out[-1])
    return res


def _seg_crosses_rect(p, q, rect, margin=0.5):
    """正交线段是否穿入矩形内部（贴边不算）。"""
    x, y, w, h = rect
    x0, y0, x1, y1 = x + margin, y + margin, x + w - margin, y + h - margin
    if x1 <= x0 or y1 <= y0:
        return False
    px, py = p
    qx, qy = q
    if abs(py - qy) < 0.5:
        if not (y0 < py < y1):
            return False
        lo, hi = sorted((px, qx))
        return hi > x0 and lo < x1
    if abs(px - qx) < 0.5:
        if not (x0 < px < x1):
            return False
        lo, hi = sorted((py, qy))
        return hi > y0 and lo < y1
    return False


def _point_in_rect(x, y, rect, margin=0.5):
    rx, ry, rw, rh = rect
    return rx + margin < x < rx + rw - margin and ry + margin < y < ry + rh - margin


def _inflate(rect, d):
    return (rect[0] - d, rect[1] - d, rect[2] + 2 * d, rect[3] + 2 * d)


def _add_lanes(values, gap=80):
    vals = sorted(set(round(v, 2) for v in values))
    out = list(vals)
    for a, b in zip(vals, vals[1:]):
        if b - a > gap:
            out.append((a + b) / 2.0)
    return sorted(set(out))


def _overlap_penalty(p, q, occupied):
    if not occupied:
        return 0
    pen = 0
    horiz = abs(p[1] - q[1]) < 0.5
    line = p[1] if horiz else p[0]
    lo, hi = sorted((p[0], q[0])) if horiz else sorted((p[1], q[1]))
    for axis, coord, olo, ohi in occupied:
        if (axis == 'h') != horiz:
            continue
        if abs(coord - line) > 0.5:
            continue
        ov = min(hi, ohi) - max(lo, olo)
        if ov > 2:
            pen += 300
    return pen


def _legacy_route(p1, s1, p2, s2):
    """A* 失败时的保底走线（保持老行为）。"""
    x1, y1 = p1
    x2, y2 = p2
    if abs(x1 - x2) < 0.5 and abs(y1 - y2) < 0.5:
        return [p1, p2]
    if s1 in ('T', 'B') and s2 in ('T', 'B'):
        if abs(x1 - x2) < 0.5:
            return [p1, p2]
        if abs(x1 - x2) < 80:
            return [p1, (x2, y1), p2]
        my = (y1 + y2) / 2.0
        return [p1, (x1, my), (x2, my), p2]
    if s1 in ('L', 'R') and s2 in ('L', 'R'):
        if abs(y1 - y2) < 0.5:
            return [p1, p2]
        if abs(y1 - y2) < 60:
            return [p1, (x1, y2), p2]
        mx = (x1 + x2) / 2.0
        return [p1, (mx, y1), (mx, y2), p2]
    if s1 == 'R' and s2 == 'T':
        return [p1, (x1 + 24, y1), (x1 + 24, y2 - 24), (x2, y2 - 24), p2]
    if s1 == 'B' and s2 == 'L':
        return [p1, (x1, y1 + 24), (x2 - 24, y1 + 24), (x2 - 24, y2), p2]
    if s1 == 'R' and s2 == 'B':
        return [p1, (x1 + 24, y1), (x1 + 24, y2 + 24), (x2, y2 + 24), p2]
    if s1 == 'T' and s2 == 'L':
        return [p1, (x1, y1 - 24), (x2 - 24, y1 - 24), (x2 - 24, y2), p2]
    return [p1, p2]


PORT_DIR = {'L': (-1, 0), 'R': (1, 0), 'T': (0, -1), 'B': (0, 1)}


def auto_route(p1, s1, p2, s2, obstacles=(), grid_obstacles=None, occupied=(),
               diagonal=False):
    """最短路径优先的正交布线。

    - 先试端口同轴直连；
    - 否则在“块边界 ±ROUTE_CLEAR”构成的稀疏网格上做 A*：
      代价 = 长度 + 拐弯代价 + 与已布线段重叠的惩罚；
    - 端口先垂直出线 PORT_STUB，保证不贴着块边跑；
    - 找不到路时退回 _legacy_route。

    `diagonal=True` 时直接画 p1→p2 斜线（**只用于接口详图**；
    主控制关系图必须保持正交，[S4] 会查）。
    `obstacles` 必须是**其他**功能块的矩形（端点块由调用方排除）；
    `grid_obstacles` 传全部块（含端点块），防止线穿过自身块。
    """
    if grid_obstacles is None:
        grid_obstacles = obstacles
    x1, y1 = p1
    x2, y2 = p2
    if abs(x1 - x2) < 0.5 and abs(y1 - y2) < 0.5:
        return [p1, p2]
    if diagonal:
        # 接口详图：水平出线 → 中段斜线 → 水平进端口。
        # TX/RX 这类交叉信号在中间斜线处交叉，两端仍保持水平，
        # 既不共用竖直通道，也不会像整条斜线那样难读。
        def jog(gap):
            if gap < 2:
                return 0.0
            return max(8.0, min(160.0, gap * 0.3, gap / 2.0 - 1.0))

        if s1 in ('L', 'R') and s2 in ('L', 'R'):
            d = jog(abs(x2 - x1))
            if d <= 0:
                return [p1, p2]
            sign = 1.0 if x2 > x1 else -1.0
            return [p1, (x1 + sign * d, y1), (x2 - sign * d, y2), p2]
        if s1 in ('T', 'B') and s2 in ('T', 'B'):
            d = jog(abs(y2 - y1))
            if d <= 0:
                return [p1, p2]
            sign = 1.0 if y2 > y1 else -1.0
            return [p1, (x1, y1 + sign * d), (x2, y2 - sign * d), p2]
        return [p1, p2]

    # 1) 同轴直连（必须不穿别的块）
    aligned_v = abs(x1 - x2) < 0.5 and s1 in ('T', 'B') and s2 in ('T', 'B')
    aligned_h = abs(y1 - y2) < 0.5 and s1 in ('L', 'R') and s2 in ('L', 'R')
    if (aligned_v or aligned_h) and not any(
            _seg_crosses_rect(p1, p2, r) for r in obstacles):
        return [p1, p2]

    # 2) 端口出线点；出线若不巧穿过别的块就逐步缩短
    def stub(p, side):
        dx, dy = PORT_DIR[side]
        L = PORT_STUB
        while L > 4 and any(_seg_crosses_rect(
                p, (p[0] + dx * L, p[1] + dy * L), r) for r in obstacles):
            L -= 2
        return (p[0] + dx * L, p[1] + dy * L)

    a = stub(p1, s1)
    b = stub(p2, s2)

    # 3) 稀疏正交网格：块边界 ± 间距 + 端口/出线点
    xs, ys = {x1, x2, a[0], b[0]}, {y1, y2, a[1], b[1]}
    for r in grid_obstacles:
        x, y, w, h = r
        for d in (ROUTE_CLEAR, ROUTE_CLEAR + ROUTE_LANE_EXTRA):
            xs.update((x - d, x + w + d))
            ys.update((y - d, y + h + d))
    xs = _add_lanes(xs)
    ys = _add_lanes(ys)
    xi = {v: i for i, v in enumerate(xs)}
    yi = {v: i for i, v in enumerate(ys)}
    nx, ny = len(xs), len(ys)
    grid = [_inflate(r, ROUTE_CLEAR) for r in grid_obstacles]

    def blocked(x, y):
        return any(_point_in_rect(x, y, r) for r in grid)

    si = (xi[round(a[0], 2)], yi[round(a[1], 2)])
    ti = (xi[round(b[0], 2)], yi[round(b[1], 2)])
    # 端点允许落在“端点块膨胀区”里（两块挨得近时出线点会越界）
    valid = [[not blocked(xs[i], ys[j]) for j in range(ny)] for i in range(nx)]
    valid[si[0]][si[1]] = True
    valid[ti[0]][ti[1]] = True

    moves = ((1, 0, 1), (-1, 0, 1), (0, 1, 2), (0, -1, 2))
    best = {(si[0], si[1], 0): 0.0}
    prev = {}
    heap = [(0.0, 0, 0.0, si[0], si[1], 0)]
    counter = 1
    goal = None
    while heap:
        _, _, cost, i, j, d = heapq.heappop(heap)
        key = (i, j, d)
        if cost > best.get(key, 1e18) + 1e-9:
            continue
        if (i, j) == ti:
            goal = key
            break
        x, y = xs[i], ys[j]
        for di, dj, nd in moves:
            ni, nj = i + di, j + dj
            if not (0 <= ni < nx and 0 <= nj < ny):
                continue
            if not valid[ni][nj]:
                continue
            q = (xs[ni], ys[nj])
            if any(_seg_crosses_rect((x, y), q, r) for r in grid):
                continue
            step = abs(q[0] - x) + abs(q[1] - y)
            turn = 0 if d in (0, nd) else TURN_COST
            add = step + turn + _overlap_penalty((x, y), q, occupied)
            nkey = (ni, nj, nd)
            ncost = cost + add
            if ncost + 1e-9 < best.get(nkey, 1e18):
                best[nkey] = ncost
                prev[nkey] = key
                h = abs(q[0] - b[0]) + abs(q[1] - b[1])
                heapq.heappush(heap, (ncost + h, counter, ncost, ni, nj, nd))
                counter += 1

    if goal is None:
        return _legacy_route(p1, s1, p2, s2)

    path = []
    key = goal
    while key in prev:
        path.append((xs[key[0]], ys[key[1]]))
        key = prev[key]
    path.append((xs[key[0]], ys[key[1]]))
    path.reverse()
    return simplify_path([p1] + path + [p2])


def arrowhead(p_prev, p_end, color, size=9.0):
    dx, dy = p_end[0] - p_prev[0], p_end[1] - p_prev[1]
    L = math.hypot(dx, dy) or 1.0
    ux, uy = dx / L, dy / L
    bx, by = p_end[0] - ux * size, p_end[1] - uy * size
    px, py = -uy * size * 0.45, ux * size * 0.45
    return (f'<polygon points="{p_end[0]:.1f},{p_end[1]:.1f} '
            f'{bx + px:.1f},{by + py:.1f} {bx - px:.1f},{by - py:.1f}" fill="{color}"/>')


def edge_label_geometry(label):
    lines = wrap(label, EDGE_LABEL_WRAP, EDGE_LABEL_FS)
    # 整数像素宽，避免 Linux/Windows 在 .65 处 round-half-even 差 0.1px
    w = int(max(units(l) for l in lines) * EDGE_LABEL_FS + 8.5)
    h = len(lines) * 13 + 3
    return lines, w, h


def draw_edge_label(label, lx, ly, color):
    lines, w, h = edge_label_geometry(label)
    out = [f'<rect x="{lx - w / 2:.1f}" y="{ly - h / 2:.1f}" width="{w:.1f}" '
           f'height="{h:.1f}" rx="2" fill="#ffffff" fill-opacity="0.96"/>']
    out.append(f'<text x="{lx:.1f}" y="{ly - len(lines) * 13 / 2 + 9.5:.1f}" '
               f'text-anchor="middle" font-family={quoteattr(FONT)} '
               f'font-size="{EDGE_LABEL_FS}" fill="{color}">')
    for i, ln in enumerate(lines):
        dy = 0 if i == 0 else 13
        out.append(f'<tspan x="{lx:.1f}" dy="{dy}">{xml_escape(ln)}</tspan>')
    out.append('</text>')
    return ''.join(out)


def place_edge_label(pts, w, h, obstacles, boxes):
    """在最长线段上找不压块/不压已有文字的位置；找不到就不标。

    obstacles/boxes 都必须是角点框 (x0, y0, x1, y1)。
    """
    segs = []
    for i in range(len(pts) - 1):
        (x1, y1), (x2, y2) = pts[i], pts[i + 1]
        segs.append((abs(x2 - x1) + abs(y2 - y1), i))
    for length, i in sorted(segs, reverse=True):
        if length < 50:
            continue
        (x1, y1), (x2, y2) = pts[i], pts[i + 1]
        for f in (0.5, 0.35, 0.65, 0.2, 0.8):
            cx, cy = x1 + (x2 - x1) * f, y1 + (y2 - y1) * f
            box = (cx - w / 2 - 2, cy - h / 2 - 2, cx + w / 2 + 2, cy + h / 2 + 2)
            if any(box_hits(box, r) for r in obstacles):
                continue
            if any(box_hits(box, q) for q in boxes):
                continue
            return cx, cy
    return None


def edge_segments(pts):
    segs = []
    for i in range(len(pts) - 1):
        (x1, y1), (x2, y2) = pts[i], pts[i + 1]
        axis = 'h' if abs(y1 - y2) < 0.5 else 'v'
        coord = y1 if axis == 'h' else x1
        lo, hi = sorted((x1, x2)) if axis == 'h' else sorted((y1, y2))
        segs.append((axis, coord, lo, hi))
    return segs


def segment_box(axis, coord, lo, hi, pad=2):
    if axis == 'h':
        return (lo, coord - pad, hi, coord + pad)
    return (coord - pad, lo, coord + pad, hi)


def draw_edge(e, pts, label_obstacles, label_boxes):
    p1 = pts[0]
    p2 = pts[-1]
    st = EDGE_STYLE[e['type']]
    color = e['color'] or st['color']
    dash = f' stroke-dasharray="{st["dash"]}"' if st['dash'] else ''
    d = 'M ' + ' L '.join(f'{x:.1f},{y:.1f}' for x, y in pts)
    out = [f'<g data-row="{xml_escape(e["row"])}" data-hop="1" '
           f'data-from="blk-{xml_escape(e["from"])}" data-to="blk-{xml_escape(e["to"])}" '
           f'data-edge="{xml_escape(e["id"])}" data-kind="{xml_escape(e["type"])}">',
           f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{st["width"]}" '
           f'stroke-linejoin="round" stroke-linecap="round"{dash}/>',
           arrowhead(pts[-2] if len(pts) > 1 else p1, p2, color), '</g>']
    if e['label']:
        lines, w, h = edge_label_geometry(e['label'])
        pos = place_edge_label(pts, w, h, label_obstacles, label_boxes)
        # 短连线/找不到空位就不标字：宁可不写（md 表里仍有）
        if pos is not None:
            out.append(draw_edge_label(e['label'], pos[0], pos[1], color))
            label_boxes.append((pos[0] - w / 2, pos[1] - h / 2,
                                pos[0] + w / 2, pos[1] + h / 2))
    return ''.join(out)


def draw_zone(z):
    return (f'<g data-zone="{xml_escape(z["id"])}">'
            f'<rect x="{z["x"]:.1f}" y="{z["y"]:.1f}" width="{z["w"]:.1f}" '
            f'height="{z["h"]:.1f}" rx="12" fill="{z["color"]}" fill-opacity="0.28" '
            f'stroke="#90A4AE" stroke-width="1.2" stroke-dasharray="7 5"/>'
            f'<text x="{z["x"] + 12:.1f}" y="{z["y"] + 20:.1f}" '
            f'font-family={quoteattr(FONT)} font-size="13" font-weight="bold" '
            f'fill="#37474F">{xml_escape(z["title"])}</text></g>')


def draw_grid(W, H):
    out = [f'<rect x="20" y="60" width="{W - 40}" height="{H - 90}" fill="none" '
           f'stroke="#B0BEC5" stroke-width="1"/>',
           f'<rect x="20" y="60" width="{W - 40}" height="{H - 90}" fill="none" '
           f'stroke="#B0BEC5" stroke-width="1"/>']
    for i in range(1, 9):
        x = 20 + (W - 40) * i / 9.0
        for y in (45, H - 32):
            out.append(f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle" '
                       f'font-family={quoteattr(FONT)} font-size="10" fill="#90A4AE">{i}</text>')
    for i in range(1, 7):
        y = 60 + (H - 90) * i / 7.0
        for x in (10, W - 10):
            out.append(f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle" '
                       f'font-family={quoteattr(FONT)} font-size="10" fill="#90A4AE">'
                       f'{chr(64 + i)}</text>')
    return ''.join(out)


def draw_legend(W, H):
    x, y, w, h = 40, H - 140, 700, 80
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="#FAFBFC" '
           f'stroke="#90A4AE" stroke-width="1"/>',
           f'<text x="{x + 12}" y="{y + 20}" font-family={quoteattr(FONT)} font-size="12" '
           f'font-weight="bold" fill="#263238">图例</text>']
    items = [('power', '电源'), ('control', '控制/GPIO'), ('i2c', 'I2C/数据'),
             ('int', '中断/状态'), ('analog', '模拟/反馈')]
    lx = x + 60
    for k, label in items:
        st = EDGE_STYLE[k]
        dash = f' stroke-dasharray="{st["dash"]}"' if st['dash'] else ''
        out.append(f'<line x1="{lx}" y1="{y + 32}" x2="{lx + 34}" y2="{y + 32}" '
                   f'stroke="{st["color"]}" stroke-width="{st["width"]}"{dash}/>')
        out.append(f'<text x="{lx + 40}" y="{y + 36}" font-family={quoteattr(FONT)} '
                   f'font-size="10.5" fill="#37474F">{xml_escape(label)}</text>')
        lx += 40 + units(label) * 10.5 + 18
    chips = [('core', '核心芯片'), ('peripheral', '外围'), ('soc', 'SoC 接口'),
             ('rail', '电源网络'), ('note', '说明')]
    cx = x + 60
    for k, label in chips:
        st = BLOCK_STYLE[k]
        out.append(f'<rect x="{cx}" y="{y + 50}" width="14" height="12" rx="2" '
                   f'fill="{st["header"]}"/>')
        out.append(f'<text x="{cx + 19}" y="{y + 60}" font-family={quoteattr(FONT)} '
                   f'font-size="10" fill="#37474F">{xml_escape(label)}</text>')
        cx += 19 + units(label) * 10 + 20
    out.append(f'<text x="{x + 12}" y="{y + 73}" font-family={quoteattr(FONT)} '
               f'font-size="10.5" fill="#546E7A">'
               f'p.N = 源原理图页码（块右上角，不是本图页号）</text>')
    return ''.join(out)


def draw_title_block(W, H, title, date, source):
    x, y, w, h = W - 420, H - 140, 400, 80
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="4" fill="#FFFFFF" '
           f'stroke="#37474F" stroke-width="1.2"/>',
           f'<text x="{x + 12}" y="{y + 22}" font-family={quoteattr(FONT)} font-size="14" '
           f'font-weight="bold" fill="#1A237E">{xml_escape(title)}</text>',
           f'<text x="{x + 12}" y="{y + 40}" font-family={quoteattr(FONT)} font-size="10.5" '
           f'fill="#37474F">本图 1/1　|　{xml_escape(date or "日期未标注")}</text>',
           f'<text x="{x + 12}" y="{y + 56}" font-family={quoteattr(FONT)} font-size="10.5" '
           f'fill="#37474F">依据：{xml_escape(source or "原理图 PDF + 过程 TSV")}</text>',
           f'<text x="{x + 12}" y="{y + 72}" font-family={quoteattr(FONT)} font-size="10" '
           f'fill="#78909C">control_map_svg.py --style block</text>']
    return ''.join(out)


def check_zone_containment(zones, blocks):
    zmap = {z['id']: z for z in zones}
    problems = []
    for b in blocks:
        if not b['zone']:
            continue
        z = zmap[b['zone']]
        over = []
        if b['x'] < z['x'] - 0.5:
            over.append(f'左 {z["x"] - b["x"]:.0f}px')
        if b['y'] < z['y'] - 0.5:
            over.append(f'上 {z["y"] - b["y"]:.0f}px')
        if b['x'] + b['w'] > z['x'] + z['w'] + 0.5:
            over.append(f'右 {b["x"] + b["w"] - (z["x"] + z["w"]):.0f}px')
        if b['y'] + b['h'] > z['y'] + z['h'] + 0.5:
            over.append(f'下 {b["y"] + b["h"] - (z["y"] + z["h"]):.0f}px')
        if over:
            problems.append(f'block {b["id"]} 越出分区 {b["zone"]}（{", ".join(over)}）')
    if problems:
        sys.exit('功能块超出分区背景框（框必须完全落在所属分区内）：\n  '
                 + '\n  '.join(problems)
                 + '\n请缩小/移动块，或放大 zones.tsv 里的分区；不要靠视觉硬挤。')


def fit_zones(zones, blocks, extra_boxes=None):
    """分区按成员块（含块外端口名）收紧右/下边；左/上边保持声明值，便于各分区对齐。"""
    extra_boxes = extra_boxes or {}
    for z in zones:
        members = [b for b in blocks if b['zone'] == z['id']]
        if not members:
            continue
        boxes = [(b['x'], b['y'], b['x'] + b['w'], b['y'] + b['h']) for b in members]
        for b in members:
            boxes.extend(extra_boxes.get(b['id'], []))
        x1 = max(t[2] for t in boxes)
        y1 = max(t[3] for t in boxes)
        nx1 = min(z['x'] + z['w'], x1 + ZONE_PAD)
        ny1 = min(z['y'] + z['h'], y1 + ZONE_PAD)
        if nx1 - z['x'] >= 40 and ny1 - z['y'] >= 40:
            z['w'], z['h'] = nx1 - z['x'], ny1 - z['y']


def align_zone_edges(zones, tol=40.0):
    """横向互不重叠的分区（并排的列）尽量对齐上/下边；差值 ≤ tol 才对齐。"""
    for i in range(len(zones)):
        for j in range(i + 1, len(zones)):
            a, b = zones[i], zones[j]
            if a['x'] < b['x'] + b['w'] and b['x'] < a['x'] + a['w']:
                continue
            if abs(a['y'] - b['y']) <= tol:
                top = min(a['y'], b['y'])
                a['h'] += a['y'] - top
                b['h'] += b['y'] - top
                a['y'] = b['y'] = top
            ba, bb = a['y'] + a['h'], b['y'] + b['h']
            if abs(ba - bb) <= tol:
                bottom = max(ba, bb)
                a['h'], b['h'] = bottom - a['y'], bottom - b['y']


def build_block_svg(ctrl_rows, power_rows, dev_rows, zones, blocks, edges,
                    title, date, source, diagonal=False, min_height=1250):
    bmap = {b['id']: b for b in blocks}
    for e in edges:
        if e['from'] not in bmap:
            sys.exit(f'edge {e["id"]} 的 from 块不存在: {e["from"]}')
        if e['to'] not in bmap:
            sys.exit(f'edge {e["id"]} 的 to 块不存在: {e["to"]}')
        block_port(bmap[e['from']], e['from_port'])
        block_port(bmap[e['to']], e['to_port'])
    for b in blocks:
        if b['zone'] and b['zone'] not in {z['id'] for z in zones}:
            sys.exit(f'block {b["id"]} 的区域 {b["zone"]!r} 不在 zones 里')

    used_zones = {b['zone'] for b in blocks if b['zone']}
    empty = [z['id'] for z in zones if z['id'] not in used_zones]
    if empty:
        print(f'⚠ 分区没有成员块，已省略: {", ".join(empty)}', file=sys.stderr)
        zones = [z for z in zones if z['id'] in used_zones]

    # 先布线：端口标签要避开已经布好的线，所以路由必须先于标签放置
    routed, occupied = [], []
    block_rects = [(b['x'], b['y'], b['w'], b['h']) for b in blocks]
    for e in edges:
        b1, b2 = bmap[e['from']], bmap[e['to']]
        p1, s1 = block_port(b1, e['from_port'])
        p2, s2 = block_port(b2, e['to_port'])
        others = [(b['x'], b['y'], b['w'], b['h']) for bid, b in bmap.items()
                  if bid not in (e['from'], e['to'])]
        if e['points']:
            pts = simplify_path([p1] + e['points'] + [p2])
        else:
            pts = auto_route(p1, s1, p2, s2, others, block_rects, occupied,
                             diagonal=diagonal)
        segs = edge_segments(pts)
        occupied.extend(segs)
        routed.append((e, pts, segs))

    seg_boxes = [segment_box(*s) for s in occupied]
    port_labels, port_boxes, port_boxes_by_block, warns = layout_port_labels(blocks, seg_boxes)
    for msg in warns:
        print(f'⚠ {msg}', file=sys.stderr)

    check_zone_containment(zones, blocks)
    fit_zones(zones, blocks, port_boxes_by_block)
    align_zone_edges(zones)

    right = max([z['x'] + z['w'] for z in zones] +
                [b['x'] + b['w'] for b in blocks] + [0])
    max_y = max([z['y'] + z['h'] for z in zones] +
                [b['y'] + b['h'] for b in blocks] + [0])
    W = max(2250, int(math.ceil(right + 40)))
    H = max(min_height, int(max_y + 260))

    label_boxes = list(port_boxes)
    block_boxes = [(x - 2, y - 2, x + w + 2, y + h + 2) for x, y, w, h in block_rects]
    body = [draw_grid(W, H)]
    body += [draw_zone(z) for z in zones]
    for e, pts, segs in routed:
        other_lines = [segment_box(*s) for e2, _, segs2 in routed
                       if e2['id'] != e['id'] for s in segs2]
        body.append(draw_edge(e, pts, block_boxes + other_lines, label_boxes))
    body += [draw_block(b, port_labels) for b in blocks]
    body.append(draw_legend(W, H))
    body.append(draw_title_block(W, H, title, date, source))

    manifest = {
        'schema': 1, 'style': 'block', 'title': title, 'date': date, 'source': source,
        'control_rows': ctrl_rows, 'power_rows': power_rows, 'device_rows': dev_rows,
        'layout_rows': blocks, 'zone_rows': zones, 'edge_rows': edges,
        'nodes': [{'id': 'blk-' + b['id'], 'role': 'block', 'label': b['title']} for b in blocks],
        'edges': [{'row': e['row'], 'hop': 1, 'from': 'blk-' + e['from'],
                   'to': 'blk-' + e['to'], 'id': e['id']} for e in edges],
    }
    head = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" role="img">',
        f'<title>{xml_escape(title)}</title>',
        f'<desc>{xml_escape("系统框图：功能块/电源路径/控制关系；由 TSV 确定性生成")}</desc>',
        f'<metadata id="control-map-manifest">{xml_escape(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")))}</metadata>',
        f'<rect x="0" y="0" width="{W}" height="{H}" fill="#FFFFFF"/>',
    ]
    return '\n'.join(head + body) + '\n</svg>\n'


# ---------------------------------------------------------------------------
# SVG 主体（表格样式，保留兼容）
# ---------------------------------------------------------------------------
def build_svg(ctrl_rows, power_rows, dev_rows, title, date):
    body = []
    y = M
    body.append(f'<text x="{M}" y="{y + 10}" font-family={quoteattr(FONT)} font-size="26" '
                f'font-weight="bold" fill="#16324f">{xml_escape(title)}</text>')
    y += 26
    sub = '由结构化 TSV 确定性生成；控制关系 + 电源树。完整网络与参数见同目录《原理图分析.md》。'
    if date:
        sub += f'　生成日期：{date}'
    body.append(f'<text x="{M}" y="{y + 12}" font-family={quoteattr(FONT)} font-size="12" '
                f'fill="#5b6b7f">{xml_escape(sub)}</text>')
    y += 30

    # 电源树
    panel, y, pn, pe = render_power_panel(power_rows, y, '① 电源树（Power Tree）')
    body.append(panel)
    y += PANEL_GAP

    # 控制关系
    panel, y, cn, ce = render_control_panel(ctrl_rows, y, '② 控制关系（Control Map）')
    body.append(panel)
    y += PANEL_GAP

    # 图例
    lx, ly = M, y
    body.append(f'<rect x="{M}" y="{ly}" width="{W - 2 * M}" height="58" rx="6" '
                f'fill="#fafbfc" stroke="#c9d2dd"/>')
    body.append(f'<text x="{lx + 12}" y="{ly + 20}" font-family={quoteattr(FONT)} '
                f'font-size="13" font-weight="bold" fill="#1f3a5f">图例</text>')
    items = [
        ('#3b6ea5', '1.6', '', '控制/GPIO'),
        ('#4a4a9c', '1.6', '2 3', 'I2C/数据（点线）'),
        ('#44546a', '1.6', '6 4', '中断/状态反馈（虚线）'),
        ('#2f7d32', '2.8', '', '电源路径（粗实线）'),
    ]
    lx2 = lx + 70
    for color, wd, dash, label in items:
        d = f' stroke-dasharray="{dash}"' if dash else ''
        body.append(f'<line x1="{lx2}" y1="{ly + 30}" x2="{lx2 + 34}" y2="{ly + 30}" '
                    f'stroke="{color}" stroke-width="{wd}"{d}/>')
        body.append(f'<text x="{lx2 + 42}" y="{ly + 35}" font-family={quoteattr(FONT)} '
                    f'font-size="12" fill="#1a1a1a">{xml_escape(label)}</text>')
        lx2 += 42 + 12 * units(label) + 30
    body.append(f'<text x="{lx + 12}" y="{ly + 50}" font-family={quoteattr(FONT)} '
                f'font-size="11" fill="#5b6b7f">'
                f'每个方框是一行 TSV；箭头方向 = 信号/控制方向；灰色虚线框 = 直连（无开关）。</text>')
    y = ly + 58 + 18

    footer = (f'生成：control_map_svg.py　|　控制 {len(ctrl_rows)} 条 / 电源 {len(power_rows)} 条 / '
              f'器件 {len(dev_rows)} 个')
    body.append(f'<text x="{M}" y="{y}" font-family={quoteattr(FONT)} font-size="11" '
                f'fill="#7a8796">{xml_escape(footer)}</text>')
    height = int(y + M)

    manifest = {
        'schema': 1,
        'title': title,
        'date': date,
        'control_rows': ctrl_rows,
        'power_rows': power_rows,
        'device_rows': dev_rows,
        'nodes': pn + cn,
        'edges': pe + ce,
    }
    meta = xml_escape(json.dumps(manifest, ensure_ascii=False, separators=(',', ':')))

    head = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{height}" '
        f'viewBox="0 0 {W} {height}" role="img">',
        f'<title>{xml_escape(title)}</title>',
        f'<desc>{xml_escape("控制关系与电源树；由 control_map_svg.py 从 TSV 生成")}</desc>',
        f'<metadata id="control-map-manifest">{meta}</metadata>',
        f'<rect x="0" y="0" width="{W}" height="{height}" fill="#ffffff"/>',
    ]
    return '\n'.join(head + body) + '\n</svg>\n'


# ---------------------------------------------------------------------------
# md 表格
# ---------------------------------------------------------------------------
def md_escape(s):
    return (s or '').replace('|', '\\|').replace('\n', ' ').strip()


def md_table(cols, rows):
    out = ['| ' + ' | '.join(cols) + ' |',
           '| ' + ' | '.join('---' for _ in cols) + ' |']
    for r in rows:
        out.append('| ' + ' | '.join(md_escape(r.get(c, '')) for c in cols) + ' |')
    return '\n'.join(out) + '\n'


def build_tables(ctrl_rows, power_rows, dev_rows):
    return {
        'device-table': md_table(DEVICE_COLS, dev_rows),
        'control-table': md_table(CONTROL_MD_COLS, ctrl_rows),
        'power-table': md_table(POWER_MD_COLS, power_rows),
    }


def inject_md(md_path, tables):
    with open(md_path, encoding='utf-8') as f:
        text = f.read()
    missing = []
    for key, table in tables.items():
        pat = re.compile(r'(<!--\s*BEGIN\s+' + re.escape(key) + r'[^>]*-->)(.*?)'
                         r'(<!--\s*END\s+' + re.escape(key) + r'\s*-->)', re.S)
        if not pat.search(text):
            missing.append(key)
            continue
        text = pat.sub(lambda m: m.group(1) + '\n' + table + m.group(3), text)
    if missing:
        sys.exit('md 缺少生成表标记: ' + ', '.join(missing) +
                 '\n请先用 assets/schematic-only-report-template.md 建骨架。')
    return text


def main():
    ap = argparse.ArgumentParser(description='控制关系/电源树 TSV → SVG + md 表格')
    ap.add_argument('--map', required=True, help='control-map.tsv')
    ap.add_argument('--power', required=True, help='power-tree.tsv')
    ap.add_argument('--devices', required=True, help='devices.tsv')
    ap.add_argument('--svg', required=True, help='输出 SVG 路径')
    ap.add_argument('--md', help='回填 md 里的三个生成表（只改 BEGIN/END 之间）')
    ap.add_argument('--md-table', help='另存 control-table 片段')
    ap.add_argument('--md-power', help='另存 power-table 片段')
    ap.add_argument('--md-devices', help='另存 device-table 片段')
    ap.add_argument('--title', default='控制关系图')
    ap.add_argument('--date', default='', help='写进图的日期；不填则不写（保证可复现）')
    ap.add_argument('--style', choices=('table', 'block'), default='table',
                    help='table=行式关系表（旧样式）；block=功能块框图（推荐）')
    ap.add_argument('--layout', help='block 样式：layout.tsv（功能块坐标/端口）')
    ap.add_argument('--zones', help='block 样式：zones.tsv（分区容器）')
    ap.add_argument('--edges', help='block 样式：edges.tsv（正交连线）')
    ap.add_argument('--source', default='', help='标题栏里的依据（如原理图 PDF 文件名）')
    ap.add_argument('--diagonal', action='store_true',
                    help='接口详图：水平出线→中段斜线→水平进端口；主图不要用（[S4] 查正交）')
    ap.add_argument('--min-height', type=int, default=1250,
                    help='画布最小高度（默认 1250；接口详图可传 900 收紧空白）')
    ap.add_argument('--check', action='store_true',
                    help='只生成不写盘，与现有文件逐字节比对；不同则退出 1')
    a = ap.parse_args()

    ctrl = read_tsv(a.map, CONTROL_COLS, 'control-map.tsv')
    power = read_tsv(a.power, POWER_COLS, 'power-tree.tsv')
    dev = read_tsv(a.devices, DEVICE_COLS, 'devices.tsv', key=None)

    ids = [r['id'] for r in ctrl + power]
    dup = sorted({x for x in ids if ids.count(x) > 1})
    if dup:
        sys.exit('id 重复: ' + ', '.join(dup))

    if a.style == 'block':
        if not (a.layout and a.zones and a.edges):
            sys.exit('--style block 需要 --layout layout.tsv --zones zones.tsv --edges edges.tsv')
        blocks = parse_blocks(read_tsv(a.layout, BLOCK_COLS, 'layout.tsv'))
        zones = parse_zones(read_tsv(a.zones, ZONE_COLS, 'zones.tsv'))
        edges = parse_edges(read_tsv(a.edges, EDGE_COLS, 'edges.tsv'))
        svg = build_block_svg(ctrl, power, dev, zones, blocks, edges,
                              a.title, a.date, a.source, diagonal=a.diagonal,
                              min_height=a.min_height)
    else:
        svg = build_svg(ctrl, power, dev, a.title, a.date)
    tables = build_tables(ctrl, power, dev)

    bad = False
    if a.check:
        if not os.path.isfile(a.svg):
            sys.exit(f'--check 但文件不存在: {a.svg}')
        old = open(a.svg, encoding='utf-8').read()
        if old != svg:
            print(f'✗ SVG 与重新生成的结果不一致: {a.svg}')
            for i, (x, y) in enumerate(zip(old.splitlines(), svg.splitlines()), 1):
                if x != y:
                    print(f'  第一处差异在第 {i} 行:\n    old: {x[:160]}\n    new: {y[:160]}')
                    break
            bad = True
        else:
            print(f'✓ SVG 可复现（字节一致）: {a.svg}')
    else:
        with open(a.svg, 'w', encoding='utf-8', newline='\n') as f:
            f.write(svg)
        print(f'✓ SVG: {a.svg}')

    if a.md:
        new_md = inject_md(a.md, tables)
        if a.check:
            old = open(a.md, encoding='utf-8').read()
            if old != new_md:
                print(f'✗ md 生成表与 TSV 不一致: {a.md}')
                bad = True
            else:
                print(f'✓ md 生成表可复现: {a.md}')
        else:
            with open(a.md, 'w', encoding='utf-8', newline='\n') as f:
                f.write(new_md)
            print(f'✓ md 三张表已回填: {a.md}')

    for path, key in ((a.md_table, 'control-table'), (a.md_power, 'power-table'),
                      (a.md_devices, 'device-table')):
        if path:
            with open(path, 'w', encoding='utf-8', newline='\n') as f:
                f.write(tables[key])
            print(f'✓ 表格片段: {path}')

    print(f'  控制关系 {len(ctrl)} 条 / 电源路径 {len(power)} 条 / 器件 {len(dev)} 个')
    nodes = sum(1 for _ in re.finditer(r'data-node=', svg))
    edges = sum(1 for _ in re.finditer(r'data-hop=', svg))
    print(f'  SVG 节点 {nodes} 个 / 边 {edges} 条')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
