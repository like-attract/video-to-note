# 在主仓库内维护独立 HarmonyOS 工程

HarmonyOS App 继续以 `harmonyos/` 作为独立可构建工程留在 VideoToNo 主仓库，而不另建重复仓库。桌面端与移动端运行时完全隔离，但共享产品语言、Prompt 规则、测试素材和架构文档；这比跨仓库同步这些契约更可靠，也不要求把代码公开推送到 GitHub。
