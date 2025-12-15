from __future__ import annotations

import csv
import json
import logging
import math
import random
from pathlib import Path
from typing import Any, Dict, List, Optional

from opt.base import OptSession, create_session, get_session


logger = logging.getLogger("power_agent.opt.sa")

# 日志目录：用于持久化保存优化历史与最优结果
PROJECT_ROOT = Path(__file__).resolve().parents[2]
OPT_DATA_DIR = PROJECT_ROOT / "data" / "opt"


def _ensure_opt_dir() -> None:
    """确保优化数据目录存在。"""
    OPT_DATA_DIR.mkdir(parents=True, exist_ok=True)


def _session_history_path(session_id: str) -> Path:
    return OPT_DATA_DIR / f"{session_id}_history.csv"


def _session_best_path(session_id: str) -> Path:
    return OPT_DATA_DIR / f"{session_id}_best.json"


def _init_session_log(sess: OptSession) -> None:
    """
    为新的优化会话写入一份元数据文件，便于后续追踪。
    """
    try:
        _ensure_opt_dir()
        meta_path = OPT_DATA_DIR / f"{sess.session_id}_meta.json"
        payload: Dict[str, Any] = {
            "session_id": sess.session_id,
            "algo": sess.algo,
            "dim": sess.dim,
            "max_iter": sess.max_iter,
            "lower_bounds": sess.lower_bounds,
            "upper_bounds": sess.upper_bounds,
            "algo_state": dict(sess.algo_state),
        }
        meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("已初始化优化会话元数据日志：%s", meta_path)
    except Exception:  # noqa: BLE001
        # 日志写入失败不应影响主流程
        logger.exception("初始化优化会话日志时出错（已忽略）。")


def _append_step_log(
    sess: OptSession,
    current_fx: float,
    candidate_x: List[float],
    candidate_fx: float,
    accepted: bool,
    reason: str,
    done: bool,
) -> None:
    """将单步 SA 迭代结果追加写入 CSV。"""
    try:
        _ensure_opt_dir()
        path = _session_history_path(sess.session_id)
        write_header = not path.exists() or path.stat().st_size == 0
        with path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if write_header:
                writer.writerow(
                    [
                        "iter",
                        "accepted",
                        "reason",
                        "current_fx_in",
                        "candidate_fx",
                        "current_fx_out",
                        "best_fx",
                        "T",
                        "done",
                        "current_x",
                        "candidate_x",
                        "best_x",
                    ]
                )
            writer.writerow(
                [
                    sess.iter,
                    int(accepted),
                    reason,
                    float(current_fx),
                    float(candidate_fx),
                    float(sess.current_fx) if sess.current_fx is not None else None,
                    float(sess.best_fx) if sess.best_fx is not None else None,
                    float(sess.algo_state.get("T", 0.0)),
                    int(done),
                    json.dumps(sess.current_x, ensure_ascii=False),
                    json.dumps(candidate_x, ensure_ascii=False),
                    json.dumps(sess.best_x, ensure_ascii=False)
                    if sess.best_x is not None
                    else None,
                ]
            )
    except Exception:  # noqa: BLE001
        logger.exception("写入优化迭代历史 CSV 时出错（已忽略）。")


def _write_best_log(sess: OptSession) -> None:
    """在会话结束时写入一份汇总的最优结果 JSON。"""
    try:
        _ensure_opt_dir()
        path = _session_best_path(sess.session_id)
        payload: Dict[str, Any] = {
            "session_id": sess.session_id,
            "algo": sess.algo,
            "dim": sess.dim,
            "max_iter": sess.max_iter,
            "final_iter": sess.iter,
            "best_x": sess.best_x,
            "best_fx": sess.best_fx,
            "lower_bounds": sess.lower_bounds,
            "upper_bounds": sess.upper_bounds,
            "algo_state": dict(sess.algo_state),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("已写入优化会话最优结果日志：%s", path)
    except Exception:  # noqa: BLE001
        logger.exception("写入优化最优结果 JSON 时出错（已忽略）。")


def _clip_to_bounds(
    x: List[float],
    lower_bounds: Optional[List[float]],
    upper_bounds: Optional[List[float]],
) -> List[float]:
    """将候选解裁剪到给定的上下界范围内。"""
    if lower_bounds is None and upper_bounds is None:
        return x

    clipped: List[float] = []
    for i, v in enumerate(x):
        lo = lower_bounds[i] if lower_bounds is not None else None
        hi = upper_bounds[i] if upper_bounds is not None else None
        if lo is not None and v < lo:
            v = lo
        if hi is not None and v > hi:
            v = hi
        clipped.append(v)
    return clipped


def sa_create_session_safe(
    x0: List[float],
    lower_bounds: Optional[List[float]] = None,
    upper_bounds: Optional[List[float]] = None,
    max_iter: int = 50,
    T_init: float = 1.0,
    T_min: float = 1e-3,
    alpha: float = 0.9,
    neighbor_scale: float = 0.1,
) -> Dict[str, Any]:
    """
    对 Agent 友好的 SA 会话创建接口。

    说明：
    - 仅在“数学空间”中创建会话，不做任何建模 / 仿真；
    - x0 / bounds 由 LLM 根据具体物理问题自行决定；
    - algo_state 中记录 SA 特有参数：T, T_min, alpha, neighbor_scale。
    """
    try:
        sess: OptSession = create_session(
            algo="sa",
            x0=x0,
            lower_bounds=lower_bounds,
            upper_bounds=upper_bounds,
            max_iter=max_iter,
        )
        sess.algo_state.update(
            {
                "T": float(T_init),
                "T_min": float(T_min),
                "alpha": float(alpha),
                "neighbor_scale": float(neighbor_scale),
            }
        )
        # 初始化会话日志（不会影响主流程）
        _init_session_log(sess)
        logger.info(
            "SA 会话创建成功：session_id=%s, dim=%d, max_iter=%d",
            sess.session_id,
            sess.dim,
            sess.max_iter,
        )
        return {
            "success": True,
            "session_id": sess.session_id,
            "algo": sess.algo,
            "dim": sess.dim,
            "max_iter": sess.max_iter,
            "state": {
                "current_x": sess.current_x,
                "current_fx": sess.current_fx,
                "best_x": sess.best_x,
                "best_fx": sess.best_fx,
                "T": sess.algo_state["T"],
                "T_min": sess.algo_state["T_min"],
                "alpha": sess.algo_state["alpha"],
                "neighbor_scale": sess.algo_state["neighbor_scale"],
                "iter": sess.iter,
            },
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("SA 会话创建失败：%s", exc)
        return {
            "success": False,
            "error": f"SA 会话创建失败: {exc}",
        }


def sa_get_state_safe(session_id: str) -> Dict[str, Any]:
    """安全查询 SA 会话当前状态。"""
    sess = get_session(session_id)
    if sess is None:
        return {
            "success": False,
            "error": f"未找到 session_id={session_id} 对应的优化会话。",
        }

    if sess.algo != "sa":
        return {
            "success": False,
            "error": f"会话 {session_id} 的算法类型为 {sess.algo!r}，当前仅支持 'sa'。",
        }

    state = {
        "session_id": sess.session_id,
        "algo": sess.algo,
        "dim": sess.dim,
        "iter": sess.iter,
        "max_iter": sess.max_iter,
        "current_x": sess.current_x,
        "current_fx": sess.current_fx,
        "best_x": sess.best_x,
        "best_fx": sess.best_fx,
        "T": sess.algo_state.get("T"),
        "T_min": sess.algo_state.get("T_min"),
        "alpha": sess.algo_state.get("alpha"),
        "neighbor_scale": sess.algo_state.get("neighbor_scale"),
        "lower_bounds": sess.lower_bounds,
        "upper_bounds": sess.upper_bounds,
    }
    return {"success": True, "state": state}


def sa_propose_candidate_safe(session_id: str) -> Dict[str, Any]:
    """
    基于当前解与 SA 邻域尺度，生成一个新的候选解 x'。

    说明：
    - 这里只做数值扰动和边界裁剪；
    - 不做任何物理单位换算或 CAD/仿真调用。
    """
    sess = get_session(session_id)
    if sess is None:
        return {
            "success": False,
            "error": f"未找到 session_id={session_id} 对应的优化会话。",
        }
    if sess.algo != "sa":
        return {
            "success": False,
            "error": f"会话 {session_id} 的算法类型为 {sess.algo!r}，当前仅支持 'sa'。",
        }

    neighbor_scale = float(sess.algo_state.get("neighbor_scale", 0.1))

    current = sess.current_x
    lb = sess.lower_bounds
    ub = sess.upper_bounds

    candidate: List[float] = []
    for i, v in enumerate(current):
        # 根据有无上下界决定扰动尺度
        if lb is not None and ub is not None:
            span = float(ub[i] - lb[i])
            step = neighbor_scale * span
        else:
            step = neighbor_scale * max(1.0, abs(v))

        dv = random.uniform(-step, step)
        candidate.append(v + dv)

    candidate = _clip_to_bounds(candidate, lb, ub)

    logger.info(
        "SA propose_candidate：session_id=%s, iter=%d, current=%s, candidate=%s",
        sess.session_id,
        sess.iter,
        current,
        candidate,
    )
    return {
        "success": True,
        "session_id": sess.session_id,
        "current_x": current,
        "candidate_x": candidate,
    }


def sa_step_safe(
    session_id: str,
    current_fx: float,
    candidate_x: List[float],
    candidate_fx: float,
) -> Dict[str, Any]:
    """
    单步 SA 状态更新（不计算目标函数，只根据传入的 fx 做接受/拒绝决策）。

    说明：
    - 由 LLM 在外部完成“x → 目标值”的评估（可通过 CAD / 仿真 / 查表等方式）；
    - 本函数只依据 SA 规则更新：
      - 是否接受候选；
      - 当前解 / 最优解；
      - 温度 T 与迭代计数；
      - 是否达到终止条件。
    """
    sess = get_session(session_id)
    if sess is None:
        return {
            "success": False,
            "error": f"未找到 session_id={session_id} 对应的优化会话。",
        }
    if sess.algo != "sa":
        return {
            "success": False,
            "error": f"会话 {session_id} 的算法类型为 {sess.algo!r}，当前仅支持 'sa'。",
        }

    T = float(sess.algo_state.get("T", 1.0))
    T_min = float(sess.algo_state.get("T_min", 1e-3))
    alpha = float(sess.algo_state.get("alpha", 0.9))

    # 如果当前会话还没有 current_fx，则把传入的 current_fx 作为起点
    if sess.current_fx is None:
        sess.current_fx = float(current_fx)
        sess.current_x = list(float(v) for v in sess.current_x)

    delta = float(candidate_fx - current_fx)
    accept = False
    reason = ""

    if delta <= 0:
        # 更优解，必然接受
        accept = True
        reason = "candidate_better"
    elif T > 0:
        prob = math.exp(-delta / T)
        r = random.random()
        accept = r < prob
        reason = f"candidate_worse_prob_accept (p={prob:.3g}, r={r:.3g})"
    else:
        accept = False
        reason = "temperature_zero"

    if accept:
        sess.current_x = list(float(v) for v in candidate_x)
        sess.current_fx = float(candidate_fx)
    else:
        sess.current_fx = float(current_fx)

    # 更新最优解
    if sess.best_fx is None or (accept and candidate_fx < sess.best_fx):
        sess.best_x = list(float(v) for v in sess.current_x)
        sess.best_fx = float(sess.current_fx)

    # 迭代与降温
    sess.iter += 1
    T = max(T_min, T * alpha)
    sess.algo_state["T"] = T

    done = sess.iter >= sess.max_iter or T <= T_min

    # 记录单步迭代到 CSV（失败时仅记录日志，不打断主流程）
    _append_step_log(
        sess,
        current_fx=current_fx,
        candidate_x=candidate_x,
        candidate_fx=candidate_fx,
        accepted=accept,
        reason=reason,
        done=done,
    )

    # 如已达到终止条件，则同步写入最优结果 JSON
    if done:
        _write_best_log(sess)

    logger.info(
        "SA step：session_id=%s, iter=%d, accept=%s, reason=%s, "
        "current_fx=%s, candidate_fx=%s, best_fx=%s, T=%.4g, done=%s",
        sess.session_id,
        sess.iter,
        accept,
        reason,
        current_fx,
        candidate_fx,
        sess.best_fx,
        T,
        done,
    )

    return {
        "success": True,
        "session_id": sess.session_id,
        "accepted": accept,
        "reason": reason,
        "current_x": sess.current_x,
        "current_fx": sess.current_fx,
        "best_x": sess.best_x,
        "best_fx": sess.best_fx,
        "T": T,
        "iter": sess.iter,
        "max_iter": sess.max_iter,
        "done": done,
    }



