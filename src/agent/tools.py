"""
把 `src/tools` 目录中的底层函数封装成 LangChain Tool，并提供统一的工具列表。

"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

import json
import logging
from pathlib import Path

from langchain_core.tools import BaseTool, tool

from src.tools.cad_build import build_step_model_safe
from src.tools.template_merge import build_device_config_from_template_safe
from src.tools.sim_tools import compute_chip_maxT_safe, run_sim_from_step_safe
from src.tools.json_precheck import run_precheck as run_precheck_safe
from opt.sa import (
    sa_create_session_safe,
    sa_get_state_safe,
    sa_propose_candidate_safe,
    sa_step_safe,
)


logger = logging.getLogger("power_agent.tools")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
JSON_DIR = DATA_DIR / "json"


@tool("build_step_model")
def build_step_model_tool(device_config: Union[str, Dict[str, Any]]) -> str:
    """
    根据功率器件的 JSON 配置生成 3D CAD 模型（STEP 文件）。

    使用说明（请严格遵守）：
    1. 你必须先构造一个完整的器件 JSON 描述（可以是 JSON 字符串或字典），
       其字段名、嵌套层级、数组/列表结构要与 `cad/reference/*.json` 示例完全一致，
       包括但不限于：
       - 基本几何参数：ceramics_width, ceramics_length, ceramics_thickness,
         upper_copper_thickness, lower_copper_thickness, fillet_radius 等；
       - 芯片尺寸参数：igbt_width, igbt_length, fwd_width, fwd_length；
       - 间隙/边界：cu2cu_margin, cu2ceramics_margin, dbc2dbc_margin, substrate_edge_margin；
       - 工艺参数：substrate_solder_thickness, die_solder_thickness, die_thickness；
       - 键合线数量：igbt_bondwires, fwd_bondwires；
       - gate_design: 定义 Gate 铜线的起点(start)和相对移动路径(moves)，
         最终在模板中通过 `generate_sketch` / `create_upper_Cu_base` / `create_labeled_gates`
         决定 Gate 区域的几何形状和位置；
       - cutting_design: 定义用于切割上铜层的路径序列，
         在模板中传入 `cut_copper`，决定上铜被划分为多少个 Zone_i 以及每个区的轮廓；
       - igbt_positions / fwd_positions: 以陶瓷板宽/长为 1 的归一化坐标，
         在模板中被乘以 width_ceramics / length_ceramics 转换成实际位置，
         决定每个 IGBT/FWD 芯片在单块 DBC 上的放置位置；
       - igbt_rotations / fwd_rotations: 每个芯片的旋转角度(度)，
         在 `process_igbt_configs` / `process_chip_connections` 中控制芯片朝向和焊线方向；
       - dbc_count / dbc_rotations: 模块包含的 DBC 个数及其绕 Z 轴旋转角度，
         在模板中通过 `generate_positions_DBC` 和 `translate_assembly` 决定 DBC 在基板上的布置；
       - module_connections / dbc_connections: 用于描述 Zone 与 Zone / 芯片与 Zone 之间的电连接拓扑，
         模板中通过 `connection_module` / `connection_DBC`、`create_dbc_connections` 等函数
         生成 DBC 间和芯片与铜区之间的键合线/连接件。
       这些字段会经过严格的 Pydantic 校验（参考 `src/tools/cad_schema.py` 中的 DeviceConfig 模型），
       如果格式或类型不符合要求，工具会返回详细的错误信息。
    2. 工具会执行以下步骤：
       - 将该 JSON 写入 `data/json/`；
       - 调用 `cad/merge.py` 与 `cad/template.j2` 生成 CAD Python 脚本到 `data/py/`；
       - 运行该脚本并使用 cadquery 导出 STEP 文件到 `data/step/` 目录。
    3. 返回值：
       - 成功时：返回生成的 STEP 文件的绝对路径（字符串）；
       - 失败时：返回包含错误原因和堆栈信息的文本，你应阅读并据此修改 JSON 后再次调用。
    """
    logger.info("build_step_model_tool 被调用。")
    result: Dict[str, Any] = build_step_model_safe(device_config)
    if not result.get("success"):
        logger.error(
            "build_step_model_tool 失败：error=%s",
            result.get("error"),
        )
        # 将错误信息直接返回给 LLM，方便它根据错误提示修正 JSON
        return (
            "构建 STEP 模型失败。\n"
            f"错误信息：{result.get('error')}\n"
            f"详细堆栈（可选阅读）：\n{result.get('traceback')}"
        )

    step_path = result["step_path"]
    script_path = result["script_path"]
    json_path = result["json_path"]
    logger.info(
        "build_step_model_tool 成功：step=%s, script=%s, json=%s",
        step_path,
        script_path,
        json_path,
    )
    return (
        "STEP 模型已成功生成。\n"
        f"- STEP 文件路径: {step_path}\n"
        f"- 对应 CAD 脚本路径: {script_path}\n"
        f"- 使用的器件 JSON 路径: {json_path}\n"
        "你可以将 STEP 文件作为 3D 模型导入下游 CAD/仿真工具。"
    )


@tool("build_device_config_from_template")
def build_device_config_from_template_tool(
    template_id: str,
    overrides: Optional[Dict[str, Any]] = None,
) -> str:
    """
    基于参考模板 + 少量参数修改，生成一份**完整的器件 JSON 配置**（不直接导出 STEP）。

    使用场景：
    - 当你设计的新功率模块在拓扑和几何结构上与某个参考模板非常接近时（如 3P6P / HB 系列），
      优先使用本工具：
        1. 先选择最接近的模板 ID：3P6P_V1, 3P6P_V2, HB_V1, HB_V2, HB_V3, HB_V4；
        2. 再在 overrides 中只修改少量关键参数，而不是从零开始写完整 JSON。

    返回值：
    - 成功时：返回“说明文字 + 格式化后的 JSON 文本”，可直接再交给 `build_step_model` 工具生成 STEP；
    - 失败时：返回详细错误信息，便于你据此调整 overrides 或选择更合适的模板。
    """
    logger.info(
        "build_device_config_from_template_tool 被调用：template_id=%s, overrides_keys=%s",
        template_id,
        list(overrides.keys()) if overrides else [],
    )
    result: Dict[str, Any] = build_device_config_from_template_safe(
        template_id=template_id,
        overrides=overrides or {},
    )

    if not result.get("success"):
        logger.error(
            "build_device_config_from_template_tool 失败：template_id=%s, error=%s",
            template_id,
            result.get("error"),
        )
        return (
            "基于模板生成器件配置失败。\n"
            f"错误信息：{result.get('error')}\n"
            f"详细堆栈（可选阅读）：\n{result.get('traceback', '')}"
        )

    config_dict: Dict[str, Any] = result["config"]
    json_text = json.dumps(config_dict, ensure_ascii=False, indent=2)

    logger.info(
        "build_device_config_from_template_tool 成功：template_id=%s, module_id=%s",
        template_id,
        str(config_dict.get("module_id")),
    )
    return (
        "已基于参考模板生成完整的器件 JSON 配置（尚未导出 STEP）。\n"
        f"- 使用的模板 ID: {template_id}\n"
        "以下是合并并校验后的完整配置 JSON：\n"
        f"{json_text}"
    )


@tool("read_device_json")
def read_device_json_tool(module_id: str) -> str:
    """
    读取当前工程中某个功率模块对应的 JSON 配置内容（只读）。

    使用说明（非常重要）：
    1. 该工具会在 `data/json/` 目录下查找名为 `<module_id>.json` 的文件：
       - 例如 module_id 为 "3P6P_A" 时，对应路径为 `data/json/3P6P_A.json`；
       - 如果文件存在，则返回完整的 JSON 文本（UTF-8 编码）；
       - 如果文件不存在，则返回一段错误说明，提示调用方确认 ID 或先生成该模块。
    2. **当你需要"在已有模块基础上修改参数"时，必须先调用本工具读取当前 JSON，**
       **禁止猜测、禁止凭记忆、禁止假设当前配置值。你必须真正调用这个工具，获取真实的 JSON 内容。**
       在理解原有参数（如陶瓷尺寸、键合线数量、芯片布局等）的基础上再决定如何修改。
    3. 注意：本工具**不会**对 JSON 做任何修改，只是读取内容用于分析和展示。
       真正的修改仍然通过 `build_device_config_from_template` + `build_step_model`
       （或直接构造完整 JSON 后调用 `build_step_model`）完成。
    """
    path = JSON_DIR / f"{module_id}.json"
    if not path.exists():
        logger.warning(
            "read_device_json_tool 未找到 JSON：module_id=%s, path=%s",
            module_id,
            path,
        )
        return (
            "读取 JSON 失败：未在 data/json/ 目录下找到对应文件。\n"
            f"- 尝试访问的路径: {path}\n"
            "请确认 module_id 是否正确，或者先生成该模块的 JSON/STEP 文件。"
        )

    try:
        content = path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "read_device_json_tool 读取文件失败：module_id=%s, path=%s, error=%s",
            module_id,
            path,
            exc,
        )
        return (
            "读取 JSON 失败：文件存在但无法读取。\n"
            f"- 路径: {path}\n"
            f"- 错误信息: {exc}"
        )

    logger.info("read_device_json_tool 成功：module_id=%s, path=%s", module_id, path)
    return content


@tool("list_data_files")
def list_data_files_tool(
    subdir: Optional[str] = None,
    max_files: int = 200,
) -> str:
    """
    列出工程 `data/` 目录下的文件列表（只列出文件名与相对路径，不读取文件内容）。

    使用说明：
    - subdir:
        可选的子目录名称，例如 "json"、"step"、"sim_results"、"py" 等；
        为空或 None 时，列出整个 `data/` 树下的文件；
    - max_files:
        最多返回多少个文件条目，默认 200，防止输出过长。

    返回值：
    - 文本列表，每行一个文件，格式类似：
        [json] json/3P6P_V2_default.json
        [step] step/3P6P_V2_default.step
        [sim_results] sim_results/xxx_thermal.mph
    """
    base = DATA_DIR
    if subdir:
        base = base / subdir

    if not base.exists():
        return (
            "列出 data 文件失败：目标目录不存在。\n"
            f"- 尝试访问的路径: {base}"
        )

    files: List[Path] = []
    for p in base.rglob("*"):
        if p.is_file():
            files.append(p)
            if len(files) >= max_files:
                break

    if not files:
        return f"在目录 {base} 下未找到任何文件。"

    lines: List[str] = []
    for p in files:
        rel = p.relative_to(DATA_DIR)
        parts = rel.parts
        tag = parts[0] if parts else ""
        lines.append(f"[{tag}] {rel.as_posix()}")

    if len(files) >= max_files:
        lines.append(f"...（仅显示前 {max_files} 个文件）")

    return "当前 data 目录下的文件列表如下（相对路径）：\n" + "\n".join(lines)


@tool("precheck_cad_json")
def precheck_cad_json_tool(
    json_path: str,
) -> str:
    """
    在将 CAD JSON 送入 merge / build_step_model 之前，执行几何预检：
    - 检查芯片是否超出其归属 Zone 边界；
    - 检查芯片之间是否发生几何重叠；

    使用说明：
    1) 传入完整的 JSON/YAML 文件路径（与 cad/reference/ 结构一致的配置）。
    2) 工具返回结构化 JSON 字符串：
       {
         "ok": bool,
         "errors": [
           {"type": "chip_out_of_zone", "chip": "IGBT_0", "zone": "Zone_1", "detail": "..."},
           {"type": "chip_overlap", "chips": ["IGBT_0", "FWD_1"], "detail": "..."},
           {"type": "missing_zone_binding", "chip": "FWD_2", "detail": "..."},
           {"type": "missing_zone_geometry", "chip": "IGBT_0", "zone": "Zone_3", "detail": "..."}
         ],
         "warnings": [],
         "summary": "..."
       }
    3) LLM 应读取 ok / errors；若有错误，可选择：
       - 调整/重生成 JSON 后重试；或
       - 将问题反馈给用户。
    """
    logger.info("precheck_cad_json_tool 被调用：json_path=%s", json_path)
    try:
        result = run_precheck_safe(json_path)
    except Exception as exc:  # noqa: BLE001
        logger.exception("precheck_cad_json_tool 执行失败：%s", exc)
        return (
            "预检失败，工具抛出异常。\n"
            f"- 输入路径: {json_path}\n"
            f"- 错误: {exc}\n"
            "请检查路径是否存在、JSON 是否可读。"
        )

    return json.dumps(result, ensure_ascii=False, indent=2)


@tool("run_thermal_sim_from_step")
def run_thermal_sim_from_step_tool(
    step_filename: str,
    P_igbt: float = 150.0,
    P_fwd: float = 100.0,
    h: float = 2000.0,
    run_id: Optional[str] = None,
) -> str:
    """
    调用 MATLAB 的 run_sim_from_step.m，完成一次基于 STEP 几何的稳态热仿真，
    并将已求解的模型保存为 .mph 文件，供后续独立后处理中使用。

    使用说明：
    - step_filename:
        只需要给出 STEP 几何文件名，例如 "3P6P_V2_default.step" 或 "3P6P_V2_default"；
        实际文件会在工程根目录下的 `data/step/` 目录中查找。
    - P_igbt, P_fwd, h:
        与 MATLAB 端 run_sim_from_step.m 中的物理含义和默认值保持一致。
    - run_id:
        可选的“仿真运行 ID”。
        - 在同一会话中，如果你希望持续在同一个仿真 case 上修改参数、重复求解，
          应该始终复用同一个 run_id（除非用户明确要求创建新的 run_id）；
        - 如果提供了 run_id，则 .mph 文件名形如 `<run_id>_thermal.mph`；
        - 如果未提供，则回退为 `<step_base>_thermal.mph`。

    当前版本只负责在 COMSOL/MATLAB 内完成一次稳态求解，并将结果保存为 .mph 文件；
    后处理（例如统计芯片最高温、导出温度场标签）应通过独立的后处理工具完成，
    这些工具会以本工具返回的 `model_path` 为输入。
    """
    logger.info(
        "run_thermal_sim_from_step_tool 被调用：step_filename=%s, P_igbt=%s, P_fwd=%s, h=%s, run_id=%s",
        step_filename,
        P_igbt,
        P_fwd,
        h,
        run_id,
    )
    result = run_sim_from_step_safe(
        step_filename=step_filename,
        P_igbt=P_igbt,
        P_fwd=P_fwd,
        h=h,
        run_id=run_id,
    )
    if not result.get("success"):
        return (
            "调用 MATLAB 稳态热仿真失败。\n"
            f"错误信息：{result.get('error')}"
        )

    model_path = result.get("model_path", "<unknown>")
    used_run_id = result.get("run_id")

    lines = [
        "MATLAB 单次稳态热仿真已完成，并已保存 .mph 模型文件（尚未做后处理）。\n",
        f"- STEP 几何文件名: {result['step_filename']}\n",
        f"- P_igbt = {result['P_igbt']} W\n",
        f"- P_fwd  = {result['P_fwd']} W\n",
        f"- h      = {result['h']} W/(m^2*K)\n",
    ]
    if used_run_id:
        lines.append(f"- 运行 ID (run_id): {used_run_id}\n")
    lines.append(f"- 已保存的 COMSOL 模型文件 (.mph): {model_path}\n")
    lines.append(
        "你可以将上述 model_path 作为后处理工具（如 compute_chip_maxT_from_mph）的输入，"
        "以统计芯片最高温度或生成其他温度场标签。"
    )

    return "".join(lines)


@tool("compute_chip_maxT_from_mph")
def compute_chip_maxT_from_mph_tool(
    model_path: str,
    P_igbt: float,
    P_fwd: float,
    h: float,
    dataset: str = "dset1",
) -> str:
    """
    基于已保存的 COMSOL .mph 模型文件，调用 MATLAB 的 compute_chip_maxT.m
    统计每个 Silicon 芯片域的最高温度。

    使用说明：
    - model_path:
        由 `run_thermal_sim_from_step` 工具返回的 .mph 模型文件路径；
    - P_igbt, P_fwd, h:
        应与仿真时的设置保持一致（这些值仅用于结果表中记录工况信息）；
    - dataset:
        COMSOL 结果数据集名称，默认 'dset1'。

    返回值：
    - 文本格式的人类可读报告，其中包含：
        - 所用模型文件路径与数据集名称；
        - 芯片数量；
        - 每个芯片域的最高温度等信息（以 JSON 表格形式嵌入）。
    """
    logger.info(
        "compute_chip_maxT_from_mph_tool 被调用：model_path=%s, P_igbt=%s, P_fwd=%s, h=%s, dataset=%s",
        model_path,
        P_igbt,
        P_fwd,
        h,
        dataset,
    )
    result = compute_chip_maxT_safe(
        model_path=model_path,
        P_igbt=P_igbt,
        P_fwd=P_fwd,
        h=h,
        dataset=dataset,
    )
    if not result.get("success"):
        return (
            "基于 .mph 模型的芯片最高温度统计失败。\n"
            f"错误信息：{result.get('error')}"
        )

    chips = result.get("chips", [])
    n_chips = len(chips)
    chips_json = json.dumps(chips, ensure_ascii=False, indent=2)
    csv_path = result.get("csv_path")

    lines = [
        "已基于给定的 COMSOL .mph 模型文件完成芯片最高温度统计。\n",
        f"- 模型文件: {result['model_path']}\n",
        f"- 使用的数据集: {result['dataset']}\n",
        f"- 芯片数量: {n_chips}\n",
    ]
    if csv_path:
        lines.append(f"- 结果已另存为 CSV: {csv_path}\n")
    lines.append("以下为每个芯片域的最高温度等详细信息（JSON 数组形式）：\n")
    lines.append(f"{chips_json}")

    return "".join(lines)


def get_all_tools() -> List[BaseTool]:
    """
    返回当前可用于智能体的全部工具列表。

    后续如果新增工具，只需要在这里扩展列表即可。
    """

    return [
        build_step_model_tool,
        build_device_config_from_template_tool,
        read_device_json_tool,
        list_data_files_tool,
        precheck_cad_json_tool,
        run_thermal_sim_from_step_tool,
        compute_chip_maxT_from_mph_tool,
        # 通用优化相关工具（目前仅实现 SA 的数学层，但接口保持通用 opt_* 命名）
        opt_create_session_tool,
        opt_get_state_tool,
        opt_propose_candidate_tool,
        opt_step_tool,
        # 未来可以在这里追加：更多 MATLAB 仿真工具, dataset_io 工具等
    ]


@tool("opt_create_session")
def opt_create_session_tool(
    x0: List[float],
    lower_bounds: Optional[List[float]] = None,
    upper_bounds: Optional[List[float]] = None,
    max_iter: int = 50,
    algo: str = "sa",
    T_init: float = 1.0,
    T_min: float = 1e-3,
    alpha: float = 0.9,
    neighbor_scale: float = 0.1,
) -> str:
    """
    创建一个通用“优化会话”，当前仅支持算法 algo="sa"（模拟退火）。

    设计原则：
    - 本工具只负责数学空间中的会话管理，不参与任何 CAD / MATLAB / 仿真；
    - 设计变量 x0 / 上下界 / 目标函数含义等，完全由 LLM 自行根据具体物理问题决定；
    - 返回值中包含 session_id 和当前状态概要，后续应配合 opt_get_state / opt_propose_candidate / opt_step 使用。
    """
    logger.info(
        "opt_create_session_tool 被调用：algo=%s, dim=%d, max_iter=%d",
        algo,
        len(x0) if isinstance(x0, list) else -1,
        max_iter,
    )

    if algo != "sa":
        return (
            "当前系统仅实现了模拟退火算法（algo='sa'）。\n"
            "如果你希望使用其他优化算法，请在对话中说明需求，"
            "由开发者在 opt/<algo>/ 下新增实现。"
        )

    result = sa_create_session_safe(
        x0=x0,
        lower_bounds=lower_bounds,
        upper_bounds=upper_bounds,
        max_iter=max_iter,
        T_init=T_init,
        T_min=T_min,
        alpha=alpha,
        neighbor_scale=neighbor_scale,
    )
    if not result.get("success"):
        return (
            "创建优化会话失败。\n"
            f"错误信息：{result.get('error')}"
        )

    state = result.get("state", {})
    return (
        "已创建一个新的优化会话（当前使用模拟退火 SA 算法，仅在数值空间中维护状态）。\n"
        f"- session_id: {result.get('session_id')}\n"
        f"- 维度 dim: {result.get('dim')}\n"
        f"- 最大迭代次数 max_iter: {result.get('max_iter')}\n"
        f"- 初始温度 T_init: {state.get('T')}\n"
        f"- 最小温度 T_min: {state.get('T_min')}\n"
        f"- 降温因子 alpha: {state.get('alpha')}\n"
        f"- 邻域尺度 neighbor_scale: {state.get('neighbor_scale')}\n"
        "你应在后续步骤中：\n"
        "1) 自行决定如何根据 x 启动 CAD/仿真来计算目标函数；\n"
        "2) 使用 opt_propose_candidate 获取候选解；\n"
        "3) 用 opt_step 根据 SA 规则更新会话状态。"
    )


@tool("opt_get_state")
def opt_get_state_tool(session_id: str) -> str:
    """
    查询某个优化会话的当前状态（算法类型、当前解与最优解、温度等）。

    注意：
    - 本工具不会触发任何仿真，只是读取内存中的优化状态；
    - 方便 LLM 在多轮优化过程中了解进度，决定是否提前停止或调整策略。
    """
    logger.info("opt_get_state_tool 被调用：session_id=%s", session_id)
    result = sa_get_state_safe(session_id=session_id)
    if not result.get("success"):
        return (
            "查询优化会话状态失败。\n"
            f"错误信息：{result.get('error')}"
        )

    state = result["state"]
    return (
        "当前优化会话状态如下：\n"
        f"- session_id: {state.get('session_id')}\n"
        f"- 算法类型 algo: {state.get('algo')}\n"
        f"- 维度 dim: {state.get('dim')}\n"
        f"- 已迭代次数 iter / max_iter: {state.get('iter')} / {state.get('max_iter')}\n"
        f"- 当前解 current_x: {state.get('current_x')}\n"
        f"- 当前目标 current_fx: {state.get('current_fx')}\n"
        f"- 最优解 best_x: {state.get('best_x')}\n"
        f"- 最优目标 best_fx: {state.get('best_fx')}\n"
        f"- 当前温度 T: {state.get('T')}\n"
        f"- 最小温度 T_min: {state.get('T_min')}\n"
        f"- 降温因子 alpha: {state.get('alpha')}\n"
        f"- 邻域尺度 neighbor_scale: {state.get('neighbor_scale')}\n"
        f"- 变量下界 lower_bounds: {state.get('lower_bounds')}\n"
        f"- 变量上界 upper_bounds: {state.get('upper_bounds')}\n"
        "你可以据此决定：是否继续迭代、是否调整 SA 参数，或结束本次优化流程。"
    )


@tool("opt_propose_candidate")
def opt_propose_candidate_tool(session_id: str) -> str:
    """
    基于当前解与 SA 邻域尺度，生成一个新的候选解 x_candidate。

    说明：
    - 本工具只在“抽象变量空间”中工作，不做任何 CAD/仿真；
    - 拿到 candidate_x 后，你（LLM）应自行决定如何翻译为具体 JSON/CAD 参数，
      并通过现有建模与仿真工具计算其目标函数值。
    """
    logger.info("opt_propose_candidate_tool 被调用：session_id=%s", session_id)
    result = sa_propose_candidate_safe(session_id=session_id)
    if not result.get("success"):
        return (
            "生成候选解失败。\n"
            f"错误信息：{result.get('error')}"
        )

    current_x = result.get("current_x")
    candidate_x = result.get("candidate_x")
    return (
        "已基于当前解生成一个新的候选解（仅在数值空间中）：\n"
        f"- session_id: {result.get('session_id')}\n"
        f"- 当前解 current_x: {current_x}\n"
        f"- 候选解 candidate_x: {candidate_x}\n"
        "你现在应当：\n"
        "1) 将 candidate_x 映射为具体的设计参数 / JSON 字段；\n"
        "2) 调用 CAD/仿真工具计算该设计的目标函数值（如芯片最高温度）；\n"
        "3) 使用 opt_step 将 current_fx 与 candidate_fx 提交给 SA 进行状态更新。"
    )


@tool("opt_step")
def opt_step_tool(
    session_id: str,
    current_fx: float,
    candidate_x: List[float],
    candidate_fx: float,
) -> str:
    """
    调用 SA 的单步状态更新逻辑。

    说明：
    - 本工具本身不计算目标函数值，只接受你已计算好的 current_fx / candidate_fx；
    - 通常的调用顺序是：
      1) 使用 opt_propose_candidate 获取 candidate_x；
      2) 在外部调用 CAD/仿真或查表等方式得到 candidate_fx；
      3) 使用 opt_step 提交 current_fx 与 candidate_fx，让 SA 决定是否接受并更新状态。
    """
    logger.info(
        "opt_step_tool 被调用：session_id=%s, current_fx=%s, candidate_fx=%s",
        session_id,
        current_fx,
        candidate_fx,
    )
    result = sa_step_safe(
        session_id=session_id,
        current_fx=current_fx,
        candidate_x=candidate_x,
        candidate_fx=candidate_fx,
    )
    if not result.get("success"):
        return (
            "优化迭代（SA step）失败。\n"
            f"错误信息：{result.get('error')}"
        )

    return (
        "本次 SA 迭代结果如下：\n"
        f"- session_id: {result.get('session_id')}\n"
        f"- 是否接受候选解 accepted: {result.get('accepted')}（原因: {result.get('reason')}）\n"
        f"- 当前解 current_x: {result.get('current_x')}\n"
        f"- 当前目标 current_fx: {result.get('current_fx')}\n"
        f"- 历史最优解 best_x: {result.get('best_x')}\n"
        f"- 历史最优目标 best_fx: {result.get('best_fx')}\n"
        f"- 当前温度 T: {result.get('T')}\n"
        f"- 当前迭代次数 iter / max_iter: {result.get('iter')} / {result.get('max_iter')}\n"
        f"- 是否已满足终止条件 done: {result.get('done')}\n"
        "你可以根据 done 的取值决定是否继续：\n"
        "- 若 done=False：通常继续通过 opt_propose_candidate + CAD/仿真 + opt_step 进行下一轮；\n"
        "- 若 done=True：可以结束本次优化流程，并基于 best_x 对应的设计做总结与后处理。"
    )

