#!/usr/bin/env python3
"""
配置处理器 - 负责读取 JSON 配置并与 CAD 模板 (template.j2) 融合
"""

import os
import json
from pathlib import Path
from jinja2 import Template
from typing import Dict, Any, List, Optional

# 扁平化配置键到嵌套路径的映射
OVERRIDE_MAPPING = {
    # Basic module info
    "module_id": "template_id",
    
    # Geometry parameters
    "ceramics_width": "geometry.ceramics.width",
    "ceramics_length": "geometry.ceramics.length", 
    "ceramics_thickness": "geometry.ceramics.thickness",
    "upper_copper_thickness": "geometry.copper.upper_thickness",
    "lower_copper_thickness": "geometry.copper.lower_thickness",
    "fillet_radius": "geometry.fillet_radius",
    
    # Die parameters
    "igbt_width": "dies.igbt.size.width",
    "igbt_length": "dies.igbt.size.length",
    "fwd_width": "dies.fwd.size.width", 
    "fwd_length": "dies.fwd.size.length",
    
    # Margin parameters
    "cu2cu_margin": "margins.cu2cu",
    "cu2ceramics_margin": "margins.cu2ceramics",
    "dbc2dbc_margin": "margins.dbc2dbc",
    "substrate_edge_margin": "margins.substrate_edge",
    
    # Process parameters
    "substrate_solder_thickness": "process.solder.substrate",
    "die_solder_thickness": "process.solder.die",
    "die_thickness": "process.die_thickness",
    
    # Bondwire counts
    "igbt_bondwires": "counts.bondwires.igbt",
    "fwd_bondwires": "counts.bondwires.fwd",
    
    # DBC layout parameters (simplified)
    "dbc_count": "dbc_layout.count",
    
    # Gate design parameters (simplified structure will be handled separately)
    # Cutting design parameters (simplified structure will be handled separately)  
    # Position parameters (simplified structure will be handled separately)
    # Rotation parameters (simplified structure will be handled separately)
    # Connection parameters (simplified structure will be handled separately)
}


class ConfigProcessor:
    def __init__(self, template_filename: str = "template.j2"):
        """
        配置处理器：
        - 不再根据 module_id 去查找 / 合并任何 YAML 默认配置
        - 直接使用输入的 JSON（或 dict）配置与同目录下的 Jinja2 模板合并
        """
        self.template_filename = template_filename

    def load_json_config(self, json_path: str) -> Dict[str, Any]:
        """从 JSON 文件加载扁平配置"""
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def render_template(
        self,
        config: Dict[str, Any],
        template_file: Optional[str] = None,
        device_name: Optional[str] = None,
    ) -> str:
        """
        使用配置渲染模板:
        - config: 期望是“扁平结构”的配置（键与原 default.yaml 保持一致）
        - 模板文件固定位于当前文件同一目录下，默认名为 template.j2
        """
        # 将扁平配置转换为嵌套结构，以匹配模板中的层级访问
        nested_config = self.convert_flat_default_to_nested(config)

        # 计算 device_name：
        # - 优先使用传入的 device_name
        # - 否则使用 config['module_id']（如果有）
        # - 再否则回退为 "device"
        if device_name is None:
            device_name = str(
                config.get("module_id") or config.get("module_name") or "device"
            )

        # 确定模板路径：当前文件同目录 + template.j2（或自定义）
        base_dir = os.path.dirname(os.path.abspath(__file__))
        template_name = template_file or self.template_filename
        template_path = os.path.join(base_dir, template_name)

        with open(template_path, "r", encoding="utf-8") as f:
            template_content = f.read()

        template = Template(template_content)
        return template.render(device_name=device_name, **nested_config)

    def process_json_file(
        self, json_path: str, template_file: Optional[str] = None
    ) -> str:
        """
        从 JSON 文件直接生成渲染结果：
        - 不再加载 / 合并任何 YAML 默认配置
        - 只使用给定 JSON 中的字段驱动模板
        """
        flat_config = self.load_json_config(json_path)

        # 默认将 device_name 设为 JSON 文件名（不含扩展名），
        # 例如 data/json/2baf22e8.json -> device_name="2baf22e8"
        default_device_name = Path(json_path).stem

        return self.render_template(
            flat_config,
            template_file=template_file,
            device_name=default_device_name,
        )

    def convert_flat_default_to_nested(self, flat_config: Dict[str, Any]) -> Dict[str, Any]:
        """将扁平的default配置转换为嵌套结构以匹配模板期望"""
        nested = {}
        
        # 首先处理所有直接映射的扁平键
        for flat_key, value in flat_config.items():
            if flat_key in OVERRIDE_MAPPING:
                nested_path = OVERRIDE_MAPPING[flat_key]
                self._set_nested_value(nested, nested_path, value)
            elif flat_key == "module_id":
                nested["template_id"] = value
        
        # 处理复杂结构
        for flat_key, value in flat_config.items():
            # 处理门极设计参数
            if flat_key == "gate_design":
                if 'gate_design' not in nested:
                    nested['gate_design'] = {}
                if 'types' not in nested['gate_design']:
                    nested['gate_design']['types'] = {}
                
                for type_key, type_config in value.items():
                    type_id = type_key.replace('type_', '')
                    nested['gate_design']['types'][type_id] = {}
                    
                    if 'start' in type_config:
                        start_data = type_config['start']
                        # 检查是否是多个门极（嵌套列表格式）
                        if isinstance(start_data[0], list):
                            # 多个门极的情况 - 支持所有门极
                            nested['gate_design']['types'][type_id]['start_points'] = []
                            for start_point in start_data:
                                nested['gate_design']['types'][type_id]['start_points'].append({
                                    'x_ratio': start_point[0],
                                    'y_ratio': start_point[1]
                                })
                        else:
                            # 单个门极的情况
                            nested['gate_design']['types'][type_id]['start_points'] = [{
                                'x_ratio': start_data[0],
                                'y_ratio': start_data[1]
                            }]
                    
                    if 'moves' in type_config:
                        moves_data = type_config['moves']
                        # 检查是否是多个门极（三层嵌套列表格式）
                        if isinstance(moves_data[0][0], list):
                            # 多个门极的情况 - 支持所有门极的moves
                            nested['gate_design']['types'][type_id]['moves_list'] = []
                            for move_group in moves_data:
                                move_list = []
                                for move in move_group:
                                    move_list.append({
                                        'x_ratio': move[0],
                                        'y_ratio': move[1]
                                    })
                                nested['gate_design']['types'][type_id]['moves_list'].append(move_list)
                        else:
                            # 单个门极的情况
                            nested['gate_design']['types'][type_id]['moves_list'] = []
                            move_list = []
                            for move in moves_data:
                                move_list.append({
                                    'x_ratio': move[0],
                                    'y_ratio': move[1]
                                })
                            nested['gate_design']['types'][type_id]['moves_list'].append(move_list)
            
            # 处理切割路径参数
            elif flat_key == "cutting_design":
                if 'cutting_design' not in nested:
                    nested['cutting_design'] = {}
                if 'paths' not in nested['cutting_design']:
                    nested['cutting_design']['paths'] = []
                
                path_index = 0
                for path_key, points in value.items():
                    path_data = {
                        'name': f"cut_{path_index + 1}",
                        'points': []
                    }
                    
                    for point in points:
                        x_val = point[0]
                        y_val = point[1]
                        
                        # 处理特殊值 MAX/MIN
                        if x_val == "MAX":
                            x_val = 999
                        elif x_val == "MIN":
                            x_val = -999
                        if y_val == "MAX":
                            y_val = 999
                        elif y_val == "MIN":
                            y_val = -999
                            
                        path_data['points'].append({
                            'x_ratio': x_val,
                            'y_ratio': y_val
                        })
                    
                    nested['cutting_design']['paths'].append(path_data)
                    path_index += 1
            
            # 处理IGBT位置参数
            elif flat_key == "igbt_positions":
                nested['igbt_positions'] = value
            
            # 处理FWD位置参数
            elif flat_key == "fwd_positions":
                nested['fwd_positions'] = value
            
            # 处理IGBT旋转参数
            elif flat_key == "igbt_rotations":
                nested['igbt_rotations'] = value
            
            # 处理FWD旋转参数
            elif flat_key == "fwd_rotations":
                nested['fwd_rotations'] = value
            
            # 处理DBC旋转参数
            elif flat_key == "dbc_rotations":
                nested['dbc_rotations'] = value
            
            # 处理模块连接参数
            elif flat_key == "module_connections":
                if 'topology' not in nested:
                    nested['topology'] = {}
                
                module_entities = set()
                nested['topology']['module_connections'] = []
                
                for conn in value:
                    source = conn[0].replace('zone', 'Zone_')
                    target = conn[1].replace('zone', 'Zone_')
                    module_entities.add(source)
                    module_entities.add(target)
                    nested['topology']['module_connections'].append({
                        'source': source,
                        'target': target,
                        'value': 1
                    })
                
                nested['topology']['module_entities'] = sorted(list(module_entities))
            
            # 处理DBC连接参数
            elif flat_key == "dbc_connections":
                if 'topology' not in nested:
                    nested['topology'] = {}
                
                dbc_entities = set()
                nested['topology']['dbc_connections'] = []
                
                for conn in value:
                    source_raw = str(conn[0]).lower()
                    target_raw = str(conn[1]).lower()
                    
                    # 实体命名转换
                    if source_raw.startswith('igbt'):
                        source = source_raw.replace('igbt', 'IGBT_')
                    elif source_raw.startswith('fwd'):
                        source = source_raw.replace('fwd', 'FWD_')
                    elif source_raw.startswith('zone'):
                        source = source_raw.replace('zone', 'Zone_')
                    else:
                        source = source_raw
                    
                    if target_raw.startswith('igbt'):
                        target = target_raw.replace('igbt', 'IGBT_')
                    elif target_raw.startswith('fwd'):
                        target = target_raw.replace('fwd', 'FWD_')
                    elif target_raw.startswith('zone'):
                        target = target_raw.replace('zone', 'Zone_')
                    else:
                        target = target_raw
                    
                    dbc_entities.add(source)
                    dbc_entities.add(target)
                    nested['topology']['dbc_connections'].append({
                        'source': source,
                        'target': target,
                        'value': 1
                    })
                
                nested['topology']['dbc_entities'] = sorted(list(dbc_entities))
        
        return nested

    def _set_nested_value(self, nested_dict: Dict[str, Any], path: str, value: Any):
        """在嵌套字典中设置值，支持数组索引访问"""
        keys = path.split('.')
        current = nested_dict
        
        for i, key in enumerate(keys[:-1]):
            # 检查是否是数组索引
            if key.isdigit():
                key = int(key)
                # 确保当前节点是列表
                if not isinstance(current, list):
                    # 如果父级需要初始化为列表
                    parent_key = keys[i-1] if i > 0 else None
                    if parent_key:
                        current = []
                        return  # 需要重新构建结构
                
                # 确保列表足够长
                while len(current) <= key:
                    current.append({})
                current = current[key]
            else:
                if key not in current:
                    current[key] = {}
                current = current[key]
        
        final_key = keys[-1]
        if final_key.isdigit():
            final_key = int(final_key)
            if not isinstance(current, list):
                current = []
            while len(current) <= final_key:
                current.append(None)
            current[final_key] = value
        else:
            current[final_key] = value

    def flatten_override_to_nested(self, flat_overrides: Dict[str, Any]) -> Dict[str, Any]:
        """将扁平化的override转换为嵌套结构 - 支持新的简洁格式"""
        nested = {}
        
        for flat_key, value in flat_overrides.items():
            # 处理标准映射
            if flat_key in OVERRIDE_MAPPING:
                nested_path = OVERRIDE_MAPPING[flat_key]
                self._set_nested_value(nested, nested_path, value)
            elif flat_key == "module_id":
                nested["template_id"] = value
            
            # 处理门极设计参数
            elif flat_key == "gate_design":
                if 'gate_design' not in nested:
                    nested['gate_design'] = {}
                if 'types' not in nested['gate_design']:
                    nested['gate_design']['types'] = {}
                
                for type_key, type_config in value.items():
                    type_id = type_key.replace('type_', '')
                    nested['gate_design']['types'][type_id] = {}
                    
                    if 'start' in type_config:
                        start_data = type_config['start']
                        # 检查是否是多个门极（嵌套列表格式）
                        if isinstance(start_data[0], list):
                            # 多个门极的情况
                            nested['gate_design']['types'][type_id]['start_points'] = []
                            for start_point in start_data:
                                nested['gate_design']['types'][type_id]['start_points'].append({
                                    'x_ratio': start_point[0],
                                    'y_ratio': start_point[1]
                                })
                        else:
                            # 单个门极的情况
                            nested['gate_design']['types'][type_id]['start_point'] = {
                                'x_ratio': start_data[0],
                                'y_ratio': start_data[1]
                            }
                    
                    if 'moves' in type_config:
                        moves_data = type_config['moves']
                        # 检查是否是多个门极（三层嵌套列表格式）
                        if isinstance(moves_data[0][0], list):
                            # 多个门极的情况：每个门极有一组moves
                            nested['gate_design']['types'][type_id]['moves_list'] = []
                            for move_group in moves_data:
                                move_list = []
                                for move in move_group:
                                    move_list.append({
                                        'x_ratio': move[0],
                                        'y_ratio': move[1]
                                    })
                                nested['gate_design']['types'][type_id]['moves_list'].append(move_list)
                        else:
                            # 单个门极的情况
                            nested['gate_design']['types'][type_id]['moves'] = []
                            for move in moves_data:
                                nested['gate_design']['types'][type_id]['moves'].append({
                                    'x_ratio': move[0],
                                    'y_ratio': move[1]
                                })
            
            # 处理切割路径参数
            elif flat_key == "cutting_design":
                if 'cutting_design' not in nested:
                    nested['cutting_design'] = {}
                if 'paths' not in nested['cutting_design']:
                    nested['cutting_design']['paths'] = []
                
                path_index = 0
                for path_key, points in value.items():
                    path_data = {
                        'name': f"cut_{path_index + 1}",
                        'points': []
                    }
                    
                    for point in points:
                        x_val = point[0]
                        y_val = point[1]
                        
                        # 处理特殊值 MAX/MIN
                        if x_val == "MAX":
                            x_val = 999
                        elif x_val == "MIN":
                            x_val = -999
                        if y_val == "MAX":
                            y_val = 999
                        elif y_val == "MIN":
                            y_val = -999
                            
                        path_data['points'].append({
                            'x_ratio': x_val,
                            'y_ratio': y_val
                        })
                    
                    nested['cutting_design']['paths'].append(path_data)
                    path_index += 1
            
            # 处理IGBT位置参数
            elif flat_key == "igbt_positions":
                nested['igbt_positions'] = value
            
            # 处理FWD位置参数
            elif flat_key == "fwd_positions":
                nested['fwd_positions'] = value
            
            # 处理IGBT旋转参数
            elif flat_key == "igbt_rotations":
                nested['igbt_rotations'] = value
            
            # 处理FWD旋转参数
            elif flat_key == "fwd_rotations":
                nested['fwd_rotations'] = value
            
            # 处理DBC旋转参数
            elif flat_key == "dbc_rotations":
                nested['dbc_rotations'] = value
            
            # 其他参数保持原样
            else:
                nested[flat_key] = value
        
        return nested

# 辅助函数，用于模板中使用
def build_connection_matrix(connections: List[Dict[str, Any]], entities: List[str]) -> Dict[str, Dict[str, int]]:
    """从连接列表构建连接矩阵"""
    matrix = {}
    
    # 初始化矩阵，所有连接值为0
    for entity in entities:
        matrix[entity] = {e: 0 for e in entities}
    
    # 填入实际连接
    for conn in connections:
        source = conn["source"]
        target = conn["target"]
        value = conn.get("value", 1)
        
        if source in matrix and target in matrix[source]:
            matrix[source][target] = value
    
    return matrix

def build_igbt_configs(setup: Dict[str, Any], positions: List[List[float]]) -> Dict[int, Dict[str, Any]]:
    """从参数构建IGBT配置"""
    configs = {}
    
    for type_id_str, type_config in setup["types"].items():
        type_id = int(type_id_str)
        igbt_positions = []
        
        for idx in type_config["positions"]:
            pos = positions[idx]
            igbt_positions.append(f"Vector({pos[0]:.4f}, {pos[1]:.4f}, 0)")
        
        configs[type_id] = {
            "positions": igbt_positions,
            "rotations": type_config["rotations"]
        }
    
    return configs

def build_fwd_positions(setup: Dict[str, Any], positions: List[List[float]]) -> List[str]:
    """构建FWD位置列表"""
    fwd_positions = []
    
    for idx in setup["positions"]:
        pos = positions[idx]
        fwd_positions.append(f"Vector({pos[0]:.4f}, {pos[1]:.4f}, 0)")
    
    return fwd_positions

def build_gate_configs(design: Dict[str, Any], width_var: str, length_var: str, margin_var: str) -> Dict[int, Dict[str, Any]]:
    """构建门极配置"""
    configs = {}
    
    for type_id_str, type_config in design["types"].items():
        type_id = int(type_id_str)
        
        # 处理多个门极的起始点
        start_points = []
        for start_point in type_config["start_points"]:
            start_x = f"{start_point['x_ratio']} * {width_var} + {margin_var}"
            start_y = f"{start_point['y_ratio']} * {length_var} + {margin_var}"
            start_points.append(f"({start_x}, {start_y})")
        
        # 处理多个门极的移动路径
        relative_moves = []
        for move_list in type_config["moves_list"]:
            moves = []
            for move in move_list:
                move_x = f"{move['x_ratio']} * {width_var}"
                move_y = f"{move['y_ratio']} * {length_var}"
                moves.append(f"({move_x}, {move_y})")
            relative_moves.append(moves)
        
        configs[type_id] = {
            "start_points": start_points,
            "relative_moves": relative_moves
        }
    
    return configs

def build_cutting_paths(paths: List[Dict[str, Any]], width_var: str, length_var: str, margin_var: str, extreme_val: str) -> List[List[str]]:
    """构建切割路径"""
    cut_paths = []
    
    for path in paths:
        points = []
        for point in path["points"]:
            x_ratio = point["x_ratio"]
            y_ratio = point["y_ratio"]
            
            # 处理特殊值（EXTREME）
            if x_ratio == 999:
                x = extreme_val
            elif x_ratio == -999:
                x = f"-{extreme_val}"
            else:
                x = f"{x_ratio} * {width_var} + {margin_var}"
            
            if y_ratio == 999:
                y = extreme_val
            elif y_ratio == -999:
                y = f"-{extreme_val}"
            else:
                y = f"{y_ratio} * {length_var} + {margin_var}"
            
            points.append(f"({x}, {y})")
        
        cut_paths.append(points)
    
    return cut_paths

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="使用 JSON 配置与同目录下的 template.j2 合并生成 CAD 脚本"
    )
    parser.add_argument("json_file", help="输入的 JSON 配置文件路径")
    parser.add_argument(
        "-o",
        "--output",
        help="渲染结果输出路径（不指定则打印到标准输出）",
        default=None,
    )

    args = parser.parse_args()

    processor = ConfigProcessor()

    try:
        rendered_code = processor.process_json_file(args.json_file)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(rendered_code)
        else:
            print(rendered_code)
    except Exception as e:
        print(f"处理失败: {e}")