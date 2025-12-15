from __future__ import annotations

"""
优化算法通用基础设施。

约定：
- 具体算法放在二级子目录下，例如：
  - opt/sa/core.py  实现模拟退火（Simulated Annealing）的核心逻辑；
  - 未来可以新增 opt/ga/core.py、opt/pso/core.py 等。
- 本包只提供“会话级”公共数据结构和注册表，完全不依赖 CAD / MATLAB / COMSOL。
"""

from .base import OptSession, get_session, remove_session, register_session

__all__ = ["OptSession", "get_session", "remove_session", "register_session"]


