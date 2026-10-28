#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_schematic_report.py — 模式 B（仅原理图分析）交付物校验。

为什么单独一个检查器：
    模式 A 的 check_deliverable_consistency.py 查的是 md ↔ xlsx；模式 B 没有 xlsx，
    交付物是 md + SVG，外加三张过程 TSV。**md 里的表、SVG 里的图、TSV 里的数据
    必须来自同一份事实**，靠人眼对三份文件是查不出来的（实测同一块板两份产物
    各自"看着都对"，合起来打架）。这里用稳定主键 id 做三方逐行比对。

检查项:
    [S1] 文件名/配对/TSV 表头
    [S2] md 必备章节、范围声明、三个生成表标记
    [S3] control-map.tsv ↔ md 控制表 ↔ SVG manifest 三方一致
    [S3P] power-tree.tsv ↔ md 电源表 ↔ SVG manifest 三方一致
    [S4] SVG 合法性（XML/viewBox/禁用元素/字号/节点不重叠/边不悬空）
    [S5] 禁用词（工具字段、过程参数、skill 验证结论、模式 A 的 muxmode）
    [S6] 电源引用完整性（控制行的电源轨目标必须在 power-tree 里）
    [S7] 器件引用 / 待确认闭环（位号必须在 devices.tsv；待确认 Qnn 必须在 md 闭环）
    [S8] 可复现性（--check 时由生成器负责；本检查器只查 manifest 与文件一致）

用法:
    python3 check_schematic_report.py --md <板名>-原理图分析.md \\
        --svg <板名>-控制关系图.svg \\
        --map <板名>-过程记录/control-map.tsv \\
        --power <板名>-过程记录/power-tree.tsv \\
        --devices <板名>-过程记录/devices.tsv \\
        [--out report.txt] [--selftest]

退出码: 0 = S1~S7 全过；1 = 有失败

★ 自测：`--selftest` 会现场构造一份最小合法样本，再逐项注入错误，断言每个检查
  都能报出来 —— 对应 skill 的「检查器必须拿已知有错的样本回归」原则。
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
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

CONTROL_COLS = ['id', '分组', '状态', '控制源', '源类型', '方向', '控制器件', '器件型号',
                '器件引脚', '开关/通道', '目标', '目标类型', '关系', '有效状态/条件',
                '动作与结果', 'I2C地址', '中断', '原理图页', '确认状态', '备注']
POWER_COLS = ['id', '状态', '上游网络', '来源器件', '器件型号', '器件引脚', '通道/开关',
              '下游网络', '电压', '条件/使能', '动作与结果', '原理图页', '确认状态', '备注']
DEVICE_COLS = ['位号', '型号', '类别', '原理图页', '作用', '确认状态', '备注']
CONTROL_MD_COLS = ['id', '控制源', '源类型', '方向', '控制器件', '器件引脚', '开关/通道',
                   '目标', '有效状态/条件', '动作与结果', 'I2C地址', '中断',
                   '原理图页', '确认状态', '备注']
POWER_MD_COLS = ['id', '状态', '上游网络', '来源器件', '器件引脚', '通道/开关',
                 '下游网络', '电压', '条件/使能', '动作与结果', '原理图页', '确认状态']

# 框图样式的布局 / 分区 / 连线（--style block）
ZONE_COLS = ['id', '标题', 'x', 'y', '宽', '高', '颜色']
BLOCK_COLS = ['id', '区域', 'x', 'y', '宽', '高', '样式', '标题', '副标题',
              '端口', '位号', '页码', '备注']
EDGE_COLS = ['id', 'row', 'from', 'from_port', 'to', 'to_port', '类型', '标签',
             '颜色', '折点', '页码']
BLOCK_STYLES = {'connector', 'pd', 'charger', 'battery', 'switch', 'regulator',
                'protection', 'rail', 'control', 'signal', 'note',
                'core', 'peripheral', 'soc'}
EDGE_TYPES = {'power', 'control', 'i2c', 'int', 'status', 'analog', 'bus'}

MD_SECTIONS = ['概览', '快速结论', '电源树', '接口', '器件清单',
               '软件视角', '硬件视角', '上电', '待确认']
FORBIDDEN = ['判据A命中页', '判据B', 'sha256', '--conn-page', '--page-re', '--audit',
             '产物清单', 'Windows 通用性验证', '建议反馈到 Skill', 'muxmode']
REF_RE = re.compile(r'\b(?:U|Q|D|F|J|JP|SW|BT|CON|T|Y|X|FB)\d{1,3}[A-Z]?\b')
PENDING_RE = re.compile(r'待确认\s*[:：]?\s*(Q\d+)', re.I)


# ---------------------------------------------------------------------------
# 基础读取
# ---------------------------------------------------------------------------
def read_tsv(path, cols, label, key='id'):
    if not os.path.isfile(path):
        raise ValueError(f'找不到{label}: {path}')
    with open(path, encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f, delimiter='\t')
        header = reader.fieldnames or []
        if header != cols:
            raise ValueError(f'{label} 表头不符\n    实际: {header}\n    契约: {cols}')
        rows = []
        for i, r in enumerate(reader, 1):
            if not any((v or '').strip() for v in r.values()):
                continue
            r = {k: (v or '').strip() for k, v in r.items()}
            if key and not r.get(key):
                raise ValueError(f'{label} 第 {i} 行缺 {key}')
            rows.append(r)
    return rows


def split_md_row(line):
    line = line.strip()
    if line.startswith('|'):
        line = line[1:]
    if line.endswith('|'):
        line = line[:-1]
    cells, cur, esc = [], '', False
    for ch in line:
        if esc:
            cur += ch
            esc = False
        elif ch == '\\':
            cur += ch
            esc = True
        elif ch == '|':
            cells.append(cur.strip())
            cur = ''
        else:
            cur += ch
    cells.append(cur.strip())
    return [c.replace('\\|', '|') for c in cells]


def extract_table(md_text, key):
    pat = re.compile(r'<!--\s*BEGIN\s+' + re.escape(key) + r'[^>]*-->(.*?)'
                     r'<!--\s*END\s+' + re.escape(key) + r'\s*-->', re.S)
    m = pat.search(md_text)
    if not m:
        return None, f'md 缺少 {key} 的 BEGIN/END 标记'
    lines = [ln for ln in m.group(1).splitlines() if ln.strip().startswith('|')]
    if len(lines) < 2:
        return None, f'{key} 表格为空（至少要有表头 + 分隔行）'
    header = split_md_row(lines[0])
    rows = []
    for ln in lines[2:]:
        cells = split_md_row(ln)
        if len(cells) != len(header):
            return None, f'{key} 有一行列数不符: {ln[:80]}'
        rows.append(dict(zip(header, cells)))
    return rows, None


def compare_rows(expected, actual, cols, label, errs, key='id'):
    exp = {r[key]: r for r in expected}
    act = {r.get(key, ''): r for r in actual if r.get(key)}
    for i in sorted(set(exp) - set(act)):
        errs.append(f'{label}: 缺 id {i}')
    for i in sorted(set(act) - set(exp)):
        errs.append(f'{label}: 多出 id {i}')
    for i in sorted(set(exp) & set(act)):
        for c in cols:
            a = (exp[i].get(c, '') or '').strip()
            b = (act[i].get(c, '') or '').strip()
            if a != b:
                errs.append(f'{label}: {i} 的「{c}」不一致（TSV「{a}」 vs 表「{b}」）')


def est_units(s):
    return sum(1.0 if ord(ch) > 0x2E7F else 0.55 for ch in s)


# ---------------------------------------------------------------------------
# SVG
# ---------------------------------------------------------------------------
def local(tag):
    return tag.split('}')[-1]


def check_svg(svg_text):
    errs = []
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError as e:
        return [f'SVG XML 解析失败: {e}'], None, set(), {}

    if local(root.tag) != 'svg':
        errs.append(f'SVG 根元素不是 svg，而是 {local(root.tag)}')
    vb = root.attrib.get('viewBox', '')
    nums = []
    try:
        nums = [float(x) for x in re.split(r'[ ,]+', vb.strip()) if x]
    except ValueError:
        nums = []
    if len(nums) != 4 or any(not math.isfinite(x) for x in nums) or nums[2] <= 0 or nums[3] <= 0:
        errs.append(f'SVG viewBox 非法: {vb!r}')
    W, H = (nums[2], nums[3]) if len(nums) == 4 else (0, 0)

    for el in root.iter():
        t = local(el.tag)
        if t in ('script', 'foreignObject', 'image', 'iframe', 'object', 'embed'):
            errs.append(f'SVG 含禁用元素 <{t}>')
        for k, v in el.attrib.items():
            if local(k) == 'href' and not v.startswith('#'):
                errs.append(f'SVG 含外部引用 href={v!r}（只允许 #id）')

    ids = [el.attrib['id'] for el in root.iter() if 'id' in el.attrib]
    dup = sorted({x for x in ids if ids.count(x) > 1})
    if dup:
        errs.append(f'SVG id 重复: {dup}')

    rects = []
    for el in root.iter():
        if local(el.tag) == 'rect' and 'data-node' in el.attrib:
            try:
                x, y = float(el.attrib['x']), float(el.attrib['y'])
                w, h = float(el.attrib['width']), float(el.attrib['height'])
            except (KeyError, ValueError):
                errs.append(f'SVG 节点矩形缺/坏坐标: {el.attrib.get("id")}')
                continue
            if not all(math.isfinite(v) for v in (x, y, w, h)) or w <= 0 or h <= 0:
                errs.append(f'SVG 节点矩形尺寸非法: {el.attrib.get("id")}')
                continue
            rects.append((el.attrib.get('id', ''), x, y, w, h))

    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            _, x1, y1, w1, h1 = rects[i]
            _, x2, y2, w2, h2 = rects[j]
            if not (x1 + w1 <= x2 + 0.5 or x2 + w2 <= x1 + 0.5 or
                    y1 + h1 <= y2 + 0.5 or y2 + h2 <= y1 + 0.5):
                errs.append(f'SVG 节点重叠: {rects[i][0]} ↔ {rects[j][0]}')
                break

    node_ids = {r[0] for r in rects}
    node_rects = {r[0]: (r[1], r[2], r[3], r[4]) for r in rects}

    def seg_rect_cross(p, q, rect, margin=1.5):
        """线段是否穿过矩形内部（Liang-Barsky，留 margin 避免贴边误报）。"""
        x, y, w, h = rect
        x0, y0, x1, y1 = x + margin, y + margin, x + w - margin, y + h - margin
        dx, dy = q[0] - p[0], q[1] - p[1]
        t0, t1 = 0.0, 1.0
        for pp, qq in ((-dx, p[0] - x0), (dx, x1 - p[0]),
                       (-dy, p[1] - y0), (dy, y1 - p[1])):
            if abs(pp) < 1e-9:
                if qq < 0:
                    return False
            else:
                t = qq / pp
                if pp < 0:
                    t0 = max(t0, t)
                else:
                    t1 = min(t1, t)
                if t0 > t1:
                    return False
        return True

    # 文字不能越出所属功能块（标题/副标题/端口名贴边或压框）
    for el in root.iter():
        if local(el.tag) != 'text':
            continue
        txt = ''.join(el.itertext()).strip()
        if not txt:
            continue
        try:
            tx = float(el.attrib.get('x', '0'))
            ty = float(el.attrib.get('y', '0'))
            fs = float(el.attrib.get('font-size', '0'))
        except ValueError:
            continue
        br = None
        for rid, x, y, w, h in rects:
            # 只看“明显在块内部”的文字（离边 6px 以上）；贴边的端口名由端口规则管
            if x + 6 <= tx <= x + w - 6 and y + 6 <= ty <= y + h - 6:
                br = (rid, x, y, w, h)
                break
        if br is None:
            # 块外文字（端口名/线标签）不得压在任何功能块上
            tspans = [ch for ch in el if local(ch.tag) == 'tspan']
            lines = [''.join(ch.itertext()) for ch in tspans] or [txt]
            anchor = el.attrib.get('text-anchor', 'start')
            for i, line in enumerate(lines):
                line = ' '.join(line.split())
                if not line:
                    continue
                wp = est_units(line) * fs
                if anchor == 'middle':
                    left, right = tx - wp / 2, tx + wp / 2
                elif anchor == 'end':
                    left, right = tx - wp, tx
                else:
                    left, right = tx, tx + wp
                top = ty - fs + 15 * i
                bottom = top + fs + 2
                for rid, x, y, w, h in rects:
                    if (left < x + w - 2 and right > x + 2
                            and top < y + h - 2 and bottom > y + 2):
                        errs.append(f'SVG 块外文字压住功能块 {rid}: {line[:24]!r}')
                        break
            continue
        rid, x, y, w, h = br
        tspans = [ch for ch in el if local(ch.tag) == 'tspan']
        lines = [''.join(ch.itertext()) for ch in tspans] or [txt]
        anchor = el.attrib.get('text-anchor', 'start')
        for i, line in enumerate(lines):
            line = ' '.join(line.split())
            if not line:
                continue
            wp = est_units(line) * fs
            if anchor == 'middle':
                left, right = tx - wp / 2, tx + wp / 2
            elif anchor == 'end':
                left, right = tx - wp, tx
            else:
                left, right = tx, tx + wp
            top = ty - fs + 15 * i
            bottom = top + fs + 2
            if left < x + 2 or right > x + w - 2 or top < y + 2 or bottom > y + h - 2:
                errs.append(f'SVG 文字越出块 {rid}: {line[:24]!r}')
                break

    # 边不能穿过非端点的功能块、每段必须水平/垂直、不同边不能并行重叠
    edge_segments = []
    for el in root.iter():
        if local(el.tag) != 'g' or 'data-edge' not in el.attrib:
            continue
        eid = el.attrib.get('data-edge', '?')
        frm, to = el.attrib.get('data-from', ''), el.attrib.get('data-to', '')
        path = next((ch for ch in el if local(ch.tag) == 'path'), None)
        if path is None:
            continue
        d = path.attrib.get('d', '')
        pts = [(float(a), float(b)) for a, b in
               re.findall(r'(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)', d)]
        for i in range(len(pts) - 1):
            x1, y1 = pts[i]
            x2, y2 = pts[i + 1]
            edge_segments.append((eid, pts[i], pts[i + 1]))
            if abs(x1 - x2) > 0.5 and abs(y1 - y2) > 0.5:
                errs.append(f'SVG 边 {eid} 有非正交线段 '
                            f'({x1:.0f},{y1:.0f})→({x2:.0f},{y2:.0f})（应拉直或拐 90°）')
        for i in range(len(pts) - 1):
            for rid, x, y, w, h in rects:
                if rid in (frm, to):
                    continue
                if seg_rect_cross(pts[i], pts[i + 1], (x, y, w, h)):
                    errs.append(f'SVG 边 {eid} 穿过功能块 {rid}（应绕行）')
                    break

    seen_pairs = set()
    for i in range(len(edge_segments)):
        e1, p1, p2 = edge_segments[i]
        for j in range(i + 1, len(edge_segments)):
            e2, q1, q2 = edge_segments[j]
            if e1 == e2:
                continue
            key = tuple(sorted((e1, e2)))
            if key in seen_pairs:
                continue
            # 水平段同 y 且 x 区间重叠
            if (abs(p1[1] - p2[1]) < 0.5 and abs(q1[1] - q2[1]) < 0.5
                    and abs(p1[1] - q1[1]) < 0.5):
                lo = max(min(p1[0], p2[0]), min(q1[0], q2[0]))
                hi = min(max(p1[0], p2[0]), max(q1[0], q2[0]))
                if hi - lo > 5:
                    errs.append(f'SVG 边 {e1} 与 {e2} 有并行重叠段（应分道走线）')
                    seen_pairs.add(key)
            # 垂直段同 x 且 y 区间重叠
            if (abs(p1[0] - p2[0]) < 0.5 and abs(q1[0] - q2[0]) < 0.5
                    and abs(p1[0] - q1[0]) < 0.5):
                lo = max(min(p1[1], p2[1]), min(q1[1], q2[1]))
                hi = min(max(p1[1], p2[1]), max(q1[1], q2[1]))
                if hi - lo > 5:
                    errs.append(f'SVG 边 {e1} 与 {e2} 有并行重叠段（应分道走线）')
                    seen_pairs.add(key)

    for el in root.iter():
        if local(el.tag) == 'text':
            fs = el.attrib.get('font-size', '')
            try:
                f = float(fs)
            except ValueError:
                errs.append(f'SVG <text> 缺 font-size: {el.attrib.get("x")},{el.attrib.get("y")}')
                f = 0
            if 0 < f < 10:
                errs.append(f'SVG 字号 {f} < 10: {el.attrib.get("x")},{el.attrib.get("y")}')
            txt = ''.join(el.itertext()).strip()
            if not txt:
                errs.append('SVG 有空的 <text>')
            if len(nums) == 4 and f > 0 and txt:
                try:
                    x = float(el.attrib.get('x', '0'))
                    y = float(el.attrib.get('y', '0'))
                except ValueError:
                    x = y = -1
                anchor = el.attrib.get('text-anchor', 'start')
                # 按 tspan 的**每一行**量宽；把多行拼成一整串量会把正常换行误报成越界
                tspans = [ch for ch in el if local(ch.tag) == 'tspan']
                lines = ([''.join(ch.itertext()) for ch in tspans] if tspans else [txt])
                for line in lines:
                    line = ' '.join(line.split())
                    if not line:
                        continue
                    wpx = est_units(line) * f
                    left = x - wpx / 2 if anchor == 'middle' else x
                    right = x + wpx / 2 if anchor == 'middle' else x + wpx
                    if left < -0.5 or right > W + 0.5:
                        errs.append(f'SVG 文字越界: {line[:30]!r} @({x:.0f},{y:.0f})')
                        break
                if y - f < -0.5 or y > H + 0.5:
                    errs.append(f'SVG 文字垂直越界: {txt[:30]!r} @({x:.0f},{y:.0f})')

    for el in root.iter():
        if local(el.tag) == 'g' and 'data-row' in el.attrib:
            for k in ('data-from', 'data-to'):
                if k not in el.attrib:
                    errs.append(f'SVG 边缺 {k}: row={el.attrib.get("data-row")}')
                elif el.attrib[k] not in node_ids:
                    errs.append(f'SVG 边悬空: {el.attrib[k]} 不是任何 data-node 的 id')

    meta = None
    for el in root.iter():
        if local(el.tag) == 'metadata' and el.attrib.get('id') == 'control-map-manifest':
            meta = el
            break
    if meta is None:
        errs.append('SVG 缺 <metadata id="control-map-manifest">（三方校验的锚点）')
        manifest = None
    else:
        try:
            manifest = json.loads(meta.text or '{}')
        except json.JSONDecodeError as e:
            errs.append(f'SVG manifest JSON 解析失败: {e}')
            manifest = None
        else:
            if manifest.get('schema') != 1:
                errs.append(f'SVG manifest schema 不是 1: {manifest.get("schema")}')
            for k in ('control_rows', 'power_rows', 'device_rows', 'nodes', 'edges'):
                if k not in manifest:
                    errs.append(f'SVG manifest 缺 {k}')
    return errs, manifest, node_ids, node_rects


# ---------------------------------------------------------------------------
# 单项检查
# ---------------------------------------------------------------------------
def check_s1(md, svg, map_path, power_path, dev_path):
    errs = []
    mb, sb = os.path.basename(md), os.path.basename(svg)
    msuf, ssuf = '-原理图分析.md', '-控制关系图.svg'
    mstem = mb[:-len(msuf)] if mb.endswith(msuf) else ''
    sstem = sb[:-len(ssuf)] if sb.endswith(ssuf) else ''
    if not mstem:
        errs.append(f'md 文件名「{mb}」不符合 `<板名>{msuf}`')
    if not sstem:
        errs.append(f'svg 文件名「{sb}」不符合 `<板名>{ssuf}`')
    if mstem and sstem and mstem != sstem:
        errs.append(f'两份交付物板名不一致：「{mstem}」vs「{sstem}」')
    for path, cols, label in ((map_path, CONTROL_COLS, 'control-map.tsv'),
                              (power_path, POWER_COLS, 'power-tree.tsv'),
                              (dev_path, DEVICE_COLS, 'devices.tsv')):
        try:
            read_tsv(path, cols, label, key=None if label == 'devices.tsv' else 'id')
        except ValueError as e:
            errs.append(str(e))
    return errs


def check_s2(md_text):
    errs = []
    for kw in MD_SECTIONS:
        if kw not in md_text:
            errs.append(f'md 缺必备关键词/章节「{kw}」')
    for phrase in ('未使用核心板引脚表', '不做引脚复用'):
        if phrase not in md_text:
            errs.append(f'md 缺范围声明「{phrase}」（模式 B 必须写明不读引脚表、不做引脚复用）')
    for key in ('device-table', 'control-table', 'power-table'):
        b = len(re.findall(r'<!--\s*BEGIN\s+' + key + r'\b', md_text))
        e = len(re.findall(r'<!--\s*END\s+' + key + r'\s*-->', md_text))
        if b != 1 or e != 1:
            errs.append(f'md 的 {key} 标记应各出现 1 次（实际 BEGIN={b} END={e}）')
    if not re.search(r'[^\s]+\.pdf', md_text, re.I):
        errs.append('md 头部缺原理图 PDF 文件名')
    if not re.search(r'\d{4}-\d{2}-\d{2}', md_text):
        errs.append('md 头部缺分析日期（YYYY-MM-DD）')
    if not re.search(r'poppler|pypdfium2', md_text, re.I):
        errs.append('md 头部缺本次提取路线（poppler / pypdfium2）')
    return errs


def check_s5(md_text, svg_text):
    errs = []
    for bad in FORBIDDEN:
        if bad in md_text:
            errs.append(f'md 含禁用词「{bad}」（工具字段/过程参数/模式 A 概念，应移出交付物）')
        if bad in svg_text:
            errs.append(f'SVG 含禁用词「{bad}」')
    if re.search(r'(?<![A-Za-z0-9_])未判定(?![A-Za-z0-9_])', md_text):
        errs.append('md 出现裸「未判定」—— 必须写成「待确认 Qnn」并进待确认章节')
    return errs


def check_s3(ctrl, md_text, manifest):
    errs = []
    table, e = extract_table(md_text, 'control-table')
    if e:
        return [e]
    compare_rows(ctrl, table, CONTROL_MD_COLS, 'md 控制表', errs)
    if manifest is None:
        return errs + ['SVG manifest 不可用，无法做三方比对']
    mrows = manifest.get('control_rows') or []
    compare_rows(ctrl, mrows, CONTROL_COLS, 'SVG manifest 控制行', errs)
    # SVG 里每条 TSV 行都要有对应的边组
    ids_in_svg = {r.get('id') for r in mrows}
    for r in ctrl:
        if r['id'] not in ids_in_svg:
            errs.append(f'SVG manifest 缺控制行 {r["id"]}')
    return errs


def check_s3p(power, md_text, manifest):
    errs = []
    table, e = extract_table(md_text, 'power-table')
    if e:
        return [e]
    compare_rows(power, table, POWER_MD_COLS, 'md 电源表', errs)
    if manifest is None:
        return errs + ['SVG manifest 不可用，无法做三方比对']
    compare_rows(power, manifest.get('power_rows') or [], POWER_COLS,
                 'SVG manifest 电源行', errs)
    return errs


def check_s6(ctrl, power, manifest):
    errs = []
    nets = set()
    for r in power:
        for c in ('上游网络', '下游网络'):
            if r[c] and r[c] != '—':
                nets.add(r[c])
    for r in ctrl:
        if r['目标类型'] != '电源轨':
            continue
        for token in re.split(r'[/+、,，\s]+', r['目标']):
            if token and token not in nets:
                errs.append(f'控制行 {r["id"]} 的目标电源轨「{token}」不在 power-tree.tsv 里')
    if manifest is not None:
        mrows = manifest.get('power_rows') or []
        mset = {(x.get('上游网络'), x.get('下游网络')) for x in mrows}
        tset = {(x['上游网络'], x['下游网络']) for x in power}
        for x in sorted(tset - mset):
            errs.append(f'SVG manifest 缺电源路径 {x}')
    return errs


def check_s7(ctrl, power, dev, md_text, manifest):
    errs = []
    table, e = extract_table(md_text, 'device-table')
    if e:
        errs.append(e)
    else:
        compare_rows(dev, table, DEVICE_COLS, 'md 器件表', errs, key='位号')
    if manifest is not None:
        compare_rows(dev, manifest.get('device_rows') or [], DEVICE_COLS,
                     'SVG manifest 器件行', errs, key='位号')

    refs = {r['位号'] for r in dev}
    for label, rows in (('control-map', ctrl), ('power-tree', power)):
        for r in rows:
            for c in ('控制器件', '开关/通道', '来源器件'):
                v = r.get(c, '')
                if not v or v in ('—', '直连'):
                    continue
                for m in REF_RE.findall(v):
                    if m not in refs:
                        errs.append(f'{label} {r["id"]} 的「{c}」引用了未收录位号 {m}')
    # 待确认闭环
    pending = []
    for label, rows in (('control-map', ctrl), ('power-tree', power), ('devices', dev)):
        for r in rows:
            st = r.get('确认状态', '')
            if '待确认' in st:
                m = PENDING_RE.search(st)
                rid = r.get('id') or r.get('位号') or '?'
                if not m:
                    errs.append(f'{label} {rid} 的状态「{st}」缺 Q 编号（应写「待确认 Q3」）')
                else:
                    pending.append((m.group(1).upper(), label, rid))
    # 取 md 的「待确认问题」章节
    sec = ''
    m = re.search(r'^#+\s*[^\n]*待确认问题[^\n]*$', md_text, re.M)
    if m:
        rest = md_text[m.end():]
        nxt = re.search(r'^#+\s', rest, re.M)
        sec = rest[:nxt.start()] if nxt else rest
    for qid, label, rid in pending:
        if qid not in sec:
            errs.append(f'{label} {rid} 的 {qid} 没在 md 的「待确认问题」章节闭环')
    return errs


# ---------------------------------------------------------------------------
# 框图布局检查（--style block）
# ---------------------------------------------------------------------------
def parse_ports_check(s):
    ports = {}
    for item in (s or '').split(';'):
        item = item.strip()
        if not item:
            continue
        parts = item.split(':')
        if len(parts) != 3:
            raise ValueError(f'端口格式应为 name:side:offset，实际 {item!r}')
        name, side, off = parts
        side = side.strip().upper()
        if side not in ('L', 'R', 'T', 'B'):
            raise ValueError(f'端口 {name!r} 的 side 必须是 L/R/T/B，实际 {side!r}')
        off = off.strip()
        if off.endswith('%'):
            pct = float(off[:-1])
            if not 0 <= pct <= 100:
                raise ValueError(f'端口 {name!r} 的百分比偏移必须在 0~100: {off!r}')
            ports[name.strip()] = (side, ('%', pct))
        else:
            ports[name.strip()] = (side, float(off))
    return ports


def check_s8(layout_path, zones_path, edges_path, ctrl, power, dev, manifest, node_ids,
             node_rects=None):
    errs = []
    node_rects = node_rects or {}
    try:
        zones = read_tsv(zones_path, ZONE_COLS, 'zones.tsv')
        blocks = read_tsv(layout_path, BLOCK_COLS, 'layout.tsv')
        edges = read_tsv(edges_path, EDGE_COLS, 'edges.tsv')
    except ValueError as e:
        return [str(e)]

    zids, zmap = set(), {}
    for z in zones:
        if z['id'] in zids:
            errs.append(f'zones.tsv id 重复: {z["id"]}')
        zids.add(z['id'])
        zmap[z['id']] = z
        try:
            vals = [float(z[k]) for k in ('x', 'y', '宽', '高')]
            if vals[2] <= 0 or vals[3] <= 0:
                errs.append(f'zone {z["id"]} 宽/高必须 > 0')
        except ValueError:
            errs.append(f'zone {z["id"]} 有非数字坐标')

    bmap = {}
    for b in blocks:
        if b['id'] in bmap:
            errs.append(f'layout.tsv block id 重复: {b["id"]}')
        bmap[b['id']] = b
        if b['样式'] not in BLOCK_STYLES:
            errs.append(f'block {b["id"]} 样式 {b["样式"]!r} 不在允许列表')
        if b['区域'] and b['区域'] not in zids:
            errs.append(f'block {b["id"]} 的区域 {b["区域"]!r} 不在 zones.tsv')
        w = h = None
        auto_w = b['宽'].strip().lower() == 'auto'
        auto_h = b['高'].strip().lower() == 'auto'
        if auto_w or auto_h:
            if b['样式'] not in ('rail', 'soc'):
                errs.append(f'block {b["id"]}: 只有 rail/soc 样式支持 宽/高=auto')
            if auto_w and auto_h and f'blk-{b["id"]}' not in node_rects:
                errs.append(f'block {b["id"]}: auto 尺寸必须用 control_map_svg.py 生成 SVG')
            if not b['标题'].strip():
                errs.append(f'block {b["id"]}: auto 尺寸需要标题来计算宽度')
        if not auto_w:
            try:
                w = float(b['宽'])
                if w <= 0:
                    errs.append(f'block {b["id"]} 宽必须 > 0')
            except ValueError:
                errs.append(f'block {b["id"]} 有非数字宽')
        if not auto_h:
            try:
                h = float(b['高'])
                if h <= 0:
                    errs.append(f'block {b["id"]} 高必须 > 0')
            except ValueError:
                errs.append(f'block {b["id"]} 有非数字高')
        try:
            b['_ports'] = parse_ports_check(b['端口'])
        except ValueError as e:
            errs.append(f'block {b["id"]}: {e}')
            b['_ports'] = {}
        if node_ids and f'blk-{b["id"]}' not in node_ids:
            errs.append(f'block {b["id"]} 在 SVG 里找不到 id="blk-{b["id"]}" 的块')
        rr = node_rects.get(f'blk-{b["id"]}')
        if rr:
            b['_rect'] = rr
        elif w is not None and h is not None:
            try:
                b['_rect'] = (float(b['x']), float(b['y']), w, h)
            except ValueError:
                errs.append(f'block {b["id"]} 有非数字坐标')
                b['_rect'] = None
        else:
            b['_rect'] = None
        for name, (side, off) in b['_ports'].items():
            if isinstance(off, tuple) or b['_rect'] is None:
                continue
            limit = b['_rect'][2] if side in ('T', 'B') else b['_rect'][3]
            if off < 0 or off > limit + 0.5:
                errs.append(f'block {b["id"]} 端口 {name} 偏移 {off} 超出块边长 {limit:.0f}'
                            f'（side={side}）')
        if b['样式'] == 'rail' and b['_rect'] is not None:
            bw, bh = b['_rect'][2], b['_rect'][3]
            page_pad = 62 if b['页码'].strip() not in ('', '—', '-') else 42
            max_w = max(110.0, est_units(b['标题'] or '') * 13 + page_pad + 8)
            if bw > max_w + 0.5 or bh > 40.5:
                errs.append(f'block {b["id"]}: rail 网络标签块 {bw:.0f}×{bh:.0f} 偏大'
                            f'（宽≤{max_w:.0f}，高≤40）；请用 宽=auto 高=auto')
        if b['区域'] in zmap and b['_rect'] is not None:
            z = zmap[b['区域']]
            try:
                zx, zy, zw, zh = (float(z['x']), float(z['y']),
                                  float(z['宽']), float(z['高']))
            except ValueError:
                continue
            bx, by, bw, bh = b['_rect']
            over = []
            if bx < zx - 0.5:
                over.append(f'左 {zx - bx:.0f}px')
            if by < zy - 0.5:
                over.append(f'上 {zy - by:.0f}px')
            if bx + bw > zx + zw + 0.5:
                over.append(f'右 {bx + bw - (zx + zw):.0f}px')
            if by + bh > zy + zh + 0.5:
                over.append(f'下 {by + bh - (zy + zh):.0f}px')
            if over:
                errs.append(f'block {b["id"]} 越出分区 {b["区域"]}（{", ".join(over)}）')

    for z in zmap.values():
        if not any(b['区域'] == z['id'] for b in blocks):
            errs.append(f'zone {z["id"]} 没有任何块；删掉该分区或把块移进去')

    def port_xy(block, name):
        if name not in block.get('_ports', {}) or block.get('_rect') is None:
            return None
        side, off = block['_ports'][name]
        x, y, w, h = block['_rect']
        if isinstance(off, tuple):
            off = (w if side in ('T', 'B') else h) * off[1] / 100.0
        if side == 'L':
            return (x, y + off)
        if side == 'R':
            return (x + w, y + off)
        if side == 'T':
            return (x + off, y)
        return (x + off, y + h)

    ed_ids = set()
    rows_used = {r['id'] for r in (ctrl or []) + (power or [])}
    edge_rows = set()
    for e in edges:
        if e['id'] in ed_ids:
            errs.append(f'edges.tsv id 重复: {e["id"]}')
        ed_ids.add(e['id'])
        if e['类型'] not in EDGE_TYPES:
            errs.append(f'edge {e["id"]} 类型 {e["类型"]!r} 不在允许列表')
        if e['from'] not in bmap:
            errs.append(f'edge {e["id"]} 的 from 块不存在: {e["from"]}')
        elif e['from_port'] not in bmap[e['from']].get('_ports', {}):
            errs.append(f'edge {e["id"]} 的 from_port {e["from_port"]!r} 不在块 {e["from"]}')
        if e['to'] not in bmap:
            errs.append(f'edge {e["id"]} 的 to 块不存在: {e["to"]}')
        elif e['to_port'] not in bmap[e['to']].get('_ports', {}):
            errs.append(f'edge {e["id"]} 的 to_port {e["to_port"]!r} 不在块 {e["to"]}')
        if e['from'] in bmap and e['to'] in bmap:
            p1 = port_xy(bmap[e['from']], e['from_port'])
            p2 = port_xy(bmap[e['to']], e['to_port'])
            if p1 and p2:
                dist = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
                if dist < 20:
                    errs.append(f'edge {e["id"]}: 端口间距 {dist:.0f}px < 20px'
                                f'（箭头太短），请拉开块间距')
        edge_rows.add(e['row'])
        if e['row'] not in rows_used and e['row'] not in ('—', '-', ''):
            errs.append(f'edge {e["id"]} 的 row {e["row"]!r} 不是 control/power 的 id')
    for rid in sorted(rows_used - edge_rows):
        errs.append(f'TSV 行 {rid} 没有对应的框图连线（edges.tsv）')

    refs = {r['位号'] for r in (dev or [])}
    for b in blocks:
        for ref in re.split(r'[,，、/]+', b['位号'] or ''):
            ref = ref.strip()
            if ref and ref not in ('—', '-') and ref not in refs:
                errs.append(f'block {b["id"]} 的位号 {ref} 不在 devices.tsv')

    if manifest is None:
        errs.append('SVG manifest 不可用，无法核对 layout/edges')
    else:
        if manifest.get('style') != 'block':
            errs.append(f'SVG manifest style 不是 block: {manifest.get("style")!r}')
        mblocks = {x.get('id') for x in (manifest.get('layout_rows') or [])}
        medges = {x.get('id') for x in (manifest.get('edge_rows') or [])}
        if mblocks != set(bmap):
            errs.append(f'SVG manifest layout_rows 与 layout.tsv 不一致: {sorted(mblocks ^ set(bmap))}')
        if medges != ed_ids:
            errs.append(f'SVG manifest edge_rows 与 edges.tsv 不一致: {sorted(medges ^ ed_ids)}')
    return errs


# ---------------------------------------------------------------------------
# 主检查
# ---------------------------------------------------------------------------
def run_checks(md, svg, map_path, power_path, dev_path,
               layout_path=None, zones_path=None, edges_path=None):
    failures = {k: [] for k in ('S1', 'S2', 'S3', 'S3P', 'S4', 'S5', 'S6', 'S7', 'S8')}
    failures['S1'] += check_s1(md, svg, map_path, power_path, dev_path)

    try:
        ctrl = read_tsv(map_path, CONTROL_COLS, 'control-map.tsv')
    except ValueError as e:
        ctrl = None
        failures['S1'].append(str(e))
    try:
        power = read_tsv(power_path, POWER_COLS, 'power-tree.tsv')
    except ValueError as e:
        power = None
        failures['S1'].append(str(e))
    try:
        dev = read_tsv(dev_path, DEVICE_COLS, 'devices.tsv', key=None)
    except ValueError as e:
        dev = None
        failures['S1'].append(str(e))

    md_text = open(md, encoding='utf-8', errors='replace').read() if os.path.isfile(md) else ''
    svg_text = open(svg, encoding='utf-8', errors='replace').read() if os.path.isfile(svg) else ''
    if not md_text:
        failures['S1'].append(f'找不到/空文件: {md}')
    if not svg_text:
        failures['S1'].append(f'找不到/空文件: {svg}')

    failures['S2'] += check_s2(md_text)
    svg_errs, manifest, node_ids, node_rects = (
        check_svg(svg_text) if svg_text else ([], None, set(), {}))
    failures['S4'] += svg_errs
    failures['S5'] += check_s5(md_text, svg_text)

    if ctrl is not None:
        failures['S3'] += check_s3(ctrl, md_text, manifest)
    if power is not None:
        failures['S3P'] += check_s3p(power, md_text, manifest)
    if ctrl is not None and power is not None:
        failures['S6'] += check_s6(ctrl, power, manifest)
    if ctrl is not None and power is not None and dev is not None:
        failures['S7'] += check_s7(ctrl, power, dev, md_text, manifest)
    if layout_path and zones_path and edges_path:
        if ctrl is not None and power is not None and dev is not None:
            failures['S8'] += check_s8(layout_path, zones_path, edges_path,
                                        ctrl, power, dev, manifest, node_ids,
                                        node_rects)
    elif manifest and manifest.get('style') == 'block':
        failures['S8'].append('SVG 是 block 样式，但检查时没给 --layout/--zones/--edges')
    return failures


def report(failures, md, svg):
    lines = [f'# 模式 B 交付物校验: {os.path.basename(md)} ↔ {os.path.basename(svg)}', '']
    titles = {
        'S1': '文件名/配对/TSV 表头',
        'S2': 'md 章节/范围声明/生成表标记',
        'S3': 'control-map ↔ md 控制表 ↔ SVG manifest',
        'S3P': 'power-tree ↔ md 电源表 ↔ SVG manifest',
        'S4': 'SVG 合法性/几何',
        'S5': '禁用词/裸未判定',
        'S6': '电源引用完整性',
        'S7': '器件引用/待确认闭环',
        'S8': '框图布局/连线完整性',
    }
    total = 0
    for k in ('S1', 'S2', 'S3', 'S3P', 'S4', 'S5', 'S6', 'S7', 'S8'):
        v = failures.get(k, [])
        total += len(v)
        mark = '✓' if not v else '✗'
        lines.append(f'[{k}] {titles[k]}: {len(v)} 处 {mark}')
        for x in v:
            lines.append(f'    ✗ {x}')
    lines.append('')
    lines.append('✅ 全部通过。' if total == 0 else f'❌ 共 {total} 处不符。')
    return '\n'.join(lines), total


# ---------------------------------------------------------------------------
# 自测：最小样本 + 逐项注入错误
# ---------------------------------------------------------------------------
SAMPLE_TSV = {
    'devices.tsv': '位号\t型号\t类别\t原理图页\t作用\t确认状态\t备注\n'
                   'U0\tPART0\t电源\t1\t上游电源\t已看图\t\n'
                   'U1\tPART1\t控制器\t1\t测试控制器\t已看图\t\n'
                   'SW1\tPART_SW\t开关\t1\t测试开关\t已看图\t\n',
    'control-map.tsv': 'id\t分组\t状态\t控制源\t源类型\t方向\t控制器件\t器件型号\t器件引脚\t'
                       '开关/通道\t目标\t目标类型\t关系\t有效状态/条件\t动作与结果\tI2C地址\t'
                       '中断\t原理图页\t确认状态\t备注\n'
                       'C01\tG1\tON\tSIG_A\tGPIO\tSOC_OUT\tU1\tPART1\tG(1)\tSW1\tRAIL_A\t'
                       '电源轨\t控制\t高有效\tSIG_A=1 时 RAIL_0→RAIL_A\t—\t—\t1\t已看图\t测试\n',
    'power-tree.tsv': 'id\t状态\t上游网络\t来源器件\t器件型号\t器件引脚\t通道/开关\t下游网络\t'
                      '电压\t条件/使能\t动作与结果\t原理图页\t确认状态\t备注\n'
                      'P01\t常开\tRAIL_0\tU0\tPART0\t—\t—\tRAIL_A\t5V\t—\tRAIL_0→RAIL_A\t'
                      '1\t已看图\t测试\n',
}

SAMPLE_MD = '''# T 原理图分析

- **原理图**：`T.pdf`（2 页，Altium，2026-10-22）
- **提取路线**：poppler（pdftotext -layout）
- **分析日期**：2026-10-22
- **范围**：本分析未使用核心板引脚表，不做引脚复用。

## 1. 概览
测试板。

## 2. 快速结论
SIG_A 控制 RAIL_A。

## 3. 系统框图与电源树
RAIL_0 → RAIL_A。

## 4. 接口与连接器
J1。

## 5. 器件清单
<!-- BEGIN device-table (generated from devices.tsv; do not edit) -->
<!-- END device-table -->

## 6. 控制接口（软件视角）
<!-- BEGIN control-table (generated from control-map.tsv; do not edit) -->
<!-- END control-table -->

## 7. 电源路径与开关（硬件视角）
<!-- BEGIN power-table (generated from power-tree.tsv; do not edit) -->
<!-- END power-table -->

## 8. 上电/复位/默认状态
默认关断。

## 9. 保护与检测
无。

## 10. 软件注意事项
无。

## 11. 待确认问题
| 序号 | 问题 | 影响 |
|---|---|---|
| Q1 | 示例问题 | 示例影响 |
'''


def selftest():
    d = tempfile.mkdtemp(prefix='schmode_b_')
    gen = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'control_map_svg.py')
    try:
        for name, txt in SAMPLE_TSV.items():
            open(os.path.join(d, name), 'w', encoding='utf-8').write(txt)
        md = os.path.join(d, 'T-原理图分析.md')
        svg = os.path.join(d, 'T-控制关系图.svg')
        open(md, 'w', encoding='utf-8').write(SAMPLE_MD)
        cmd = [sys.executable, '-I', gen,
               '--map', os.path.join(d, 'control-map.tsv'),
               '--power', os.path.join(d, 'power-tree.tsv'),
               '--devices', os.path.join(d, 'devices.tsv'),
               '--svg', svg, '--md', md, '--title', 'T 控制关系图']
        r = subprocess.run(cmd, text=True, encoding='utf-8', errors='replace',
                           capture_output=True)
        if r.returncode != 0:
            print('✗ 自测样本生成失败:\n' + r.stdout + r.stderr)
            return 1

        def run(md_p=md, svg_p=svg):
            return run_checks(md_p, svg_p, os.path.join(d, 'control-map.tsv'),
                              os.path.join(d, 'power-tree.tsv'),
                              os.path.join(d, 'devices.tsv'))

        base = run()
        ok = True

        def expect(name, key, failures):
            nonlocal ok
            good = bool(failures.get(key))
            print(f'  {"✓" if good else "✗"} {name}: {key} 报出 {len(failures.get(key, []))} 处')
            if not good:
                ok = False

        # 0) 合法样本必须 0 处
        base_total = sum(len(v) for v in base.values())
        print(f'  {"✓" if base_total == 0 else "✗"} 合法样本: {base_total} 处')
        if base_total:
            ok = False
            print(report(base, md, svg)[0])

        # 1) S1 文件名
        bad = os.path.join(d, 'wrong.md')
        shutil.copyfile(md, bad)
        expect('文件名不带板名', 'S1', run(md_p=bad))

        # 2) S2 删章节
        p = os.path.join(d, 'T-原理图分析.md')
        orig = open(p, encoding='utf-8').read()
        open(p, 'w', encoding='utf-8').write(orig.replace('## 2. 快速结论', '## 2. 结论'))
        expect('删「快速结论」', 'S2', run())
        open(p, 'w', encoding='utf-8').write(orig)

        # 3) S3 改 md 控制表的目标
        txt = open(p, encoding='utf-8').read().replace('| RAIL_A | 高有效 |', '| RAIL_X | 高有效 |')
        open(p, 'w', encoding='utf-8').write(txt)
        expect('改 md 控制表目标', 'S3', run())
        open(p, 'w', encoding='utf-8').write(orig)

        # 4) S4 插 <script>
        svg_orig = open(svg, encoding='utf-8').read()
        open(svg, 'w', encoding='utf-8').write(svg_orig.replace('</svg>', '<script>x</script></svg>'))
        expect('SVG 插 script', 'S4', run())
        open(svg, 'w', encoding='utf-8').write(svg_orig)

        # 5) S5 禁用词
        open(p, 'w', encoding='utf-8').write(orig + '\nsha256: deadbeef\n')
        expect('md 加 sha256', 'S5', run())
        open(p, 'w', encoding='utf-8').write(orig)

        # 6) S6 改 md 电源表下游
        txt = open(p, encoding='utf-8').read().replace('| RAIL_A | 5V |', '| RAIL_X | 5V |')
        open(p, 'w', encoding='utf-8').write(txt)
        expect('改 md 电源表下游', 'S3P', run())
        open(p, 'w', encoding='utf-8').write(orig)

        # 7) S7 改 md 器件型号
        txt = open(p, encoding='utf-8').read().replace('| PART1 |', '| PART_X |')
        open(p, 'w', encoding='utf-8').write(txt)
        expect('改 md 器件型号', 'S7', run())
        open(p, 'w', encoding='utf-8').write(orig)

        # 8) S7 待确认没闭环：追加一行并重新生成
        cfile = os.path.join(d, 'control-map.tsv')
        orig_c = open(cfile, encoding='utf-8').read()
        open(cfile, 'w', encoding='utf-8').write(
            orig_c + 'C99\tG2\t—\tSIG_B\tGPIO\tSOC_OUT\tU1\tPART1\tG(2)\t—\tSIG_C\t信号\t控制\t'
                     '高有效\t透传\t—\t—\t1\t待确认 Q99\t测试\n')
        r = subprocess.run(cmd, text=True, encoding='utf-8', errors='replace', capture_output=True)
        expect('待确认 Q99 未闭环', 'S7', run())
        open(cfile, 'w', encoding='utf-8').write(orig_c)
        subprocess.run(cmd, text=True, encoding='utf-8', errors='replace', capture_output=True)

        # 9) S8 框图布局/连线
        zpath = os.path.join(d, 'zones.tsv')
        lpath = os.path.join(d, 'layout.tsv')
        epath = os.path.join(d, 'edges.tsv')
        open(zpath, 'w', encoding='utf-8').write(
            'id\t标题\tx\ty\t宽\t高\t颜色\nZ\t测试区\t20\t20\t400\t200\t#E3F2FD\n')
        open(lpath, 'w', encoding='utf-8').write(
            'id\t区域\tx\ty\t宽\t高\t样式\t标题\t副标题\t端口\t位号\t页码\t备注\n'
            'A\tZ\t40\t40\t100\t60\tsignal\tA\t\tOUT:R:30\t—\t1\t\n'
            'B\tZ\t260\t40\t100\t60\tsignal\tB\t\tIN:L:30\t—\t1\t\n')
        open(epath, 'w', encoding='utf-8').write(
            'id\trow\tfrom\tfrom_port\tto\tto_port\t类型\t标签\t颜色\t折点\t页码\n'
            'X01\tC01\tA\tOUT\tB\tIN\tcontrol\tX\t\t\t1\n'
            'X02\tP01\tA\tOUT\tB\tIN\tpower\tP\t\t\t1\n')
        ctrl8 = read_tsv(os.path.join(d, 'control-map.tsv'), CONTROL_COLS, 'control-map.tsv')
        power8 = read_tsv(os.path.join(d, 'power-tree.tsv'), POWER_COLS, 'power-tree.tsv')
        dev8 = read_tsv(os.path.join(d, 'devices.tsv'), DEVICE_COLS, 'devices.tsv', key=None)
        man8 = {'style': 'block', 'layout_rows': [{'id': 'A'}, {'id': 'B'}],
                'edge_rows': [{'id': 'X01'}, {'id': 'X02'}]}
        errs8 = check_s8(lpath, zpath, epath, ctrl8, power8, dev8, man8,
                         {'blk-A', 'blk-B'})
        print(f'  {"✓" if not errs8 else "✗"} S8 合法框图样本: {len(errs8)} 处')
        if errs8:
            ok = False
            print('\n'.join('    ✗ ' + e for e in errs8))
        open(epath, 'w', encoding='utf-8').write(
            'id\trow\tfrom\tfrom_port\tto\tto_port\t类型\t标签\t颜色\t折点\t页码\n'
            'X01\tC01\tA\tOUT\tC\tIN\tcontrol\tX\t\t\t1\n'
            'X02\tP01\tA\tOUT\tB\tIN\tpower\tP\t\t\t1\n')
        errs8 = check_s8(lpath, zpath, epath, ctrl8, power8, dev8, man8,
                         {'blk-A', 'blk-B'})
        good8 = any('不存在' in e for e in errs8)
        print(f'  {"✓" if good8 else "✗"} S8 边指向不存在的块: 报出 {len(errs8)} 处')
        if not good8:
            ok = False

        print('\n✅ 自测全部通过。' if ok else '\n❌ 自测有失败。')
        return 0 if ok else 1
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description='模式 B 交付物校验')
    ap.add_argument('--md', help='<板名>-原理图分析.md')
    ap.add_argument('--svg', help='<板名>-控制关系图.svg')
    ap.add_argument('--map', help='control-map.tsv')
    ap.add_argument('--power', help='power-tree.tsv')
    ap.add_argument('--devices', help='devices.tsv')
    ap.add_argument('--layout', help='block 样式：layout.tsv（可选；给了才查 [S8]）')
    ap.add_argument('--zones', help='block 样式：zones.tsv')
    ap.add_argument('--edges', help='block 样式：edges.tsv')
    ap.add_argument('--out', help='报告另存到文件')
    ap.add_argument('--selftest', action='store_true', help='构造样本并逐项注入错误自测')
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if not all((a.md, a.svg, a.map, a.power, a.devices)):
        ap.error('需要 --md --svg --map --power --devices（或用 --selftest）')

    failures = run_checks(a.md, a.svg, a.map, a.power, a.devices,
                          a.layout, a.zones, a.edges)
    text, total = report(failures, a.md, a.svg)
    print(text)
    if a.out:
        open(a.out, 'w', encoding='utf-8').write(text + '\n')
    return 1 if total else 0


if __name__ == '__main__':
    sys.exit(main())
