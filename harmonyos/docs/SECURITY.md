# 本地安全与数据边界

- B站 Cookie、refresh token 与用户选择保存的 LLM API Key 使用同一 HUKS AES-256-GCM 密钥加密。
- 每次写入生成新的 12 字节 nonce；Preferences 只保存版本、nonce、密文和认证标签，不保存密钥材料。
- LLM API Key 默认仅驻留当前进程。只有用户打开“使用 HUKS 在本机安全保存”后才写入密文；关闭后删除对应密文。
- 非敏感的服务商、Base URL、模型、笔记风格和思考强度保存到 Preferences。
- Markdown 笔记保存到 ArkData RDB。待生成或失败任务会在独立 RDB 中保存字幕快照、Profile ID、
  笔记风格与运行状态，以支持离页继续和中断恢复；不会保存 API Key、Cookie 或 refresh token。
  删除任务/清除全部数据会删除这些字幕快照。Cookie、API Key、字幕正文与模型响应不得写入普通日志。
- 笔记同时保存生成时的服务商、模型、笔记风格和思考设置快照，用于本地展示；不保存 Base URL、
  Profile ID 或 API Key。
- 退出 B站登录只删除 B站会话，不删除用户的模型设置和笔记。后续“清除全部数据”会单独确认并清理数据库、Preferences 和 HUKS alias。
- 设置页的“清除全部本机数据”经二次确认后删除 B站会话、LLM 设置、加密 API Key、RDB 笔记并销毁 HUKS alias。
- B站接口返回登录失效时立即清除旧会话并回到扫码页；首版不实现逆向 Cookie 静默续期。
- 临时 HUKS/ArkData 读取失败不会静默删除笔记或普通设置；仅当 HUKS 密钥已丢失、旧密文确定不可恢复时清理敏感密文。
- HUKS 不可用时禁止降级为明文保存。模拟器只验证功能，TEE 安全能力必须在 HarmonyOS NEXT 真机复核。
