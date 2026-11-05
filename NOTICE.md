# 来源与第三方许可

本项目是桌面宠物 **云岚** 的 DSH 插件与原生补丁，派生自：

- **dsh-dafeiyu** — Copyright (c) 2026 QCYTSN — MIT License
  https://github.com/QCYTSN/dsh-dafeiyu
  本项目仅适配其 **0.1.23** 版本的 Helper 接口；仓库内 `native/upstream-LICENSE`
  保留原许可全文。

本项目自身代码同样以 **MIT License** 发布，见根目录 `LICENSE`。

`native/runtime/` 与 `native/helper.py` 中部分代码派生自上游 0.1.23，按上游 MIT 许可保留
Copyright (c) 2026 QCYTSN 与许可全文；修改与再分发时请同时保留根目录 `LICENSE` 和
`native/upstream-LICENSE`。

## 角色素材不在本仓库

本仓库**不分发**上游的任何角色帧、光标或音效：

- **dsh-pet 角色形象与 109 段动画**：来自
  [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet)，固定提交
  `3c99b1bb4d1bcb831880e8871718c50b64af0d9b`；角色素材适用 **CC BY-NC-SA 4.0**
  （署名、非商业、相同方式共享），不是 MIT。dsh-pet **代码**本身为 MIT。
- **dsh-dafeiyu 的旧 BigFish 帧**：不在 MIT 范围，适用其
  [ASSET_LICENSE.md](https://github.com/QCYTSN/dsh-dafeiyu/blob/main/ASSET_LICENSE.md)。
- Chromium BSD-3-Clause 光标和程序生成提示音由你安装的 `dsh-dafeiyu` 提供。

运行本补丁前请自行确认已合法获得上游包。本仓库公开截图只包含自绘 UI 与合成测试数据，不含上述角色素材。

**自包含发行版例外**：`tools/package-self-contained.ps1` 生成的发行包会把
`runtime/prepared-3c99b1b` 中的 dsh-pet 角色动画一并分发。这些素材不是 MIT，
按 [ASSET_LICENSE.md](ASSET_LICENSE.md) 的 **CC BY-NC-SA 4.0**（署名、非商业、
相同方式共享）处理；发行包必须保留该文件、上游链接和固定提交。

本项目是非官方粉丝作品，与 DeepSeek 无隶属或背书关系；相关名称、商标与角色权利归
各自权利人。

## 情感数据集

`native/emotion_dataset_update.py` 可选地读取公开许可的中文情感数据集：

- **zzhdbw/Simplified_Chinese_Multi-Emotion_Dialogue_Dataset** — Apache-2.0，修订 `d0d148b971f09c8269bc7afa045e37802a73eaa9`
- 翻译自 **Johnson8187/Chinese_Multi-Emotion_Dialogue_Dataset** — MIT，修订 `f3854f9489071a4dc0bb69f2366d95095837ce68`

仅用于本地生成哈希特征；原始文本不分发、不写入本仓库，也不加入日志。

## 本地语义模型（可选，不随仓库分发）

`native/embedding_backend.py` 可选下载 **BAAI/bge-small-zh-v1.5**（MIT License，
FlagEmbedding）的量化 ONNX 权重与词表，经 ModelScope 上的 `Xenova` 转换仓库固定
revision 获取：

- 固定 revision：`313386ad7ffad360a4870fd1910f8ecaf1a05151`
- 本地目录：`%LOCALAPPDATA%\dsh-dafeiyu\models\bge-small-zh-v1.5`
- 首次下载约 24 MB；之后离线推理，记忆文本不发送到任何服务器
- 模型权重不提交到本仓库；未下载时自动回退到本地哈希检索

## 公开爬虫来源

`native/public_crawler.py` 只读取一个固定公开文件：

- **chinese-poetry/chinese-poetry** — MIT License，Copyright (c) 2016 JackeyGao；
  文件 `诗经/shijing.json` 中的《诗经》文本为公版，经 GitHub 内容 API 读取。
  每次只取一首诗的标题与前两行，不抓取仓库其他内容、不接收用户提供的 URL。

## 原生目录中的第三方资料

| 文件 | 内容 | 许可 |
| --- | --- | --- |
| `native/third_party/Unicode-CLDR-48-LICENSE.txt` | Unicode License V3：内置表情/情绪词典与 Unicode CLDR 数据 | Unicode License V3 |
| `native/third_party/Unicode-CLDR-LICENSE.txt` | Unicode CLDR 规范版权与使用声明 | Unicode Terms of Use |
| `native/third_party/Countries-LICENSE.txt` | 公开国家资料（Annexare Studio） | MIT |
| `native/third_party/MIME-DB-LICENSE.txt` | MIME DB 公开媒体类型资料 | MIT |
| `native/third_party/Stack-Overflow-ATTRIBUTION.txt` | Stack Overflow 审阅问答中文摘要 | CC BY-SA 4.0 |

内置的少量表情符号与中文情绪词来自 Unicode CLDR 注释数据，按 **Unicode License V3** 随附声明；运行时的完整 CLDR 注释、国家、单位、MIME 和 Stack Overflow 资料都由文档中列出的官方公开接口按需读取，不在本仓库分发。Stack Overflow 的三条中文摘要属于 CC BY-SA 4.0 内容：转载或再分发这些摘要时须保留署名、许可与来源链接；本项目的其余代码仍按 MIT 发布。

## 运行时与服务

- 本机语音使用 Windows 预装 SAPI 中文识别/播报，不随仓库分发语音模型。
- 可选实时语音使用 OpenAI Realtime 兼容接口，需要使用者自己的账号与密钥；
  本项目不包含、也不代发任何密钥或服务账户。
- 公开资料读取只访问文档中列出的固定官方接口；本项目不分发其数据副本。
