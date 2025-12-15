from __future__ import annotations

"""
简单测试当前 LLM（通过 ChatDeepSeek）是否真的产生 tool_calls。
运行方式：在项目根目录执行

    python debug_tool_calls.py
"""

from langchain_core.tools import tool
from langchain_core.messages import AIMessage
from langchain_core.language_models import BaseChatModel

from src.config import get_llm_config


@tool
def add(a: int, b: int) -> int:
    """返回 a + b 的和"""
    return a + b


def main() -> None:
    cfg = get_llm_config()
    if not cfg.api_key:
        raise RuntimeError("未检测到 API Key，请在 src/config.py 中配置你的 Key。")

    # 和 run_agent.py 保持一致：根据 base_url 自动选择适配类
    is_deepseek_official = (
        not cfg.base_url
        or "api.deepseek.com" in cfg.base_url.lower()
        or cfg.base_url == "https://api.deepseek.com"
    )

    if is_deepseek_official:
        # 尝试使用 DeepSeek 官方集成类
        try:
            from langchain_deepseek import ChatDeepSeek

            llm: BaseChatModel = ChatDeepSeek(
                model=cfg.model,
                api_key=cfg.api_key,
                temperature=0.0,
            )
        except ImportError:
            # 如果 langchain_deepseek 未安装，回退到 ChatOpenAI
            from langchain_openai import ChatOpenAI

            llm = ChatOpenAI(
                api_key=cfg.api_key,
                base_url=cfg.base_url or "https://api.deepseek.com",
                model=cfg.model,
                temperature=0.0,
            )
    else:
        # 第三方网关（如 SJTU）必须使用 OpenAI 兼容接口
        # 因为 ChatDeepSeek 通常不支持自定义 base_url
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            model=cfg.model,
            temperature=0.0,
        )

    llm_with_tools = llm.bind_tools([add])

    print("=== 发送指令，让模型必须通过工具来算 1+2 ===")
    query = "请调用 add 工具计算 1+2，只能通过工具完成，不要自己在消息里直接给出结果。"
    resp: AIMessage = llm_with_tools.invoke(query)  # type: ignore[assignment]

    print("\n=== 原始 AIMessage ===")
    print(resp)

    print("\n=== tool_calls 属性 ===")
    print(getattr(resp, "tool_calls", None))


if __name__ == "__main__":
    main()