from __future__ import annotations

import logging
import os
import socket
import subprocess
import time
from pathlib import Path

import matlab.engine  # type: ignore[import]


logger = logging.getLogger("power_agent.matlab")


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SIM_DIR = PROJECT_ROOT / "sim"

# COMSOL 的 mli 目录（用于 MATLAB mphstart）
# 建议通过环境变量覆盖，便于不同机器/版本复现：
# - COMSOL_MLI_DIR，例如：C:\Program Files\COMSOL\COMSOL62\Multiphysics\mli
COMSOL_MLI_DIR = Path(
    os.getenv("COMSOL_MLI_DIR", r"C:\Program Files\COMSOL\COMSOL62\Multiphysics\mli")
)

# COMSOL Server 可执行文件目录（用于自动启动本地 server）
# - COMSOL_BIN_DIR，例如：C:\Program Files\COMSOL\COMSOL62\Multiphysics\bin\win64
COMSOL_BIN_DIR = Path(
    os.getenv("COMSOL_BIN_DIR", r"C:\Program Files\COMSOL\COMSOL62\Multiphysics\bin\win64")
)
COMSOL_MPH_SERVER = COMSOL_BIN_DIR / "comsolmphserver.exe"
DEFAULT_COMSOL_PORT = int(os.getenv("COMSOL_PORT", "2036"))

_ENG = None


def _is_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """检查端口是否可连，用于判断本地 COMSOL Server 是否已启动。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def ensure_comsol_server(port: int = DEFAULT_COMSOL_PORT, wait_s: int = 20) -> bool:
    """
    确保本地 COMSOL Server 已启动：

    - 如果目标端口已可用，直接返回 True；
    - 如果端口不可用且 comsolmphserver.exe 存在，则尝试启动；
    - 启动后轮询等待端口就绪；超时则返回 False。
    """
    host = "localhost"
    if _is_port_open(host, port):
        logger.info("检测到 COMSOL Server 已在 %s:%s 运行。", host, port)
        return True

    if not COMSOL_MPH_SERVER.exists():
        logger.warning(
            "未找到 comsolmphserver.exe，路径预期为：%s。如安装路径不同，请修改 COMSOL_BIN_DIR。",
            COMSOL_MPH_SERVER,
        )
        return False

    try:
        logger.info("准备启动本地 COMSOL Server：%s", COMSOL_MPH_SERVER)
        subprocess.Popen(
            [str(COMSOL_MPH_SERVER), "-port", str(port)],
            cwd=str(COMSOL_BIN_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
    except Exception:  # noqa: BLE001
        logger.exception("启动 COMSOL Server 失败。")
        return False

    # 等待端口开放
    for _ in range(wait_s):
        if _is_port_open(host, port):
            logger.info("COMSOL Server 已在 %s:%s 启动。", host, port)
            return True
        time.sleep(1)

    logger.warning("等待 COMSOL Server 启动超时（%s 秒）。", wait_s)
    return False


def get_matlab_engine():
    """
    懒加载并返回一个全局共享的 MATLAB Engine 会话。

    - 第一次调用时启动 MATLAB，并将工程下的 `sim` 目录加入 MATLAB 路径；
    - 后续重复使用同一个会话，避免频繁启动 MATLAB 带来的巨大开销。
    """
    global _ENG

    if _ENG is not None:
        return _ENG

    logger.info("启动 MATLAB Engine 会话……")
    eng = matlab.engine.start_matlab()

    # 1) 将 sim 目录加入 MATLAB 搜索路径，确保可以找到 run_sim_from_step.m 等函数
    eng.addpath(str(SIM_DIR), nargout=0)
    logger.info("已将 sim 目录加入 MATLAB 路径: %s", SIM_DIR)

    # 2) 将 COMSOL 的 mli 目录加入路径，并调用 mphstart 挂接 COMSOL
    try:
        if COMSOL_MLI_DIR.exists():
            eng.addpath(str(COMSOL_MLI_DIR), nargout=0)
            logger.info("已将 COMSOL mli 目录加入 MATLAB 路径: %s", COMSOL_MLI_DIR)
            server_ready = ensure_comsol_server(DEFAULT_COMSOL_PORT)
            try:
                if server_ready:
                    eng.mphstart("localhost", float(DEFAULT_COMSOL_PORT), nargout=0)
                    logger.info(
                        "已在 MATLAB Engine 中调用 mphstart 并指定本地 COMSOL Server（端口 %s）。",
                        DEFAULT_COMSOL_PORT,
                    )
                else:
                    # 回退到默认行为，让 mphstart 自行决定（可能会再次失败，但能提示原因）
                    eng.mphstart(nargout=0)
                    logger.info("已在 MATLAB Engine 中调用默认 mphstart（未显式指定 server）。")
            except Exception:  # noqa: BLE001
                logger.exception("在 MATLAB Engine 中调用 mphstart 失败。")
        else:
            logger.warning(
                "预期的 COMSOL mli 目录不存在：%s，请检查 COMSOL 安装路径是否正确。",
                COMSOL_MLI_DIR,
            )
    except Exception:  # noqa: BLE001
        logger.exception("在 MATLAB Engine 中初始化 COMSOL LiveLink 失败。")

    _ENG = eng
    return _ENG


def shutdown_matlab_engine() -> None:
    """显式关闭全局 MATLAB Engine，会在需要时自动重新启动。"""
    global _ENG
    if _ENG is not None:
        try:
            _ENG.quit()  # type: ignore[call-arg]
        except Exception:  # noqa: BLE001
            logger.exception("关闭 MATLAB Engine 时发生异常，已忽略。")
        finally:
            _ENG = None



