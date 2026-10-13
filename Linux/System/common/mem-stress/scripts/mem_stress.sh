#!/bin/bash
#
# mem_stress.sh — 内存 + CPU 压测，带温度实时监控（跨平台）
#
# 用法: ./mem_stress.sh <内存大小> [测试轮数]
#   例: ./mem_stress.sh 1G
#       ./mem_stress.sh 512M 2
#
# 前台运行，Ctrl+C 退出并自动清理所有压测进程。
# 输出同时打印到控制台和日志文件，日志默认 /root/mem_stress_<时间戳>.log
#
# 内存大小必须手动指定，脚本不做自动检测、不自动取值。
#
# 温度采集：读 /sys/class/thermal/thermal_zone*/temp（标准 thermal 框架）。
# ARM SoC 的 CPU 温度都在这里，有几个传感器就对应几个 zone。
# 只跑 ARM，因此不读 hwmon —— 只有 x86 的每核温度才必须走 hwmon(coretemp)；
# 而且 thermal 框架会给每个 zone 自动注册一个 hwmon 镜像，混读会重复计数。
#
# 可选环境变量:
#   LOG_DIR=/root/mem_stress_log   日志目录（不存在会自动创建）
#   SERIAL_DEV=/dev/ttyLP0 若非空，额外把输出写到该串口设备

set -u

LOG_DIR="${LOG_DIR:-/root/mem_stress_log}"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="${LOG_DIR}/mem_stress_${STAMP}.log"
PIDFILE="${LOG_DIR}/mem_stress.pid"
MAIN_PID=$$

MEM_SIZE="${1:-}"
LOOPS="${2:-}"

STRESS_PID=""
MEM_PID=""
MON_PID=""

# ---------------------------------------------------------------- 输出
# 同时写日志、控制台，以及可选的串口设备
LOG_MSG() {
    printf '%s\n' "$*" >>"$LOG" 2>/dev/null
    printf '%s\n' "$*"
    if [ -n "${SERIAL_DEV:-}" ] && [ -w "$SERIAL_DEV" ]; then
        printf '%s\n' "$*" >"$SERIAL_DEV"
    fi
}

usage() {
    cat <<EOF
用法: $(basename "$0") <内存大小> [测试轮数]

  <内存大小>   必填，手动指定，如 256M / 512M / 1G（脚本不自动检测）
  [测试轮数]   可选。不填则交给 memtester 自己无限循环，直到 Ctrl+C；
               填了则 memtester 跑完这么多轮后本次压测结束

示例:
  $(basename "$0") 1G          # 压测 1G 内存 + CPU 全核拉满，一直测到 Ctrl+C
  $(basename "$0") 512M 3      # 压测 512M，memtester 跑 3 轮后自动结束

前台运行，Ctrl+C 退出并清理所有压测进程。
日志: ${LOG_DIR}/mem_stress_<时间戳>.log
EOF
}

# ---------------------------------------------------------------- 清理
cleanup() {
    trap - INT TERM
    printf '\n'
    LOG_MSG "[$(date +%T)] $1，正在清理压测进程 ..."

    [ -n "$MON_PID" ]    && kill "$MON_PID"    2>/dev/null
    [ -n "$STRESS_PID" ] && kill "$STRESS_PID" 2>/dev/null
    [ -n "$MEM_PID" ]    && kill "$MEM_PID"    2>/dev/null

    pkill -TERM -f 'memtester' 2>/dev/null
    pkill -TERM -f 'stress-ng' 2>/dev/null
    sleep 1
    pkill -KILL -f 'memtester' 2>/dev/null
    pkill -KILL -f 'stress-ng' 2>/dev/null
    sleep 1

    if pgrep -f 'memtester|stress-ng' >/dev/null 2>&1; then
        LOG_MSG "[$(date +%T)] 警告：仍有残留进程"
        while read -r l; do LOG_MSG "    $l"; done < <(pgrep -a -f 'memtester|stress-ng')
    else
        LOG_MSG "[$(date +%T)] 已清理干净"
    fi
    LOG_MSG "[$(date +%T)] 日志: $LOG"
    rm -f "$PIDFILE"
    exit 130
}
trap 'cleanup "收到退出信号"' INT TERM

# ---------------------------------------------------------------- 参数检查
if [ -z "$MEM_SIZE" ]; then
    usage
    exit 1
fi

for C in memtester stress-ng; do
    if ! command -v "$C" >/dev/null 2>&1; then
        echo "错误：找不到 $C，请先安装" >&2
        exit 1
    fi
done

if ! mkdir -p "$LOG_DIR" 2>/dev/null; then
    echo "错误：无法创建日志目录 $LOG_DIR" >&2
    exit 1
fi

printf '%s\n' "$MAIN_PID" > "$PIDFILE"

# 行缓冲，避免 memtester / stress-ng 的进度输出攒批后才出现
if command -v stdbuf >/dev/null 2>&1; then
    BUFFER="stdbuf -oL"
else
    BUFFER=""
fi

# ---------------------------------------------------------------- 温度源收集
# 每个源存 路径 + 显示名，一一对应
TEMP_PATH=()
TEMP_NAME=()

add_temp() {
    TEMP_PATH+=("$1")
    TEMP_NAME+=("$2")
}

for Z in /sys/class/thermal/thermal_zone*; do
    [ -r "$Z/temp" ] || continue
    ZT="$(cat "$Z/type" 2>/dev/null)"
    [ -n "$ZT" ] || ZT="$(basename "$Z")"
    add_temp "$Z/temp" "$ZT"
done

TEMP_N="${#TEMP_PATH[@]}"

# ---------------------------------------------------------------- 温度监控
# 内核统一以毫摄氏度上报；整数运算避免每秒 fork 出 awk
monitor_temp() {
    local i v line now msg sign
    while :; do
        line=""
        i=0
        while [ "$i" -lt "$TEMP_N" ]; do
            v=""
            read -r v <"${TEMP_PATH[$i]}" 2>/dev/null
            case "$v" in
                ''|*[!0-9-]*) : ;;                 # 读不到或非数字，跳过
                *)
                    sign=""
                    if [ "$v" -lt 0 ]; then sign="-"; v=$((0 - v)); fi
                    line="${line}${TEMP_NAME[$i]}=${sign}$((v / 1000)).$((v % 1000 / 100))°C "
                    ;;
            esac
            i=$((i + 1))
        done
        now="$(date +%T)"
        msg="[$now] 温度: ${line:-N/A}"
        printf '%s\n' "$msg"
        printf '%s\n' "$msg" >>"$LOG" 2>/dev/null
        if [ -n "${SERIAL_DEV:-}" ] && [ -w "$SERIAL_DEV" ]; then
            printf '%s\n' "$msg" >"$SERIAL_DEV"
        fi
        sleep 1
    done
}

# ---------------------------------------------------------------- 信息头
NCPU="$(nproc 2>/dev/null)"
case "${NCPU:-}" in ''|*[!0-9]*) NCPU=0 ;; esac

TOTAL_KB="$(awk '/^MemTotal:/{print $2}' /proc/meminfo 2>/dev/null)"
case "${TOTAL_KB:-}" in ''|*[!0-9]*) TOTAL_KB=0 ;; esac

LOG_MSG "=============================================="
LOG_MSG " 内存 / CPU 压测   $(date '+%F %T')"
LOG_MSG "----------------------------------------------"
LOG_MSG " 脚本 PID   : $MAIN_PID"
LOG_MSG " 停止方式   : Ctrl+C，或另开一个终端 kill $MAIN_PID"
LOG_MSG " PID 文件   : $PIDFILE"
LOG_MSG " 内存大小   : $MEM_SIZE   (手动指定)"
if [ -n "$LOOPS" ]; then
    LOG_MSG " memtester  : ${MEM_SIZE}，跑 $LOOPS 轮后结束"
else
    LOG_MSG " memtester  : ${MEM_SIZE}，memtester 自身无限循环"
fi
LOG_MSG " CPU        : ${NCPU:-?} 核，stress-ng 全部拉满（-t 0 无限跑）"
LOG_MSG " 温度源     : ${TEMP_N} 个 — ${TEMP_NAME[*]:-未找到}"
if [ "$TEMP_N" -eq 0 ]; then
    LOG_MSG "              ⚠ 未找到任何温度源，温度行将显示 N/A"
elif [ "$NCPU" -gt 0 ] && [ "$TEMP_N" -lt "$NCPU" ]; then
    LOG_MSG "              ⚠ 温度源少于核数：多个核共享同一热域（硬件无 per-core 传感器），"
    LOG_MSG "                不是脚本漏读。"
fi
LOG_MSG " 日志       : $LOG"
LOG_MSG " 退出       : Ctrl+C"
LOG_MSG "=============================================="

# 仅提示，不改变用户指定的值
case "$MEM_SIZE" in
    *[Gg]) WANT_KB=$(( ${MEM_SIZE%[Gg]} * 1024 * 1024 )) ;;
    *[Mm]) WANT_KB=$(( ${MEM_SIZE%[Mm]} * 1024 )) ;;
    *[Kk]) WANT_KB=${MEM_SIZE%[Kk]} ;;
    *)     WANT_KB=0 ;;
esac
case "${WANT_KB:-}" in ''|*[!0-9]*) WANT_KB=0 ;; esac
if [ "$WANT_KB" -gt 0 ] && [ "$TOTAL_KB" -gt 0 ] && [ "$WANT_KB" -gt $((TOTAL_KB * 80 / 100)) ]; then
    LOG_MSG ""
    LOG_MSG "警告：指定的 ${MEM_SIZE} 已超过总内存 (${TOTAL_KB}KB) 的 80%，"
    LOG_MSG "      memtester 可能因无法锁定内存而失败。"
    LOG_MSG ""
fi

# ---------------------------------------------------------------- 启动
monitor_temp &
MON_PID=$!

# -t 0 = 无限跑。stress-ng 不带 -t 时默认只跑 1 天就会自己退出，
# 长跑超过 24h 会静默丢掉 CPU 压测，所以这里显式指定。
LOG_MSG "[$(date +%T)] 温度监控已启动（${TEMP_N} 个温度源，每 1s 一次）"
LOG_MSG "[$(date +%T)] 启动 CPU 压测：stress-ng --cpu 0 -t 0"
stress-ng --cpu 0 -t 0 --metrics-brief 2>&1 | $BUFFER tee -a "$LOG" &
STRESS_PID=$!

if [ -n "$LOOPS" ]; then
    LOG_MSG "[$(date +%T)] 启动内存压测：memtester $MEM_SIZE $LOOPS"
    $BUFFER memtester "$MEM_SIZE" "$LOOPS" 2>&1 | $BUFFER tee -a "$LOG" &
else
    LOG_MSG "[$(date +%T)] 启动内存压测：memtester $MEM_SIZE （memtester 自己无限循环）"
    $BUFFER memtester "$MEM_SIZE" 2>&1 | $BUFFER tee -a "$LOG" &
fi
MEM_PID=$!

# 不给轮数时 memtester 永不退出，只有 Ctrl+C 能打断；
# 给了轮数则跑完自行结束，走到下一行统一清理。
wait "$MEM_PID"
cleanup "memtester 已完成指定轮数"
