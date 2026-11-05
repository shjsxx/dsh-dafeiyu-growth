# 素材与第三方授权

本仓库的**源代码**按根目录 [LICENSE](LICENSE) 的 MIT License 发布。角色帧、动画、字体、图标等**视觉素材不是统一 MIT**，分发时必须同时保留本文件和上游署名。

## 角色与动画素材

云岚使用的 109 段桌面动画来自 [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet)，固定提交：

`3c99b1bb4d1bcb831880e8871718c50b64af0d9b`

上游 README 对角色形象声明：

- **CC BY-NC-SA 4.0**：署名、非商业、相同方式共享
- 原始声明：<https://www.bilibili.com/opus/1247156596114456581>
- 许可见：<https://creativecommons.org/licenses/by-nc-sa/4.0/>
- 角色为社区同人形象，不是 DeepSeek 官方角色
- 原作者：明月的画师 <https://space.bilibili.com/4456176>；DeepSeek 女仆鲸设计 <https://space.bilibili.com/4168597>

`dsh-dafeiyu` 的 `ASSET_LICENSE.md` 对其当前导入素材另有 MIT 表述；该表述与上述上游声明存在冲突。自包含发行版按更严格的 **CC BY-NC-SA 4.0** 处理，不据此主张商业使用权。

本仓库的公开截图与 README 配图只包含自绘 UI、图标和合成测试数据，不含上述角色帧；只有自包含发行包才会随运行时一并分发角色动画。新增的运动模型截图同样只是合成 UI。

## 再分发要求

如果你分发包含素材的自包含版本，必须：

1. 保留本文件、根目录 `LICENSE`、`NOTICE.md` 和上游许可全文；
2. 保留 dsh-pet 的 GitHub 链接、固定提交和作者署名；
3. 不使用“官方”“DeepSeek 官方角色”等表述；
4. 不将包含素材的整包标为单一 MIT；
5. 不用于商业销售、付费墙或商业服务；
6. 对修改后的素材按 CC BY-NC-SA 4.0 相同方式共享。

商业使用需要分别取得上游权利人的书面许可；本项目不代为授权。

## 其他组件

| 组件 | 授权 | 说明 |
| --- | --- | --- |
| 本插件源代码 | MIT | 根目录 `LICENSE` |
| dsh-pet 代码 | MIT | Copyright (c) 2026 PC2005-cloud |
| dsh-dafeiyu 代码 | MIT | Copyright (c) 2026 QCYTSN；`native/upstream-LICENSE` |
| Electron | MIT | 发行包需附带 Electron 的 LICENSE |
| Chromium 光标 | BSD 3-Clause | 上游 `assets/cursors/LICENSE` |
| 内置字体 | 各自字体许可 | 发行包保留字体许可 |
| 本地语音 | Windows SAPI | 不随包分发语音模型 |
| BGE 语义模型 | 模型自身的 MIT/转换仓库许可 | 可选下载，不默认随包 |

## 不包含的内容

本仓库不包含用户数据、云端 API Key、聊天记录、模型权重或商业字体。自包含发行包构建脚本只复制公开运行时和素材，不读取用户目录中的密钥。

如对授权有疑问，请先联系上游权利人；在澄清前按非商业方式使用。
