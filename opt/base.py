from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import uuid


@dataclass
class OptSession:
    """
    通用的“优化会话”状态结构。

    说明：
    - 不关心具体物理问题，仅管理：
      - 算法类型（algo）
      - 当前解 / 当前目标函数值
      - 历史最优解 / 最优目标函数值
      - 变量维度与上下界
      - 迭代计数与最大迭代数
      - algo_state：留给具体算法（如 SA 的温度 T 等）存放内部状态。
    """

    session_id: str
    algo: str
    dim: int
    current_x: List[float]
    current_fx: Optional[float] = None
    best_x: Optional[List[float]] = None
    best_fx: Optional[float] = None
    lower_bounds: Optional[List[float]] = None
    upper_bounds: Optional[List[float]] = None
    iter: int = 0
    max_iter: int = 50
    algo_state: Dict[str, Any] = field(default_factory=dict)


_SESSIONS: Dict[str, OptSession] = {}


def _new_session_id() -> str:
    """生成简短的会话 ID。"""
    return uuid.uuid4().hex[:8]


def register_session(session: OptSession) -> OptSession:
    """在全局注册表中登记一个会话。"""
    _SESSIONS[session.session_id] = session
    return session


def create_session(
    algo: str,
    x0: List[float],
    lower_bounds: Optional[List[float]] = None,
    upper_bounds: Optional[List[float]] = None,
    max_iter: int = 50,
) -> OptSession:
    """
    创建一个基础优化会话（不包含具体算法状态），供各算法模块在其上扩展。
    """
    if not isinstance(x0, list) or not x0:
        raise ValueError("x0 必须是非空的一维列表。")

    dim = len(x0)

    if lower_bounds is not None and len(lower_bounds) != dim:
        raise ValueError("lower_bounds 的长度必须与 x0 相同。")
    if upper_bounds is not None and len(upper_bounds) != dim:
        raise ValueError("upper_bounds 的长度必须与 x0 相同。")

    session_id = _new_session_id()
    sess = OptSession(
        session_id=session_id,
        algo=algo,
        dim=dim,
        current_x=list(float(v) for v in x0),
        lower_bounds=list(float(v) for v in lower_bounds) if lower_bounds else None,
        upper_bounds=list(float(v) for v in upper_bounds) if upper_bounds else None,
        max_iter=int(max_iter),
    )
    return register_session(sess)


def get_session(session_id: str) -> Optional[OptSession]:
    """根据 session_id 获取会话，如果不存在则返回 None。"""
    return _SESSIONS.get(session_id)


def remove_session(session_id: str) -> None:
    """从注册表移除会话（如果存在）。"""
    _SESSIONS.pop(session_id, None)



