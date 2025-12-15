"""
提供“跑智能体”的统一入口。

这里不直接写死某一个具体功能，而是：
- 负责组装 LLM、工作流（workflows）、工具列表；
- 对外暴露一个 `build_cli_agent` 函数，返回一个 LangChain Runnable，
  便于命令行 / Web API 统一调用。
"""

from __future__ import annotations

from typing import List

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_core.language_models import BaseChatModel

from src.config import get_llm_config

from .state import ConversationState
from .workflows import create_power_device_agent


class CLITokenPrinter(BaseCallbackHandler):
    """
    命令行环境下的简单流式输出回调：
    - on_llm_new_token：每产生一个 token，就立刻打印到终端；
    - on_llm_end：在一条 LLM 调用结束时补一个换行。

    注意：
    - 该回调不会区分“是否为最终回答”与“中间工具决策轮次”，
      因此在带工具调用的 Agent 中，你可能会看到多段分批打印的内容。
    """

    def on_llm_new_token(self, token: str, **kwargs) -> None:  # type: ignore[override]
        print(token, end="", flush=True)

    def on_llm_end(self, *args, **kwargs) -> None:  # type: ignore[override]
        # 为本次 LLM 调用补一个换行，避免和后续提示混在一行
        print()


def _make_history_from_state(state: ConversationState) -> List[BaseMessage]:
    """从状态对象中取出历史消息，后续方便扩展。"""
    return state.history


def build_cli_agent() -> Runnable:
    """
    构建一个适合命令行 / 简单服务调用的智能体 Runnable。

    约定输入 / 输出：
    - 输入：{"history": List[BaseMessage], "input": str}
    - 输出：AIMessage（最终回答）
    """
    cfg = get_llm_config()
    if not cfg.api_key:
        raise RuntimeError(
            "未检测到 API Key，请在 src/config.py 中配置你的 Key。"
        )

    # 根据 base_url 自动选择适配类：
    # - 如果是 DeepSeek 官方 API（或未指定 base_url），优先尝试 ChatDeepSeek
    # - 如果是第三方网关（如 SJTU），使用 ChatOpenAI + base_url（更通用）
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
                temperature=1e-5,
                streaming=True,
                callbacks=[CLITokenPrinter()],
            )
        except ImportError:
            # 如果 langchain_deepseek 未安装，回退到 ChatOpenAI
            from langchain_openai import ChatOpenAI

            llm = ChatOpenAI(
                api_key=cfg.api_key,
                base_url=cfg.base_url or "https://api.deepseek.com",
                model=cfg.model,
                temperature=1e-5,
                streaming=True,
                callbacks=[CLITokenPrinter()],
            )
    else:
        # 第三方网关（如 SJTU）必须使用 OpenAI 兼容接口
        # 因为 ChatDeepSeek 通常不支持自定义 base_url
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            model=cfg.model,
            temperature=1e-5,
             streaming=True,
             callbacks=[CLITokenPrinter()],
            extra_body={
                "do_sample": True,
                "repetition_penalty": 1.0,
                "top_k": 20,
            },
        )

    # 这里返回的可能是一个 AgentExecutor，也可能是一个通用 Runnable，
    # 只要它实现了 `.invoke()` 接口即可。
    executor = create_power_device_agent(llm)

    def invoke_fn(state: dict) -> AIMessage:
        """
        将 CLI 侧的简单字典状态映射到 AgentExecutor 期望的输入，
        并把输出封装为 AIMessage。
        """
        raw_history = state.get("history", [])
        user_input = state.get("input", "")

        if not isinstance(raw_history, list):
            raise ValueError("state['history'] 必须是消息列表（List[BaseMessage]]）。")

        # 为后续扩展保留一个 ConversationState 层
        conv_state = ConversationState(history=list(raw_history), metadata={})
        history: List[BaseMessage] = _make_history_from_state(conv_state)

        result = executor.invoke({"input": user_input, "history": history})

        # 兼容不同版本 LangChain 的返回格式：
        # - 新版 AgentExecutor 通常返回 {"output": "...", ...}
        # - 某些 Runnable 可能直接返回字符串或 AIMessage
        if isinstance(result, dict) and "output" in result:
            output_text = str(result.get("output", ""))
        else:
            output_text = str(getattr(result, "content", result))

        return AIMessage(content=output_text)

    return RunnableLambda(invoke_fn)



