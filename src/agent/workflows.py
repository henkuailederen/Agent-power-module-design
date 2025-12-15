"""
在这里定义不同“工作流”，例如：
- 纯对话问答
- CAD 设计 → JSON 生成 → STEP 编译
- STEP → MATLAB 仿真 → 结果分析

注意：
- 为了兼容不同版本的 langchain，本文件不再依赖 `langchain.agents` 模块，
  而是使用 `llm.bind_tools` + 手写一轮简单的“工具调用循环”。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import logging
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableLambda

from .prompts import BASE_SYSTEM_PROMPT
from .tools import get_all_tools


logger = logging.getLogger("power_agent.agent")


def create_power_device_agent(
    llm: BaseChatModel,
    system_prompt: Optional[str] = None,
) -> Any:
    """
    创建一个“功率器件 CAD + 仿真”方向的通用 Agent。

    返回值是一个 Runnable，可接受如下输入：
    - {"history": List[BaseMessage], "input": str}
    并返回一个字典：{"output": str}
    """

    tools = get_all_tools()
    tools_by_name = {t.name: t for t in tools}

    sys_prompt = system_prompt or BASE_SYSTEM_PROMPT

    # 将工具“绑定”到 LLM 上，让它可以产生 tool_calls
    llm_with_tools = llm.bind_tools(tools)

    def _agent_call(state: Dict[str, Any]) -> Dict[str, str]:
        """
        一次对话轮次的核心逻辑（支持多轮工具调用）。

        流程：
        1. 先用 llm_with_tools 让模型决定是否调用工具；
        2. 如有 tool_calls，则逐个执行工具并把结果封装成 ToolMessage，追加到 messages；
        3. 重复 1-2，直到某一轮不再产生 tool_calls，或达到最大轮数上限。
        """
        raw_history = state.get("history", [])
        user_input = state.get("input", "")

        if not isinstance(raw_history, list):
            raise ValueError("state['history'] 必须是消息列表（List[BaseMessage]）。")

        messages: List[BaseMessage] = [SystemMessage(content=sys_prompt)]
        messages.extend(raw_history)
        messages.append(HumanMessage(content=str(user_input)))

        logger.info(
            "Agent 收到输入，history_len=%d, user_input=%s",
            len(raw_history),
            user_input,
        )

        max_tool_rounds = 500

        for round_idx in range(max_tool_rounds):
            logger.info("开始第 %d 轮 LLM 调用（带工具）", round_idx + 1)
            ai_reply: AIMessage = llm_with_tools.invoke(messages)  # type: ignore[assignment]
            tool_calls = getattr(ai_reply, "tool_calls", None)

            logger.info(
                "LLM 回复：has_tool_calls=%s, content_preview=%s",
                bool(tool_calls),
                str(ai_reply.content)[:200],
            )

            # 如果本轮没有触发任何工具，直接返回语言模型的回答
            if not tool_calls:
                logger.info("本轮对话未触发任何工具调用，直接返回自然语言回答。")
                return {"output": str(ai_reply.content)}

            # 有工具调用时：逐个执行工具，并将结果封装成 ToolMessage 回传给下一轮 LLM
            tool_messages: List[ToolMessage] = []
            for call in tool_calls:
                # 兼容不同版本的 tool_call 结构（可能是 dataclass，也可能是 dict）
                name = getattr(call, "name", None) or getattr(call, "tool", None)
                if isinstance(call, dict):
                    name = call.get("name") or call.get("tool")
                    args = call.get("args", {})
                    call_id = call.get("id", "")
                else:
                    args = getattr(call, "args", {}) or {}
                    call_id = getattr(call, "id", "")

                logger.info(
                    "检测到工具调用：name=%s, args=%s, id=%s", name, args, call_id
                )

                tool = tools_by_name.get(str(name))
                if tool is None:
                    logger.warning(
                        "未找到名称为 %s 的工具，将返回错误信息提示可用工具列表。", name
                    )
                    available_tool_names = ", ".join(sorted(tools_by_name.keys()))
                    error_message = (
                        f"工具调用错误：名为 '{name}' 的工具在当前系统中不存在。\n"
                        f"当前可用工具为：{available_tool_names}。\n"
                        "请仅从上述工具中选择重新发起工具调用，或改用自然语言说明你的需求。"
                    )
                    tool_messages.append(
                        ToolMessage(
                            content=error_message,
                            tool_call_id=str(call_id),
                        )
                    )
                    continue

                try:
                    result = tool.invoke(args)
                    logger.info(
                        "工具 %s 调用成功，结果预览：%s", tool.name, str(result)[:200]
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("工具 %s 执行过程中发生异常。", tool.name)
                    raise

                tool_messages.append(
                    ToolMessage(
                        content=str(result),
                        tool_call_id=str(call_id),
                    )
                )

            # 将本轮 LLM 回复和工具结果加入对话历史，进入下一轮决策
            messages.append(ai_reply)
            messages.extend(tool_messages)

        # 超过最大轮数仍未得到最终自然语言回答时，做一次退路调用并给出提示
        logger.warning(
            "已达到最大工具调用轮数（%d），将不再继续调用工具，直接让 LLM 给出总结性回答。",
            max_tool_rounds,
        )
        final_reply: AIMessage = llm.invoke(messages)  # type: ignore[assignment]
        return {
            "output": (
                "（警告：本次需要的工具调用轮次过多，已强制结束工具调用并让模型直接给出总结。）\n"
                f"{str(final_reply.content)}"
            )
        }

    return RunnableLambda(_agent_call)



