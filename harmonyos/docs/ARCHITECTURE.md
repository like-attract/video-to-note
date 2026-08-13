# HarmonyOS 工程结构

当前代码来自 PoC，产品化期间采用渐进迁移：新代码进入目标目录，旧 `pages/services/stores/models` 在调用方全部迁移并通过测试后再删除。

```text
entry/src/main/ets/
├── app/                    应用入口、Navigation 路由和全局依赖装配
├── core/                   错误、网络、日志、资源与通用安全边界
├── domain/                 领域模型、Repository 接口和用例
├── data/                   B站/LLM远端数据源、HUKS、Preferences、RDB及Repository实现
├── features/
│   ├── auth/               B站登录和会话管理
│   ├── capture/            分享链接接收、视频和字幕选择
│   ├── generation/         LLM配置、生成任务和进度
│   ├── notes/              笔记库、阅读、编辑、导出和分享
│   └── settings/           账号、模型、数据和隐私设置
└── legacy/                 仅在必要时临时容纳尚未迁移的PoC代码
```

## 依赖方向

```text
features → domain ← data
    ↓         ↑       ↑
   app      core ─────┘
```

- `domain` 不导入 ArkUI、HTTP、数据库或 HUKS。
- `features` 不直接创建 HTTP 请求或拼接 B站接口地址。
- `data` 实现 `domain` 定义的 Repository，但不依赖页面。
- `app` 负责装配实现、路由和生命周期，不承载业务规则。
- `core` 只放跨领域的技术基础，不成为杂物目录。

## 迁移顺序

1. 定义统一错误和 Repository 契约。
2. 用适配器包住现有 `BiliLoginService`、`BiliApiService` 和 `LlmService`。
3. 将页面状态迁入各 feature 的 ViewModel。
4. 建立 Navigation 根路由并逐页迁移。
5. 阶段 2 接入真实持久化后替换内存 Store。
6. 所有调用方迁移完成后删除旧目录。
