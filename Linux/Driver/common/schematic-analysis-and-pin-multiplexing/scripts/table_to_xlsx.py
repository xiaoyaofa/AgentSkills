#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""table_to_xlsx.py — 把 TSV 表格拼成多 sheet 的 xlsx（免装 Python 库）。

走"生成 .fods（扁平 ODS，就是个 XML）→ LibreOffice headless 转 xlsx"这条路：
不需要 openpyxl/xlsxwriter，不用联网装包，且表头加粗、列宽、边框都能带上。
.sheet 数据来源通常就是本工具链前面几步产出的 TSV。

用法:
    table_to_xlsx.py 输出.xlsx \\
        --sheet "0-说明:notes.tsv:2.6,11" \\
        --sheet "1-已用信号:used.tsv:2,3.2,2.2,1.6,2.8,4.2,1.2,1.6,6,6" \\
        --sheet "2-未用信号:unused.tsv:2.2,4,1.6,2.8,2,1.2,7"

    --sheet 语法:  "表名:TSV文件[:列宽cm,用逗号分隔]"
                  路径可含 Windows 盘符（D:/work/x.tsv），解析见 parse_sheet_spec()

没有 LibreOffice 时的替代路径：
    · 只要能出 .fods 再手工用 LibreOffice/WPS 打开另存；
    · 或用 ssconvert（gnumeric）：ssconvert in.tsv out.xlsx（单 sheet）。

★ 为什么表格用 xlsx、说明用 markdown：
    引脚/网络/ball/GPIO 这种 100+ 行 x 10 列的对照关系，xlsx 能筛选、排序、加批注，
    且和厂商给的引脚表格式一致，必要时可直接发客户；
    框图、电源树、结论、待确认项属于叙述内容，markdown 更好维护。
"""

import argparse
import os
import re
import shutil
import subprocess
import sys

ESC = (('&', '&amp;'), ('<', '&lt;'), ('>', '&gt;'), ('"', '&quot;'))


def esc(s):
    for a, b in ESC:
        s = s.replace(a, b)
    return s


WIDTHS_RX = re.compile(r'^[\d.]+(?:,[\d.]+)*$')


def parse_sheet_spec(spec):
    """解析 --sheet 的 "表名:路径[:列宽]"。

    ★ 不能用朴素的 spec.split(':')：Windows 路径带盘符（D:/work/x.tsv）会被切碎，
      取到 'D' 当路径 → 报"找不到 D"。规则：
        第一个 ':' 之前是表名；
        末尾那段**只有**形如 "2,3.2" 才当列宽（且要求候选路径存在，或该段含 ',' / '.'）；
        剩下整段都是路径（含盘符）。

    返回 (name, path, widths)。
    """
    if ':' not in spec:
        sys.exit(f'--sheet 语法错误（应为 "表名:TSV文件[:列宽]"）: {spec}')
    name, rest = spec.split(':', 1)
    widths = []
    if ':' in rest:
        cand_path, cand_w = rest.rsplit(':', 1)
        looks_like_path = ('/' in cand_path or '\\' in cand_path or '.' in cand_path)
        if WIDTHS_RX.match(cand_w) and (os.path.isfile(cand_path) or ',' in cand_w
                                        or '.' in cand_w or looks_like_path):
            rest, widths = cand_path, [float(x) for x in cand_w.split(',')]
    if not name:
        sys.exit(f'--sheet 表名为空: {spec}')
    return name, rest, widths


def read_tsv(path):
    rows = []
    with open(path, encoding='utf-8', errors='replace') as f:
        for line in f:
            rows.append(line.rstrip('\n').split('\t'))
    return rows


def build_fods(sheets):
    """sheets: [(name, rows, widths)]"""
    styles = [
        '<style:style style:name="ceH" style:family="table-cell">'
        '<style:table-cell-properties fo:background-color="#d9e2f3" '
        'fo:border="0.5pt solid #999999"/>'
        '<style:text-properties fo:font-weight="bold"/></style:style>',
        '<style:style style:name="ceB" style:family="table-cell">'
        '<style:table-cell-properties fo:border="0.5pt solid #cccccc"/>'
        '</style:style>',
    ]
    maxw = 1
    for _, _, w in sheets:
        for i, x in enumerate(w):
            styles.append(
                f'<style:style style:name="co{i}" style:family="table-column">'
                f'<style:table-column-properties style:column-width="{x}cm"/>'
                f'</style:style>')
        maxw = max(maxw, len(w))

    body = []
    for name, rows, widths in sheets:
        body.append(f'<table:table table:name="{esc(name)}">')
        n = max((len(r) for r in rows), default=0) or max(len(widths), 1)
        for i in range(n):
            if i < len(widths):
                body.append(f'<table:table-column table:style-name="co{i}"/>')
            else:
                body.append('<table:table-column/>')
        for ri, r in enumerate(rows):
            body.append('<table:table-row>')
            for v in r:
                st = ' table:style-name="ceH"' if ri == 0 else ''
                body.append(
                    f'<table:table-cell office:value-type="string"{st}>'
                    f'<text:p>{esc(str(v))}</text:p></table:table-cell>')
            body.append('</table:table-row>')
        body.append('</table:table>')

    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<office:document '
            'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
            'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
            'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
            'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
            'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" '
            'office:version="1.2" '
            'office:mimetype="application/vnd.oasis.opendocument.spreadsheet">'
            f'<office:automatic-styles>{"".join(styles)}</office:automatic-styles>'
            f'<office:body><office:spreadsheet>{"".join(body)}'
            '</office:spreadsheet></office:body></office:document>\n')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('out')
    ap.add_argument('--sheet', action='append', required=True,
                    metavar='NAME:FILE[:W1,W2,...]')
    a = ap.parse_args()

    sheets = []
    for spec in a.sheet:
        name, path, widths = parse_sheet_spec(spec)
        if not os.path.isfile(path):
            sys.exit(f'找不到文件 {path}（--sheet 给的是 "{spec}"）')
        sheets.append((name, read_tsv(path), widths))

    fods = os.path.splitext(a.out)[0] + '.fods'
    open(fods, 'w', encoding='utf-8').write(build_fods(sheets))
    print(f'FODS: {fods}  ({len(sheets)} sheet)')
    for n, r, _ in sheets:
        print(f'   {n}: {max(len(r) - 1, 0)} 行数据')

    soffice = shutil.which('soffice') or shutil.which('libreoffice')
    if not soffice:
        sys.exit('没找到 LibreOffice/soffice —— 请用 LibreOffice/WPS 打开上面的 .fods 另存为 xlsx')

    outdir = os.path.dirname(os.path.abspath(a.out)) or '.'
    prof = 'file:///tmp/lo_profile_pinmux'
    subprocess.run([soffice, '--headless', f'-env:UserInstallation={prof}',
                    '--convert-to', 'xlsx:Calc MS Excel 2007 XML',
                    '--outdir', outdir, fods],
                   check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    made = os.path.join(outdir, os.path.splitext(os.path.basename(fods))[0] + '.xlsx')
    if not os.path.exists(made):
        sys.exit('转换失败：请用 LibreOffice 手工打开 .fods 另存为 xlsx')
    if os.path.abspath(made) != os.path.abspath(a.out):
        shutil.move(made, a.out)
    print(f'XLSX: {a.out}')


if __name__ == '__main__':
    try:
        main()
    except BrokenPipeError:      # 接 head/less 时提前关管道，别打印一堆栈
        import os
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
