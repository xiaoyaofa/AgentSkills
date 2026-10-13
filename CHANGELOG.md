# Changelog

本文件记录仓库结构变更与各技能版本。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [1.0.0] - 2026-10-13

仓库格式规范化，5 个技能版本统一升级为 `1.0.0`。

### Added

- `.claude-plugin/marketplace.json`：仓库成为可安装的插件市场（市场名 `xiaoyaofa-agentskills`），
  5 个技能各自作为独立插件，可单独安装
- 每个技能目录新增 `.claude-plugin/plugin.json`
- `README.md`、`LICENSE`（MIT）、`CHANGELOG.md`、`.gitignore`
- `SKILL.md` frontmatter 补齐 `license` 与 `metadata`
  （承载 `category`/`platform`/`soc`/`board`/`vendor`/`customer` 等分类信息）

### Changed

- 技能入口文件 `skill-<name>.md` → `SKILL.md`，满足标准 skill 发现机制
- 目录名统一 kebab-case 并与 frontmatter `name` 对齐：
  - `Schematic_Analysis_and_Pin_Multiplexing` → `schematic-analysis-and-pin-multiplexing`
  - `mem_stress` → `mem-stress`
  - `stm32mp257` → `stm32mp257-overlayfs`
- `bin/` 按内容语义拆分：可执行脚本 → `scripts/`，预编译产物 → `assets/`
- `tebiandiangong` 保留为客户分类层，其下新建插件根 `c7z020-driver-dev`
- 统一 `description` 标点风格（半角括号／冒号／引号 → 全角与「」）；
  技术词 `WIC/flashlayout`、`pdftotext`、`300dpi` 保留半角斜杠

### Fixed

- `schematic-analysis-and-pin-multiplexing`：正文写「见 `bin/` —— 6 个」与实际
  7 个脚本不符，已改为 7 个；正文中 10 处 `bin/` 路径引用同步更新，避免断链
- `c7z020-driver-dev`：`description` 补上客户名，避免客户标识只存在于目录路径里

### Removed

- 冗余的 `Linux/Driver/.gitkeep`、`Linux/System/.gitkeep`（父目录已有真实子项）

### 后续约定

- 技能内容变更后必须 bump 对应 `plugin.json` 的 `version`，否则已安装用户拿不到更新
