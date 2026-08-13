# VideoToNo HarmonyOS

这是 VideoToNo 的 HarmonyOS NEXT 原生应用工程。核心技术链路已经通过 PoC 验证，当前进入产品化重构阶段。

1. B 站二维码登录并在本机安全保存会话；
2. 解析视频和分 P，获取已有的 AI/人工中文字幕；
3. 使用用户提供的 OpenAI-compatible LLM；
4. 生成、保存和分享 Markdown 时间轴笔记。

当前笔记页已提供基础 Markdown 预览、原文切换、复制和 HarmonyOS Share Kit 系统分享。
真机上可从系统分享面板选择华为笔记等接收文本的应用；由于没有面向普通三方应用的公开接口，
本项目不会绕过用户确认直接写入华为笔记私有数据。

视频输入支持纯 BV 号、含 BV 的完整链接/分享文本，以及手机端常见的 `b23.tv` 短链接。

阶段 2 已完成：B站会话与各 LLM Profile 的 API Key 使用 HUKS 加密，Profile 元数据使用 Preferences，
笔记使用 ArkData RDB；启动可恢复会话与配置，Cookie 失效会回到扫码页，设置页可退出登录或
经二次确认清除全部本机数据。

阶段 3 的第一项已经完成：启动首页为本地笔记库，可离线浏览，并支持搜索、排序、打开、
重命名和删除；新建笔记时才要求 B站登录。系统分享接收入口也已接入，支持从分享记录识别
BV、`b23.tv` 和 `bilibili.com`，未登录时会先扫码并保留待处理链接；分享面板端到端行为仍需
HarmonyOS 真机上的 B站 App 验证。

LLM 配置已移入设置页，可维护多个命名 Profile、默认项、服务商/URL/模型和思考强度；旧单配置会
幂等迁移为 Profile。新建笔记采用三步流程：选择视频/字幕/Profile/笔记风格、独立生成进度、阅读与
分享。生成任务支持取消、失败后原地重试或返回修改选项；快速切换分 P 不会让旧响应覆盖当前选择。

第一版不包含视频下载、Whisper、离线转写或自建服务器。

## 产品化文档

- [新窗口开发交接](docs/HANDOFF.md)
- [执行路线图](docs/ROADMAP.md)
- [领域词汇](CONTEXT.md)
- [目标架构与迁移规则](docs/ARCHITECTURE.md)
- [PoC 冻结基线](docs/POC-BASELINE.md)
- [架构决策记录](docs/adr/)

## DevEco Studio 首次打开

1. 用 DevEco Studio 打开本目录 `harmonyos/`。
2. 如果 IDE 提示工程工具链不完整，推荐新建一个当前版本的 **Empty Ability / ArkTS / Stage / Phone** 临时工程。
3. 将临时工程生成的 `hvigor/`、Hvigor wrapper、`oh-package.json5` 中的工具链依赖版本，以及 IDE 要求的 SDK 配置合并到本目录。不要猜测或手工固定旧版本。
4. 在 Project Structure 中选择当前安装的 HarmonyOS SDK。
5. 让 IDE 生成调试签名；仓库不提交证书、Profile 或私钥。
6. 创建并启动 Phone 模拟器，然后运行 `entry` 模块。

当前首页应显示本地笔记库；没有历史数据时显示空状态，并可通过“新建笔记”进入完整生成流程。

## 目录职责

- `pages/`：登录、首页、处理过程和笔记页面。
- `services/`：B 站登录、字幕 API、WBI 签名、字幕转换和 LLM 调用。
- `models/`：跨页面与服务共享的稳定数据模型。
- `src/test/`：无需设备的纯逻辑测试。
- `src/ohosTest/`：需要模拟器或真机的集成/UI 测试。

## 安全边界

- 禁止将 Cookie、`SESSDATA`、refresh token 或 LLM API Key 输出到日志。
- 敏感值最终必须经 HUKS 加密后落盘；Preferences 仅保存非敏感设置。
- B 站 Web 字幕接口不是对第三方承诺稳定性的正式开放 API，PoC 成功不等于具备公开上架授权。
