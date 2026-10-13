---
name: qt-eglfs-mirror
description: AM62x(TI SDK 11)Qt eglfs 双屏同显插件方案。LVDS 主屏 + HDMI 硬件缩放直通的完整实现:插件架构(Qt eglfs_kms 私有类扩展)、构建部署、tidss plane 绑定与硬件 scaler 决策、全部踩坑记录与故障排查。
---

# AM62x Qt eglfs 双屏同显(eglfs-mirror 插件)

- **硬件平台**: MYD-YM62X(AM625x,LVDS 1024x600 + HDMI 1920x1080)
- **内核**: 6.12.35-ti(分支 develop-ym62x-linux-6.12.y_11.01.05.03)
- **TI Processor SDK**: 11.01.05.03(arago 2025.01,scarthgap)
- **Qt**: 6.9.2(eglfs + eglfs_kms 集成,GBM 后端)
- **交叉编译 SDK**: /opt/myd-ym62x-v2.0(sysroot aarch64-oe-linux)
- **插件产物**: libqeglfs-myclone-integration.so + mycloneintegration.json

## 方案架构(最终形态:硬件缩放直通)

```
LVDS 主屏(1024x600,应用原生渲染,全速)
  └─ 每帧 flip 后:主屏 framebuffer 直接 atomic 提交到 HDMI plane
       SRC 1024x600 → CRTC 1920x1080(tidss vid plane 硬件 scaler 放大)
       零 CPU 拷贝 · 零 GPU 渲染 · ~0.4ms/帧 · 应用动画不受影响
```

## 硬件核心事实(方案决策依据)

| 事实 | 细节 |
|---|---|
| DSS 双 video port | VP1→OLDI/LVDS、VP2→DPI/HDMI,各自独立 CRTC |
| plane-VP 硬绑定 | **VP1 只能 vidl1(lite,无缩放);VP2 的 vid plane 带硬件 scaler** |
| 硬件 scaler 能力 | 5-tap 滤波器,最大放大 16x / 缩小 4x(vid plane 专属) |
| 跨 VP 挂 plane | tidss 拒绝("no unused plane available for CRTC") |
| 同 VP 缩放提交 | SRC≠CRTC 的 atomic 提交被接受,硬件自动缩放 |
| DRM 设备序号 | 不固定(PVR GPU 可能占 card0),用 QDeviceDiscovery 自动发现 |
| weston same-as | shared-CRTC 克隆,双 VP 硬件不支持,走不通 |

**核心决策:缩放必须放 HDMI 侧**(LVDS 侧 vidl1 无 scaler 是硬约束)。

## 项目结构(00_app 仓库)

GitHub 仓库: <https://github.com/xiaoyaofa/qt-eglfs-mirror-test>(分支 ti-sdk11-am625x)

| 路径 | 说明 |
|---|---|
| eglfs-mirror/mycloneintegration.cpp/.h | 插件主体(继承 Qt eglfs_kms 私有类) |
| eglfs-mirror/myclone.pro | qmake 工程(include 路径/MYCLONE_DEBUG 宏) |
| eglfs-mirror/build.sh | 构建脚本(强制重编 + 手动链接) |
| eglfs-mirror/gles-headers/ | 补充的 GLES3/KHR 头(sysroot 缺失) |
| eglfs-mirror/mycloneintegration.json | 插件元数据 |
| qt-mirror-test/stress/ | 弹窗/虚拟键盘压力测试程序 |

## 插件架构(Qt eglfs_kms 扩展点)

继承链(类均 Q_EGLFS_EXPORT 导出,插件 .so 可直接继承):

```
QEglFSDeviceIntegrationPlugin(入口)
  └─ MyCloneIntegration : QEglFSKmsGbmIntegration
       createDevice() → MyCloneDevice : QEglFSKmsGbmDevice
                          createScreen() → MyCloneScreen : QEglFSKmsGbmScreen
                                            flip() override ← 镜像时机
```

- Qt 6.9 移除了 eglfs_kms.json 配置(含 clones 字段),但扩展点 API 完整保留
- 私有头路径(SDK sysroot 嵌套目录):`QtEglFsKmsGbmSupport/6.9.2/QtEglFsKmsGbmSupport/private/` 等,升级 Qt 需同步核对
- moc 注意:TI 的 moc 对宏形式 IID 解析失败,Q_PLUGIN_METADATA 的 IID 用字符串字面量

## 关键代码(直通镜像路径)

```cpp
// flip():主屏 swap 后读主屏 CRTC 当前 fb,atomic 提交到 HDMI
void MyCloneScreen::flip()
{
    QEglFSKmsGbmScreen::flip();
    // ... 默认直通(MYCLONE_SOFT_SCALE=1 时走 GPU 缩放管线)

    drmModeCrtcPtr c = drmModeGetCrtc(fd, srcOut.crtc_id); // 读主屏当前 fb
    // SRC = 主屏尺寸,CRTC = HDMI 模式尺寸 → 硬件自动缩放
    drmModeAtomicAddProperty(req, p->id, p->srcwidthPropertyId, sw << 16);
    drmModeAtomicAddProperty(req, p->id, p->crtcwidthPropertyId, dmode.hdisplay);
    drmModeAtomicCommit(fd, req, DRM_MODE_ATOMIC_NONBLOCK, nullptr);
}
```

关键点:
- 首次提交带 `DRM_MODE_ATOMIC_ALLOW_MODESET`(CRTC 模式 + plane 一次提交;
  SetCrtc 会因 fb 小于模式被 tidss 拒 ENOSPC)
- mode blob 按请求模式创建(MYCLONE_MIRROR_MODE 覆盖时不跟随 preferred)
- EBUSY 重试(10ms×10):应用退出后立即重启时前次 flip 未完成

## 构建

```sh
source /opt/myd-ym62x-v2.0/environment-setup-aarch64-oe-linux
cd eglfs-mirror && ./build.sh
```

要点:
- 链接必须进 DT_NEEDED:`libQt6EglFsKmsGbmSupport.so` / `libQt6EglFsKmsSupport.so` /
  `libQt6KmsSupport.a` / `libQt6DeviceDiscoverySupport.a`(手动链接 +
  `-Wl,--no-as-needed`,qmake 自动链接会丢依赖)
- 改 .pro 宏后需 `rm -f mycloneintegration.o` 强制重编(build.sh 已内建)

## 部署(4 步)

```sh
# 1. 上传插件与元数据(skill 附带 assets/ 下已有编译好的产物)
scp libqeglfs-myclone-integration.so mycloneintegration.json \
    root@<板IP>:/usr/lib/plugins/egldeviceintegrations/

# 2. 停 weston(eglfs 独占 DRM master)
ssh root@<板IP> 'systemctl stop emptty'

# 3. 启动应用(无环境变量即默认直通)
ssh root@<板IP> \
  'QT_QPA_PLATFORM=eglfs QT_QPA_EGLFS_INTEGRATION=myclone /root/Demo'
```

部署后无需重启系统,重新启动应用即生效(插件随应用进程加载)。

## 运行选项

| 环境变量 | 默认 | 说明 |
|---|---|---|
| (无) | — | 硬件缩放直通(推荐):HDMI 满屏、零开销、动画全速 |
| `MYCLONE_SOFT_SCALE=1` | 关 | GPU 缩放管线(readback+软件缩放 ~18ms/帧,CPU-bound 应用动画慢一倍) |
| `MYCLONE_MIRROR_MODE=WxH@R` | HDMI preferred | 覆盖镜像目标模式,如 1280x720@60 |
| `MYCLONE_DEBUG=1` | 0 | 分段计时与调试日志 |

## 验证与调试

- **kmsprint 判据**(板上自带):HDMI 的 Plane 行显示
  `0,0 1024x600 -> 0,0 1920x1080`(SRC≠CRTC)即硬件缩放生效
- 分辨率适配:换任意 HDMI 屏/LVDS 面板零改动(全部尺寸动态读取);
  实测 640x480~1920x1080 正常;换屏后重启应用(eglfs 启动枚举)
- LVDS 高分辨率面板:1920x1080 级需 dual-link LVDS(companion OLDI,dts 配置)
- 插件内 `MYCLONE_DEBUG=1` 输出每 60 帧的分段耗时(readback/render/swap/lock/submit)

## 踩坑记录(实测有效结论)

| 问题 | 结论/解法 |
|---|---|
| weston same-as 同显 | shared-CRTC 克隆,AM62x 双 VP 不支持 |
| LVDS 侧硬件缩放 | VP1 只能 vidl1(lite),缩放必须放 HDMI 侧 |
| atomic 提交 | 同 VP 内缩放提交 tidss 接受;legacy SetPlane 有 ~23ms vsync 等待,atomic NONBLOCK ~0.4ms |
| Qt 动画变慢(CPU-bound 应用) | 镜像管线同步阻塞主屏帧回调,Qt 动画时间轴被拉长(与镜像帧率节流无关);硬件直通彻底解决 |
| 应用重启后 EBUSY | 前次 NONBLOCK flip 未完成;提交加 EBUSY 重试(10ms×10) |
| SetCrtc ENOSPC | fb(1024x600)小于 CRTC 模式(1920x1080)被拒;改 atomic ALLOW_MODESET 提交 |
| gbm lock 语义(PVR) | "取走"语义,flip 后不可再 lock;EGLImage 路线受阻 |
| PBO 异步 readback(PVR) | 跨 context map 数据不可靠(黑屏),不可用 |
| 镜像 surface 尺寸 | 必须按目标模式尺寸自建(复用镜像屏 preferred 几何会导致非默认分辨率错位) |
| mode blob | MYCLONE_MIRROR_MODE 覆盖时需按请求模式重建 blob |
| HDMI 主屏形态 | 应用窗口 screen 分配落到首个注册屏幕导致 HDMI 黑屏,已移除 |

## 故障排查

| 现象 | 排查 |
|---|---|
| HDMI 黑屏 | kmsprint 看 HDMI Plane 是否有 fb;无 → 插件未加载/镜像未触发(查 registerScreen 日志) |
| 画面半屏/错位 | 镜像 surface 尺寸与目标模式不符;确认 surface 按目标模式自建 |
| 应用动画慢一倍 | 检查是否设了 MYCLONE_SOFT_SCALE=1;直通模式应无影响 |
| 启动报 no screens | QT_QPA_EGLFS_INTEGRATION 拼写/插件加载失败;或 DRM master 被占(先停 emptty) |
| 插件不生效 | ls /usr/lib/plugins/egldeviceintegrations/ 确认 .so+json 都在;strings 确认宏编译正确 |

## 附带产物

`assets/` 目录含编译好的 `libqeglfs-myclone-integration.so`(aarch64)与
`mycloneintegration.json` —— 同版本环境(TI SDK 11.01.05.03 / Qt 6.9.2)
可直接部署免编译;换环境需用上文构建步骤重编。
