# 自包含插件：DSH-only 安装

目标：用户已经安装 DSH 桌面版时，只安装云岚养成插件即可使用全部**本地**功能，不再安装 `dsh-dafeiyu`、Python、Node 或单独准备 Electron。

## 运行结构

```text
dsh-dafeiyu-growth/
├─ src/index.js                 DSH 插件入口
├─ src/native-supervisor.js     启动/守护原生 helper
├─ desktop/                     Electron 宿主、菜单、托盘
├─ runtime/
│  ├─ electron/                 Electron 运行时
│  ├─ prepared-3c99b1b/         pinned dsh-pet renderer + 素材
│  ├─ helper/                   onedir 原生 helper
│  └─ manifest.json             固定提交、helper SHA-256、路径
├─ ASSET_LICENSE.md             角色素材 CC BY-NC-SA 4.0
├─ NOTICE.md                    上游署名
└─ cordis.patch.yml             nativeBundle: true
```

插件加载时：

1. `src/native-supervisor.js` 检查 `runtime/` 是否完整；
2. 生成 `~/.dsh/yunlan-renderer/active.json`；
3. 启动 `runtime/helper/dsh-dafeiyu-helper.exe --external-renderer --renderer-config ...`；
4. helper 启动 `runtime/electron` 和 `desktop/main.cjs`，显示桌宠、托盘和成长卡；
5. 插件 dispose 时停止 helper；helper 断开控制管道时 Electron 自动退出。

没有 `runtime/` 时 supervisor 不启动，仍回退到现有 `dsh-dafeiyu` 流程，开发环境不会被破坏。

## 构建

先准备 onedir helper：

```powershell
.\native\build-onedir.ps1
```

再把本机已验证的 runtime 和 Electron 打进插件：

```powershell
.\tools\package-self-contained.ps1 `
  -RuntimeSource "$env:USERPROFILE\.dsh\yunlan-dsh-pet-runtime" `
  -HelperSource ".\native\dist-onedir\dsh-dafeiyu-helper" `
  -Out ".\dist\self-contained" `
  -Zip
```

`-DryRun` 只打印体积和路径，不复制文件。

如果只需要轻量插件包（不含 368MB Electron），可把 runtime/Electron/helper 打成固定 release 资产，首次安装后运行：

```powershell
.\tools\fetch-runtime.ps1 `
  -Url "https://example.invalid/yunlan-runtime-3c99b1b.zip" `
  -Sha256 "<release 中公布的 SHA256>" `
  -Force
```

脚本会校验 SHA-256、拒绝路径逃逸，并检查 `electron`、`prepared-3c99b1b`、`helper` 三项后才安装；下载失败不会留下半包。插件 supervisor 检测到完整 `runtime/` 后自动启动。

可选本地语义模型：

```powershell
.\tools\package-self-contained.ps1 -IncludeModels ...
```

这会把约 24MB 的 BGE 模型放进 `runtime/models/bge-small-zh-v1.5`；supervisor 启动 helper 时自动设置 `DSH_DAFEIYU_EMBEDDING_DIR`，不再从旧的 dsh-dafeiyu 目录读取。未包含模型时仍可运行，记忆检索回退到本地哈希。

输出目录可以直接作为 DSH 插件目录安装，也可以压缩后在 DSH 插件管理页选择本地包。安装后重启 DSH。

> 如果机器上仍装有 `dsh-dafeiyu`，请先停用/卸载它，或至少关闭其原生桌宠，避免两个 helper 同时出现。自包含包使用 `nativeBundle: true`；开发仓库默认 `auto`，没有 `runtime/` 时不会抢现有 helper。

## 体积

| 组件 | 约 |
| --- | ---: |
| Electron | 368 MB |
| pinned renderer + 素材 | 85 MB |
| onedir helper | 197 MB |
| 插件代码 | 数 MB |
| 总计 | **约 650 MB** |

如果不想让插件包这么大，可以只保留 helper + runtime，首次启动时从固定 URL 下载 Electron 并校验 SHA-256；这仍只需安装一个插件，但首次需要联网。

## 为什么使用 onedir

当前 onefile helper 每次启动都要把 PySide6 解压到临时目录，Windows 杀毒/文件锁可能导致 `MSVCP140.dll` 提取失败。自包含版本使用 onedir：文件只解压一次，启动更快、失败面更小。

## 授权

- 插件代码：MIT，保留 `LICENSE`。
- 角色/动画素材：上游声明 CC BY-NC-SA 4.0，非商业、署名、相同方式共享。
- Electron：MIT；Chromium 光标：BSD 3-Clause。
- dsh-pet 代码：MIT；dsh-dafeiyu 代码：MIT。

自包含包不能标成单一 MIT。`tools/package-self-contained.ps1` 会把发行版 `package.json` 的 license 改为 `SEE LICENSE IN ASSET_LICENSE.md`，并保留 `ASSET_LICENSE.md`、`NOTICE.md`、`native/upstream-LICENSE`。商业使用需另行取得权利人许可。

## 仍然需要用户自备

- 已安装的 DSH 桌面版（插件运行在 DSH 内）。
- 云端聊天的接口地址和 Key。
- Realtime 语音的账号和 Key。
- 网络连接（公开知识抓取、首次模型下载）。
- 可选的 BGE 语义模型约 24 MB；不内置时回退本地哈希检索。

本地成长、桌宠、动作、记忆、动作偏好、训练、菜单、Windows SAPI 语音不依赖外部服务。

## 验证清单

- [x] `node --test test/native-supervisor.test.js`（7 项）
- [x] `native\build-onedir.ps1` 成功，onedir 目录含 `_internal`
- [x] `package-self-contained.ps1` 成功生成 866 文件、652.8MB staged 包
- [x] staged 包隔离端到端：真实 onedir helper + staged Electron 启动，9 秒后 helper 仍 running，marker 正确；stop 后 helper/Electron 退出、marker 清理
- [x] supervisor 保留 stdin 管道（helper 以关闭 stdin 为退出信号；不能用 `ignore`）
- [ ] 干净 Windows 用户：只装 DSH + 插件，确认桌宠、成长卡、托盘、记忆
- [ ] 云端/语音不填 Key 时明确报未配置，不伪装成功
- [ ] 发布包含全部授权文件，不含用户数据或 Key
