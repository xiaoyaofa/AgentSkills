# AgentSkills

嵌入式 Linux 实战技能集。每个技能是一个独立的 Claude Code 插件，可单独安装；同时遵循
[Agent Skills](https://agentskills.io) 开放标准，可被其他支持该标准的工具使用。

## 技能清单

| 技能                                                                                                       | 分类         | 平台         | 说明                                                |
| ---------------------------------------------------------------------------------------------------------- | ------------ | ------------ | --------------------------------------------------- |
| [`schematic-analysis-and-pin-multiplexing`](Linux/Driver/common/schematic-analysis-and-pin-multiplexing/) | Linux/Driver | common       | 原理图 PDF + 引脚表 → 硬件资源分析／引脚映射表     |
| [`c7z020-driver-dev`](Linux/Driver/kehu/c7z010_20/tebiandiangong/c7z020-driver-dev/)                      | Linux/Driver | c7z010_20    | MYD-C7Z020 PetaLinux 内核驱动开发 |
| [`qt-eglfs-mirror`](Linux/System/am625X/sdk11/qt-eglfs-mirror/)                                           | Linux/System | am625X/sdk11 | AM62x Qt eglfs 双屏同显                             |
| [`mem-stress`](Linux/System/common/mem-stress/)                                                           | Linux/System | common       | 内存／CPU 压测 + 温度监控                           |
| [`stm32mp257-overlayfs`](Linux/System/stm32mp257-overlayfs/)                                              | Linux/System | stm32mp257   | Yocto 只读 rootfs + overlay                         |

## 安装

**Claude Code**（通过插件市场）：

```text
/plugin marketplace add xiaoyaofa/AgentSkills
/plugin install mem-stress@xiaoyaofa-agentskills
```

把 `mem-stress` 换成上表里任意一个技能名即可单独安装。安装后可用 `/mem-stress` 调用
（完整形式 `/mem-stress:mem-stress`）。

如果当前环境没有 `/plugin` 斜杠命令（VSCode 扩展、脚本、CI 等场合），用等价的 CLI：

```bash
claude plugin marketplace add xiaoyaofa/AgentSkills
claude plugin install mem-stress@xiaoyaofa-agentskills
claude plugin list                                          # 确认已装
claude plugin uninstall mem-stress@xiaoyaofa-agentskills    # 卸载
```

> 添加市场时 Claude Code **优先尝试 SSH** 克隆（`git@github.com:...`），失败会自动降级到
> HTTPS（日志里会写 `SSH not configured, cloning via HTTPS`）。公开仓库走 HTTPS 不需要
> 任何凭据，所以**没有配 GitHub SSH key 也能装**。
>
> 若克隆因网络慢而超时，用 `CLAUDE_CODE_PLUGIN_GIT_TIMEOUT_MS` 放宽限制（毫秒，默认 120000）。

**只想本机自用？** 软链更省事 —— 改仓库里的 `SKILL.md` 会话内立即生效，不用 bump `version`：

```bash
mkdir -p ~/.claude/skills
cd /path/to/AgentSkills
find Linux -name SKILL.md -printf '%h\n' | while read -r d; do
  ln -sfn "$PWD/$d" ~/.claude/skills/"$(basename "$d")"
done
```

这些目录带 `.claude-plugin/plugin.json`，挂上去后会以 `<name>@skills-dir` 的形态作为插件加载。
新建 `~/.claude/skills/` 后需要跑一次 `/reload-skills` 才会被监视，之后增删自动生效。

**其他支持 Agent Skills 的工具**（Cursor、Codex、GitHub Copilot、Gemini CLI 等）：

```bash
npx skills add xiaoyaofa/AgentSkills --full-depth
```

> `--full-depth` 是必需的。技能分散在 `Linux/` 下的分类目录里，不在该 CLI 默认扫描的
> `skills/` 容器路径中，不加这个参数会发现不到。

> ⚠️ **需要 Node.js ≥ 22.20.0**（`skills` CLI 的要求）。Ubuntu 22.04 的 `universe`
> 源里只有 Node 12，`apt install nodejs` 解决不了，而 `/usr/bin/node` 被若干 Debian
> 的 `node-*` 包依赖，直接替换有风险。推荐用 nvm 装到用户目录：
>
> ```bash
> curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.8/install.sh | bash
> export NVM_DIR="$HOME/.nvm"; [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
> nvm install 24 && nvm alias default 24
> ```
>
> 不想动 Node 环境的话，可以跳过这一步，直接把技能目录软链到目标宿主的 skills 目录
> （见上面的软链方式），效果等价且不需要 Node。

## 目录结构

```
Linux/<分类>/<平台>/<技能>/          ← 技能目录 = 插件根
```

| 路径                             | 含义                          |
| -------------------------------- | ----------------------------- |
| `Linux/Driver/`                | 驱动相关技能                  |
| `Linux/System/`                | 系统／显示／存储相关技能      |
| `Linux/Driver/c7z010_20/`      | 平台分类位（空占位）          |
| `Linux/Driver/kehu/c7z010_20/` | 客户分类位（`kehu` = 客户） |

> ⚠️ 上面两个 `c7z010_20` 同名不同义。二者都只是分类目录，**不是插件根**，
> 不要在其下放 `.claude-plugin/plugin.json`。

每个技能目录内部：

```
Linux/System/common/mem-stress/
├── SKILL.md                    ← 技能入口，模型读这个
├── .claude-plugin/plugin.json  ← 插件清单
├── scripts/                    ← 可执行脚本
└── assets/                     ← 随插件分发的预编译产物
```

## 关于 `assets/libqeglfs-myclone-integration.so`

`qt-eglfs-mirror` 随附一个 3.29 MB 的 aarch64 预编译产物，入库是为了免去使用者的交叉
编译步骤。换 Qt 版本或工具链后需要重新编译，源码与编译步骤见该技能的 `SKILL.md`。

## 新增或修改技能

见 [CLAUDE.md](CLAUDE.md)：目录命名规则、frontmatter 字段约束、`bin/` 禁令与替代目录、
版本 bump 约定、本地校验命令。

## License

MIT，见 [LICENSE](LICENSE)。
