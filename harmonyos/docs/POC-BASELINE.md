# PoC 冻结基线

冻结日期：2026-08-12。

PoC 用于证明技术链路，不再承担正式 App 的结构扩展。已验证能力以 `docs/ROADMAP.md` 的“当前基线”为准；正式实现允许替换内部结构，但必须保持这些用户行为，除非 ADR 明确废弃。

## 已知限制

- B站会话和 LLM 配置仅在当前进程内存中。
- 使用弃用的页面 router 和全局 WorkStore。
- 页面直接依赖具体 Service，缺少 Repository 和 ViewModel 边界。
- Markdown 渲染仅覆盖基础块元素。
- Share Kit 已编译通过，但华为笔记目标需要真机验证。
- B站 Web登录与字幕接口不承诺第三方稳定性或公开上架授权。

## 构建基线

使用 DevEco Studio 当前工程 SDK、JBR 和 Hvigor 执行 `entry@default` 的 debug `assembleHap`。签名由本地 DevEco 配置提供，不提交证书、Profile 或私钥。
