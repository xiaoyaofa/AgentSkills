#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SVG → PNG 预览导出（跨平台，优先本机浏览器）。

引擎顺序：Edge/Chrome/Chromium（headless 截图）→ Inkscape → LibreOffice → cairosvg。
浏览器路线不需要任何 Python 依赖；cairosvg 只是最后兜底（装了才用）。

用法:
    python svg_to_png.py <in.svg> [out.png] [--dpi 120] [--engine auto|browser|inkscape|soffice|cairosvg]
    python svg_to_png.py --list

说明:
    - 默认 120dpi；浏览器/Inkscape/cairosvg 按 dpi 输出；LibreOffice 兜底由它自己的
      导出尺寸决定（常见 2048 宽），脚本打印实际像素。
    - 浏览器路线会在临时目录生成一个只含 <img> 的 HTML，再按目标像素截图；
      Windows 一般自带 Edge，无需额外安装。
    - 本脚本只做“预览图”；SVG 才是交付物，[S1]~[S8] 不依赖 PNG。
"""
import argparse
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass


def die(msg, code=1):
    print(msg, file=sys.stderr)
    sys.exit(code)


def svg_size(path):
    """返回 SVG 的 CSS 像素宽高；优先 width/height，退回 viewBox。"""
    try:
        root = ET.parse(str(path)).getroot()
    except Exception as e:
        die(f'读不了 SVG: {path}: {e}')
    def num(v):
        if not v or v.strip().endswith('%'):
            return None
        m = re.match(r'^\s*([0-9.]+)', v)
        return float(m.group(1)) if m else None
    w, h = num(root.get('width')), num(root.get('height'))
    if w and h:
        return w, h
    vb = (root.get('viewBox') or '').replace(',', ' ').split()
    if len(vb) == 4:
        return float(vb[2]), float(vb[3])
    die(f'SVG 没有 width/height/viewBox: {path}')


def find_browser():
    names = ['msedge', 'microsoft-edge', 'microsoft-edge-stable',
             'google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser', 'chrome']
    found = []
    for n in names:
        p = shutil.which(n)
        if p:
            found.append((n, p))
    if os.name == 'nt':
        bases = [os.environ.get('ProgramFiles(x86)'), os.environ.get('ProgramFiles'),
                 os.environ.get('LOCALAPPDATA')]
        rels = [r'Microsoft\Edge\Application\msedge.exe',
                r'Google\Chrome\Application\chrome.exe']
        for b in bases:
            if not b:
                continue
            for rel in rels:
                p = os.path.join(b, rel)
                if os.path.isfile(p):
                    found.append((Path(p).name, p))
    seen, out = set(), []
    for name, p in found:
        key = os.path.normcase(os.path.abspath(p))
        if key not in seen:
            seen.add(key)
            out.append((name, p))
    return out


def find_exe(names, win_rels=()):
    out = []
    for n in names:
        p = shutil.which(n)
        if p:
            out.append((n, p))
    if os.name == 'nt':
        bases = [os.environ.get('ProgramFiles'), os.environ.get('ProgramFiles(x86)')]
        for b in bases:
            if not b:
                continue
            for rel in win_rels:
                p = os.path.join(b, rel)
                if os.path.isfile(p):
                    out.append((Path(p).name, p))
    seen, res = set(), []
    for name, p in out:
        key = os.path.normcase(os.path.abspath(p))
        if key not in seen:
            seen.add(key)
            res.append((name, p))
    return res


def run(cmd, timeout=120):
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding='utf-8', errors='replace', timeout=timeout)


def png_ok(path):
    try:
        return Path(path).is_file() and Path(path).stat().st_size > 0
    except OSError:
        return False


def png_size(path):
    """读 PNG IHDR，返回 (w,h)；读不到返回 None。"""
    try:
        with open(path, 'rb') as f:
            head = f.read(24)
        if head[:8] != b'\x89PNG\r\n\x1a\n' or len(head) < 24:
            return None
        return int.from_bytes(head[16:20], 'big'), int.from_bytes(head[20:24], 'big')
    except OSError:
        return None


def via_browser(svg, out, ow, oh):
    browsers = find_browser()
    if not browsers:
        return None
    name, exe = browsers[0]
    svg_uri = Path(svg).resolve().as_uri()
    with tempfile.TemporaryDirectory(prefix='svg2png-') as td:
        html_path = Path(td) / 'page.html'
        profile = Path(td) / 'profile'
        doc = ('<!doctype html><html><head><meta charset="utf-8">'
               '<style>html,body{margin:0;padding:0;background:#fff}'
               f'img{{display:block;width:{ow}px;height:{oh}px}}</style></head>'
               f'<body><img src="{html.escape(svg_uri, quote=True)}"></body></html>')
        html_path.write_text(doc, encoding='utf-8')
        last = None
        for headless in ('--headless=new', '--headless'):
            try:
                Path(out).unlink()
            except OSError:
                pass
            cmd = [exe, headless, '--disable-gpu', '--hide-scrollbars',
                   '--allow-file-access-from-files', '--no-first-run',
                   '--no-default-browser-check', f'--user-data-dir={profile}',
                   f'--window-size={ow},{oh}', f'--screenshot={out}',
                   '--virtual-time-budget=5000', html_path.resolve().as_uri()]
            try:
                last = run(cmd, timeout=120)
            except Exception as e:
                last = e
                continue
            if png_ok(out):
                return f'浏览器 {Path(exe).name}'
        tail = ''
        if last is not None and hasattr(last, 'stderr'):
            tail = (last.stderr or '')[-300:]
        print(f'⚠ 浏览器路线失败: {tail}', file=sys.stderr)
    return None


def via_inkscape(svg, out, dpi):
    exes = find_exe(['inkscape'], [r'Inkscape\bin\inkscape.exe'])
    if not exes:
        return None
    name, exe = exes[0]
    cmds = [
        [exe, str(svg), '--export-type=png', f'--export-filename={out}', f'--export-dpi={dpi}'],
        [exe, str(svg), f'--export-png={out}', f'--export-dpi={dpi}'],
    ]
    for cmd in cmds:
        try:
            Path(out).unlink()
        except OSError:
            pass
        try:
            r = run(cmd, timeout=120)
        except Exception:
            continue
        if png_ok(out):
            return f'Inkscape ({Path(exe).name})'
        print(f'⚠ Inkscape 路线失败: {(r.stderr or "")[-200:]}', file=sys.stderr)
    return None


def via_soffice(svg, out, dpi):
    exes = find_exe(['soffice', 'libreoffice'],
                    [r'LibreOffice\program\soffice.exe'])
    if not exes:
        return None
    name, exe = exes[0]
    with tempfile.TemporaryDirectory(prefix='svg2png-lo-') as td:
        profile = Path(td) / 'profile'
        try:
            r = run([exe, '--headless', f'-env:UserInstallation={profile.as_uri()}',
                     '--convert-to', 'png', '--outdir', td, str(svg)], timeout=180)
        except Exception as e:
            print(f'⚠ LibreOffice 路线失败: {e}', file=sys.stderr)
            return None
        produced = Path(td) / (Path(svg).stem + '.png')
        if produced.is_file():
            shutil.move(str(produced), str(out))
            return f'LibreOffice ({Path(exe).name})'
        print(f'⚠ LibreOffice 没有生成 PNG: {(r.stderr or "")[-200:]}', file=sys.stderr)
    return None


def via_cairosvg(svg, out, ow, oh):
    try:
        import cairosvg  # type: ignore
    except Exception:
        return None
    try:
        cairosvg.svg2png(url=str(svg), write_to=str(out),
                         output_width=ow, output_height=oh)
    except Exception as e:
        print(f'⚠ cairosvg 路线失败: {e}', file=sys.stderr)
        return None
    if png_ok(out):
        return 'cairosvg'
    return None


def main():
    ap = argparse.ArgumentParser(description='SVG → PNG 预览导出（优先浏览器）')
    ap.add_argument('svg', nargs='?', help='输入 .svg')
    ap.add_argument('png', nargs='?', help='输出 .png（默认同名）')
    ap.add_argument('--dpi', type=float, default=120.0, help='输出 DPI（默认 120）')
    ap.add_argument('--engine', default='auto',
                    choices=['auto', 'browser', 'inkscape', 'soffice', 'cairosvg'],
                    help='指定引擎；默认按 browser→inkscape→soffice→cairosvg 顺序')
    ap.add_argument('--list', action='store_true', help='列出检测到的引擎')
    a = ap.parse_args()

    engines = {
        'browser': find_browser(),
        'inkscape': find_exe(['inkscape'], [r'Inkscape\bin\inkscape.exe']),
        'soffice': find_exe(['soffice', 'libreoffice'], [r'LibreOffice\program\soffice.exe']),
    }
    try:
        import cairosvg  # noqa: F401
        engines['cairosvg'] = [('python', 'cairosvg')]
    except Exception:
        engines['cairosvg'] = []
    if a.list:
        for k, v in engines.items():
            print(f'{k}: ' + (', '.join(p for _, p in v) if v else '（未检测到）'))
        return 0

    if a.dpi <= 0:
        die('--dpi 必须 > 0')
    if not a.svg:
        ap.error('缺少输入 SVG')
    svg = Path(a.svg)
    if not svg.is_file():
        die(f'找不到 SVG: {svg}')
    out = Path(a.png) if a.png else svg.with_suffix('.png')
    out.parent.mkdir(parents=True, exist_ok=True)
    out = out.resolve()
    if out == svg.resolve():
        die('输出 PNG 不能与输入 SVG 相同')
    w, h = svg_size(svg)
    scale = a.dpi / 96.0
    ow, oh = max(1, round(w * scale)), max(1, round(h * scale))

    order = (['browser', 'inkscape', 'soffice', 'cairosvg']
             if a.engine == 'auto' else [a.engine])
    for eng in order:
        if not engines.get(eng):
            continue
        if png_ok(out):
            out.unlink()
        if eng == 'browser':
            used = via_browser(svg, out, ow, oh)
        elif eng == 'inkscape':
            used = via_inkscape(svg, out, a.dpi)
        elif eng == 'soffice':
            used = via_soffice(svg, out, a.dpi)
        else:
            used = via_cairosvg(svg, out, ow, oh)
        if used:
            actual = png_size(out) or (ow, oh)
            print(f'✓ PNG: {out}  ({actual[0]}×{actual[1]})  引擎: {used}')
            return 0
        print(f'⚠ {eng} 不可用/失败，尝试下一个引擎', file=sys.stderr)
    die('没有可用的 SVG→PNG 引擎。优先装 Edge/Chrome/Inkscape/LibreOffice，'
        '或 pip install cairosvg。')


if __name__ == '__main__':
    sys.exit(main())
