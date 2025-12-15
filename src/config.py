import os
from dataclasses import dataclass

from dotenv import load_dotenv


# 加载 .env 文件中的环境变量
load_dotenv()


@dataclass
class LLMConfig:
    """大模型相关配置。

    说明：
    - 现在默认对接的是 DeepSeek 官方 API，而不是 SJTU 网关；
    - 你可以：
      - 直接在下面的默认值里写入真实的 DeepSeek API Key；
      - 或者通过环境变量 OPENAI_API_KEY / OPENAI_MODEL / OPENAI_BASE_URL 覆盖。
    """

    # 开源项目中不要在代码里硬编码任何 API Key。
    # 请通过环境变量或项目根目录的 .env 文件提供 OPENAI_API_KEY。
    api_key: str = os.getenv("OPENAI_API_KEY", "")

    # DeepSeek 官方 API 的默认 base_url（如无特殊需要不要改）
    # Chat 接口路径 `/chat/completions` 由客户端自己拼接，不要写进 base_url 里。
    base_url: str = os.getenv(
        "OPENAI_BASE_URL",
        "https://api.deepseek.com",
    )

    # 默认模型：deepseek-chat 一般支持对话和工具调用；
    # 如果你要用其它 DeepSeek 模型，可以在这里修改，
    # 但请注意官方文档中提到 deepseek-reasoner 等模型当前不支持工具调用。
    model: str = os.getenv("OPENAI_MODEL", "deepseek-chat")


def get_llm_config() -> LLMConfig:
    cfg = LLMConfig()
    if not cfg.api_key:
        # 这里不直接抛异常，而是留给上层友好提示
        pass
    return cfg


