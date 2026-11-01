# 来源与第三方许可

本项目是桌面宠物 **云岚** 的 DSH 插件与原生补丁，派生自：

- **dsh-dafeiyu** — Copyright (c) 2026 QCYTSN — MIT License
  https://github.com/QCYTSN/dsh-dafeiyu
  本项目仅适配其 **0.1.23** 版本的 Helper 接口；仓库内 `native/upstream-LICENSE`
  保留原许可全文。

本项目自身代码同样以 **MIT License** 发布，见根目录 `LICENSE`。

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
