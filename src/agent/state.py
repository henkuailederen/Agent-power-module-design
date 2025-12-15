"""
定义智能体在一次对话 / 一次任务中的“状态结构”。

目前先给出一个最小可用的会话状态，后续可以在这里加入：
- 当前选中的仿真工况 profile
- 当前正在设计 / 优化的功率模块 ID
- 中间产生的 JSON / STEP / 仿真结果的路径等
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from langchain_core.messages import BaseMessage


@dataclass
class ConversationState:
    """
    多轮对话的基础状态。

    目前只存历史消息和一个可扩展的元数据字典。
    后续可以在这里加入：
    - 当前选中的器件拓扑、封装版本
    - 最近一次生成的 JSON / STEP / 仿真结果 ID
    """

    history: List[BaseMessage] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)



