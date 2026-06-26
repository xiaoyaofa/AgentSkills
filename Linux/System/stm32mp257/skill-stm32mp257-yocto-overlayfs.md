---
name: stm32mp257-overlayfs
description: STM32MP257 (MYD-LD25X) Yocto overlayfs 只读 rootfs + overlay 分区方案。涵盖分区定义、initramfs-overlayroot、内核参数、WIC/flashlayout 生成、验证和故障排查。
---

# STM32MP257 Overlayfs 只读 Rootfs 方案

- **Yocto 版本**: Scarthgap (5.0.3) — `meta-myir/meta-myir-stm32mp`
- **Linux 内核**: 6.6.48
- **U-Boot**: 2023.10-stm32mp-r1
- **OP-TEE**: 4.0.0-stm32mp-r1
- **TF-A**: v2.10.5-stm32mp-r1
- **机器**: `myd-ld25x` (MYD-LD25X, STM32MP257DAK)
- **发行版**: `openstlinux-weston`

## 架构

```
/dev/mmcblkXp8  (rootfs, ext4, RO/lowerdir)     /dev/mmcblkXp10 (upperdirfs, ext4, RW)
        │                                                │
        └────────────┬───────────────────────────────────┘
                     │
              overlay mount (initramfs 91-overlayroot)
                     │
                / (merged, RW)
                │
           /sbin/init (systemd)

启动流程:
  TF-A → OP-TEE → U-Boot → Kernel → initramfs:
                                      01-udev
                                      10-e2fs
                                      90-rootfs (挂载 rootfs RO)
                                      91-overlayroot (挂载 upperdir, 创建 overlay)
                                      99-finish (switch_root → systemd)
```

## 项目路径

| 路径 | 说明 |
|------|------|
| `layers/meta-myir/meta-myir-stm32mp/` | MYIR STM32MP 层（分区定义、extlinux、FIP） |
| `layers/meta-myir/meta-myir-openstlinux/` | OpenSTLinux 层（镜像、initramfs） |
| `layers/openembedded-core/meta/recipes-core/initrdscripts/` | OE-core initramfs 模块 |

## Yocto 编译入口

```bash
cd layers/../
DISTRO=openstlinux-weston MACHINE=myd-ld25x source layers/meta-myir/scripts/envsetup.sh
```

## 涉及的文件

### 新增文件

- `meta-myir-stm32mp/recipes-st/images/st-image-upperdir.bb` — 空的 ext4 upperdir 分区镜像

### 修改文件

- `meta-myir-stm32mp/conf/machine/include/st-machine-image-partition-stm32mp.inc` — 添加 `ST_UPPERDIRFS` 开关、`PARTITIONS_IMAGES[upperdir]`、`PARTITIONS_LIST`
- `meta-myir-stm32mp/conf/machine/include/st-machine-extlinux-config-stm32mp.inc` — `rw` → `ro overlayrootrwdev=/dev/disk/by-partlabel/upperdirfs`
- `meta-myir-openstlinux/recipes-st/images/st-image-resize-initrd.bb` — 添加 `initramfs-module-overlayroot`

### 关键变量定义

```bitbake
# st-machine-image-partition-stm32mp.inc
ST_UPPERDIRFS  ?= "1"
STM32MP_UPPERDIRFS_IMAGE      ?= "st-image-upperdir"
STM32MP_UPPERDIRFS_LABEL      ?= "upperdirfs"
STM32MP_UPPERDIRFS_MOUNTPOINT ?= "/upperdir"
STM32MP_UPPERDIRFS_SIZE       ?= "262144"  # 256MB

PARTITIONS_IMAGES[upperdir] ?= "${STM32MP_UPPERDIRFS_IMAGE},${STM32MP_UPPERDIRFS_LABEL},${STM32MP_UPPERDIRFS_MOUNTPOINT},${STM32MP_UPPERDIRFS_SIZE},FileSystem"
PARTITIONS_LIST += "${@bb.utils.contains('ST_UPPERDIRFS', '1', 'upperdir', '', d)}"
PARTITIONS_LIST_EFI += "${@bb.utils.contains('ST_UPPERDIRFS', '1', 'upperdir', '', d)}"
```

### st-image-upperdir.bb 模板

```bitbake
SUMMARY = "STM32MP upperdir Image (overlayfs upper layer storage)"
LICENSE = "MIT"

include recipes-st/images/st-image-partitions.inc

IMAGE_NAME_SUFFIX = ".upperdir"

# Define to null ROOTFS_MAXSIZE
IMAGE_ROOTFS_MAXSIZE = ""

# Empty image - upperdir is just overlay storage, no pre-installed content needed
PACKAGE_INSTALL = ""
```

## 编译命令

```bash
# 先编译各分区镜像
bitbake st-image-upperdir
bitbake st-image-resize-initrd

# 完整镜像（生成 TSV 和 WIC）
bitbake myir-image-full

# 生成 SD 卡镜像
cd tmp-glibc/deploy/images/myd-ld25x/scripts
./create_sdcard_from_flashlayout.sh --compress \
    ../flashlayout_myir-image-full/optee/FlashLayout_sdcard_myb-stm32mp257x-2GB-optee.tsv
```

## 验证 overlayfs

```bash
ssh root@<board_ip>

# 1. 确认根 overlay
mount | grep ' / '
# 预期: overlay on / type overlay (rw,lowerdir=/overlay/rofs,upperdir=/overlay/upper,workdir=/overlay/work)

# 2. 确认 lowerdir 只读
mount | grep rofs
# 预期: /dev/mmcblkXp8 on /rofs type ext4 (ro)

# 3. 写入测试
touch /test-overlay.txt && echo "可写" || echo "只读!"
ls /rofs/test-overlay.txt   # 应不存在（CoW 正确）
rm /test-overlay.txt
```

## 常见问题排查

### 1. 启动后根文件系统只读

**原因**: upperdir ext4 镜像太小（默认 256MB），分区虽大但文件系统没扩展。

**修复**: resize2fs 扩展文件系统
```bash
resize2fs /dev/mmcblkXp10
```

### 2. overlayroot 挂载失败 "No such file or directory"

**原因**: busybox mount 不支持 `PARTLABEL=` 语法。

**修复**: 使用 `/dev/disk/by-partlabel/upperdirfs`
```bash
# st-machine-extlinux-config-stm32mp.inc 中:
UBOOT_EXTLINUX_KERNEL_ARGS ?= "rootwait ro overlayrootrwdev=/dev/disk/by-partlabel/upperdirfs"
```

### 3. TSV 不生成 "upperdir.ext4 binary is missing"

**原因**: label 不以 `fs` 结尾时，`st-partitions-image.bbclass` 自动追加 `fs` 后缀导致文件名不匹配。

**修复**: label 设为 `upperdirfs`
```bitbake
STM32MP_UPPERDIRFS_LABEL ?= "upperdirfs"
```

### 4. SD 卡 vs eMMC 设备路径不同

**原因**: SD 卡是 `mmcblk0`，eMMC 是 `mmcblk1`。不要硬编码设备路径。

**修复**: 用 `PARTLABEL=` 或 `/dev/disk/by-partlabel/` 方式。

## 恢复原始文件系统

备份 overlay 配置后，修改 extlinux.conf：
```bash
# 备份
cp /boot/mmc0_extlinux/extlinux.conf /boot/mmc0_extlinux/extlinux.conf.overlay.bak

# 去掉 overlayroot: ro overlayrootrwdev=/dev/disk/by-partlabel/upperdirfs → rw
sed -i 's/ro overlayrootrwdev=\/dev\/disk\/by-partlabel\/upperdirfs/rw/' \
    /boot/mmc0_extlinux/extlinux.conf

reboot
```

恢复 overlay：
```bash
cp /boot/mmc0_extlinux/extlinux.conf.overlay.bak /boot/mmc0_extlinux/extlinux.conf
reboot
```

## 恢复出厂（清空 upperdir）

原始 rootfs（lowerdir）永不被修改，所有改动在 upperdir：

1. 改 extlinux.conf 去掉 overlayroot 参数，重启到原始 rootfs
2. 挂载 upperdir 分区：`mount /dev/mmcblkXp10 /mnt`
3. 清空：`rm -rf /mnt/upper/* /mnt/work/*`
4. 恢复 overlayroot 参数，重启
