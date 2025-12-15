from __future__ import annotations

"""
使用 Pydantic 定义功率模块 JSON 配置的结构约束。

注意：
- 这里的字段和层级是参考 cad/reference 目录下的示例文件整理出来的，
  主要用于在调用 build_step_model 之前做基本格式校验。
- 校验通过后，会返回一个“结构正确、类型合理”的 dict，供后续 CAD 构建使用。
"""

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field


Number = Union[int, float]
Coord = List[Number]  # [x, y]
CoordWithExtreme = List[Union[Number, str]]  # [x, y]，其中 y 可能为 "MAX"/"MIN"


class GateTypeConfig(BaseModel):
    """
    单一 gate 类型的布线配置。

    - 支持两种格式（与 reference 中一致）：
      1) 单个门极：
         start: [x, y]
         moves: [[dx, dy], [dx, dy], ...]
      2) 多个门极：
         start: [[x1, y1], [x2, y2], ...]
         moves: [
             [[dx11, dy11], [dx12, dy12], ...],   # 第 1 个门极的轨迹
             [[dx21, dy21], [dx22, dy22], ...],   # 第 2 个门极的轨迹
         ]
    """

    start: Union[Coord, List[Coord]]
    moves: Union[List[Coord], List[List[Coord]]]


class DeviceConfig(BaseModel):
    """
    功率模块“设计空间 JSON”的整体结构约束。

    该结构是根据 cad/reference/*.json 中的 6 个示例整理而成：
    - 基本几何 / 工艺 / 芯片尺寸等为必填；
    - gate_design / cutting_design / 位置 / 旋转 / 拓扑结构等与 merge.ConfigProcessor 的预期一致。
    """

    # 可选：模块 ID，用于生成任务 ID
    module_id: Optional[str] = None

    # 几何尺寸
    ceramics_width: Number
    ceramics_length: Number
    ceramics_thickness: Number
    upper_copper_thickness: Number
    lower_copper_thickness: Number
    fillet_radius: Number

    # 芯片尺寸
    igbt_width: Number
    igbt_length: Number
    fwd_width: Number
    fwd_length: Number

    # 间隙 / 边界
    cu2cu_margin: Number
    cu2ceramics_margin: Number
    dbc2dbc_margin: Number
    substrate_edge_margin: Number

    # 工艺参数
    substrate_solder_thickness: Number
    die_solder_thickness: Number
    die_thickness: Number

    # 键合线数量
    igbt_bondwires: int
    fwd_bondwires: int

    # 门极设计：type_x -> GateTypeConfig
    gate_design: Dict[str, GateTypeConfig]

    # 切割路径：path_x -> [[x_ratio 或 "MIN"/"MAX", y_ratio 或 "MIN"/"MAX"], ...]
    cutting_design: Dict[str, List[CoordWithExtreme]]

    # IGBT 位置 / 旋转：type_x -> [[x_ratio, y_ratio], ...]
    igbt_positions: Dict[str, List[Coord]]
    igbt_rotations: Dict[str, List[Number]]

    # FWD 位置 / 旋转：[[x_ratio, y_ratio], ...]
    fwd_positions: List[Coord]
    fwd_rotations: List[Number]

    # DBC 数量与旋转
    dbc_count: int
    dbc_rotations: List[Number]

    # 模块 / DBC 拓扑连接关系
    # 允许元素是字符串或整数索引，后续在 merge.convert_flat_default_to_nested 中会统一转为字符串
    module_connections: List[List[Union[str, int]]] = Field(default_factory=list)
    dbc_connections: List[List[Union[str, int]]] = Field(default_factory=list)

    class Config:
        # 默认允许额外字段，以便后续扩展；核心字段缺失会抛出异常
        extra = "allow"


def validate_device_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    使用 DeviceConfig 对原始 dict 做格式 / 类型校验。

    返回通过 Pydantic 解析后的标准化 dict（数值类型会被规整为 int/float，
    且必需字段缺失时会抛出 ValidationError）。
    """

    model = DeviceConfig(**config)
    # 使用 .dict() 以兼容 pydantic v1 / v2
    return model.dict()



