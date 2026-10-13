# AgentSkills

嵌入式 Linux 实战技能集。每个技能是一个独立的 Claude Code 插件，可单独安装；同时遵循
[Agent Skills](https://agentskills.io) 开放标准，可被其他支持该标准的工具使用。

## 技能清单

| 技能 | 分类 | 平台 | 说明 |
|---|---|---|---|
| [`schematic-analysis-and-pin-multiplexing`](Linux/Driver/common/schematic-analysis-and-pin-multiplexing/) | Linux/Driver | common | 原理图 PDF + 引脚表 → 硬件资源分析／引脚映射表 |
| [`c7z020-driver-dev`](Linux/Driver/kehu/c7z010_20/tebiandiangong/c7z020-driver-dev/) | Linux/Driver | c7z010_20 | MYD-C7Z020 PetaLinux 内核驱动开发（客户：特变电工） |
| [`qt-eglfs-mirror`](Linux/System/am625X/sdk11/qt-eglfs-mirror/) | Linux/System | am625X/sdk11 | AM62x Qt eglfs 双屏同显 |
| [`mem-stress`](Linux/System/common/mem-stress/) | Linux/System | common | 内存／CPU 压测 + 温度监控 |
| [`stm32mp257-overlayfs`](Linux/System/stm32mp257-overlayfs/) | Linux/System | stm32mp257 | Yocto 只读 rootfs + overlay |

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
claude plugin list                    # 确认已装
claude plugin uninstall mem-stress@xiaoyaofa-agentskills   # 卸载
```

> 添加市场时 Claude Code 默认走 **SSH** 克隆（日志里的 `Cloning via SSH: git@github.com:...`），
> 所以本机需要有一个注册到 GitHub 的 SSH key。SSH 不可用时可以改用 git URL 形式：
> `claude plugin marketplace add https://github.com/xiaoyaofa/AgentSkills.git`
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

## 目录约定

```
Linux/<分类>/<平台>/<技能>/          ← 技能目录 = 插件根
```

| 路径 | 含义 |
|---|---|
| `Linux/Driver/` | 驱动相关技能 |
| `Linux/System/` | 系统／显示／存储相关技能 |
| `Linux/Driver/c7z010_20/` | **平台分类位**（当前为空占位） |
| `Linux/Driver/kehu/c7z010_20/` | **客户分类位**（`kehu` = 客户） |

> ⚠️ 上面两个 `c7z010_20` 同名不同义，注意区分。二者**都只是分类目录，不是插件根**，
> 不要在其下放 `.claude-plugin/plugin.json` —— 那会让同一个仓库出现两层插件，语义混乱。

命名规则：

- 一个平台只有一个技能时，**平台目录与技能目录合并**，命名为 `<平台>-<技能>`（如 `stm32mp257-overlayfs`）
- 同一平台出现第二个技能时，再拆出平台目录
- 客户项目保留 `kehu/<客户>/` 分类层，技能目录在其下另起一层（客户名直接从路径可见）
- `Linux/Driver/c7z010_20/`、`Linux/Driver/imx8mp/` 是空占位目录，靠 `.gitkeep` 存活。
  git 不跟踪空目录，**不要清理它们**

## 技能目录结构

```
Linux/System/common/mem-stress/          ← 插件根 = 技能目录
├── SKILL.md                             ← 唯一入口，必须叫这个名字
├── .claude-plugin/plugin.json           ← 插件清单
├── scripts/                             ← 可执行脚本
└── assets/                              ← 随插件分发的预编译产物／配置
```

`SKILL.md` 直接放在插件根，**不要**再套 `skills/<name>/`。依据官方规则：插件根有
`SKILL.md`、既无 `skills/` 目录、`plugin.json` 里也无 `skills` 键时，整个插件会加载为
单个 skill。加了这个键反而会破坏该判定。

## 新增技能

1. 在 `Linux/<分类>/<平台>/` 下建 `<kebab-case-技能名>/`
2. 写 `SKILL.md`（frontmatter 见下节）
3. 加 `.claude-plugin/plugin.json`，其 `name` 与目录名一致
4. 在 `.claude-plugin/marketplace.json` 的 `plugins` 数组加一条，`source` 用 `./` 开头的相对路径
5. 校验：`claude plugin validate ./Linux/<分类>/<平台>/<技能名>`

安装时技能名取自 `plugin.json` 的 `name`，而 marketplace 条目名又必须与之一致 ——
**目录名、`plugin.json` 的 `name`、marketplace 条目 `name`、`SKILL.md` 的 `name`
四处保持同名**，是最省心的做法。

## 硬规范

### SKILL.md frontmatter

只允许 6 个键：`name`、`description`、`license`、`allowed-tools`、`compatibility`、`metadata`。

```yaml
---
name: <kebab-case，与所在目录同名，≤64 字符>
description: <≤1024 字符，不能含尖括号 <>>
license: MIT
metadata:
  category: Linux/System
  platform: common
  vendor: MYIR
---
```

- **多一个键就是错误。** `when_to_use`、`argument-hint`、`model` 等在 Claude Code 里合法，
  但会让 claude.ai／Skills API 的打包路径硬报错
- `---` 必须在文件**第 1 行**。YAML 解析失败时所有字段会被**静默丢弃**且不报错 ——
  排查「技能不触发」时先查这里
- `description` 里出现 `: `（冒号+空格）或 ` #` 会被 YAML 截断，需要整体加引号
- 不要存 BOM

### 禁止使用 `bin/` 目录

`bin/` 是插件根的保留目录：里面的文件会被加入 Bash 的 `PATH`（污染命令名空间），
而且 **claude.ai 和 Cowork 会拒绝安装含该目录的插件**。按内容选目录：

| 内容 | 目录 |
|---|---|
| 可执行脚本 | `scripts/` |
| 预编译产物、配置、图片等非可执行资源 | `assets/` |

脚本不在 `PATH` 上，调用时要写显式路径，例如 `<技能目录>/scripts/xxx.py`。

### 版本

技能内容变更后**必须 bump** 对应 `plugin.json` 的 `version`，否则已安装用户拿不到更新
（设置了 `version` 的插件会被钉在该版本，直到你改动它）。

## 关于 `assets/libqeglfs-myclone-integration.so`

`qt-eglfs-mirror` 随附一个 3.29 MB 的 aarch64 预编译产物，入库是为了免去使用者的交叉
编译步骤。换 Qt 版本或工具链后需要重新编译，源码与编译步骤见该技能的 `SKILL.md`。

**不要对它使用 Git LFS** —— 插件市场拉取时不处理 LFS，会导致插件安装失败。

## 本地校验

```bash
CLAUDE=<你的 claude 可执行文件路径>

$CLAUDE plugin validate .                                     # marketplace 根
$CLAUDE plugin validate --strict .                            # 把 warning 升级为失败
$CLAUDE plugin validate ./Linux/System/common/mem-stress      # 单个插件
```

`claude plugin validate` 会连带检查 `SKILL.md` 的 frontmatter。注意它**读不到**
`source` 指向的目录是否存在 —— 那种问题只在真正 install 时才暴露，所以改动
`marketplace.json` 后建议实际装一次验证。

## License

MIT，见 [LICENSE](LICENSE)。
