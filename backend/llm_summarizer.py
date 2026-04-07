import os
from pathlib import Path
from typing import List, Dict, Optional
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()


class LLMSummarizer:
    """支持 DeepSeek 及其他兼容 OpenAI API 的模型"""

    def __init__(self, model_type: str = "deepseek", **kwargs):
        """
        初始化总结器

        Args:
            model_type: 模型类型 (deepseek, openai, qwen, glm, moonshot, custom)
            **kwargs: api_key, base_url, model
        """
        self.model_type = model_type

        # 获取 API 配置
        api_key = kwargs.get("api_key") or os.getenv("DEEPSEEK_API_KEY")
        base_url = kwargs.get("base_url") or "https://api.deepseek.com"
        model = kwargs.get("model") or "deepseek-chat"

        # 如果是 DeepSeek，使用推荐的配置
        if model_type == "deepseek":
            base_url = "https://api.deepseek.com"
            model = "deepseek-chat"

        # 初始化客户端
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    async def generate_summary(self, title: str, transcription: Dict, screenshot_paths: List[Path], task_id: str = None) -> str:
        """生成视频总结"""
        # 提取转录文本
        full_text = " ".join([seg["text"] for seg in transcription["segments"]])

        # 限制文本长度（避免超过 token 限制）
        if len(full_text) > 100000:
            full_text = full_text[:100000] + "..."

        # 构建提示词
        prompt = f"""
你是一个专业的视频内容总结助手。请为以下视频生成一份结构清晰的Markdown格式总结。

## 视频标题
{title}

## 视频完整字幕
{full_text}

## 输出格式要求
请严格按照以下Markdown格式输出：

# 视频总结：《{title}》

## 📌 一句话摘要
（用1-2句话概括视频的核心内容）

## 🎯 核心观点
（提取3-5个视频的核心观点，每条用列表形式呈现）

## ⏱️ 关键时间节点
| 时间点 | 内容摘要 |
|--------|----------|
| 00:00 | 开场介绍 |
| ... | ... |

## 💡 学习要点
（列出最重要的3个知识点或技巧）

## 🎬 总结评价
（对视频质量的总体评价和推荐程度）

注意：
1. 内容要准确反映视频原意
2. 语言简洁明了，适合快速阅读
3. 使用emoji增强可读性
"""

        system_prompt = "你是一个专业的视频内容分析师，擅长总结和提炼关键信息。"

        # 调用 API
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            temperature=0.5,
            max_tokens=2000
        )

        summary = response.choices[0].message.content

        # 添加截图引用（使用相对路径，便于打包）
        if screenshot_paths and task_id:
            summary += "\n\n## 📸 视频截图预览\n\n"
            for i, path in enumerate(screenshot_paths[:10]):
                # 使用相对路径，便于打包到zip中
                filename = path.name
                summary += f"![截图{i+1}](./images/{filename})\n\n"

        return summary
