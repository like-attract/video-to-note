import os
from typing import Dict, List, Optional
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()


class BaseLLM:
    """大模型基类"""
    def __init__(self, api_key: str, base_url: str, model: str):
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    async def generate(self, prompt: str, system_prompt: str = None) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt or "你是一个专业的视频内容分析师。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.5,
            max_tokens=2000
        )
        return response.choices[0].message.content


class DeepSeekLLM(BaseLLM):
    def __init__(self, api_key: str = None, base_url: str = None, model: str = None):
        super().__init__(
            api_key=api_key or os.getenv("DEEPSEEK_API_KEY"),
            base_url=base_url or "https://api.deepseek.com",
            model=model or os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        )


class OpenAILLM(BaseLLM):
    def __init__(self, api_key: str = None, base_url: str = None, model: str = None):
        super().__init__(
            api_key=api_key or os.getenv("OPENAI_API_KEY"),
            base_url=base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            model=model or "gpt-4o-mini"
        )


class QwenLLM(BaseLLM):
    def __init__(self, api_key: str = None, base_url: str = None, model: str = None):
        super().__init__(
            api_key=api_key or os.getenv("QWEN_API_KEY"),
            base_url=base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1",
            model=model or os.getenv("QWEN_MODEL", "qwen-plus")
        )


class ZhipuLLM(BaseLLM):
    def __init__(self, api_key: str = None, base_url: str = None, model: str = None):
        super().__init__(
            api_key=api_key or os.getenv("ZHIPU_API_KEY"),
            base_url=base_url or "https://open.bigmodel.cn/api/paas/v4",
            model=model or os.getenv("ZHIPU_MODEL", "glm-4-flash")
        )


class MoonshotLLM(BaseLLM):
    def __init__(self, api_key: str = None, base_url: str = None, model: str = None):
        super().__init__(
            api_key=api_key or os.getenv("MOONSHOT_API_KEY"),
            base_url=base_url or "https://api.moonshot.cn/v1",
            model=model or os.getenv("MOONSHOT_MODEL", "moonshot-v1-8k")
        )


class CustomLLM(BaseLLM):
    def __init__(self, api_key: str, base_url: str, model: str):
        super().__init__(api_key, base_url, model)


class LLMFactory:
    """大模型工厂"""

    @staticmethod
    def create(model_type: str, **kwargs) -> BaseLLM:
        model_type = model_type.lower()

        # 提取通用参数
        api_key = kwargs.get("api_key")
        base_url = kwargs.get("base_url")
        model = kwargs.get("model")

        if model_type == "deepseek":
            return DeepSeekLLM(api_key=api_key, base_url=base_url, model=model)
        elif model_type == "openai":
            return OpenAILLM(api_key=api_key, base_url=base_url, model=model or "gpt-4o-mini")
        elif model_type == "openai_gpt4":
            return OpenAILLM(api_key=api_key, base_url=base_url, model=model or "gpt-4o")
        elif model_type == "openai_gpt35":
            return OpenAILLM(api_key=api_key, base_url=base_url, model=model or "gpt-3.5-turbo")
        elif model_type == "qwen":
            return QwenLLM(api_key=api_key, base_url=base_url, model=model)
        elif model_type == "glm":
            return ZhipuLLM(api_key=api_key, base_url=base_url, model=model)
        elif model_type == "moonshot":
            return MoonshotLLM(api_key=api_key, base_url=base_url, model=model)
        elif model_type == "custom":
            return CustomLLM(
                api_key=kwargs.get("api_key"),
                base_url=kwargs.get("base_url"),
                model=kwargs.get("model")
            )
        else:
            return DeepSeekLLM(api_key=api_key, base_url=base_url, model=model)