from __future__ import annotations

"""
在“大模型输出”和底层复杂 JSON 配置之间，提供一个**基于模板的参数合并工具**。

设计目标：
- 让大模型只需要决定：
  1) 选用哪一个参考模板（3P6P_V1 / 3P6P_V2 / HB_V1 / HB_V2 / HB_V3 / HB_V4）；
  2) 在该模板基础上，修改哪些关键参数（例如陶瓷尺寸、铜厚、间隙、焊料厚度、芯片尺寸、键合线数量等）。
- 由本模块负责：
  1) 读取 cad/reference 下对应的模板文件；
  2) 应用 overrides 覆盖生成新的配置字典；
  3) 使用 DeviceConfig(Pydantic) 做严格校验；
- 本模块**只负责生成合法的配置 dict，不直接导出 STEP**。
  生成好的配置可交给：
  - `cad_build.build_step_model_from_config` / `build_step_model_safe` 去生成 STEP；
  - 或者直接写入 JSON 文件，供后续人工或其他程序使用。
"""

from pathlib import Path
from typing import Any, Dict, Optional

import logging
import yaml

from .cad_schema import validate_device_config


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REFERENCE_DIR = PROJECT_ROOT / "cad" / "reference"

logger = logging.getLogger("power_agent.template_merge")

# 记录每个模板最近一次成功构建所使用的 module_id，便于“默认在旧模块上修改”
LAST_MODULE_ID: Dict[str, str] = {}

# 支持的模板 ID 与对应参考文件名的映射
TEMPLATE_FILE_MAP: Dict[str, str] = {
    "3P6P_V1": "3P6P_V1_default.json",
    "3P6P_V2": "3P6P_V2_default.json",
    "HB_V1": "HB_V1_default.json",
    "HB_V2": "HB_V2_default.json",
    "HB_V3": "HB_V3_default.json",
    "HB_V4": "HB_V4_default.json",
}


def _load_template_config(template_id: str) -> Dict[str, Any]:
    """
    从 cad/reference 目录加载指定模板的“设计空间配置”。

    注意：这些文件虽然扩展名是 .json，但内容格式是 YAML，本函数使用 yaml.safe_load 解析。
    """

    if template_id not in TEMPLATE_FILE_MAP:
        raise ValueError(
            f"不支持的 template_id: {template_id!r}，"
            f"必须是 {sorted(TEMPLATE_FILE_MAP.keys())} 之一。"
        )

    filename = TEMPLATE_FILE_MAP[template_id]
    path = REFERENCE_DIR / filename
    if not path.exists():
        logger.error("模板文件不存在：%s", path)
        raise FileNotFoundError(f"未找到模板文件: {path}")

    try:
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        logger.info("成功加载模板配置：template_id=%s, path=%s", template_id, path)
    except Exception as exc:  # noqa: BLE001
        logger.exception("解析模板文件失败：%s", path)
        raise RuntimeError(f"解析模板文件失败: {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"模板文件的顶层结构必须是映射(dict)，但实际为: {type(data)!r}")

    return data


def build_device_config_from_template(
    template_id: str,
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    读取指定模板并应用 overrides，返回通过 Pydantic(DeviceConfig) 校验后的配置字典。

    参数
    ----
    template_id:
        模板 ID，必须是 TEMPLATE_FILE_MAP 中的键之一：
        - "3P6P_V1", "3P6P_V2"
        - "HB_V1", "HB_V2", "HB_V3", "HB_V4"
        这些模板对应 cad/reference 目录下的 *_default.json 文件。

    overrides:
        一个字典，用于在模板基础上覆盖字段。
        - 键名应与参考模板 / DeviceConfig 模型中的字段一致，例如：
          ceramics_width, ceramics_length, ceramics_thickness,
          upper_copper_thickness, lower_copper_thickness, fillet_radius,
          cu2cu_margin, cu2ceramics_margin, dbc2dbc_margin, substrate_edge_margin,
          substrate_solder_thickness, die_solder_thickness, die_thickness,
          igbt_width, igbt_length, fwd_width, fwd_length,
          igbt_bondwires, fwd_bondwires,
          gate_design, cutting_design,
          igbt_positions, fwd_positions,
          igbt_rotations, fwd_rotations,
          dbc_count, dbc_rotations,
          module_connections, dbc_connections 等。
        - 未提供的字段将直接使用模板中的默认值。
        - 如果你传入 gate_design / cutting_design / 连接拓扑等复杂结构，
          则会整块替换模板中的对应字段，请确保结构与参考示例一致。

    返回
    ----
    Dict[str, Any]:
        已通过 DeviceConfig(Pydantic) 严格校验的配置字典，
        可以直接传给 `cad_build.build_step_model_from_config` / `build_step_model_safe`，
        或自行写入 JSON 文件。
    """

    base = _load_template_config(template_id)
    merged: Dict[str, Any] = dict(base)

    if overrides:
        # 浅层覆盖：同名键用 overrides 中的值替换
        # 对于复杂结构（如 gate_design / cutting_design），建议整块给出完整结构。
        for key, value in overrides.items():
            # 适配大模型给出的简化格式：
            # - 如果 igbt_positions 是 [[x,y], ...] 这样的列表，而不是 {"type_1": [[x,y], ...]}
            #   则自动包装为 {"type_1": [[x,y], ...]}，以匹配模板和 DeviceConfig 的预期结构。
            if key == "igbt_positions" and isinstance(value, list):
                if value and isinstance(value[0], (list, tuple)):
                    merged[key] = {"type_1": value}
                else:
                    merged[key] = value
            # 同理，如果 igbt_rotations 是 [angle, ...] 而不是 {"type_1": [angle, ...]}
            elif key == "igbt_rotations" and isinstance(value, list):
                # 简单检测：列表元素是数字而不是子列表/字典，则认为是单一 type_1 的旋转列表
                if value and isinstance(value[0], (int, float)):
                    merged[key] = {"type_1": value}
                else:
                    merged[key] = value
            else:
                merged[key] = value

    logger.info(
        "准备基于模板生成配置：template_id=%s, overrides_keys=%s",
        template_id,
        list(overrides.keys()) if overrides else [],
    )

    # 使用 DeviceConfig 做最终的格式 / 类型校验与规范化
    validated = validate_device_config(merged)
    logger.info(
        "模板配置校验通过：template_id=%s, module_id=%s",
        template_id,
        str(validated.get("module_id")),
    )
    return validated


def build_device_config_from_template_safe(
    template_id: str,
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    对 Agent 更友好的包装：基于模板 + 覆盖生成**完整 JSON 配置**（不直接生成 STEP）。

    返回值格式：
    - 成功时：
        {
            "success": true,
            "config": { ... }  # 已通过 Pydantic 校验的配置 dict
        }
    - 失败时：
        {
            "success": false,
            "error": "<人类可读的错误信息>",
            "traceback": ""
        }
    """

    # 确保 overrides 是可变字典
    overrides = dict(overrides or {})

    # 如果调用方未显式给出 module_id，则优先复用同一模板最近一次成功构建的 module_id
    if "module_id" not in overrides and template_id in LAST_MODULE_ID:
        overrides["module_id"] = LAST_MODULE_ID[template_id]
        logger.info(
            "未显式提供 module_id，自动复用最近一次的 module_id：template_id=%s, module_id=%s",
            template_id,
            overrides["module_id"],
        )

    try:
        device_config = build_device_config_from_template(template_id, overrides)
    except Exception as exc:  # noqa: BLE001
        logger.error("基于模板生成配置失败：template_id=%s, error=%s", template_id, exc)
        return {
            "success": False,
            "error": f"基于模板生成配置失败: {exc}",
            "traceback": "",
        }

    # 记录该模板最近一次成功使用的 module_id
    module_id = device_config.get("module_id")
    if module_id:
        LAST_MODULE_ID[template_id] = str(module_id)
        logger.info(
            "更新模板最近一次使用的 module_id：template_id=%s, module_id=%s",
            template_id,
            module_id,
        )

    return {
        "success": True,
        "config": device_config,
    }



