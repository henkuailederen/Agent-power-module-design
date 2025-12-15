from __future__ import annotations

"""
模拟退火（Simulated Annealing, SA）算法实现。

本模块只负责 SA 的数学细节：
- 会话初始化（温度、降温策略等）；
- 候选解的产生（邻域扰动）；
- 接受准则与状态更新。

不依赖 CAD / MATLAB / COMSOL，只在数值空间中工作。
"""

from .core import (
    sa_create_session_safe,
    sa_get_state_safe,
    sa_propose_candidate_safe,
    sa_step_safe,
)

__all__ = [
    "sa_create_session_safe",
    "sa_get_state_safe",
    "sa_propose_candidate_safe",
    "sa_step_safe",
]


