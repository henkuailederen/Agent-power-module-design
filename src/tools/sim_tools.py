from __future__ import annotations

import csv
import io
import json
import logging
from pathlib import Path
from typing import Any, Dict

from .matlab_session import get_matlab_engine


logger = logging.getLogger("power_agent.sim_tools")


def run_sim_from_step_safe(
    step_filename: str,
    P_igbt: float = 150.0,
    P_fwd: float = 100.0,
    h: float = 2000.0,
    run_id: str | None = None,
) -> Dict[str, Any]:
    """
    对 LLM/Agent 友好的包装：调用 MATLAB 的 run_sim_from_step 完成一次稳态热仿真，
    并将已求解的模型保存为 .mph 文件。

    参数
    ----
    step_filename:
        STEP 几何文件名，对应工程根目录下 `data/step` 目录中的文件名。
        可以带或不带 `.step` 后缀（与 MATLAB 函数保持一致）。
    P_igbt, P_fwd, h:
        与 MATLAB 端 `run_sim_from_step.m` 中定义的物理参数一致，
        单位分别为 [W]、[W]、[W/(m^2*K)]，并具有与 MATLAB 端相同的默认值。
    run_id:
        可选的仿真运行 ID。
        - 如果提供，则 .mph 文件名为 `<run_id>_thermal.mph`（覆盖模式）；
        - 如果不提供，则回退为 `<step_base>_thermal.mph`。

    返回
    ----
    一个 dict，结构类似：
    - 成功：
        {
            "success": True,
            "step_filename": "...",
            "P_igbt": 150.0,
            "P_fwd": 100.0,
            "h": 2000.0,
            "model_path": "data/sim_results/xxx_yyy_thermal.mph",
            "message": "MATLAB 热仿真已完成，并保存了 .mph 模型文件。"
        }
    - 失败：
        {
            "success": False,
            "error": "<人类可读的错误信息>"
        }

    说明
    ----
    - 本函数只负责“建模 + 求解 + 保存 .mph”，不做任何后处理；
    - 后续可以使用独立的后处理工具（例如 compute_chip_maxT_safe）
      读取返回的 `model_path`，执行芯片最高温统计等分析。
    """
    logger.info(
        "run_sim_from_step_safe 被调用：step_filename=%s, P_igbt=%s, P_fwd=%s, h=%s, run_id=%s",
        step_filename,
        P_igbt,
        P_fwd,
        h,
        run_id,
    )
    try:
        eng = get_matlab_engine()

        # 捕获 MATLAB / COMSOL 侧的 stdout/stderr，避免大量 Java 堆栈直接打印到终端。
        buf = io.StringIO()

        # MATLAB 端的 run_sim_from_step 现已定义为：
        #   [model, modelPath] = run_sim_from_step(..., run_id)
        # 其中 modelPath 是保存到磁盘的 .mph 文件路径。
        if run_id is None:
            _model, model_path = eng.run_sim_from_step(
                step_filename,
                float(P_igbt),
                float(P_fwd),
                float(h),
                nargout=2,
                stdout=buf,
                stderr=buf,
            )
        else:
            _model, model_path = eng.run_sim_from_step(
                step_filename,
                float(P_igbt),
                float(P_fwd),
                float(h),
                str(run_id),
                nargout=2,
                stdout=buf,
                stderr=buf,
            )

        matlab_output = buf.getvalue().strip()
        if matlab_output:
            logger.info("[MATLAB run_sim_from_step 输出]\n%s", matlab_output)

        logger.info("MATLAB run_sim_from_step 执行完成，模型已保存。")

        payload: Dict[str, Any] = {
            "success": True,
            "step_filename": step_filename,
            "P_igbt": float(P_igbt),
            "P_fwd": float(P_fwd),
            "h": float(h),
            "model_path": str(model_path),
             # 尽量把最终采用的 run_id 暴露给上层，便于后续复用。
            "run_id": run_id,
            "message": "MATLAB 热仿真已完成，并保存了 .mph 模型文件。",
        }
        return payload
    except Exception as exc:  # noqa: BLE001
        # 如果前面已创建 buf，则尝试记录 MATLAB 侧的文本输出
        matlab_output = ""
        try:
            if "buf" in locals():
                matlab_output = buf.getvalue().strip()
        except Exception:  # noqa: BLE001
            matlab_output = ""

        logger.exception(
            "调用 MATLAB run_sim_from_step 失败：%s。MATLAB 输出：%s",
            exc,
            matlab_output,
        )
        return {
            "success": False,
            "error": f"调用 MATLAB run_sim_from_step 失败: {exc}",
            "matlab_output": matlab_output,
        }


def compute_chip_maxT_safe(
    model_path: str,
    P_igbt: float,
    P_fwd: float,
    h: float,
    dataset: str = "dset1",
) -> Dict[str, Any]:
    """
    使用已保存的 .mph 模型文件，调用 MATLAB 的 compute_chip_maxT 进行芯片最高温度统计。

    参数
    ----
    model_path:
        run_sim_from_step 保存的 .mph 文件绝对路径或相对路径。
    P_igbt, P_fwd, h:
        与仿真时使用的物理参数保持一致（用于结果表中记录，方便追踪工况）。
    dataset:
        COMSOL 结果数据集名称，默认 'dset1'。

    返回
    ----
    一个 dict，结构类似：
        {
            "success": True,
            "model_path": "...",
            "dataset": "dset1",
            "chips": [
                {
                    "domain_id": 1,
                    "x": ...,
                    "y": ...,
                    "Tmax_C": ...,
                    "P_igbt": ...,
                    "P_fwd": ...,
                    "h": ...
                },
                ...
            ]
        }
    或在失败时：
        {
            "success": False,
            "error": "<人类可读的错误信息>"
        }
    """
    logger.info(
        "compute_chip_maxT_safe 被调用：model_path=%s, P_igbt=%s, P_fwd=%s, h=%s, dataset=%s",
        model_path,
        P_igbt,
        P_fwd,
        h,
        dataset,
    )
    try:
        eng = get_matlab_engine()

        # 1) 加载 .mph 模型
        model = eng.mphload(model_path, nargout=1)

        # 2) 调用 MATLAB 端的 compute_chip_maxT
        SiMax = eng.compute_chip_maxT(
            model,
            float(P_igbt),
            float(P_fwd),
            float(h),
            "Dataset",
            dataset,
            nargout=1,
        )

        # SiMax 是 matlab.double 类型的二维数组，形状 [nSi × 7]
        chips = []
        for row in SiMax:
            r = list(row)
            chips.append(
                {
                    "domain_id": int(r[0]),
                    "x": float(r[1]),
                    "y": float(r[2]),
                    "Tmax_C": float(r[3]),
                    "P_igbt": float(r[4]),
                    "P_fwd": float(r[5]),
                    "h": float(r[6]),
                }
            )

        # 3) 组织返回数据
        payload: Dict[str, Any] = {
            "success": True,
            "model_path": model_path,
            "dataset": dataset,
            "chips": chips,
        }

        # 4) 额外：将芯片最高温结果保存为 CSV，便于后续独立分析
        try:
            mph_path = Path(model_path)
            out_dir = mph_path.parent
            out_name = mph_path.stem + "_chip_maxT.csv"
            out_path = out_dir / out_name

            out_dir.mkdir(parents=True, exist_ok=True)

            # 采用“追加”模式：同一 CSV 文件中累积多次后处理结果，方便做历史对比。
            # 仅在文件不存在或为空时写入表头。
            write_header = not out_path.exists() or out_path.stat().st_size == 0

            with out_path.open("a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if write_header:
                    writer.writerow(
                        [
                            "DomainID",
                            "x",
                            "y",
                            "Tmax_C",
                            "P_igbt",
                            "P_fwd",
                            "h",
                        ]
                    )
                for chip in chips:
                    writer.writerow(
                        [
                            chip["domain_id"],
                            chip["x"],
                            chip["y"],
                            chip["Tmax_C"],
                            chip["P_igbt"],
                            chip["P_fwd"],
                            chip["h"],
                        ]
                    )

            payload["csv_path"] = str(out_path)
            logger.info("芯片最高温结果 CSV 已写入：%s", out_path)
        except Exception as exc:  # noqa: BLE001
            logger.exception("写入芯片最高温 CSV 文件失败（已忽略，不影响主流程）：%s", exc)
        logger.info(
            "compute_chip_maxT_safe 成功：%s",
            json.dumps(payload, ensure_ascii=False)[:500],
        )
        return payload
    except Exception as exc:  # noqa: BLE001
        logger.exception("调用 MATLAB compute_chip_maxT 失败：%s", exc)
        return {
            "success": False,
            "error": f"调用 MATLAB compute_chip_maxT 失败: {exc}",
        }

