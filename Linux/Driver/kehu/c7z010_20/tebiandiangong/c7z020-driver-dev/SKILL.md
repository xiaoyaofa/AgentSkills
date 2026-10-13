---
name: c7z020-driver-dev
description: MYD-C7Z020 PetaLinux 内核驱动开发、设备树修改、补丁生成和编译打包的完整工作流。涉及 uartlite RS485、PWM、XADC 等驱动。客户项目「特变电工」（tebiandiangong）。
license: MIT
metadata:
  category: Linux/Driver
  platform: c7z010_20
  customer: 特变电工（tebiandiangong）
  soc: Zynq-7020
  board: MYD-C7Z020
  petalinux: "2024.2"
  vendor: MYIR
---

# MYD-C7Z020 Linux Driver 开发技能

## 项目路径

| 目录 | 说明 |
|------|------|
| `project-spec/` | PetaLinux 项目配置 |
| `myir-xilinx-linux/` | 内核源码（git 仓库，分支 tebiandiangong） |
| `components/` | 自动生成文件（pl.dtsi 等，不可直接修改） |
| `images/linux/` | 最终输出镜像 |

## 编译流程

```bash
cd /home/xie/Source/MYD-C7Z01020/v4.0/petalinux_20242/MYD-C7Z020
source ../settings.sh

# 全量重编（改内核驱动后）
petalinux-build -c kernel -x cleansstate
petalinux-build -c kernel

# 只重建设备树
petalinux-build -c device-tree -x cleansstate
petalinux-build -c device-tree

# 整体编译（含 FSBL、U-Boot、kernel、rootfs）
petalinux-build

# 打包 BOOT.BIN
petalinux-package --boot --fpga --u-boot --force
```

## 补丁工作流

### 内核驱动改动

```bash
cd /home/xie/Source/MYD-C7Z01020/v4.0/myir-xilinx-linux

# 1. 修改代码后，git reset 之前提交（如需要）
git reset HEAD~1

# 2. 分别提交不同功能
git add drivers/tty/serial/uartlite.c
git commit -m "FEAT: uartlite: add RS485 RTS GPIO control"

git add drivers/misc/myip-pwm.c drivers/misc/Kconfig drivers/misc/Makefile
git commit -m "FEAT: myip-pwm: DEVICE_ATTR sysfs"

# 3. 生成补丁
git format-patch HEAD~1 --stdout > /home/xie/Source/.../recipes-kernel/linux/linux-xlnx/0001-FEAT-xxx.patch

# 4. 在 bbappend 中添加（如需要）
# SRC_URI += "file://0001-FEAT-xxx.patch"
```

### 设备树改动

直接修改 `project-spec/meta-user/recipes-bsp/device-tree/files/system-user.dtsi`，不要改 `components/plnx_workspace/device-tree/device-tree/pl.dtsi`（自动生成）。

## .xsa 导入注意

每次导入新 .xsa 后，检查 `system.xsa` 中的 `drivers/myip_pwm_test_v1_0/src/Makefile`：
- `($wildcard` 是错误语法，应改为 `$(wildcard`
- 修复方法见下方 .xsa Makefile 修复

## .xsa Makefile 修复

```bash
XSA=/home/xie/Source/.../hw-description/system.xsa
cd /tmp && rm -rf xsa_fix && mkdir xsa_fix && cd xsa_fix
unzip -o $XSA drivers/myip_pwm_test_v1_0/src/Makefile
# 修改 LIBSOURCES=($wildcard *.c) → LIBSOURCES=$(wildcard *.c)
# 修改 OUTS = ($wildcard *.o) → OUTS = $(LIBSOURCES:.c=.o)
zip -u $XSA drivers/myip_pwm_test_v1_0/src/Makefile
```

## RS485 RTS GPIO 控制（uartlite.c）

### 设备树配置

```dts
&axi_uartlite_0 {
    rts-gpios = <&gpio0 57 GPIO_ACTIVE_HIGH>;
    linux,rs485-enabled-at-boot-time;
};
```

### 驱动关键逻辑

- `devm_gpiod_get_optional(&pdev->dev, "rts", GPIOD_OUT_LOW)` 获取 GPIO
- `port->rs485_supported.flags` 必须初始化，否则 `uart_get_rs485_mode()` 直接返回
- `start_tx`: 有数据才拉高 RTS，没数据不动
- `stop_tx`: 直接拉低 RTS（兜底）
- ISR: 检查 `xmit_empty && TXEMPTY && gpiod_get_value(rts_gpio)` 才拉低
- 接收时不碰 RTS

### 常见坑

1. `uart_get_rs485_mode()` 要求 `port->rs485_supported.flags & SER_RS485_ENABLED`，不设就跳过所有 RS485 解析
2. ISR 里不要用 `read_poll_timeout_atomic`（忙等），直接读一次 TXEMPTY 即可
3. `start_tx` 不能无条件拉高 RTS，要先判断 `has_data = port->x_char || !uart_circ_empty()`
4. 接收时 GPU 已低，ISR 加 `gpiod_get_value(rts_gpio)` 判断可以跳过

## PWM 驱动（myip-pwm.c）

### 关键参数

- 基地址：0x43C50000
- 占空比寄存器：base + 0x00（0-100 直接对应 0%-100%）
- 频率寄存器：base + 0x04（reg_val = 100000000 / hz）
- 频率范围：2000Hz ~ 1000000Hz
- 默认值：占空比 0%，频率 5kHz（reg = 20000）

### sysfs 节点

使用 `DEVICE_ATTR` 标准宏，不要用 `__ATTR` + `sysfs_create_group`：

```c
static DEVICE_ATTR(duty, 0664, duty_show, duty_store);
static DEVICE_ATTR(freq, 0664, freq_show, freq_store);

// probe:
device_create_file(&pdev->dev, &dev_attr_duty);
device_create_file(&pdev->dev, &dev_attr_freq);

// remove:
device_remove_file(&pdev->dev, &dev_attr_duty);
device_remove_file(&pdev->dev, &dev_attr_freq);
```

### Kconfig 注意

`depends on ARCH_ZYNQ` 在某些 Zynq 平台上不生效，改用 `depends on ARM || COMPILE_TEST`。

## XADC（xadc_wiz_0）

### 通道编号映射

| sysfs | VAUX 通道 |
|-------|-----------|
| in_voltage8 | vaux0 |
| in_voltage9 | vaux1 |
| ... | ... |
| in_voltage23 | vaux15 |

- 内部通道（vccint, vccaux 等）占用 in_voltage0-7
- 外部 AUX 通道从 in_voltage8 开始
- 只开实际使用的通道，浮空通道会产生串扰
- P 端接信号、N 端接地 → 单端模式，不加 `xlnx,bipolar`

### 电压换算

- XADC 12-bit: raw 范围 0-4095，对应 0-1.0V
- 正确 scale: **1.0 / 4096 = 0.000244140625**
- sysfs 上报的 `_scale` 可能错误（1.25V 参考电压），建议直接用 raw 值计算

## 常用调试命令

```bash
# 查看内核日志
dmesg | tail -50

# 查看 GPIO 状态
cat /sys/kernel/debug/gpio

# 查看内核配置
zcat /proc/config.gz | grep CONFIG_MYIP_PWM

# 查看设备树
dtc -I fs -O dts /sys/firmware/devicetree/base

# 查看 IIO 设备
ls /sys/bus/iio/devices/iio:device1/
cat /sys/bus/iio/devices/iio:device1/in_voltage8_raw
```
