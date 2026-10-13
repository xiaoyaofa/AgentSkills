#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""img_crop.py — 原理图局部裁剪放大，用于 Read 工具精读细节。

原理图整页缩到 Read 能看的尺寸后，小号文字（网络名、位号）会糊；
裁一小块再放大，才能看清"哪个三极管由哪个信号驱动"这类拓扑。

用法:
    img_crop.py <原图.png> <输出.png> X1 Y1 X2 Y2 [放大倍数]
    img_crop.py page.png out.png 950 1150 2600 2481 1.1

    # 只给中心点 + 尺寸（更省事）
    img_crop.py page.png out.png --center 1800 1500 --size 1400 900 --scale 1.5

坐标是**原图像素**。若整图是 160dpi 渲染的，想看某页某处时：
    原图坐标 = 160dpi 图上肉眼坐标 × (300/160) ≈ ×1.875
先在整图上估算位置 → 裁出来 → 若偏了按偏移量再裁一次即可。

依赖: Pillow (PIL)。没有的话可用 ImageMagick: convert -crop WxH+X+Y +repage -resize N%
"""

import argparse
import sys

try:
    from PIL import Image
except ImportError:
    sys.exit('需要 Pillow：pip install pillow  （或用 ImageMagick convert -crop）')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('dst')
    ap.add_argument('coords', nargs='*', type=float,
                    help='X1 Y1 X2 Y2 [放大倍数]（与 --center 二选一；'
                         '第 5 个数等价于 --scale）')
    ap.add_argument('--center', nargs=2, type=int, metavar=('X', 'Y'))
    ap.add_argument('--size', nargs=2, type=int, metavar=('W', 'H'))
    ap.add_argument('--scale', type=float, default=1.0, help='放大倍数（LANCZOS）')
    a = ap.parse_args()

    im = Image.open(a.src)
    W, H = im.size
    scale = a.scale
    if a.center:
        if not a.size:
            sys.exit('--center 需配 --size')
        cx, cy = a.center
        w, h = a.size
        box = (cx - w // 2, cy - h // 2, cx + w // 2, cy + h // 2)
    else:
        if len(a.coords) == 5 and a.scale == 1.0:      # X1 Y1 X2 Y2 SCALE
            scale = a.coords[4]
        elif len(a.coords) != 4:
            sys.exit('需要 4 个坐标 X1 Y1 X2 Y2 [放大倍数]，或用 --center/--size')
        box = tuple(int(v) for v in a.coords[:4])

    # 夹到图内，避免越界后得到空白图（常见误操作）
    box = (max(0, box[0]), max(0, box[1]), min(W, box[2]), min(H, box[3]))
    if box[2] - box[0] < 8 or box[3] - box[1] < 8:
        sys.exit(f'裁剪区域太小/越界: {box}（原图 {W}x{H}）')

    out = im.crop(box)
    if scale != 1.0:
        out = out.resize((int(out.width * scale), int(out.height * scale)),
                         Image.LANCZOS)
    out.save(a.dst)
    print(f'{a.dst}  crop={box}  scale={scale}  输出={out.size}  原图={W}x{H}')


if __name__ == '__main__':
    main()
