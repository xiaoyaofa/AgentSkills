---
name: mem-stress
description: Linux 开发板内存 + CPU 压测脚本（stress-ng + memtester），带 CPU 温度实时监控、日志留存、Ctrl+C/PID 双停止方式。涵盖 memtester 轮数语义、stress-ng 超时默认值、thermal zone 温度采集、非交互场景停止等踩坑记录。
---

# 内存 / CPU 压测脚本（mem_stress）

- **脚本**: [`bin/mem_stress.sh`](bin/mem_stress.sh)
- **依赖**: `stress-ng`、`memtester`（busybox 的 `devmem` 无关；核心命令只用 coreutils）
- **适用**: 任意 Linux 开发板；温度采集走标准 thermal 框架，ARM 平台通用
- **首次验证平台**: MYD-LMX9X（i.MX93，2×A55，2GB LPDDR4X）

## 用途

给开发板做内存/CPU 稳定性压测，同时盯 CPU 温度。主要用于 DDR 换料后的验证（内存压测能覆盖到真实物理页，比 U-Boot 里读容量寄存器可靠得多）。

## 用法

```bash
/root/mem_stress.sh <内存大小> [测试轮数]

/root/mem_stress.sh 1G          # 压测 1G，CPU 全核拉满，一直测到 Ctrl+C
/root/mem_stress.sh 512M 3      # 压测 512M，memtester 跑 3 轮后自动结束
```

**内存大小必须手动指定**，脚本不做自动检测。不传参数只打印用法。

可选环境变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `LOG_DIR` | `/root/mem_stress_log` | 日志目录，不存在会自动 `mkdir -p` |
| `SERIAL_DEV` | 空 | 非空时额外把输出写到该串口设备，如 `/dev/ttyLP0` |

## 输出形态

```
==============================================
 内存 / CPU 压测   2026-10-10 17:42:20
----------------------------------------------
 脚本 PID   : 2099
 停止方式   : Ctrl+C，或另开一个终端 kill 2099
 PID 文件   : /root/mem_stress_log/mem_stress.pid
 内存大小   : 1G   (手动指定)
 memtester  : 1G，memtester 自身无限循环
 CPU        : 2 核，stress-ng 全部拉满（-t 0 无限跑）
 温度源     : 1 个 — cpu-thermal
              ⚠ 温度源少于核数：多个核共享同一热域（硬件无 per-core 传感器），
                不是脚本漏读。
 日志       : /root/mem_stress_log/mem_stress_20261010_174220.log
 退出       : Ctrl+C
==============================================
[17:42:20] 温度: cpu-thermal=44.8°C
...
[17:43:46] 收到退出信号，正在清理压测进程 ...
[17:43:49] 已清理干净
```

温度每秒一行，带传感器名字；日志与控制台内容一致（全程 `tee`，不丢 `/dev/null`）。

## 停止方式

| 启动方式 | 怎么停 |
|---|---|
| 串口控制台直接跑 | **Ctrl+C** |
| SSH 非交互 / `nohup &` / 后台 | `kill $(cat /root/mem_stress_log/mem_stress.pid)` |

`kill` 发 SIGTERM，脚本的 `trap` 会接住，走与 Ctrl+C 完全相同的清理流程（TERM → 等 1s → KILL → 复查残留 → 删 pidfile）。

## 关键设计要点 / 踩坑记录

### 1. memtester 的轮数语义 —— 不传才是无限

```
memtester 1G        →  Loop 1:      Loop 2:  ...     ← 无分母，自身无限循环
memtester 1G 1      →  Loop 1/1:                     ← 有分母，跑完就退
```

**不给轮数参数时 memtester 自己就会无限循环。** 不需要在 bash 外面再套 `while` 循环——那样反而要多传一个显式的 `1` 把它的无限循环关掉，纯属画蛇添足。

脚本据此设计：不给轮数 → `memtester <size>`（无限）；给 N → `memtester <size> N`（跑完自退，然后统一清理）。结尾用 `wait "$MEM_PID"`：不给轮数时永不返回只能 Ctrl+C，给了轮数则跑完走到清理。

### 2. stress-ng 默认只跑 1 天，必须显式 `-t 0`

| 命令 | stress-ng 自己打印 |
|---|---|
| 不传 `-t` | `defaulting to a **1 day** run per stressor` |
| `-t 0` | `setting to a **infinite** run per stressor` |
| `-t N` | `setting to a N secs run per stressor` |

这是个**静默失效**的坑：长跑超过 24h 后 CPU 压测会自己退出，只剩 memtester 还在跑，串口上完全看不出来压测强度掉了。所以必须写 `stress-ng --cpu 0 -t 0`。

（`-t 0` = 无限是 man page 明确写的行为："A 0 timeout will run stress-ng forever with no timeout"。）

### 3. 温度只读 `/sys/class/thermal/thermal_zone*`，不要混读 hwmon

ARM SoC 的 CPU 温度都在标准 thermal 框架里，有几个传感器就对应几个 zone。**只跑 ARM 的话这一处就够了。**

**不要同时枚举 `/sys/class/hwmon/*/temp*_input`** —— thermal 框架会给**每个 zone 自动再注册一个 hwmon 镜像**，混读会重复计数：i.MX93 会报 2 个、i.MX8M Quad 会报 8 个（4 真实 + 4 镜像）。

如果将来真要支持 x86：x86 的 per-core 温度**不在 thermal 框架里**，只在 hwmon 的 `coretemp`(Intel)/`k10temp`(AMD) 下，thermal_zone 只有 `x86_pkg_temp`（封装温度）。那时才需要加 hwmon，并按 `hwmon/*/device` 是否链回 `thermal_zone` 去重：

```
hwmon0/device -> /sys/devices/virtual/thermal/thermal_zone0    ← 镜像，跳过
```

另外 hwmon 编号（`hwmon0`/`hwmon1`）不可预测，必须按 `name` 文件匹配，不能写死索引。

### 4. 「温度源少于核数」≠ 脚本漏读

脚本会对此告警，因为这是**硬件事实**而非 bug。典型案例 i.MX93：`imx93.dtsi` 只定义了一个 `cpu-thermal` zone，并把 `A55_0`/`A55_1` 都作为它的 cooling device —— 整个 A55 簇共用一个热域。QorIQ TMU 驱动虽然支持 16 个测点（`SITES_MAX`、`REGS_TRITSR(n) = 0x100+16n`），但只使能 DT 引用的那些；i.MX93 **硬件就没有 per-core 温度**。

### 5. 满载时 SSH 会掉线

板子核心少的时候（如 2 核），`stress-ng --cpu 0` 占满后 **sshd 会被饿死**，SSH 连接超时断开。脚本本身在板子上照常跑完并清理，不是脚本的问题。

所以正式压测**走串口控制台**；非交互场景就后台启动 + pidfile 停。

### 6. 几个效率/正确性细节

- 温度读取用 **bash 整数运算**（内核给的是毫摄氏度），避免每秒 fork 出 `awk`；文件读取用 `read` 内建而非 `cat`，也不 fork。CPU 拉满时不添额外抖动。
- `stdbuf -oL` 强制行缓冲，否则 memtester / stress-ng 接管道后会块缓冲，输出攒批才出现。
- 负数温度按符号单独处理（`-v` 后再除，避免 `-5.-5°C` 这种错误格式）。
- 内存大小超过总内存 80% 时给**非阻塞警告**（memtester 要 mlock，超了会直接失败），但不改变用户指定的值。

## 部署到新板子

```bash
scp bin/mem_stress.sh root@<板子>:/root/mem_stress.sh
ssh root@<板子> 'chmod +x /root/mem_stress.sh'
```

新平台先确认三件事：

```bash
ls /sys/class/thermal/thermal_zone*     # 有几个温度源、type 是什么
nproc                                    # 核数
command -v stress-ng memtester           # 依赖是否齐
```

缺依赖时 Yocto 目标装包（或在板子上装 deb/ipk）。

## 移植性边界

| 依赖 | 说明 |
|---|---|
| `stress-ng` | 需支持 `-t 0`（现代版本都有） |
| `memtester` | 4.x 支持 `256M`/`1G` 这类后缀 |
| thermal sysfs | 标准内核接口，ARM 平台通用 |
| `stdbuf` | coreutils，没有会自动降级（`BUFFER` 置空） |

只有 x86 平台需要改温度采集部分（见要点 3）。
