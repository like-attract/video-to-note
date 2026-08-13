# VideoToNo HarmonyOS 开发交接

更新日期：2026-08-13

本文供新的 Codex 窗口直接接手 HarmonyOS 子项目。开始工作前先阅读本文件、
`harmonyos/README.md`、`harmonyos/docs/ROADMAP.md` 和 `harmonyos/docs/ARCHITECTURE.md`。

## 1. 产品目标与当前边界

第一版专注 B站：用户扫码登录后获取视频已有的 AI/人工字幕，调用用户配置的
OpenAI-compatible LLM，生成 Markdown 时间轴笔记并保存在本机。

- 支持纯 BV、B站完整链接、分享文本和 `b23.tv` 短链接。
- 支持单 P、多 P、字幕轨道选择和动态 WBI 签名。
- 不下载视频，不集成 Whisper，不运行离线模型，不依赖自建服务器。
- LLM API Key 由用户提供。优先保证完整功能与输出质量，不要为了少量 Token 成本主动截断能力。
- 笔记通过 HarmonyOS Share Kit 让用户选择华为笔记等接收方；不要尝试直接写入华为笔记私有数据。

## 2. 当前已经实现

### B站链路

- 二维码生成、轮询登录、Cookie 多来源解析和 HUKS 会话保存。
- 登录失效后回到扫码页，并保留待处理的视频输入。
- 视频信息、分 P、字幕列表、字幕下载和时间轴解析。
- `b23.tv` 短链接解析。
- 分 P 请求有代次保护，旧响应不会覆盖用户最新选择。

### LLM 与生成链路

- OpenAI-compatible 接口，服务商预设、模型下拉、自定义 URL/模型。
- DeepSeek、OpenAI、GLM、Qwen、Moonshot 和 custom 预设。
- 三档笔记风格：详细+点评、忠实详细、精炼。
- 思考强度：auto/off/low/medium/high/max。
- 使用桌面端 `backend/llm_summarizer.py` 的笔记策略，并已加入长字幕分块归并、
  时间轴覆盖检查、进度回调、取消和失败重试。
- DeepSeek 思考模式不要因节省成本而关闭；此前验证关闭思考会明显降低质量。
- LLM 请求已改为模型默认参数优先：不发送 `temperature`/`top_p`，思考设置只保留
  `auto/off/high/max`；`auto` 不附加私有推理参数。DeepSeek 官方及可识别的中转模型仍使用
  专项 thinking 协议和充足输出额度，避免推理耗尽正文预算。

### 多 LLM Profile（最新完成）

- LLM 长期配置已移入设置页，不再作为新建流程中的独立页面。
- 可保存多个命名 Profile，设置默认项，编辑和删除。
- Profile 包含服务商、Base URL、模型、思考强度；笔记风格属于每次生成选择。
- Profile 元数据存 Preferences，API Key 按 Profile 使用 HUKS 加密。
- 编辑已有 Profile 时 API Key 留空表示保留原密钥。
- 删除默认 Profile 时自动回退到剩余第一项；最多保存 12 项。
- 旧单配置可幂等迁移为 `legacy-default`，迁移顺序避免中断后重复损坏。
- GenerationRepository 只保存本次生成工作区，不再覆盖长期配置。

当前新建笔记流程是三步：

1. 视频、分 P、字幕、LLM Profile 和笔记风格；
2. 独立生成进度、取消与重试；
3. 阅读、保存、复制和系统分享。

### 本地笔记与应用框架

- 首页是本地笔记库，可离线搜索、排序、打开、重命名和删除。
- 笔记使用 ArkData RDB，现为 v2 schema，并处理迁移中断重入。
- 系统分享接收入口已注册，可解析 BV、`b23.tv` 与 `bilibili.com` 文本。
- Navigation 路由、domain/data/core 分层、统一错误映射和网络取消已建立。

## 3. 最新验证状态

已通过：

- `hvigor test` 本地单元测试，BUILD SUCCESSFUL。
- 主应用 `assembleHap`，ArkTS 编译和 HAP 打包成功。
- `entry@ohosTest assembleHap` 设备测试 HAP 编译成功。
- 最新 HAP 已覆盖安装并在 API 24 Phone 模拟器启动。
- 设置页、多 Profile 编辑器、服务商预设 URL、思考强度和模型下拉完成视觉验收。
- 模型下拉初始空白问题已修复，当前显示 `DeepSeek V4 Flash`。

新增测试：

- `MemoryLlmProfileRepository.test.ets`：默认项、本次风格合成、删除回退、编辑保留密钥。
- `Persistence.test.ets`：Profile 元数据和 HUKS API Key 跨 Repository 实例恢复。

本轮用户已确认：

- 真实 LLM 三步生成链路已经验收，可作为当前功能基线。

尚未确认：

- CLI 执行单个 Hypium 设备测试会一直不返回；设备测试代码和 HAP 已编译，但不能据此宣称运行通过。
- 模拟器目前没有可迁移的旧 LLM API Key，因此本轮尚未做新的真实 LLM API 短测。

模拟器视觉证据：

- `harmonyos/artifacts/videotono-settings-profiles.jpeg`
- `harmonyos/artifacts/videotono-profile-editor-final.jpeg`

## 4. 人工 GUI 协作规则（重要）

该项目必须在 DevEco Studio 和 HarmonyOS 模拟器中真实运行。页面交互、扫码、输入密钥、
下拉选择和 Hypium GUI 运行通常由用户操作更快、更准确。

后续 Codex 应遵循：

1. 代码修改、静态检查、构建和日志分析由 Codex 完成。
2. 遇到 GUI 才能可靠验证的环节，立即给用户最短点击清单、预期结果和需回传内容。
3. 不要连续多次用 HDC 坐标点击猜测页面，也不要让无输出的 CLI Runner 长时间占用。
4. 用户只需回传截图、具体错误文案或 DevEco Build/Run/Test 输出；不要让用户发送 API Key。
5. 如果一次人工操作即可确认问题，优先请求用户操作，不要反复自动化尝试。

推荐请求格式：

```text
请在 DevEco/模拟器完成以下 3 步：
1. ...
2. ...
3. ...
预期：...
请回传：截图或第一条红色错误。
```

## 5. 新窗口首先需要用户完成的验证

### A. 运行 Profile/HUKS 设备测试

在 DevEco Studio 打开：

`entry/src/ohosTest/ets/test/Persistence.test.ets`

点击 `restoresLlmProfilesAndEncryptedApiKey` 左侧运行按钮。

预期：`1 passed`。若失败，只需回传第一条 Assertion/Error 和堆栈首个项目文件位置。

### B. 在应用中添加真实 LLM Profile

模拟器中进入：

`我的笔记 → 设置 → LLM 配置 → 添加`

选择服务商或自定义接口，确认 URL/模型，输入 API Key，选择思考强度并保存。
API Key 只在模拟器内输入，不要发到聊天或日志。

### C. 完整在线链路验收

进入“新建视频笔记”，使用一个有字幕的 B站链接：

1. 获取视频与字幕；
2. 选择刚保存的 Profile 和笔记风格；
3. 直接生成；
4. 检查进度、Markdown 正文、时间轴覆盖和笔记库保存。

如果生成失败，回传页面完整错误文案和所选 provider/model/reasoning（不含 API Key）。

## 6. 下一步执行顺序

1. 完成上述 Profile/HUKS 设备测试，修复发现的问题。
2. 验证 Profile 新增、编辑、默认、删除后重启应用仍可恢复。
3. 完善 Markdown 渲染、时间戳交互、文件导出和 Share Kit 体验。
4. 在真机验证 B站分享进入、扫码登录、华为笔记接收和 HUKS。
5. 完成深色模式、字体缩放、无障碍、多尺寸和错误/空状态。
6. 最后处理 AGC、正式包名、签名、隐私材料和应用市场发布。

## 7. 关键代码位置

- `entry/src/main/ets/data/local/PersistentLlmProfileRepository.ets`
- `entry/src/main/ets/domain/generation/LlmProfileRepository.ets`
- `entry/src/main/ets/data/memory/MemoryLlmProfileRepository.ets`
- `entry/src/main/ets/pages/SettingsPage.ets`
- `entry/src/main/ets/pages/SubtitlePage.ets`
- `entry/src/main/ets/pages/ProcessingPage.ets`
- `entry/src/main/ets/services/LlmService.ets`
- `entry/src/main/ets/models/LlmPresets.ets`
- `entry/src/test/MemoryLlmProfileRepository.test.ets`
- `entry/src/ohosTest/ets/test/Persistence.test.ets`

## 8. 本地工具与注意事项

- DevEco Studio：`D:\Program Files\Huawei\DevEco Studio`
- HDC：`D:\Program Files\Huawei\DevEco Studio\sdk\default\openharmony\toolchains\hdc.exe`
- 最近模拟器 target：`127.0.0.1:5555`，每次应先重新执行 `hdc list targets` 确认。
- 主 HAP：`entry/build/default/outputs/default/entry-default-unsigned.hap`
- 测试 HAP：`entry/build/default/outputs/ohosTest/entry-ohosTest-unsigned.hap`
- 当前编译仍有既存的 ArkTS “Function may throw exceptions” 警告和 NotePage deprecated 警告，
  不影响本轮构建成功，后续应专项清理。
- 不记录或输出 Cookie、SESSDATA、refresh token、API Key。
- 不要修改无关的根目录 `.gitignore` 或 `scripts/read_bili_cookie.py`。

## 9. LLM 兼容模式与任务中心决策（2026-08-13）

- Profile 兼容模式为 `auto | generic | deepseek | openai | glm | qwen`。
- `auto` 优先跟随已选服务商；自定义接口会继续用模型名和 Base URL 识别 DeepSeek。
- 中转使用不透明别名时必须显式选协议族；`generic` 表示标准 OpenAI-compatible，绝不附加厂商私有 thinking 参数。
- 思考强度仅保留 `auto | off | high | max`；HarmonyOS 请求不发送 `temperature` 或 `top_p`。
- Profile 文档已升级为 v2；旧 v1 或 legacy 配置缺少兼容模式时恢复为 `auto`，HUKS 密钥命名不变。
- 后台任务中心采用持久化、应用级单任务 runner；页面退出不取消任务，进程被系统终止后下次启动恢复。详见 `docs/adr/0004-persistent-generation-task-center.md`。

## 10. UI 方向（2026-08-13）

- 用户已选定设计稿 B「青澈」。品牌色取自 `sources/icon.png`，主色为深青与薄荷青。
- `resources/base/element/color.json` 与 `resources/dark/element/color.json` 提供同名语义色，应用跟随系统明暗模式。
- 首页已落地青澈 Hero、主操作、搜索框和卡片边框；登录、处理、阅读、设置等页面统一使用青澈资源与卡片边界。
- 二维码始终使用独立黑白色板，避免暗色模式影响扫码可靠性。
- 启动窗口改用正式应用图标，并有明暗两套启动背景，避免暗色冷启动白闪。
- 完整四项底部导航与任务中心运行时入口需在持久化 `GenerationTask` 实现后一起接入，当前不展示虚假的后台能力。
- 当前 Git 状态中整个 `harmonyos/` 仍是未跟踪目录，尚未提交或推送。
