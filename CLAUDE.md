# AgentSkills 仓库工作规范

本仓库是一个 Claude Code 插件市场（`.claude-plugin/marketplace.json`），每个技能目录
是一个独立插件。这份文件面向**在本仓库新增或修改技能**的场景，人和 Claude 都适用。
只是想安装使用的话看 [README](README.md)。

## 命名与目录规则

```
Linux/<分类>/<平台>/<技能>/          ← 技能目录 = 插件根
```

- **一个平台只有一个技能时，平台目录与技能目录合并**，命名为 `<平台>-<技能>`
  （如 `stm32mp257-overlayfs`）。同一平台出现第二个技能时，再拆出平台目录
- 客户项目保留 `kehu/<客户>/` 分类层，技能目录在其下另起一层，客户名从路径直接可见
- 两个 `c7z010_20` 同名不同义，注意区分：
  - `Linux/Driver/c7z010_20/` —— **平台分类位**（当前为空占位）
  - `Linux/Driver/kehu/c7z010_20/` —— **客户分类位**
  - 二者**都只是分类目录，不是插件根**，不要在其下放 `.claude-plugin/plugin.json`
- `Linux/Driver/c7z010_20/` 与 `Linux/Driver/imx8mp/` 是空占位，靠 `.gitkeep` 存活。
  git 不跟踪空目录，**不要清理它们**

### 四处同名

`SKILL.md` 的 `name`、目录名、`plugin.json` 的 `name`、marketplace 条目 `name` 保持一致。

严格说目录名与 `name` 不同名**不会**导致加载失败（两个名字都能调用技能），但 Agent Skills
的打包路径预期二者相同，且不一致会让日志、`/skills` 列表和排障对不上号。

## 技能目录结构

```
Linux/System/common/mem-stress/          ← 插件根 = 技能目录
├── SKILL.md                             ← 唯一入口，必须叫这个名字
├── .claude-plugin/plugin.json           ← 插件清单
├── scripts/                             ← 可执行脚本
└── assets/                              ← 随插件分发的预编译产物／配置
```

`SKILL.md` 直接放插件根，**不要**再套一层 `skills/<name>/`。官方规则：插件根有 `SKILL.md`、
既无 `skills/` 目录、`plugin.json` 里也无 `skills` 键时，整个插件会加载为单个 skill。
加了 `skills` 键反而会破坏这个判定。

## 新增技能

1. 在 `Linux/<分类>/<平台>/` 下建 `<kebab-case-技能名>/`
2. 写 `SKILL.md`（frontmatter 见下）
3. 加 `.claude-plugin/plugin.json`，其 `name` 与目录名一致
4. 在 `.claude-plugin/marketplace.json` 的 `plugins` 数组加一条，`source` 用 `./` 开头的相对路径
5. 校验：`claude plugin validate --strict ./Linux/<分类>/<平台>/<技能名>`

## 硬规范

### SKILL.md frontmatter：只允许 6 个键

`name`、`description`、`license`、`allowed-tools`、`compatibility`、`metadata`

```yaml
---
name: <kebab-case，与所在目录同名，≤64 字符>
description: <≤1024 字符，不能含尖括号 <>
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
- `description` 里出现 `: `（冒号 + 空格）或 ` #` 会被 YAML 截断，需要整体加引号
- 不要存 BOM

### 禁止使用 `bin/` 目录

`bin/` 是插件根的保留目录：里面的文件会被加入 Bash 的 `PATH`（污染命令名空间），
而且 **claude.ai 和 Cowork 会拒绝安装含该目录的插件**。按内容选目录：

| 内容 | 目录 |
| --- | --- |
| 可执行脚本 | `scripts/` |
| 预编译产物、配置、图片等非可执行资源 | `assets/` |

脚本不在 `PATH` 上，调用时要写显式路径，例如 `<技能目录>/scripts/xxx.py`。

### 改内容必须 bump version

技能内容变更后 bump 对应 `plugin.json` 的 `version`，否则已安装用户拿不到更新
（设置了 `version` 的插件会被钉在该版本，直到你改动它）。

## 已知坑

- **`assets/libqeglfs-myclone-integration.so` 不要用 Git LFS** —— 插件市场拉取时不处理
  LFS，会导致插件安装失败。它是 aarch64 预编译产物，换 Qt 版本或工具链后需重新编译
- **技能目录（插件根）里不要放 `CLAUDE.md`** —— 不会被加载为上下文，且会让
  `claude plugin validate --strict` 失败。本文件放在仓库根，那里是 marketplace 根
  而非插件根，不影响校验
- **marketplace 条目不要写 `description` / `version`** —— 前者会覆盖 `plugin.json` 的值
  造成两处漂移，后者与 `plugin.json` 冲突会告警。条目只放
  `name` + `source` + `category` + `tags`
- **`plugin.json` 不要写 `homepage`** —— 不能 parse 成 URL 会导致插件加载失败；
  `repository` 不校验，写它

## 本地校验

```bash
CLAUDE=<你的 claude 可执行文件路径>

$CLAUDE plugin validate .                                     # marketplace 根
$CLAUDE plugin validate --strict .                            # 把 warning 升级为失败
$CLAUDE plugin validate ./Linux/System/common/mem-stress      # 单个插件
```

`claude plugin validate` 会连带检查 `SKILL.md` 的 frontmatter。但注意它**读不到**
`source` 指向的目录是否存在 —— 那类问题只在真正 install 时才暴露，所以改动
`marketplace.json` 后建议实际装一次验证。

本机自用的挂载方式（软链到 `~/.claude/skills/`）见 README，改完 `SKILL.md` 会话内即时生效。
