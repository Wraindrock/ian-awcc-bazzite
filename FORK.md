# 关于本仓库 / About this repository

## 上游 / Upstream

本仓库 **`Grant-Felix/ian-awcc-bazzite`** 派生自：

> **[AndersonDinizDev/g15-control-center](https://github.com/AndersonDinizDev/g15-control-center)**
> 面向 Linux 的 Dell G15 原生控制中心（葡萄牙语项目）

原作者的 **22 个提交与 `v1.0.0` / `v1.0.1` / `v1.0.2` 标签完整保留**在本仓库历史中，
本仓库的改动是在 `0cd889c docs: ajuste na documentacao` 之上叠加的一个提交。

所有原始代码的版权与署名归原作者 **Anderson Diniz** 所有（MIT License）。

## 本仓库的改动

| 方面 | 说明 |
|---|---|
| 界面汉化 | 窗口、卡片、标签页、按钮、状态标签、托盘菜单、全部弹窗改为简体中文 |
| 终端与桌面项 | `install.sh` / `uninstall.sh` 提示汉化；`.desktop` 增加 `[zh_CN]` 字段 |
| 暗色/浅色主题 | 新增 `src/g15_theme.py` 集中调色板，默认暗色，可切换并持久化 |
| 只读降级模式 | daemon 未运行时直读 `hwmon`，仍可查看温度/转速，写控件自动禁用 |
| 中文文档 | 新增 [README.zh_CN.md](README.zh_CN.md) |

## 与上游同步

```bash
git remote add upstream https://github.com/AndersonDinizDev/g15-control-center.git  # 若尚未配置
git fetch upstream
git merge upstream/main
```

本仓库已按此约定配置远端：

- `origin` → `https://github.com/Grant-Felix/ian-awcc-bazzite.git`
- `upstream` → `https://github.com/AndersonDinizDev/g15-control-center.git`

## 相关仓库

- [`Grant-Felix/g15-control-center`](https://github.com/Grant-Felix/g15-control-center) —— 通过 GitHub fork 功能建立的镜像分支，带有 GitHub 原生的 fork 关联标识，内容与上游 `main` 一致。日常开发以 `ian-awcc-bazzite` 为准。

## 为什么不是 GitHub 原生 fork？

`ian-awcc-bazzite` 是**独立创建**的公开仓库（`gh repo create --source=.`），因此 GitHub 的
`isFork` 标识为 `false`，网页上不会显示 "forked from ..." 横幅。
GitHub 不允许事后把已有仓库转为原生 fork，故通过以下方式明确关联：

1. 仓库 description 注明 fork 来源；
2. 仓库 homepage 指向上游；
3. 本文件（`FORK.md`）与两份 README 均写明上游出处；
4. 额外建立 `Grant-Felix/g15-control-center` 作为带原生 fork 标识的对照分支。
