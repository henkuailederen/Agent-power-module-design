import cadquery as cq
from cadquery import Assembly,Location, Vector, Color, Edge,Face,Workplane
import math
import copy
import numpy as np
from typing import List, Tuple, Union,Callable
from scipy.spatial import distance
from shapely.geometry import Polygon, LineString, Point
from shapely.ops import unary_union
import shapely.geometry as sg
import random


def _assembly_find(self, name):
    for child in self.children:
        if hasattr(child, "name") and child.name == name:
            return child
    return None

Assembly.find = _assembly_find


class BoundaryViolationError(ValueError):
    """自定义边界违规异常"""
    def __init__(self, message, violations, container_bb, path_bb):
        super().__init__(message)
        self.violations = violations
        self.container_bb = container_bb  # (xmin, xmax, ymin, ymax)
        self.path_bb = path_bb  # (min_x, max_x, min_y, max_y)


def generate_relative_points(start, moves):
    """根据相对位置和初始点，得到绝对位置"""
    points = [start]
    current = start
    for dx, dy in moves:
        current = (current[0] + dx, current[1] + dy)
        points.append(current)
    # 添加回到起始点的操作，使图形封闭
    points.append(start)
    return points

def get_color(color_name: str) -> cq.Color:
    """颜色工厂函数，集中管理所有颜色配置"""
    colors = {
        "copper": (184, 115, 51),
        "ceramics": (200, 200, 200),
        "solder": (	100, 100, 100),
        "Al": (128, 128, 128),
        "bond_wire": (255, 255, 255),
        "die": (60, 60, 60),
        "bond_wire_2": (255, 255, 255),
    }
    r, g, b = colors[color_name]
    return cq.Color(r/255, g/255, b/255)

def generate_sketch(start_point, relative_moves, sketch_plane):
    """草图生成"""
    # 生成路径点
    points = generate_relative_points(start_point, relative_moves)
    # 构建草图路径
    sketch = sketch_plane.moveTo(*points[0])
    for point in points[1:]:  # 自动处理所有后续点
        sketch = sketch.lineTo(*point)
    return sketch.close()

def create_extrude(sketch_plane, sketch, thickness, fillet_value):
    """拉伸体生成"""
    # 关键步骤：使用 newObject 创建独立实体
    return (
        sketch_plane  # 继承原始草图平面
        .newObject([sketch.val().wrapped])  # 将草图作为新对象
        .extrude(thickness)
    )

def copy_assembly(original_assy, suffix_id):
    """装配体复制（支持ID嵌入名称）"""
    new_assy = Assembly()
    for child in original_assy.children:
        copied_part = child.obj
        new_loc = child.loc
        new_color = child.color
        # 生成带后缀的新名称
        new_name = f"{child.name}_{suffix_id}"  # 嵌入int参数
        
        if child.children:
            # 递归复制时保持使用同一个后缀ID
            sub_assy = copy_assembly(child, suffix_id)
            new_assy.add(sub_assy, name=new_name, loc=new_loc, color=new_color)
        else:
            new_assy.add(copied_part, name=new_name, loc=new_loc, color=new_color)
    return new_assy

def translate_assembly(assembly: Assembly, angle_degrees: float, translate_vec: Vector) -> Assembly:
    """先平移装配体，再绕新的几何中心旋转指定角度（Z轴旋转）"""
    # 计算旋转角度（转化为弧度）
    angle_rad = math.radians(angle_degrees)
    cos_theta = math.cos(angle_rad)
    sin_theta = math.sin(angle_rad)

    # 构建旋转矩阵（XY平面内旋转，Z轴不变）
    rotation_matrix = [
        [cos_theta, -sin_theta],
        [sin_theta, cos_theta]
    ]
    
    # 先平移装配体
    #assembly.loc = cq.Location(translate_vec) * assembly.loc
    for child in assembly.children:
        child.loc = cq.Location(translate_vec) * child.loc
    # 计算平移后的装配体几何中心
    compound = assembly.toCompound()
    bbox = compound.BoundingBox()
    center = Vector(
        (bbox.xmin + bbox.xmax) / 2,
        (bbox.ymin + bbox.ymax) / 2,
        (bbox.zmin + bbox.zmax) / 2
    )
    
    # 旋转操作，绕新的几何中心进行
    rotation = Location(Vector(), Vector(0, 0, 1), angle_degrees)
    
    # 构建变换矩阵：平移 → 旋转（绕新的几何中心）
    transform = (
        Location(center) * 
        rotation * 
        Location(-center)
    )
    for child in assembly.children:
        child.loc = transform * child.loc
        
    #assembly.loc = rotation * assembly.loc

    return assembly

# --- Helper function to get top face reliably ---
def get_top_face(part: Union[cq.Workplane, cq.Assembly, cq.Solid, cq.Compound]) -> cq.Face:
    """Safely get the top face (+Z) of a CadQuery object."""
    if isinstance(part, cq.Assembly):
        # Assume assembly has a single main object or we take the first one
        # Need to consider the assembly's location
        if not part.obj:
             raise ValueError(f"Assembly '{part.name}' has no object.")
        geom = part.obj.val() # Get shape from assembly's object
        if isinstance(geom, cq.Workplane):
             geom = geom.combine().val()
        located_geom = geom.located(part.loc)
        return cq.Workplane().add(located_geom).faces(">Z").first().val()
    elif isinstance(part, cq.Workplane):
        geom = part.val()
        if isinstance(geom, (cq.Solid, cq.Compound)):
             return part.faces(">Z").first().val()
        else: # Try combining if it's complex
             return part.combine().faces(">Z").first().val()
    elif isinstance(part, (cq.Solid, cq.Compound)):
        return cq.Workplane().add(part).faces(">Z").first().val()
    else:
        raise TypeError(f"Unsupported type for get_top_face: {type(part)}")

# --- Helper function to check if point is inside face (basic) ---
def is_point_on_face_2d(face: cq.Face, point: cq.Vector, tolerance=1e-3) -> bool:
    """Basic check if 2D projection of point is within face bounds (XY plane)."""
    # More robust check would use face.isInside() or Shapely
    try:
        # Project face vertices to 2D
        wp = cq.Workplane("XY").workplane(offset=face.Center().z)
        verts_2d = [wp.plane.toLocalCoords(v).toTuple()[:2] for v in face.Vertices()]
        if not verts_2d:
            # Handle cases like circular faces with no simple vertices
            # Use bounding box as fallback for now
             bbox = face.BoundingBox()
             return (bbox.xmin - tolerance <= point.x <= bbox.xmax + tolerance and
                     bbox.ymin - tolerance <= point.y <= bbox.ymax + tolerance)

        # Create Shapely polygon
        poly = Polygon(verts_2d)
        # Create Shapely point
        pnt = (point.x, point.y)
        # Check if point is within the polygon
        return poly.contains(Point(pnt)) or poly.boundary.distance(Point(pnt)) < tolerance
    except ImportError:
         print("Shapely not installed, falling back to BBox check for is_point_on_face_2d")
         # Fallback to bounding box check
         bbox = face.BoundingBox()
         return (bbox.xmin - tolerance <= point.x <= bbox.xmax + tolerance and
                 bbox.ymin - tolerance <= point.y <= bbox.ymax + tolerance)
    except Exception as e:
        print(f"Error in is_point_on_face_2d: {e}. Falling back to BBox check.")
        bbox = face.BoundingBox()
        return (bbox.xmin - tolerance <= point.x <= bbox.xmax + tolerance and
                 bbox.ymin - tolerance <= point.y <= bbox.ymax + tolerance)

def sample_points_on_face_shapely(
    face,
    num_points=10,
    offset_distance=0.25,
    sampling_method="boundary", # "boundary", "grid", "random", "combined"
    tolerance=0.05,
    min_edge_points=3,
    grid_density=5,
    random_points=10,
    preserve_corners=True,
    default_z=None
):
    """
    在面上采样点，使用shapely库进行轮廓内缩然后采样。
    适合处理复杂形状的面，确保采样点严格位于面内部。
    
    Args:
        face: CadQuery的Face对象
        num_points: 需要采样的点数
        offset_distance: 边界内缩距离
        sampling_method: 
            - "boundary": 在内缩后的边界上均匀采样
            - "grid": 在内缩后的区域内用网格采样
            - "random": 在内缩后的区域内随机采样
            - "combined": 结合边界和内部采样
        tolerance: 几何计算的容差
        min_edge_points: 每条边缘至少采样的点数
        grid_density: 网格密度倍数(用于"grid"和"combined"方法)
        random_points: 随机点数量(用于"random"和"combined"方法)
        preserve_corners: 是否保留内缩后多边形的角点
        default_z: 可选，默认Z坐标，如果为None则使用面的中心Z坐标
    
    Returns:
        采样的点列表，每个点为Vector对象
    """
    import numpy as np
    
    if num_points <= 0:
        return []
    
    # 1. 获取面的参数，包括中心点和Z坐标
    face_center = face.Center()
    z_coord = face_center.z if default_z is None else default_z
    
    try:
        # 2. 提取面的轮廓顶点
        outer_wire = face.outerWire()
        vertices_3d = [v.toTuple() for v in outer_wire.Vertices()]
        
        # 提取XY平面上的二维顶点
        vertices_2d = [(v[0], v[1]) for v in vertices_3d]
        
        # 3. 创建shapely多边形对象
        if len(vertices_2d) < 3:
            # 如果顶点过少，可能是圆形或其他特殊形状，尝试更精细的采样
            edges = face.Edges()
            all_points = []
            for edge in edges:
                # 沿每条边采样更多点
                for t in np.linspace(0, 1, 20):
                    pt = edge.positionAt(t)
                    all_points.append((pt.x, pt.y))
            vertices_2d = all_points
        
        # 确保多边形封闭
        if vertices_2d[0] != vertices_2d[-1]:
            vertices_2d.append(vertices_2d[0])
        
        # 创建shapely多边形
        original_polygon = sg.Polygon(vertices_2d)
        if not original_polygon.is_valid:
            print(f"警告: 初始多边形无效，尝试使用buffer(0)修复...")
            original_polygon = original_polygon.buffer(0)
            if not original_polygon.is_valid:
                print(f"错误: 无法创建有效多边形。返回面中心点。")
                return [face_center]
        
        # 4. 内缩多边形
        shrunk_polygon = original_polygon.buffer(-offset_distance)
        
        # 检查内缩结果
        if shrunk_polygon.is_empty:
            print(f"警告: 内缩距离 {offset_distance} 太大，导致空多边形。减小偏移并重试。")
            # 尝试减小偏移距离
            shrunk_polygon = original_polygon.buffer(-offset_distance/2)
            if shrunk_polygon.is_empty:
                print(f"错误: 即使减小偏移距离，仍无法获得有效内缩轮廓。返回面中心点。")
                return [face_center]
        
        # 处理多多边形情况 (内缩可能将一个复杂多边形分解为多个)
        if shrunk_polygon.geom_type == 'MultiPolygon':
            # 选择最大面积的内缩多边形
            largest_poly = max(shrunk_polygon.geoms, key=lambda p: p.area)
            shrunk_polygon = largest_poly
            
        # 5. 采样点
        sampled_points = []
        
        # 5.1 边界采样
        if sampling_method in ["boundary", "combined"]:
            # 获取内缩多边形的外边界
            boundary = shrunk_polygon.exterior
            
            # 计算边界总长度
            boundary_length = boundary.length
            
            # 需要采样的点数
            num_boundary_points = num_points if sampling_method == "boundary" else max(3, num_points // 2)
            
            # 在边界上均匀采样点
            points_2d = []
            
            # 如果要保留角点，先添加轮廓的所有顶点
            if preserve_corners:
                # 获取轮廓上的顶点坐标
                coords = list(boundary.coords)
                # 移除最后一个点，因为它与第一个点重合
                corner_points = coords[:-1]
                
                # 只取部分角点，如果角点太多的话
                if len(corner_points) > num_boundary_points // 2:
                    # 均匀选择角点
                    step = len(corner_points) / (num_boundary_points // 2)
                    corner_points = [corner_points[int(i*step)] for i in range(min(len(corner_points), num_boundary_points // 2))]
                
                points_2d.extend(corner_points)
            
            # 计算还需要的边界点数量
            remaining_points = num_boundary_points - len(points_2d)
            
            if remaining_points > 0:
                # 均匀分布剩余采样点
                for i in range(remaining_points):
                    # 计算沿边界的规范化位置 (0到1)
                    distance = (i / remaining_points) * boundary_length
                    # 获取该位置的点
                    point = boundary.interpolate(distance)
                    points_2d.append((point.x, point.y))
            
            # 转换为三维点
            for x, y in points_2d:
                sampled_points.append(cq.Vector(x, y, z_coord))
        
        # 5.2 网格采样
        if sampling_method in ["grid", "combined"]:
            grid_points_needed = num_points if sampling_method == "grid" else max(2, num_points - len(sampled_points))
            
            # 获取内缩多边形的外接矩形
            minx, miny, maxx, maxy = shrunk_polygon.bounds
            
            # 确定网格密度
            x_step = (maxx - minx) / (grid_density + 1)
            y_step = (maxy - miny) / (grid_density + 1)
            
            # 生成候选网格点
            grid_candidates = []
            for i in range(1, grid_density + 1):
                for j in range(1, grid_density + 1):
                    x = minx + i * x_step
                    y = miny + j * y_step
                    point = sg.Point(x, y)
                    if shrunk_polygon.contains(point):
                        grid_candidates.append((x, y))
            
            # 如果候选点不足，尝试更密集的网格
            if len(grid_candidates) < grid_points_needed:
                denser_grid = []
                denser_steps = 2 * grid_density
                x_step = (maxx - minx) / (denser_steps + 1)
                y_step = (maxy - miny) / (denser_steps + 1)
                
                for i in range(1, denser_steps + 1):
                    for j in range(1, denser_steps + 1):
                        x = minx + i * x_step
                        y = miny + j * y_step
                        point = sg.Point(x, y)
                        if shrunk_polygon.contains(point):
                            denser_grid.append((x, y))
                
                grid_candidates = denser_grid
            
            # 选择最终网格点
            if len(grid_candidates) <= grid_points_needed:
                selected_grid_points = grid_candidates
            else:
                # 均匀选择指定数量的网格点
                step = len(grid_candidates) / grid_points_needed
                indices = [int(i * step) for i in range(grid_points_needed)]
                selected_grid_points = [grid_candidates[i] for i in indices]
            
            # 转换为三维点并添加
            for x, y in selected_grid_points:
                sampled_points.append(cq.Vector(x, y, z_coord))
        
        # 5.3 随机采样
        if sampling_method in ["random", "combined"]:
            random_points_needed = num_points if sampling_method == "random" else max(2, num_points - len(sampled_points))
            
            # 获取内缩多边形的外接矩形
            minx, miny, maxx, maxy = shrunk_polygon.bounds
            
            # 尝试生成随机点，直到获取足够数量或达到最大尝试次数
            random_candidates = []
            max_attempts = random_points_needed * 10
            attempts = 0
            
            while len(random_candidates) < random_points_needed and attempts < max_attempts:
                x = random.uniform(minx, maxx)
                y = random.uniform(miny, maxy)
                point = sg.Point(x, y)
                
                if shrunk_polygon.contains(point):
                    # 检查与已有点的距离，避免点太密集
                    too_close = False
                    for rx, ry in random_candidates:
                        if ((x - rx)**2 + (y - ry)**2)**0.5 < tolerance * 2:
                            too_close = True
                            break
                    
                    if not too_close:
                        random_candidates.append((x, y))
                
                attempts += 1
            
            # 转换为三维点并添加
            for x, y in random_candidates:
                sampled_points.append(cq.Vector(x, y, z_coord))
        
        # 6. 确保至少有一个点
        if not sampled_points:
            print(f"警告: 采样方法 '{sampling_method}' 未能生成任何点。添加多边形内部中心点。")
            
            if isinstance(shrunk_polygon.centroid, sg.Point):
                centroid = shrunk_polygon.centroid
                sampled_points.append(cq.Vector(centroid.x, centroid.y, z_coord))
            else:
                # 退回到使用面中心点
                sampled_points.append(face_center)
        
        # 7. 如果采样点还是不足，重复一些点
        while len(sampled_points) < num_points:
            idx = len(sampled_points) % len(sampled_points)
            sampled_points.append(sampled_points[idx])
        
        # 8. 如果有过多的点，截取需要的数量
        if len(sampled_points) > num_points:
            # 均匀选取点
            step = len(sampled_points) / num_points
            indices = [int(i * step) for i in range(num_points)]
            sampled_points = [sampled_points[i] for i in indices]
        
        return sampled_points
    
    except Exception as e:
        print(f"采样门极点时出错: {e}")
        # 出错时返回面中心
        return [face_center]

# --- Helper function to calculate inward normal ---
def calculate_inward_normal(face: cq.Face, edge: cq.Edge, param_t: float) -> cq.Vector:
    """Calculate inward normal vector on the face plane, perpendicular to the edge."""
    point = edge.positionAt(param_t)
    tangent = edge.tangentAt(param_t).normalized()
    # Assume top face normal is +Z
    face_normal = Vector(0, 0, 1)
    # Calculate perpendicular vector in XY plane
    perp_vec = face_normal.cross(tangent).normalized()

    # Test which direction points inward
    test_dist = 0.1 # Small distance to test
    test_point_1 = point + perp_vec * test_dist
    # test_point_2 = point - perp_vec * test_dist
    
    # Use the 2D check
    if is_point_on_face_2d(face, test_point_1):
        return perp_vec
    else:
         # Check the opposite direction too, just in case the first guess was wrong
         test_point_2 = point - perp_vec * test_dist
         if is_point_on_face_2d(face, test_point_2):
              return -perp_vec
         else:
              # Fallback if neither points inward (e.g., point exactly on corner, weird geometry)
              # Return the first guess, might cause issues but better than erroring.
              print(f"警告: 无法确定 {point.toTuple()} 处边缘的内法线方向。使用默认猜测。")
              return perp_vec

# --- Helper function to select facing edges (simplified for Y-offset) ---
def select_facing_edges_y(face1: cq.Face, face2: cq.Face) -> Tuple[cq.Edge, cq.Edge]:
    """Selects the max Y edge of face1 and min Y edge of face2."""
    # Assumes face1 is below face2 in Y
    edge1 = max(face1.Edges(), key=lambda e: e.Center().y)
    edge2 = min(face2.Edges(), key=lambda e: e.Center().y)
    # Basic check if edges seem reasonable (roughly horizontal)
    tolerance = 0.1
    if abs(edge1.tangentAt(0.5).y) > tolerance or abs(edge2.tangentAt(0.5).y) > tolerance:
         print("警告: 选择的相对边不是严格水平的。连接可能倾斜。")
    return edge1, edge2


def create_upper_Cu_base(
    Cu_slot,
    ceramics,
    start_points_Gate: list, 
    relative_moves_Gate_list: list,  
    thickness_upper_Cu,
    fillet_value,
    width_ceramics,
    distance_Cu_Ceramics,
    length_ceramics
):
    """根据Gate和Emitter创建Collector"""
    ceramics_top_plane = (
    cq.Workplane("XY")  # 初始平面
    .workplane(offset=ceramics.faces(">Z").val().Center().z)  # 手动对齐到陶瓷顶面高度
)

    offset_Gates = []
    for i in range(len(start_points_Gate)):
        # 生成Gate路径
        points_Gate = generate_relative_points(start_points_Gate[i], relative_moves_Gate_list[i])
        # 生成偏移后的Gate草图
        offset_sketch_Gate = (
            ceramics_top_plane
            .moveTo(*points_Gate[0])
            .polyline(points_Gate[1:])
            .close()
            .offset2D(Cu_slot, kind="intersection")
        )
        # 拉伸实体
        offset_Gate = create_extrude(ceramics_top_plane, offset_sketch_Gate, thickness_upper_Cu, fillet_value)
        offset_Gates.append(offset_Gate)

    # 创建基础铜层并切割
    offset_3d = (
        ceramics_top_plane
        .box(
            width_ceramics - 2*distance_Cu_Ceramics,
            length_ceramics - 2*distance_Cu_Ceramics,
            thickness_upper_Cu,
            centered=(False, False, False)
        )
        .translate((distance_Cu_Ceramics, distance_Cu_Ceramics, 0))
    )

    upper_Cu_Collector = offset_3d # 初始化为基础铜层
    for gate in offset_Gates:
        upper_Cu_Collector = upper_Cu_Collector.cut(gate) # 在上一次切割结果上继续切割

    return upper_Cu_Collector

def create_connector(distance_Cu_Ceramics,distance_DBC_DBC):
    """连接端子创建"""
    connector_thinkness = 2
    start_point_connector = (0,0)
    relative_moves_connector = [
        (0, connector_thinkness),
        (3, 0),
        (0, connector_thinkness+3),
        (2*(connector_thinkness+distance_Cu_Ceramics+distance_DBC_DBC), 0),
        (0, -(connector_thinkness+3)),
         (3,0),
         (0,-connector_thinkness),
         (-3-connector_thinkness,0),
         (0,connector_thinkness+3),
         (-2*(distance_Cu_Ceramics+distance_DBC_DBC),0),
        (0,-(connector_thinkness+3))
    ]
    connector_plane = (
        cq.Workplane("YZ")
    )
    sketch_connector = generate_sketch(
        start_point_connector,
        relative_moves_connector,
        connector_plane
    )
    return (
        sketch_connector
        .extrude(4)
        .edges("|X").fillet(0.5)
    )


def get_x_range_at_y(target_face,x_value, y_value, tolerance=1e-1):
    """确定bond wire的连接点"""
    try:
        x_coords = []
        # 明确从Face对象中提取边线
        edges = target_face.edges()
        
        for edge in edges:
            # 离散化边线为点集
            points = np.array([edge.positionAt(param).toTuple() 
                              for param in np.linspace(0, 1, 100)])
            
            # 筛选Y值附近的点
            mask = np.abs(points[:,1] - y_value) <= tolerance
            nearby_points = points[mask]
            
            if len(nearby_points) > 0:
                x_coords.extend(nearby_points[:,0])
        
        if not x_coords:
            return None
        dist_min = abs(min(x_coords) - x_value)
        dist_max = abs(max(x_coords) - x_value)
        if dist_min < dist_max:
            return min(x_coords)+2
        else:
            return max(x_coords)-2
    except Exception as e:
        print(f"计算失败: {str(e)}")
        return None

def create_ceramics(width: float, 
                        length: float, 
                        thickness: float,
                        centered: tuple = (False, False, False)) -> cq.Workplane:
    """
    创建陶瓷基板
    参数：
        width: X轴方向宽度
        length: Y轴方向长度
        thickness: Z轴方向厚度
        centered: 中心对齐方式，默认左下角对齐原点
    返回：
        cadquery生成的陶瓷板对象
    """
    return (
        cq.Workplane("XY")
        .box(width, length, thickness, centered=centered)
    )

def create_upper_Cu(
    layer_type: str,
    ceramics: cq.Workplane,
    start_point: tuple,
    relative_moves: list,
    thickness: float,
    fillet_value: float,
    margin: float = 1.5,
    offset_plane: str = "XY",
) -> cq.Workplane:
    """
    统一铜层创建接口
    
    参数：
        layer_type: 铜层类型 ['emitter', 'gate', 'collector']
        ceramics: 陶瓷基板对象
        start_point: 路径起始点 (x, y)
        relative_moves: 相对移动路径列表 [(dx, dy), ...]
        thickness: 铜层厚度 (>0)
        fillet_value: 圆角半径 (>=0)
        margin: 安全边距 (默认1.5mm)
        offset_plane: 工作平面 (默认"XY")
        kwargs: 类型专用参数
        
    返回：
        创建完成的铜层对象
        
    异常：
        ValueError: 参数无效或路径违规
        BoundaryViolationError: 超出陶瓷板边界
    """
    # 参数基础验证
    if thickness <= 0:
        raise ValueError(f"铜层厚度必须大于0，当前值：{thickness}")
    if fillet_value < 0:
        raise ValueError(f"圆角值不能为负数，当前值：{fillet_value}")
    if layer_type not in ['emitter', 'gate', 'collector']:
        raise ValueError(f"不支持的铜层类型：{layer_type}")

    # 获取陶瓷板边界
    ceramic_bb = ceramics.val().BoundingBox()
    container_bounds = (
        ceramic_bb.xmin, ceramic_bb.xmax,
        ceramic_bb.ymin, ceramic_bb.ymax
    )

    # 生成完整路径点
    path_points = generate_relative_points(start_point, relative_moves)

    # 创建顶面工作平面
    top_z = ceramics.faces(">Z").val().Center().z
    top_plane = cq.Workplane(offset_plane).workplane(offset=top_z)

    # 生成草图
    copper_sketch = generate_sketch(
        start_point=start_point,
        relative_moves=relative_moves,
        sketch_plane=top_plane
    )

    # 类型专用处理
    if layer_type == 'collector':
        # Collector特殊圆角处理
        fillet_value = max(fillet_value, 0.8)  # 保证最小圆角
    
    # 创建三维实体
    copper_layer = create_extrude(
        sketch_plane=top_plane,
        sketch=copper_sketch,
        thickness=thickness,
        fillet_value=fillet_value
    )

    return copper_layer

def create_bottom_copper_layer(
    ceramics: cq.Workplane,
    width_ceramics: float,
    length_ceramics: float,
    distance_Cu_Ceramics: float = 1.5,
    radius_bottom_hex: float = 2.0,
    hex_spacing: float = -1.5,
    thickness_bottom_Cu: float = 0.3
) -> cq.Workplane:
    """
    创建蜂窝状排列的下铜层
    
    参数：
        ceramics: 陶瓷板对象
        width_ceramics: 陶瓷板宽度
        length_ceramics: 陶瓷板长度
        distance_Cu_Ceramics: 铜层到陶瓷板边缘的距离 (默认1.5mm)
        radius_bottom_hex: 六边形外接圆半径 (默认2.0mm)
        hex_spacing: 六边形间净空距离 (默认-1.5mm)
        thickness_bottom_Cu: 下铜层厚度 (默认0.3mm)
        
    返回：
        蜂窝状排列的下铜层对象
    """
    # 参数验证
    if radius_bottom_hex <= 0:
        raise ValueError("六边形半径必须大于0")
    if thickness_bottom_Cu <= 0:
        raise ValueError("铜层厚度必须大于0")
    if distance_Cu_Ceramics < 0:
        raise ValueError("铜瓷间距不能为负数")

    # 计算有效区域
    effective_width = width_ceramics - 2 * distance_Cu_Ceramics
    effective_length = length_ceramics - 2 * distance_Cu_Ceramics

    # 计算排列参数
    center_spacing = 2 * radius_bottom_hex + hex_spacing
    vertical_spacing = center_spacing * math.sqrt(3) / 2
    row_offset = center_spacing / 2

    # 计算有效区域边界
    min_x = distance_Cu_Ceramics + radius_bottom_hex + hex_spacing / 2
    max_x = width_ceramics - distance_Cu_Ceramics - radius_bottom_hex - hex_spacing / 2
    min_y = distance_Cu_Ceramics + radius_bottom_hex + hex_spacing / 2
    max_y = length_ceramics - distance_Cu_Ceramics - radius_bottom_hex - hex_spacing / 2

    # 生成蜂窝状排列点阵
    points = generate_hex_grid_points(
        min_x, max_x,
        min_y, max_y,
        center_spacing, 
        vertical_spacing,
        row_offset
    )

    # 创建底面工作平面
    bottom_z = ceramics.faces("<Z").val().Center().z
    bottom_workplane = cq.Workplane("XY").workplane(offset=bottom_z)

    # 生成单个六边形
    single_hex = (
        cq.Workplane("XY")
        .polygon(6, radius_bottom_hex)
        .extrude(-thickness_bottom_Cu)
    )

    # 生成阵列
    return (
        bottom_workplane
        .pushPoints(points)
        .each(lambda loc: single_hex.val().moved(loc))
        .combine()
    )

def generate_hex_grid_points(
    min_x: float,
    max_x: float,
    min_y: float,
    max_y: float,
    center_spacing: float,
    vertical_spacing: float,
    row_offset: float
) -> list:
    """
    生成蜂窝状排列的中心点坐标
    """
    points = []
    for row in range(int((max_y - min_y) / vertical_spacing) + 2):
        y = min_y + row * vertical_spacing
        x_offset = (row % 2) * row_offset
        
        for col in range(int((max_x - min_x) / center_spacing) + 2):
            x = min_x + col * center_spacing + x_offset
            if x <= max_x and y <= max_y:
                points.append((x, y))
    return points

def create_substrate_solder(
    bottom_copper: cq.Workplane,
    width_ceramics: float,
    length_ceramics: float,
    distance_Cu_Ceramics: float,
    thickness_substrate_solder: float
) -> cq.Workplane:
    """
    创建基板焊料层
    
    参数：
        bottom_copper: 下铜层对象（用于确定Z轴位置）
        width_ceramics: 陶瓷板总宽度
        length_ceramics: 陶瓷板总长度
        distance_Cu_Ceramics: 铜层到陶瓷边缘的距离
        thickness_substrate_solder: 焊料层厚度
        
    返回：
        位于下铜层下方的焊料层对象
    """
    # 参数验证
    if thickness_substrate_solder<= 0:
        raise ValueError("焊料层厚度必须大于0")
    if distance_Cu_Ceramics < 0:
        raise ValueError("铜瓷间距不能为负数")
    if width_ceramics <= 2*distance_Cu_Ceramics:
        raise ValueError("陶瓷板宽度不足以容纳焊料层")
    if length_ceramics <= 2*distance_Cu_Ceramics:
        raise ValueError("陶瓷板长度不足以容纳焊料层")

    # 获取下铜层底面Z坐标
    bottom_z = bottom_copper.faces("<Z").val().Center().z
    
    # 创建工作平面
    solder_plane = (
        cq.Workplane("XY")
        .workplane(offset=bottom_z)
    )
    
    # 计算焊料层实际尺寸
    solder_width = width_ceramics - 2*distance_Cu_Ceramics
    solder_length = length_ceramics - 2*distance_Cu_Ceramics
    
    # 创建焊料层
    return (
        solder_plane
        .box(
            solder_width, 
            solder_length, 
            thickness_substrate_solder,
            centered=(False, False, False)  # 左下角对齐原点
        )
        .translate((
            distance_Cu_Ceramics,  # X方向偏移铜瓷间距
            distance_Cu_Ceramics,  # Y方向偏移铜瓷间距
            0  # Z方向保持底面位置
        ))
    )

def create_die_assembly(
    die_type: str,
    zone: cq.Workplane,
    die_width: float,
    die_length: float,
    die_thickness: float,
    solder_thickness: float,
    position: Vector,  # Changed from List[Vector] to Vector
) -> tuple[Assembly, cq.Workplane]: # Return single Assembly and Workplane
    """
    Creates a single power device assembly (die + solder) at a specified position.
    
    Args:
        die_type: Device type ('IGBT'/'FWD')
        zone: Reference Workplane (e.g., upper copper layer) to determine Z level.
        die_width: Die width
        die_length: Die length
        die_thickness: Die thickness
        solder_thickness: Solder thickness
        position: The XYZ position vector for this specific die assembly.
        
    Returns:
        (Assembly, cq.Workplane): A tuple containing the created assembly and the die Workplane object.
    """
    # Parameter validation
    if die_type not in ['IGBT', 'FWD']:
        raise ValueError("Unsupported device type")
    if not isinstance(position, Vector):
         raise TypeError("position must be a cadquery.Vector")

    base_z = zone.faces(">Z").val().Center().z
    
    # Create solder layer
    # Position is handled by the main script's translate_assembly now
    solder_plane = cq.Workplane("XY").workplane(offset=base_z)
    solder = solder_plane.box(
        die_width, die_length, solder_thickness,
        centered=(False, False, False)
    )
    
    # Create die layer
    die_plane = cq.Workplane("XY").workplane(offset=solder.faces(">Z").val().Center().z)
    die = die_plane.box(
        die_width, die_length, die_thickness,
        centered=(False, False, False)
    )
    
    # Create assembly
    assembly = Assembly()
    # Use simpler names, positioning handled outside
    assembly.add(solder, name=f"{die_type}_solder", color=get_color("solder")) 
    assembly.add(die, name=f"{die_type}_die", color=get_color("die"))
    
    # Return the single assembly and die object
    return assembly, die

# 计算芯片上的连接点坐标
def calculate_connection_points(die, rotation, position, initial_coordinates):
    # 获取 die 工作平面的边界框，计算 die 的中心点
    compound = die.toCompound()
    bbox = compound.BoundingBox()
    die_center = Vector(
        (bbox.xmin + bbox.xmax) / 2,
        (bbox.ymin + bbox.ymax) / 2,
        (bbox.zmin + bbox.zmax) / 2
    )

    # 计算旋转矩阵
    angle_rad = math.radians(rotation)  # 将旋转角度转换为弧度
    cos_theta = math.cos(angle_rad)
    sin_theta = math.sin(angle_rad)

    # 用于旋转的矩阵，假设旋转是在XY平面内进行的（Z轴不变）
    rotation_matrix = [
        [cos_theta, -sin_theta],
        [sin_theta, cos_theta]
    ]

    # 计算连接点的绝对坐标
    connection_points = []

    for (dx, dy, dz) in initial_coordinates:
        # 步骤 1：将连接点相对于 die 的中心点平移
        translated_x = dx + position.x - die_center.x
        translated_y = dy + position.y - die_center.y
        translated_z = dz + position.z
        
        # 步骤 2：绕平移后的 die 中心点旋转
        x_rot = (translated_x) * rotation_matrix[0][0] + (translated_y) * rotation_matrix[0][1] + die_center.x
        y_rot = (translated_x) * rotation_matrix[1][0] + (translated_y) * rotation_matrix[1][1] + die_center.y
        z_rot = translated_z  # Z轴不受影响

        # 添加旋转后的连接点
        connection_points.append(cq.Vector(x_rot, y_rot, z_rot))

    return connection_points

def get_coordinate(target_face, point, direction, tolerance=1e-1, offset=2):
    """沿指定方向确定边界连接点坐标，返回完整坐标 (x, y)"""
    try:
        x, y = point
        dx, dy = direction
        
        # 确定投影轴和待分析轴
        if abs(dy) > abs(dx):   # 沿y轴方向（原功能特例）
            check_axis = 1      # 检查y轴附近
            target_value = y
            process_axis = 0    # 处理x轴坐标
        else:                   # 沿x轴方向（新扩展功能）
            check_axis = 0      # 检查x轴附近
            target_value = x
            process_axis = 1    # 处理y轴坐标

        coords = []
        edges = target_face.edges()
        
        for edge in edges:
            points = np.array([edge.positionAt(param).toTuple() 
                             for param in np.linspace(0, 1, 300)])
            
            # 筛选目标轴附近的点
            mask = np.abs(points[:, check_axis] - target_value) <= tolerance
            nearby_points = points[mask]
            
            if len(nearby_points) > 0:
                coords.extend(nearby_points[:, process_axis])
        
        if not coords:
            return None

        # 计算最近边界并偏移
        boundary_min = min(coords)
        boundary_max = max(coords)
        ref_value = point[process_axis]  # 原参考点在本轴的值
        
        # 选择更接近参考值的边界，应用偏移
        if abs(boundary_min - ref_value) < abs(boundary_max - ref_value):
            adjusted_value = boundary_min + offset
        else:
            adjusted_value = boundary_max - offset
        
        # 组合成完整坐标 (x, y)
        if process_axis == 0:  # 处理的是X轴，保留原Y坐标
            return (adjusted_value, target_value,target_face.val().Center().z)
        else:                  # 处理的是Y轴，保留原X坐标
            return (target_value, adjusted_value,target_face.val().Center().z)
            
    except Exception as e:
        print(f"计算失败: {str(e)}")
        return None
     
TOLERANCE = 0.1
def compute_edge_intersection_3D(edge1, edge2, tol=1e-8):
    """
    计算两条直线边在同一平面（假定共面）的交点，并返回一个三维点（cadquery.Vector）。
    假设两条边的 z 坐标相同或非常接近。
    
    参数:
      edge1, edge2: cadquery.Edge 对象，代表直线边
      tol: 数值容差，用于判断两边是否平行
      
    返回:
      交点 (cadquery.Vector)，如果两边平行或无交点则返回 None。
    """
    # 获取每条边的起点和终点
    p1 = edge1.startPoint()
    p2 = edge1.endPoint()
    q1 = edge2.startPoint()
    q2 = edge2.endPoint()
    
    # 计算边的方向向量（归一化）
    d1 = (p2 - p1).normalized()
    d2 = (q2 - q1).normalized()
    
    # 在 XY 平面上计算叉积，仅使用 x, y 分量
    cross = d1.x * d2.y - d1.y * d2.x
    if abs(cross) < tol:
        return None  # 两边平行或近似平行
    
    diff = q1 - p1
    # 求解直线1参数 t，使得 p1 + t*d1 与直线2相交
    t = (diff.x * d2.y - diff.y * d2.x) / cross
    intersection = p1 + d1 * t
    # 取 p1.z 作为 z 坐标（假设两边共面）
    return cq.Vector(intersection.x, intersection.y, p1.z)

def get_offset_top_face(part: cq.Assembly, offset: float, extrusion_depth: float = 1.0) -> cq.Face:
    """
    改进版：获取零件顶面，尝试进行内缩偏移。如果偏移失败，则返回原始顶面。
    """
    # 获取顶面和工作平面
    # --- Ensure part.obj is handled correctly ---
    if isinstance(part.obj, cq.Workplane):
        original_geom = part.obj.combine().val() # Combine first
    else:
        original_geom = part.obj # Assume it's already a Solid/Compound
    
    if not hasattr(original_geom, 'located'):
        raise TypeError(f"Could not get locatable geometry from part '{part.name}'")
        
    part_loc = part.loc
    transformed_obj = original_geom.located(part_loc)
    # --- Use helper function ---
    top_face = get_top_face(transformed_obj)
    
    wp = (
    cq.Workplane("XY")  # 初始平面
    .workplane(top_face.Center().z)  # 手动对齐到陶瓷顶面高度
    )
    
    # 选择最大面积的外圈Wire
    wires = top_face.Wires()
    if not wires:
        raise ValueError("顶面没有找到任何 Wire")
    outer_wire = max(wires, key=lambda w: cq.Face.makeFromWires(w).Area())
    
    # 转换顶点到工作平面二维坐标
    pts = [
        wp.plane.toLocalCoords(v).toTuple()[:2]  # 投影到二维
        for v in outer_wire.Vertices()
    ]
    
    # 构建闭合草图（显式闭合）
    base_sketch = (
        wp.moveTo(*pts[0])
        .polyline(pts[1:], includeCurrent=True)
        .close()
    )
    
    # 执行内偏移（注意负号）
    offset_sketch = base_sketch.offset2D(
        offset,  # 负值表示内缩
        kind="intersection",
    )
    # 向下拉伸并提取底面
    solid = create_extrude(wp, offset_sketch, -1, 0.5)
    return solid.faces(">Z").val()


def generate_mapped_points(
    part1: cq.Workplane,
    part2: cq.Workplane,
    edge_selector1: Callable[[Face], Edge],
    edge_selector2: Callable[[Face], Edge],
    num_points: int,            # 新增：明确控制点数
    distance_interval: float,   # 新增：点间距控制
    max_angle: float = 45,
    offset_distance: float = -2,
    angle_step: float = 1
) -> Tuple[List[Vector], List[Vector], float]:
    """
    全自定义映射点生成器（点数+间距控制版）
    
    参数：
    - num_points: 生成的对称点数（必须≥1）
    - distance_interval: 相邻点间距（毫米）
    """
    
    # 获取上表面
    def get_top_face(part):
        return part.faces(">Z").val()
    
    top1 = get_offset_top_face(part1, offset_distance)
    top2 = get_offset_top_face(part2, offset_distance)
    
    # 执行自定义边选择
    try:
        edge1 = edge_selector1(top1)
        edge2 = edge_selector2(top2)
    except Exception as e:
        raise RuntimeError(f"边选择失败: {str(e)}")
    
    # 确定源边和目标边（短边作为源边）
    src_edge, tgt_edge = sorted([edge1, edge2], key=lambda e: e.Length())
    
    # 生成对称间隔点
    def generate_symmetrical_points(edge):
        L = edge.Length()
        if num_points < 1:
            raise ValueError("点数必须≥1")
        if distance_interval <= 0:
            raise ValueError("间距必须>0")
        
        # 计算参数步长（考虑几何对称性）
        t_step = distance_interval / L
        steps = [i - (num_points-1)/2 for i in range(num_points)]  # 生成对称步数
        
        # 计算所有t值（以边中点为中心）
        t_values = [0.5 + s*t_step for s in steps]
        
        # 边界检查
        if any(t < 0 or t > 1 for t in t_values):
            raise ValueError(f"参数超出边界：边长为{L}mm，需要{num_points}点，间距{distance_interval}mm")
        
        return [edge.positionAt(t) for t in sorted(t_values)]  # 确保从左到右顺序
    
    try:
        src_points = generate_symmetrical_points(src_edge)
    except ValueError as e:
        raise RuntimeError(f"源边点生成失败: {str(e)}")

    # 计算初始投影方向（垂直于边方向）
    tangent = src_edge.tangentAt(0).normalized()
    base_dir = Vector(-tangent.y, tangent.x, 0).normalized()
    
    # 智能投影（带矩阵旋转）
    def smart_projection():
    # 生成按角度绝对值排序的角度列表（包含正负方向）
        angles = sorted(
            [angle * sign for angle in np.arange(0, max_angle + angle_step, angle_step) for sign in [1, -1]],
            key=abs
            )
    
        for current_angle in angles:
            theta = math.radians(current_angle)
            cos_theta = math.cos(theta)
            sin_theta = math.sin(theta)
            # 利用旋转矩阵计算旋转后的方向
            rotated_dir = Vector(
                base_dir.x * cos_theta - base_dir.y * sin_theta,
                base_dir.x * sin_theta + base_dir.y * cos_theta,
                0
                ).normalized()
            projections = []
            valid = True
            for idx, p in enumerate(src_points):
                line = cq.Edge.makeLine(p- rotated_dir * 1000, p + rotated_dir * 1000)
                ip = compute_edge_intersection_3D(line, tgt_edge)
                print(f"角度 {current_angle}°，源点 {idx}（{p.toTuple()}）的投影交点: {ip.toTuple() if ip is not None else None}")
                if not ip:
                    valid = False
                    break
                # 选取距离 p 最近的交点
                projections.append(ip)
        
            if valid:
                return current_angle, projections

        raise RuntimeError(f"在±{max_angle}°范围内无法找到有效投影")

    
    angle, tgt_points = smart_projection()
    return src_points, tgt_points


def select_edge(
    component,                    # 输入的Workplane或Assembly
    edge_type="horizontal",       # 边的类型："horizontal"或"vertical"
    selection_type="min",         # 选择方式："min", "max", "nearest"
    axis="x",                     # 参考坐标轴："x"或"y"
    reference_value=None,         # 参考值（当selection_type="nearest"时使用）
    tolerance=0.01                # 边方向判断的容差
):
    """
    从组件上表面选择指定条件的边
    
    参数:
        component: 输入的Workplane或Assembly对象
        edge_type: 边的类型，"horizontal"(水平边)或"vertical"(垂直边)
        selection_type: 选择方式，"min"(最小值)、"max"(最大值)或"nearest"(最近)
        axis: 参考坐标轴，"x"或"y"
        reference_value: 参考值，用于"nearest"选择方式
        tolerance: 判断边方向的容差
        
    返回:
        选中的Edge对象
    """
    # 获取上表面
    if isinstance(component, cq.Workplane):
        face = component.faces("+Z").first().val()
    else:
        # 处理Assembly对象
        face = component.obj.faces("+Z").first().val().located(component.loc)
    
    # 根据edge_type筛选边
    if edge_type == "horizontal":
        # 水平边（垂直于Y轴，tangent的y分量接近0）
        edges = [e for e in face.Edges() 
                if abs(e.tangentAt(0).normalized().y) < tolerance]
        if not edges:
            raise ValueError("未找到水平边")
    elif edge_type == "vertical":
        # 垂直边（垂直于X轴，tangent的x分量接近0）
        edges = [e for e in face.Edges() 
                if abs(e.tangentAt(0).normalized().x) < tolerance]
        if not edges:
            raise ValueError("未找到垂直边")
    else:
        raise ValueError(f"不支持的边类型: {edge_type}")
    
    # 根据selection_type和axis选择边
    if selection_type == "min":
        # 选择最小坐标值的边
        if axis == "x":
            return min(edges, key=lambda e: e.Center().x)
        elif axis == "y":
            return min(edges, key=lambda e: e.Center().y)
        else:
            raise ValueError(f"无效的坐标轴: {axis}")
    
    elif selection_type == "max":
        # 选择最大坐标值的边
        if axis == "x":
            return max(edges, key=lambda e: e.Center().x)
        elif axis == "y":
            return max(edges, key=lambda e: e.Center().y)
        else:
            raise ValueError(f"无效的坐标轴: {axis}")
    
    elif selection_type == "nearest":
        # 选择距离参考值最近的边
        if reference_value is None:
            raise ValueError("使用nearest选择方式时必须提供reference_value")
        
        if axis == "x":
            return min(edges, key=lambda e: abs(e.Center().x - reference_value))
        elif axis == "y":
            return min(edges, key=lambda e: abs(e.Center().y - reference_value))
        else:
            raise ValueError(f"无效的坐标轴: {axis}")
    
    else:
        raise ValueError(f"不支持的选择方式: {selection_type}")

chip_assemblies = {}
chip_dies = {}
chip_final_positions = {} # 存储每个芯片的最终位置
chip_final_rotations = {} # 存储每个芯片的最终旋转角度

# 在 connection_DBC 循环之后
print("Adding created chips to DBC assembly...")
for name, assembly in chip_assemblies.items():
    position = chip_final_positions[name]
    rotation = chip_final_rotations[name]

    # 应用最终的位置和旋转
    translated_assembly = translate_assembly(assembly, rotation, position)

    # 添加到主 DBC 模板
    # 使用更明确的名称，例如 assembly_IGBT_0, assembly_FWD_0
    DBC.add(translated_assembly, name=f"assembly_{name}", color=get_color("die"))
print("Finished adding chips.")

def sample_points_on_face(face, num_points=1, method="random", margin_percent=5):
    """
    在给定的面上采样点
    
    参数:
        face: CadQuery面对象
        num_points: 需要采样的点数量
        method: 采样方法，可选值为"grid"(网格采样)、"random"(随机采样)、"center"(中心点)
        margin_percent: 边缘保留百分比，避免在边缘采点(0-100)
        
    返回:
        采样的点列表，每个点为Vector对象
    """
    # 获取面的边界盒
    bbox = face.BoundingBox()
    center = face.Center()
    
    # 边缘保留距离
    margin_x = (bbox.xmax - bbox.xmin) * margin_percent / 100.0
    margin_y = (bbox.ymax - bbox.ymin) * margin_percent / 100.0
    
    # 有效区域
    x_min = bbox.xmin + margin_x
    x_max = bbox.xmax - margin_x
    y_min = bbox.ymin + margin_y
    y_max = bbox.ymax - margin_y
    
    # 如果只要中心点或只要一个点，直接返回中心
    if method == "center" or num_points == 1:
        return [center]
    
    # 如果是网格采样
    if method == "grid":
        points = []
        # 计算网格大小
        num_x = int(math.sqrt(num_points))
        num_y = num_points // num_x
        
        if num_x * num_y < num_points:
            num_y += 1
            
        # 计算间隔
        step_x = (x_max - x_min) / (num_x + 1)
        step_y = (y_max - y_min) / (num_y + 1)
        
        # 生成网格点
        for i in range(1, num_x + 1):
            for j in range(1, num_y + 1):
                if len(points) < num_points:
                    x = x_min + i * step_x
                    y = y_min + j * step_y
                    point = Vector(x, y, center.z)
                    
                    # 检查点是否在面上
                    if is_point_on_face(face, point):
                        points.append(point)
        
        # 如果网格点不足，补充随机点
        remaining = num_points - len(points)
        if remaining > 0:
            random_points = sample_points_on_face(face, remaining, "random", margin_percent)
            points.extend(random_points)
            
        return points
    
    # 如果是随机采样
    elif method == "random":
        points = []
        attempts = 0
        max_attempts = num_points * 10  # 最大尝试次数
        
        import random
        
        while len(points) < num_points and attempts < max_attempts:
            # 生成随机坐标
            x = random.uniform(x_min, x_max)
            y = random.uniform(y_min, y_max)
            point = Vector(x, y, center.z)
            
            # 检查点是否在面上
            if is_point_on_face(face, point):
                # 检查点与已有点的距离，避免点太靠近
                min_distance = float('inf')
                for existing_point in points:
                    dist = math.sqrt((point.x - existing_point.x)**2 + (point.y - existing_point.y)**2)
                    min_distance = min(min_distance, dist)
                
                # 如果距离足够或者是第一个点，添加
                if not points or min_distance > (x_max - x_min) / (2 * math.sqrt(num_points)):
                    points.append(point)
            
            attempts += 1
        
        # 如果随机采样点不足，添加中心点
        if len(points) < num_points:
            remaining = num_points - len(points)
            for _ in range(remaining):
                # 添加带微小偏移的中心点
                offset_x = random.uniform(-margin_x/5, margin_x/5)
                offset_y = random.uniform(-margin_y/5, margin_y/5)
                point = Vector(center.x + offset_x, center.y + offset_y, center.z)
                points.append(point)
        
        return points
    
    # 默认返回中心点
    return [center]

def is_point_on_face(face, point, tolerance=0.01):
    """
    检查点是否在面上(增强版)
    
    参数:
        face: CadQuery面对象
        point: 要检查的点 (Vector或包含x,y,z属性的对象)
        tolerance: 容差
        
    返回:
        布尔值，表示点是否在面上
    """
    # 确保point是一个有x,y,z属性的对象
    if hasattr(point, 'x') and hasattr(point, 'y') and hasattr(point, 'z'):
        x, y, z = point.x, point.y, point.z
    elif isinstance(point, (tuple, list)) and len(point) >= 3:
        x, y, z = point[0], point[1], point[2]
    else:
        return False
    
    # 初步边界框检查作为快速过滤器
    bbox = face.BoundingBox()
    if not (bbox.xmin - tolerance <= x <= bbox.xmax + tolerance and
            bbox.ymin - tolerance <= y <= bbox.ymax + tolerance and
            abs(z - bbox.center.z) < tolerance * 10): # 确保Z坐标也大致在面的范围内
        return False

    # 使用 shapely 进行更精确的检查
    try:
        # 提取面的轮廓顶点
        outer_wire = face.outerWire()
        vertices_3d = [v.toTuple() for v in outer_wire.Vertices()]
        
        # 提取XY平面上的二维顶点
        vertices_2d = [(v[0], v[1]) for v in vertices_3d]
        
        # 确保多边形封闭
        if vertices_2d[0] != vertices_2d[-1]:
            vertices_2d.append(vertices_2d[0])
        
        # 创建shapely多边形
        polygon = sg.Polygon(vertices_2d)
        if not polygon.is_valid:
            polygon = polygon.buffer(0)  # 修复可能的自相交问题
            if not polygon.is_valid:
                # 如果仍无效，回退到CadQuery的isInside检查
                return face.isInside(cq.Vector(x, y, z), tolerance)
        
        # 检查点是否在多边形内部或边界上
        point_2d = sg.Point(x, y)
        return polygon.contains(point_2d) or polygon.boundary.distance(point_2d) <= tolerance
        
    except Exception as e:
        # 回退到CadQuery的isInside方法
        try:
            test_point = cq.Vector(x, y, face.Center().z)
            return face.isInside(test_point, tolerance)
        except Exception:
            # 最后的回退方案：使用边界框检查
            return True  # 由于之前已经通过了边界框检查，这里返回True

def create_igbt_to_gate_bondwires(
    die_IGBT_dict,
    assembly_IGBT_dict,
    rotations_IGBT,
    positions_IGBT,
    # Change: Takes labeled gates present *within* the single DBC template
    labeled_upper_Cu_Gates_in_template: list[tuple[cq.Workplane, any]],
    igbt_index_to_type: dict,
    length_die_IGBT,
    num_IGBT,
    sample_density=15,
    sampling_method="combined",
    wire_diameter=0.5
):
    """
    创建从 IGBT 到其对应类型门极的键合线（在单个 DBC 模板内）。
    使用高级Shapely采样方法进行轮廓内缩和点采样。
    """
    print(f"创建 IGBT 到 门极 的键合线 (模板内，使用高级采样)...")
    bond_wires = []
    igbt_gate_total_length = 0

    # 1. 收集所有 IGBT 的门极连接点
    igbt_points = []
    igbt_ids = []
    actual_num_igbt = num_IGBT
    if actual_num_igbt != len(rotations_IGBT) or actual_num_igbt != len(positions_IGBT):
        print("警告：num_IGBT 与传入的 rotations/positions 列表长度不一致！")
        actual_num_igbt = min(num_IGBT, len(rotations_IGBT), len(positions_IGBT))

    for j in range(actual_num_igbt):
        igbt_die_name = f"IGBT_{j}"
        igbt_assembly_name = f"IGBT_{j}"
        if igbt_die_name in die_IGBT_dict and igbt_assembly_name in assembly_IGBT_dict:
            # die_obj = die_IGBT_dict[igbt_die_name] # Original die object, might not be needed now
            # assembly_obj contains the located assembly added to the DBC template
            assembly_obj = assembly_IGBT_dict[igbt_assembly_name]

            try:
                # --- Refined Connection Point Calculation START ---
                # 1. Find the die component within the located assembly
                final_die_comp = assembly_obj.find(f"IGBT_die")
                if not final_die_comp:
                    print(f"警告: 在位于的 {igbt_assembly_name} 中找不到 IGBT_die。跳过此 IGBT。")
                    continue

                # 2. Get the located geometry of the die
                located_die_geom = None
                if isinstance(final_die_comp.obj, cq.Workplane):
                    geom_val = final_die_comp.obj.val()
                    if hasattr(geom_val, 'located'):
                        located_die_geom = geom_val.located(final_die_comp.loc)
                    else:
                        geom_val = final_die_comp.obj.combine().val()
                        if hasattr(geom_val, 'located'):
                            located_die_geom = geom_val.located(final_die_comp.loc)
                elif hasattr(final_die_comp.obj, 'located'):
                    located_die_geom = final_die_comp.obj.located(final_die_comp.loc)
                
                if located_die_geom is None:
                    print(f"警告: 无法从位于的 {igbt_assembly_name} 的 IGBT_die 获取可定位几何体。跳过此 IGBT。")
                    continue

                # 3. Get the top face of the located geometry
                # Use Workplane adder to robustly get the face
                top_face = cq.Workplane().add(located_die_geom).faces(">Z").first().val()

                # 4. Calculate the center of this top face
                face_center = top_face.Center()

                # 5. Calculate the offset vector based on rotation
                offset_dist = length_die_IGBT / 2 - 1 # Distance from center to gate connection point along local Y
                angle_rad = math.radians(rotations_IGBT[j])
                # Offset vector in the rotated die's coordinate system (local Y direction)
                # Rotate this local offset vector to global coordinates
                offset_vec = Vector(
                    -offset_dist * math.sin(angle_rad), # Global X component
                    offset_dist * math.cos(angle_rad),  # Global Y component
                    0
                )

                # 6. Calculate the final connection point
                point = face_center + offset_vec
                # --- Refined Connection Point Calculation END ---

                igbt_points.append(point)
                igbt_ids.append(igbt_assembly_name)
                print(f"  Collected gate connection point from {igbt_assembly_name} at {point.toTuple()}")

            except Exception as e:
                # Print detailed error including traceback
                import traceback
                print(f"  Error processing IGBT {j} ({igbt_assembly_name}): {e}")
                print(traceback.format_exc()) # Add traceback for debugging


    # 2. 为模板内的门极生成采样点 (按对象 ID 存储)
    gate_sample_points = {}
    gate_id_to_original_index = {}
    gate_id_to_type = {}
    print(f"  Generating sample points with Shapely for {len(labeled_upper_Cu_Gates_in_template)} gates in template...")
    for gate_idx, (gate_obj, gate_type) in enumerate(labeled_upper_Cu_Gates_in_template):
        try:
            # Ensure gate_obj is a Workplane or Solid that can provide faces
            if isinstance(gate_obj, cq.Workplane):
                gate_solid = gate_obj.val() # Get the solid/compound
                if not isinstance(gate_solid, (cq.Solid, cq.Compound)):
                     gate_solid = gate_obj.combine().val() # Try combining if it's multi-solid WP
            elif isinstance(gate_obj, (cq.Solid, cq.Compound)):
                 gate_solid = gate_obj
            else:
                 print(f"  警告：门极索引 {gate_idx} (类型 {gate_type}) 不是有效的可处理对象类型: {type(gate_obj)}")
                 continue

            gate_face = cq.Workplane().add(gate_solid).faces("+Z").first().val() # Process the solid
            
            # 使用新的Shapely采样方法
            samples = sample_points_on_face_shapely(
                face=gate_face, 
                num_points=sample_density,
                offset_distance=0.5,  # 轮廓内缩距离0.5mm
                sampling_method=sampling_method,
                grid_density=5,
                preserve_corners=True
            )
            
            # 如果采样失败，使用旧方法作为回退
            if not samples:
                print(f"  警告：使用Shapely方法为门极索引 {gate_idx} (类型 {gate_type}) 生成采样点失败，尝试旧方法...")
                samples = sample_points_on_face(gate_face, sample_density, "grid")
            
            obj_id = id(gate_obj) # Use original object ID as key
            gate_sample_points[obj_id] = samples
            gate_id_to_original_index[obj_id] = gate_idx # Store original index if needed later
            gate_id_to_type[obj_id] = gate_type
            print(f"    Gate {gate_idx} (Type {gate_type}): Generated {len(samples)} sample points")
        except Exception as e:
            print(f"  警告：为门极索引 {gate_idx} (类型 {gate_type}) 生成采样点时出错: {e}")
            # 回退到中心点
            try:
                center = gate_obj.faces("+Z").first().val().Center()
                gate_sample_points[id(gate_obj)] = [center]
                gate_id_to_original_index[id(gate_obj)] = gate_idx
                gate_id_to_type[id(gate_obj)] = gate_type
                print(f"    Fallback: Using center point for Gate {gate_idx}")
            except:
                print(f"    Critical: Cannot get any sample points for Gate {gate_idx}")

    # 3. 为每个 IGBT 找到最优的 *同类型* 门极连接点
    print(f"  Finding best connections for {len(igbt_points)} IGBTs to matching gates in template...")
    for igbt_idx, igbt_point in enumerate(igbt_points):
        current_igbt_type = igbt_index_to_type.get(igbt_idx)
        if current_igbt_type is None:
            print(f"警告：无法找到 IGBT 索引 {igbt_idx} 的类型，跳过此 IGBT 的门极连接。")
            continue

        print(f"    Processing IGBT_{igbt_idx} (Type {current_igbt_type})...")
        best_gate_original_idx = None
        best_distance = float('inf')
        best_point_on_gate = None
        gate_connections_found = False # 用于标记是否找到了连接点

        # 遍历模板中的门极
        for gate_idx, (gate_obj, gate_type) in enumerate(labeled_upper_Cu_Gates_in_template):
            if gate_type != current_igbt_type: # 类型匹配
                continue

            obj_id = id(gate_obj)
            if obj_id not in gate_sample_points or not gate_sample_points[obj_id]:
                continue # 跳过没有采样点的门极

            samples = gate_sample_points[obj_id]
            current_gate_best_dist = float('inf')
            current_gate_best_point = None
            gate_connections_found = True # 找到了至少一个同类型的门极且有采样点

            for sample_point in samples:
                dist = igbt_point.sub(sample_point).Length # Use vector subtraction and Length
                if dist < current_gate_best_dist:
                    current_gate_best_dist = dist
                    current_gate_best_point = sample_point

            if current_gate_best_dist < best_distance:
                best_distance = current_gate_best_dist
                best_gate_original_idx = gate_idx # Use the index from enumeration
                best_point_on_gate = current_gate_best_point

        if not gate_connections_found:
            print(f"  警告：IGBT_{igbt_ids[igbt_idx]} (Type {current_igbt_type}) 没有找到同类型的门极或所有门极采样点为空。")

        if best_gate_original_idx is not None:
            try:
                wire = create_bond_wire(igbt_point, best_point_on_gate, wire_diameter)
                # Use original gate index in name for clarity within the template
                wire_name = f"bond_wire_IGBT_{igbt_ids[igbt_idx]}_to_Gate_{best_gate_original_idx}"
                bond_wires.append((wire, wire_name))
                igbt_gate_total_length += best_distance
                print(f"    Created bond wire: {wire_name}, length: {best_distance:.2f}")
            except Exception as e:
                print(f"    Error creating bond wire {igbt_ids[igbt_idx]} -> Gate_{best_gate_original_idx}: {e}")
        else:
            print(f"  警告：未能为 IGBT_{igbt_ids[igbt_idx]} (Type {current_igbt_type}) 找到合适的同类型门极连接点。")

    print(f"Total IGBT-to-Gate length (template): {igbt_gate_total_length:.2f}")
    print(f"Created {len(bond_wires)} IGBT-to-Gate bond wires in template.")
    return bond_wires


def create_gate_to_gate_bondwires(
    DBCs: list[Assembly],
    rotations_DBC: list[float] = None, # <-- 新增参数
    sample_density=15,
    sampling_method="combined",
    wire_diameter=0.5
):
    """
    创建跨所有 DBC 的门极到门极键合线（按类型分组，使用 MST）。
    如果 DBC 旋转 180 度，则其门极类型会发生转换 (1<->2)。
    使用高级Shapely采样方法进行轮廓内缩和点采样。
    """
    print("创建 门极 到 门极 的键合线 (跨所有 DBC，使用高级采样)...")
    bond_wires = []
    total_gate_to_gate_length = 0

    # 1. 收集所有 DBC 中的所有门极对象及其位置和类型
    all_located_gates_raw = [] # 存储原始解析结果
    print("  Collecting gates from all DBC copies...")
    for dbc_index, dbc_assembly in enumerate(DBCs):
        print(f"    Scanning DBC_{dbc_index}...")
        count_in_dbc = 0
        for child in dbc_assembly.children:
            # Identify gates by name pattern established during template creation
            if child.name and child.name.startswith("gate_type"):
                try:
                    parts = child.name.split('_')
                    gate_type = int(parts[1].replace("type", ""))
                    original_template_index = int(parts[2]) # Not strictly needed here but parsed
                    # copy_index = int(parts[3]) # Should match dbc_index

                    # Get the object (Solid/Compound) in its final location
                    if isinstance(child.obj, cq.Workplane):
                        geometry = child.obj.val()
                        if not hasattr(geometry, 'located'):
                            print(f"      Info: Trying combine() for gate '{child.name}'...")
                            geometry = child.obj.combine().val()
                        if not hasattr(geometry, 'located'):
                            print(f"      警告: 无法从门极 Workplane '{child.name}' 提取有效的可定位几何体。类型: {type(geometry)}")
                            continue
                        located_gate_obj = geometry.located(child.loc)
                    elif hasattr(child.obj, 'located'):
                        located_gate_obj = child.obj.located(child.loc)
                    else:
                        print(f"      警告: 门极 '{child.name}' 的对象类型未知 ({type(child.obj)})，无法定位。")
                        continue

                    all_located_gates_raw.append(
                        (located_gate_obj, gate_type, child.name, dbc_index)
                    )
                    count_in_dbc += 1
                except (IndexError, ValueError, AttributeError, TypeError) as e:
                    print(f"      警告: 无法解析或定位门极名称 '{child.name}' in DBC_{dbc_index}: {e}")
                    continue
        print(f"      Found {count_in_dbc} gates in DBC_{dbc_index}.")

    if not all_located_gates_raw:
        print("  未在任何 DBC 副本中找到门极，无法创建门极间连接。")
        return []

    print(f"  Total raw gates collected: {len(all_located_gates_raw)}")

    # 1.5 调整门极类型（如果 DBC 旋转了 180 度）
    all_located_gates = [] # 存储调整类型后的结果
    if rotations_DBC is None:
        print("  警告: 未提供 rotations_DBC，无法根据旋转调整门极类型。")
        all_located_gates = all_located_gates_raw # 直接使用原始数据
    elif len(rotations_DBC) != len(DBCs):
        print(f"  警告: rotations_DBC 长度 ({len(rotations_DBC)}) 与 DBCs 列表长度 ({len(DBCs)}) 不匹配。无法根据旋转调整门极类型。")
        all_located_gates = all_located_gates_raw # 直接使用原始数据
    else:
        print("  Adjusting gate types based on DBC rotation...")
        for located_obj, original_type, name, dbc_idx in all_located_gates_raw:
            current_rotation = rotations_DBC[dbc_idx]
            adjusted_type = original_type
            if abs(current_rotation - 180) < 1e-6: # 检查是否旋转了180度 (考虑浮点误差)
                if original_type == 1:
                    adjusted_type = 2
                    print(f"    Gate '{name}' (DBC {dbc_idx}, Rot 180): Type 1 -> 2")
                elif original_type == 2:
                    adjusted_type = 1
                    print(f"    Gate '{name}' (DBC {dbc_idx}, Rot 180): Type 2 -> 1")
                # else: # 如果存在其他类型，则不改变
                #     print(f"    Gate '{name}' (DBC {dbc_idx}, Rot 180): Type {original_type} (未改变)")
            all_located_gates.append((located_obj, adjusted_type, name, dbc_idx))
        print("  Gate type adjustment complete.")

    # 2. 按调整后的门极类型分组
    gates_by_type = {}
    print("  Grouping gates by adjusted type...")
    for located_gate, adjusted_gate_type, name, dbc_idx in all_located_gates:
        if adjusted_gate_type not in gates_by_type:
            gates_by_type[adjusted_gate_type] = []
        gates_by_type[adjusted_gate_type].append({'obj': located_gate, 'name': name, 'dbc_idx': dbc_idx})
        # print(f"    Added gate '{name}' to type {adjusted_gate_type} group.")

    # --- Prim MST function definition (local or imported) ---
    def prim_mst(graph):
        n_nodes = len(graph)
        if n_nodes == 0: return []
        visited = [False] * n_nodes
        parent = [-1] * n_nodes
        key = [float('inf')] * n_nodes
        start_idx = 0 # Start MST from the first node arbitrarily
        if not key: return [] # Handle empty graph case after potentially setting start_idx
        key[start_idx] = 0
        
        # Keep track of node indices to add to the tree
        import heapq
        pq = [(0, start_idx)] # (key[i], i)

        while pq:
            d, u = heapq.heappop(pq)

            if visited[u] or d > key[u]: # If already visited or found a shorter path
                continue

            visited[u] = True

            for v in range(n_nodes):
                weight = graph[u][v]
                if not visited[v] and weight < key[v]:
                     if weight == float('inf'): continue # Skip if no edge exists
                     key[v] = weight
                     parent[v] = u
                     heapq.heappush(pq, (weight, v))
                     
        return parent
    # --- End Prim MST definition ---

    # 3. 为每种门极类型独立运行 MST
    print("  Finding optimal connections between gates of the SAME type using MST...")
    for gate_type, gates_list in gates_by_type.items():
        num_gates_this_type = len(gates_list)
        print(f"    Processing connections for gate type: {gate_type} ({num_gates_this_type} gates)")

        if num_gates_this_type < 2:
            print(f"      Skipping type {gate_type}: only {num_gates_this_type} gate(s) found.")
            continue

        # --- a. 生成采样点并计算中心点 (使用位于世界坐标的门极对象) ---
        type_sample_points = [] # List of lists, parallel to gates_list
        type_centers = []
        valid_indices_for_mst = [] # Indices within gates_list that are valid

        for i, gate_info in enumerate(gates_list):
            located_gate_obj = gate_info['obj']
            try:
                # Ensure it's a solid/compound for face operations
                if isinstance(located_gate_obj, cq.Workplane):
                    located_gate_solid = located_gate_obj.val()
                    if not isinstance(located_gate_solid, (cq.Solid, cq.Compound)):
                         located_gate_solid = located_gate_obj.combine().val()
                elif isinstance(located_gate_obj, (cq.Solid, cq.Compound)):
                     located_gate_solid = located_gate_obj
                else: raise TypeError("Invalid gate object type")

                gate_face = cq.Workplane().add(located_gate_solid).faces("+Z").first().val()
                
                # 使用新的Shapely采样方法
                samples = sample_points_on_face_shapely(
                    face=gate_face, 
                    num_points=sample_density,
                    offset_distance=0.5,
                    sampling_method=sampling_method,
                    grid_density=5,
                    preserve_corners=True
                )
                
                # 如果采样失败，使用旧方法作为回退
                if not samples:
                    print(f"      警告：使用Shapely方法为门极 '{gate_info['name']}' 生成采样点失败，尝试旧方法...")
                    samples = sample_points_on_face(gate_face, sample_density, "grid")
                
                center = gate_face.Center()
                print(f"      Gate '{gate_info['name']}': Generated {len(samples)} sample points")

                type_sample_points.append(samples)
                type_centers.append(center)
                valid_indices_for_mst.append(i) # Mark this index as valid
            except Exception as e:
                print(f"      警告：无法处理门极 '{gate_info['name']}' (类型 {gate_type}): {e}. 从 MST 中排除。")
                type_sample_points.append([]) # Add empty list to maintain index correspondence
                type_centers.append(None) # Add None to maintain index correspondence

        # Filter out invalid entries for MST calculation
        valid_gates_for_mst = [gates_list[i] for i in valid_indices_for_mst]
        valid_centers = [type_centers[i] for i in valid_indices_for_mst]
        valid_samples = [type_sample_points[i] for i in valid_indices_for_mst]
        
        n = len(valid_gates_for_mst) # Number of gates to include in MST
        if n < 2:
            print(f"      Skipping type {gate_type}: less than 2 valid gates for MST after processing.")
            continue

        # --- b. 构建距离矩阵 (使用有效中心点) ---
        distance_matrix = [[float('inf')] * n for _ in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                dist = valid_centers[i].sub(valid_centers[j]).Length
                distance_matrix[i][j] = dist
                distance_matrix[j][i] = dist

        # --- c. 运行 Prim MST ---
        parents = prim_mst(distance_matrix)

        # --- d. 创建键合线 ---
        type_gate_to_gate_length = 0
        # Iterate through the results of MST (indices 0 to n-1)
        for mst_i, parent_mst_idx in enumerate(parents):
            if parent_mst_idx != -1:
                # mst_i and parent_mst_idx are indices within the *valid* subset
                
                # Get the corresponding gate info from the valid lists
                gate1_info = valid_gates_for_mst[parent_mst_idx] # Parent gate info
                gate2_info = valid_gates_for_mst[mst_i]       # Child gate info
                
                # Get their sample points
                points1 = valid_samples[parent_mst_idx]
                points2 = valid_samples[mst_i]

                if not points1 or not points2:
                    print(f"      警告：门极 '{gate1_info['name']}' 或 '{gate2_info['name']}' 采样点列表为空，无法创建连接。")
                    continue

                # Find the best pair of points between these two gates
                best_point1, best_point2, best_dist = None, None, float('inf')
                for p1 in points1:
                    for p2 in points2:
                         try:
                             dist = p1.sub(p2).Length
                             if dist < best_dist:
                                 best_dist = dist
                                 best_point1 = p1
                                 best_point2 = p2
                         except AttributeError: continue # Handle potential None points if sample_points failed

                if best_point1 and best_point2:
                    # Sanity check distance (optional, avoid zero-length wires)
                    if best_dist < 1e-3:
                        print(f"      警告: 门极 '{gate1_info['name']}' 和 '{gate2_info['name']}' 之间距离过近 ({best_dist:.4f})，跳过连接。")
                        continue
                    try:
                        wire = create_bond_wire(best_point1, best_point2, wire_diameter)
                        # Create a unique name based on the original names
                        wire_name = f"bond_wire_GateG_Type{gate_type}_{gate1_info['name']}_to_{gate2_info['name']}"
                        bond_wires.append((wire, wire_name))
                        type_gate_to_gate_length += best_dist
                        print(f"        Created wire (MST): {wire_name}, len: {best_dist:.2f}")
                    except Exception as e:
                        print(f"        Error creating bond wire between '{gate1_info['name']}' and '{gate2_info['name']}': {e}")
                else:
                    print(f"      警告: 未能在门极 '{gate1_info['name']}' 和 '{gate2_info['name']}' 之间找到有效的连接点对。")

        print(f"      Total gate-to-gate length for type {gate_type}: {type_gate_to_gate_length:.2f}")
        total_gate_to_gate_length += type_gate_to_gate_length

    print(f"Total Gate-to-Gate length (all types combined): {total_gate_to_gate_length:.2f}")
    print(f"Created {len(bond_wires)} Gate-to-Gate bond wires across all DBCs.")
    return bond_wires

def create_ceramics(width: float, 
                        length: float, 
                        thickness: float,
                        centered: tuple = (False, False, False)) -> cq.Workplane:
    """
    创建陶瓷基板
    参数：
        width: X轴方向宽度
        length: Y轴方向长度
        thickness: Z轴方向厚度
        centered: 中心对齐方式，默认左下角对齐原点
    返回：
        cadquery生成的陶瓷板对象
    """
    return (
        cq.Workplane("XY")
        .box(width, length, thickness, centered=centered)
    )

def create_upper_Cu(
    layer_type: str,
    ceramics: cq.Workplane,
    start_point: tuple,
    relative_moves: list,
    thickness: float,
    fillet_value: float,
    margin: float = 1.5,
    offset_plane: str = "XY",
) -> cq.Workplane:
    """
    统一铜层创建接口
    
    参数：
        layer_type: 铜层类型 ['emitter', 'gate', 'collector']
        ceramics: 陶瓷基板对象
        start_point: 路径起始点 (x, y)
        relative_moves: 相对移动路径列表 [(dx, dy), ...]
        thickness: 铜层厚度 (>0)
        fillet_value: 圆角半径 (>=0)
        margin: 安全边距 (默认1.5mm)
        offset_plane: 工作平面 (默认"XY")
        kwargs: 类型专用参数
        
    返回：
        创建完成的铜层对象
        
    异常：
        ValueError: 参数无效或路径违规
        BoundaryViolationError: 超出陶瓷板边界
    """
    # 参数基础验证
    if thickness <= 0:
        raise ValueError(f"铜层厚度必须大于0，当前值：{thickness}")
    if fillet_value < 0:
        raise ValueError(f"圆角值不能为负数，当前值：{fillet_value}")
    if layer_type not in ['emitter', 'gate', 'collector']:
        raise ValueError(f"不支持的铜层类型：{layer_type}")

    # 获取陶瓷板边界
    ceramic_bb = ceramics.val().BoundingBox()
    container_bounds = (
        ceramic_bb.xmin, ceramic_bb.xmax,
        ceramic_bb.ymin, ceramic_bb.ymax
    )

    # 生成完整路径点
    path_points = generate_relative_points(start_point, relative_moves)

    # 创建顶面工作平面
    top_z = ceramics.faces(">Z").val().Center().z
    top_plane = cq.Workplane(offset_plane).workplane(offset=top_z)

    # 生成草图
    copper_sketch = generate_sketch(
        start_point=start_point,
        relative_moves=relative_moves,
        sketch_plane=top_plane
    )

    # 类型专用处理
    if layer_type == 'collector':
        # Collector特殊圆角处理
        fillet_value = max(fillet_value, 0.8)  # 保证最小圆角
    
    # 创建三维实体
    copper_layer = create_extrude(
        sketch_plane=top_plane,
        sketch=copper_sketch,
        thickness=thickness,
        fillet_value=fillet_value
    )

    return copper_layer

def create_bottom_copper_layer(
    ceramics: cq.Workplane,
    width_ceramics: float,
    length_ceramics: float,
    distance_Cu_Ceramics: float = 1.5,
    radius_bottom_hex: float = 2.0,
    hex_spacing: float = -1.5,
    thickness_bottom_Cu: float = 0.3
) -> cq.Workplane:
    """
    创建蜂窝状排列的下铜层
    
    参数：
        ceramics: 陶瓷板对象
        width_ceramics: 陶瓷板宽度
        length_ceramics: 陶瓷板长度
        distance_Cu_Ceramics: 铜层到陶瓷板边缘的距离 (默认1.5mm)
        radius_bottom_hex: 六边形外接圆半径 (默认2.0mm)
        hex_spacing: 六边形间净空距离 (默认-1.5mm)
        thickness_bottom_Cu: 下铜层厚度 (默认0.3mm)
        
    返回：
        蜂窝状排列的下铜层对象
    """
    # 参数验证
    if radius_bottom_hex <= 0:
        raise ValueError("六边形半径必须大于0")
    if thickness_bottom_Cu <= 0:
        raise ValueError("铜层厚度必须大于0")
    if distance_Cu_Ceramics < 0:
        raise ValueError("铜瓷间距不能为负数")

    # 计算有效区域
    effective_width = width_ceramics - 2 * distance_Cu_Ceramics
    effective_length = length_ceramics - 2 * distance_Cu_Ceramics

    # 计算排列参数
    center_spacing = 2 * radius_bottom_hex + hex_spacing
    vertical_spacing = center_spacing * math.sqrt(3) / 2
    row_offset = center_spacing / 2

    # 计算有效区域边界
    min_x = distance_Cu_Ceramics + radius_bottom_hex + hex_spacing / 2
    max_x = width_ceramics - distance_Cu_Ceramics - radius_bottom_hex - hex_spacing / 2
    min_y = distance_Cu_Ceramics + radius_bottom_hex + hex_spacing / 2
    max_y = length_ceramics - distance_Cu_Ceramics - radius_bottom_hex - hex_spacing / 2

    # 生成蜂窝状排列点阵
    points = generate_hex_grid_points(
        min_x, max_x,
        min_y, max_y,
        center_spacing, 
        vertical_spacing,
        row_offset
    )

    # 创建底面工作平面
    bottom_z = ceramics.faces("<Z").val().Center().z
    bottom_workplane = cq.Workplane("XY").workplane(offset=bottom_z)

    # 生成单个六边形
    single_hex = (
        cq.Workplane("XY")
        .polygon(6, radius_bottom_hex)
        .extrude(-thickness_bottom_Cu)
    )

    # 生成阵列
    return (
        bottom_workplane
        .pushPoints(points)
        .each(lambda loc: single_hex.val().moved(loc))
        .combine()
    )

def generate_hex_grid_points(
    min_x: float,
    max_x: float,
    min_y: float,
    max_y: float,
    center_spacing: float,
    vertical_spacing: float,
    row_offset: float
) -> list:
    """
    生成蜂窝状排列的中心点坐标
    """
    points = []
    for row in range(int((max_y - min_y) / vertical_spacing) + 2):
        y = min_y + row * vertical_spacing
        x_offset = (row % 2) * row_offset
        
        for col in range(int((max_x - min_x) / center_spacing) + 2):
            x = min_x + col * center_spacing + x_offset
            if x <= max_x and y <= max_y:
                points.append((x, y))
    return points

def create_substrate_solder(
    bottom_copper: cq.Workplane,
    width_ceramics: float,
    length_ceramics: float,
    distance_Cu_Ceramics: float,
    thickness_substrate_solder: float
) -> cq.Workplane:
    """
    创建基板焊料层
    
    参数：
        bottom_copper: 下铜层对象（用于确定Z轴位置）
        width_ceramics: 陶瓷板总宽度
        length_ceramics: 陶瓷板总长度
        distance_Cu_Ceramics: 铜层到陶瓷边缘的距离
        thickness_substrate_solder: 焊料层厚度
        
    返回：
        位于下铜层下方的焊料层对象
    """
    # 参数验证
    if thickness_substrate_solder<= 0:
        raise ValueError("焊料层厚度必须大于0")
    if distance_Cu_Ceramics < 0:
        raise ValueError("铜瓷间距不能为负数")
    if width_ceramics <= 2*distance_Cu_Ceramics:
        raise ValueError("陶瓷板宽度不足以容纳焊料层")
    if length_ceramics <= 2*distance_Cu_Ceramics:
        raise ValueError("陶瓷板长度不足以容纳焊料层")

    # 获取下铜层底面Z坐标
    bottom_z = bottom_copper.faces("<Z").val().Center().z
    
    # 创建工作平面
    solder_plane = (
        cq.Workplane("XY")
        .workplane(offset=bottom_z)
    )
    
    # 计算焊料层实际尺寸
    solder_width = width_ceramics - 2*distance_Cu_Ceramics
    solder_length = length_ceramics - 2*distance_Cu_Ceramics
    
    # 创建焊料层
    return (
        solder_plane
        .box(
            solder_width, 
            solder_length, 
            thickness_substrate_solder,
            centered=(False, False, False)  # 左下角对齐原点
        )
        .translate((
            distance_Cu_Ceramics,  # X方向偏移铜瓷间距
            distance_Cu_Ceramics,  # Y方向偏移铜瓷间距
            0  # Z方向保持底面位置
        ))
    )

def create_die_assembly(
    die_type: str,
    zone: cq.Workplane,
    die_width: float,
    die_length: float,
    die_thickness: float,
    solder_thickness: float,
    position: Vector,  # Changed from List[Vector] to Vector
) -> tuple[Assembly, cq.Workplane]: # Return single Assembly and Workplane
    """
    Creates a single power device assembly (die + solder) at a specified position.
    
    Args:
        die_type: Device type ('IGBT'/'FWD')
        zone: Reference Workplane (e.g., upper copper layer) to determine Z level.
        die_width: Die width
        die_length: Die length
        die_thickness: Die thickness
        solder_thickness: Solder thickness
        position: The XYZ position vector for this specific die assembly.
        
    Returns:
        (Assembly, cq.Workplane): A tuple containing the created assembly and the die Workplane object.
    """
    # Parameter validation
    if die_type not in ['IGBT', 'FWD']:
        raise ValueError("Unsupported device type")
    if not isinstance(position, Vector):
         raise TypeError("position must be a cadquery.Vector")

    base_z = zone.faces(">Z").val().Center().z
    
    # Create solder layer
    # Position is handled by the main script's translate_assembly now
    solder_plane = cq.Workplane("XY").workplane(offset=base_z)
    solder = solder_plane.box(
        die_width, die_length, solder_thickness,
        centered=(False, False, False)
    )
    
    # Create die layer
    die_plane = cq.Workplane("XY").workplane(offset=solder.faces(">Z").val().Center().z)
    die = die_plane.box(
        die_width, die_length, die_thickness,
        centered=(False, False, False)
    )
    
    # Create assembly
    assembly = Assembly()
    # Use simpler names, positioning handled outside
    assembly.add(solder, name=f"{die_type}_solder", color=get_color("solder")) 
    assembly.add(die, name=f"{die_type}_die", color=get_color("die"))
    
    # Return the single assembly and die object
    return assembly, die

def create_bond_wire(point1, point2, wire_diameter=0.5):
    """创建bond_wire（支持任意两点坐标）"""
    p1 = list(point1)
    p2 = list(point2)
    
    # 计算两点在XY平面的距离（投影长度）
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    xy_distance = math.sqrt(dx**2 + dy**2)
    
    # 计算中间控制点（在旋转平面内）
    mid_height = max(p1[2], p2[2]) + 0.3*xy_distance
    # 中间点在XY平面的中点，Z方向为计算的高度
    mid_point = [(p1[0]+p2[0])/2, (p1[1]+p2[1])/2, mid_height]
    
    # 生成三维路径点
    points_3d = []
    
    # 添加起始垂直段（2个点）
    points_3d.append((p1[0], p1[1], p1[2]))  # 起点
    points_3d.append((p1[0], p1[1], p1[2] + 0.25))  # 轻微抬起
    # 主曲线段（三维空间中的三点圆弧）
    num_points = 10
    for i in range(1, num_points-1):  # 跳过首尾点，因为它们已经包含在垂直段中
        t = i/(num_points - 1)
        # 三维二次贝塞尔曲线插值
        x = (1-t)**2 * p1[0] + 2*(1-t)*t * mid_point[0] + t**2 * p2[0]
        y = (1-t)**2 * p1[1] + 2*(1-t)*t * mid_point[1] + t**2 * p2[1]
        z = (1-t)**2 * (p1[2]+0.5) + 2*(1-t)*t * mid_point[2] + t**2 * (p2[2]+0.5)
        points_3d.append((x, y, z))
        

    # 添加结束垂直段（2个点）
    points_3d.append((p2[0], p2[1], p2[2] + 0.25))  # 轻微抬起
    points_3d.append((p2[0], p2[1], p2[2]))  # 终点
    
    # 创建路径（使用样条曲线）
    path = (
        cq.Workplane("XY")
        .newObject([cq.Edge.makeSpline([cq.Vector(*p) for p in points_3d])])
        .wire()
    )
    
    # 创建截面（在起点位置）
    section = (
        cq.Workplane("XY")
        .workplane(offset=p1[2], origin=(p1[0], p1[1]))
        .circle(wire_diameter / 2)
    )
    
    return section.sweep(path, multisection=False, clean=True)

def get_coordinate(target_face, point, direction, tolerance=1e-1, offset=2):
    """沿指定方向确定边界连接点坐标，返回完整坐标 (x, y)"""
    try:
        x, y = point
        dx, dy = direction
        
        # 确定投影轴和待分析轴
        if abs(dy) > abs(dx):   # 沿y轴方向（原功能特例）
            check_axis = 1      # 检查y轴附近
            target_value = y
            process_axis = 0    # 处理x轴坐标
        else:                   # 沿x轴方向（新扩展功能）
            check_axis = 0      # 检查x轴附近
            target_value = x
            process_axis = 1    # 处理y轴坐标

        coords = []
        edges = target_face.edges()
        
        for edge in edges:
            points = np.array([edge.positionAt(param).toTuple() 
                             for param in np.linspace(0, 1, 300)])
            
            # 筛选目标轴附近的点
            mask = np.abs(points[:, check_axis] - target_value) <= tolerance
            nearby_points = points[mask]
            
            if len(nearby_points) > 0:
                coords.extend(nearby_points[:, process_axis])
        
        if not coords:
            return None

        # 计算最近边界并偏移
        boundary_min = min(coords)
        boundary_max = max(coords)
        ref_value = point[process_axis]  # 原参考点在本轴的值
        
        # 选择更接近参考值的边界，应用偏移
        if abs(boundary_min - ref_value) < abs(boundary_max - ref_value):
            adjusted_value = boundary_min + offset
        else:
            adjusted_value = boundary_max - offset
        
        # 组合成完整坐标 (x, y)
        if process_axis == 0:  # 处理的是X轴，保留原Y坐标
            return (adjusted_value, target_value,target_face.val().Center().z)
        else:                  # 处理的是Y轴，保留原X坐标
            return (target_value, adjusted_value,target_face.val().Center().z)
            
    except Exception as e:
        print(f"计算失败: {str(e)}")
        return None
     
TOLERANCE = 0.1
def compute_edge_intersection_3D(edge1, edge2, tol=1e-8):
    """
    计算两条直线边在同一平面（假定共面）的交点，并返回一个三维点（cadquery.Vector）。
    假设两条边的 z 坐标相同或非常接近。
    
    参数:
      edge1, edge2: cadquery.Edge 对象，代表直线边
      tol: 数值容差，用于判断两边是否平行
      
    返回:
      交点 (cadquery.Vector)，如果两边平行或无交点则返回 None。
    """
    # 获取每条边的起点和终点
    p1 = edge1.startPoint()
    p2 = edge1.endPoint()
    q1 = edge2.startPoint()
    q2 = edge2.endPoint()
    
    # 计算边的方向向量（归一化）
    d1 = (p2 - p1).normalized()
    d2 = (q2 - q1).normalized()
    
    # 在 XY 平面上计算叉积，仅使用 x, y 分量
    cross = d1.x * d2.y - d1.y * d2.x
    if abs(cross) < tol:
        return None  # 两边平行或近似平行
    
    diff = q1 - p1
    # 求解直线1参数 t，使得 p1 + t*d1 与直线2相交
    t = (diff.x * d2.y - diff.y * d2.x) / cross
    intersection = p1 + d1 * t
    # 取 p1.z 作为 z 坐标（假设两边共面）
    return cq.Vector(intersection.x, intersection.y, p1.z)

def get_offset_top_face(part: cq.Assembly, offset: float, extrusion_depth: float = 1.0) -> cq.Face:
    """
    改进版：通过投影到工作平面确保二维坐标，优化偏移参数
    """
    # 获取顶面和工作平面
    original_obj = part.obj.val()
    part_loc = part.loc
    transformed_obj = original_obj.located(part_loc)
    top_face = cq.Workplane().add(transformed_obj).faces(">Z").first().val()
    wp = (
    cq.Workplane("XY")  # 初始平面
    .workplane(top_face.Center().z)  # 手动对齐到陶瓷顶面高度
    )
    
    # 选择最大面积的外圈Wire
    wires = top_face.Wires()
    if not wires:
        raise ValueError("顶面没有找到任何 Wire")
    outer_wire = max(wires, key=lambda w: cq.Face.makeFromWires(w).Area())
    
    # 转换顶点到工作平面二维坐标
    pts = [
        wp.plane.toLocalCoords(v).toTuple()[:2]  # 投影到二维
        for v in outer_wire.Vertices()
    ]
    
    # 构建闭合草图（显式闭合）
    base_sketch = (
        wp.moveTo(*pts[0])
        .polyline(pts[1:], includeCurrent=True)
        .close()
    )
    
    # 执行内偏移（注意负号）
    offset_sketch = base_sketch.offset2D(
        offset,  # 负值表示内缩
        kind="intersection",
    )
    # 向下拉伸并提取底面
    solid = create_extrude(wp, offset_sketch, -1, 0.5)
    return solid.faces(">Z").val()


def generate_mapped_points(
    part1: cq.Workplane,
    part2: cq.Workplane,
    edge_selector1: Callable[[Face], Edge],
    edge_selector2: Callable[[Face], Edge],
    num_points: int,            # 新增：明确控制点数
    distance_interval: float,   # 新增：点间距控制
    max_angle: float = 45,
    offset_distance: float = -2,
    angle_step: float = 1
) -> Tuple[List[Vector], List[Vector], float]:
    """
    全自定义映射点生成器（点数+间距控制版）
    
    参数：
    - num_points: 生成的对称点数（必须≥1）
    - distance_interval: 相邻点间距（毫米）
    """
    
    # 获取上表面
    def get_top_face(part):
        return part.faces(">Z").val()
    
    top1 = get_offset_top_face(part1, offset_distance)
    top2 = get_offset_top_face(part2, offset_distance)
    
    # 执行自定义边选择
    try:
        edge1 = edge_selector1(top1)
        edge2 = edge_selector2(top2)
    except Exception as e:
        raise RuntimeError(f"边选择失败: {str(e)}")
    
    # 确定源边和目标边（短边作为源边）
    src_edge, tgt_edge = sorted([edge1, edge2], key=lambda e: e.Length())
    
    # 生成对称间隔点
    def generate_symmetrical_points(edge):
        L = edge.Length()
        if num_points < 1:
            raise ValueError("点数必须≥1")
        if distance_interval <= 0:
            raise ValueError("间距必须>0")
        
        # 计算参数步长（考虑几何对称性）
        t_step = distance_interval / L
        steps = [i - (num_points-1)/2 for i in range(num_points)]  # 生成对称步数
        
        # 计算所有t值（以边中点为中心）
        t_values = [0.5 + s*t_step for s in steps]
        
        # 边界检查
        if any(t < 0 or t > 1 for t in t_values):
            raise ValueError(f"参数超出边界：边长为{L}mm，需要{num_points}点，间距{distance_interval}mm")
        
        return [edge.positionAt(t) for t in sorted(t_values)]  # 确保从左到右顺序
    
    try:
        src_points = generate_symmetrical_points(src_edge)
    except ValueError as e:
        raise RuntimeError(f"源边点生成失败: {str(e)}")

    # 计算初始投影方向（垂直于边方向）
    tangent = src_edge.tangentAt(0).normalized()
    base_dir = Vector(-tangent.y, tangent.x, 0).normalized()
    
    # 智能投影（带矩阵旋转）
    def smart_projection():
    # 生成按角度绝对值排序的角度列表（包含正负方向）
        angles = sorted(
            [angle * sign for angle in np.arange(0, max_angle + angle_step, angle_step) for sign in [1, -1]],
            key=abs
            )
    
        for current_angle in angles:
            theta = math.radians(current_angle)
            cos_theta = math.cos(theta)
            sin_theta = math.sin(theta)
            # 利用旋转矩阵计算旋转后的方向
            rotated_dir = Vector(
                base_dir.x * cos_theta - base_dir.y * sin_theta,
                base_dir.x * sin_theta + base_dir.y * cos_theta,
                0
                ).normalized()
            projections = []
            valid = True
            for idx, p in enumerate(src_points):
                line = cq.Edge.makeLine(p- rotated_dir * 1000, p + rotated_dir * 1000)
                ip = compute_edge_intersection_3D(line, tgt_edge)
                print(f"角度 {current_angle}°，源点 {idx}（{p.toTuple()}）的投影交点: {ip.toTuple() if ip is not None else None}")
                if not ip:
                    valid = False
                    break
                # 选取距离 p 最近的交点
                projections.append(ip)
        
            if valid:
                return current_angle, projections

        raise RuntimeError(f"在±{max_angle}°范围内无法找到有效投影")

    
    angle, tgt_points = smart_projection()
    return src_points, tgt_points

def cut_copper(
    copper: cq.Workplane,
    paths_points: list[list[tuple[float, float]]], # 修改：接收路径列表
    Cu_slot: float = 1,
    extra_height: float = 0.5,
    offset_kind: str = "intersection",
    num_copper: int = None,
    fillet_value: float = 0.2  # 新增：圆角半径参数
) -> list[cq.Solid]: # 修改：返回值统一为 Solid 列表
    """
    使用一个或多个路径切割铜层。

    Args:
        copper: 初始的铜层 Workplane 对象。
        paths_points: 一个包含一个或多个路径点列表的列表。
                      例如: [[(x1,y1), (x2,y2)], [(x3,y3), (x4,y4)]]
        Cu_slot: 切割槽的宽度。
        extra_height: 切割工具的额外深度。
        offset_kind: 传递给 offset2D 的 kind 参数。
        num_copper: 期望切割后得到的铜块数量（可选）。

    Returns:
        list[cq.Solid]: 切割后剩余部分的 Solid 对象列表。
    """

    if not paths_points: # 检查列表是否为空
        print("警告: 未提供切割路径。")
        # 如果没有路径，返回原始铜层的 Solid
        solids = copper.solids().vals()
        if num_copper is not None and len(solids) != num_copper:
             print(f"警告：未执行切割，但铜块数量 ({len(solids)}) 与预期 ({num_copper}) 不符。")
        return solids

    # 获取铜层信息
    bb = copper.val().BoundingBox()
    z_top = bb.zmax
    thickness = bb.zmax - bb.zmin

    current_copper = copper # 用于迭代切割的工作对象

    # 循环处理每一条切割路径
    print(f"Processing {len(paths_points)} cut paths...")
    for i, single_path_points in enumerate(paths_points):
        print(f"  Processing path {i+1}...")
        if len(single_path_points) < 2:
            print(f"    警告: 路径 {i+1} 的点数不足 (< 2)，跳过此路径。")
            continue

        # 创建工作平面
        wp = cq.Workplane("XY").workplane(offset=z_top + 0.01)
        
        # 特殊处理：检查是否为直线路径（只有两个点）
        is_straight_line = len(single_path_points) == 2
        
        if is_straight_line:
            # 直线路径的特殊处理
            try:
                # 获取直线的两个点
                p1 = single_path_points[0]
                p2 = single_path_points[1]
                
                # 计算直线方向向量
                dx = p2[0] - p1[0]
                dy = p2[1] - p1[1]
                length = math.sqrt(dx**2 + dy**2)
                
                # 归一化
                if length > 0:
                    dx, dy = dx/length, dy/length
                
                # 计算垂直于直线的方向向量
                nx, ny = -dy, dx
                
                # 创建直线两侧的点，形成矩形
                half_width = Cu_slot / 2
                rect_points = [
                    (p1[0] + nx * half_width, p1[1] + ny * half_width),
                    (p2[0] + nx * half_width, p2[1] + ny * half_width),
                    (p2[0] - nx * half_width, p2[1] - ny * half_width),
                    (p1[0] - nx * half_width, p1[1] - ny * half_width)
                ]
                
                # 创建多边形
                wp = wp.moveTo(*rect_points[0])
                for pt in rect_points[1:]:
                    wp = wp.lineTo(*pt)
                wp = wp.close()
                
                # 挤出矩形
                knife_body = wp.extrude(-(thickness + extra_height))
                
                # 执行切割
                current_copper = current_copper.cut(knife_body)
                print(f"    Path {i+1} (直线) cut successful.")
                
            except Exception as e:
                print(f"    处理直线路径 {i+1} 时失败: {e}。跳过此路径。")
                continue # 跳到下一条路径
        else:
            # 多边形路径的正常处理
            try:
                # 创建轮廓
                wp = wp.moveTo(*single_path_points[0])
                for pt in single_path_points[1:]:
                    wp = wp.lineTo(*pt)
                    
                # 为当前路径创建轮廓并挤出
                slot_outline = wp.offset2D(Cu_slot / 2, kind=offset_kind)
                knife_body = slot_outline.extrude(- (thickness + extra_height)).edges("|Z").fillet(fillet_value)
                
                # 执行切割，更新 current_copper
                current_copper = current_copper.cut(knife_body)
                print(f"    Path {i+1} (多边形) cut successful.")
                
            except Exception as e:
                print(f"    处理路径 {i+1} 时 offset2D 或 extrude 或 cut 失败: {e}。跳过此路径。")
                continue # 跳到下一条路径

    # 所有路径处理完毕后，获取最终结果
    result_solids = current_copper.solids().vals()

    # 检验最终切割后的铜块数量
    if num_copper is not None:
        actual_blocks = len(result_solids)
        if actual_blocks != num_copper:
            # 注意：这里改为打印警告而不是抛出错误，因为中间步骤的失败可能导致数量不符
            print(f"警告：最终切割得到的铜块数量与预期不符：预期 {num_copper} 块，实际 {actual_blocks} 块。")
            # raise ValueError(f"最终切割得到的铜块数量与预期不符：预期 {num_copper} 块，实际 {actual_blocks} 块")

    print(f"Cut copper finished. Resulting solids: {len(result_solids)}")
    return result_solids # 始终返回 Solid 列表

def select_edge(
    component,                    # 输入的Workplane或Assembly
    edge_type="horizontal",       # 边的类型："horizontal"或"vertical"
    selection_type="min",         # 选择方式："min", "max", "nearest"
    axis="x",                     # 参考坐标轴："x"或"y"
    reference_value=None,         # 参考值（当selection_type="nearest"时使用）
    tolerance=0.01                # 边方向判断的容差
):
    """
    从组件上表面选择指定条件的边
    
    参数:
        component: 输入的Workplane或Assembly对象
        edge_type: 边的类型，"horizontal"(水平边)或"vertical"(垂直边)
        selection_type: 选择方式，"min"(最小值)、"max"(最大值)或"nearest"(最近)
        axis: 参考坐标轴，"x"或"y"
        reference_value: 参考值，用于"nearest"选择方式
        tolerance: 判断边方向的容差
        
    返回:
        选中的Edge对象
    """
    # 获取上表面
    if isinstance(component, cq.Workplane):
        face = component.faces("+Z").first().val()
    else:
        # 处理Assembly对象
        face = component.obj.faces("+Z").first().val().located(component.loc)
    
    # 根据edge_type筛选边
    if edge_type == "horizontal":
        # 水平边（垂直于Y轴，tangent的y分量接近0）
        edges = [e for e in face.Edges() 
                if abs(e.tangentAt(0).normalized().y) < tolerance]
        if not edges:
            raise ValueError("未找到水平边")
    elif edge_type == "vertical":
        # 垂直边（垂直于X轴，tangent的x分量接近0）
        edges = [e for e in face.Edges() 
                if abs(e.tangentAt(0).normalized().x) < tolerance]
        if not edges:
            raise ValueError("未找到垂直边")
    else:
        raise ValueError(f"不支持的边类型: {edge_type}")
    
    # 根据selection_type和axis选择边
    if selection_type == "min":
        # 选择最小坐标值的边
        if axis == "x":
            return min(edges, key=lambda e: e.Center().x)
        elif axis == "y":
            return min(edges, key=lambda e: e.Center().y)
        else:
            raise ValueError(f"无效的坐标轴: {axis}")
    
    elif selection_type == "max":
        # 选择最大坐标值的边
        if axis == "x":
            return max(edges, key=lambda e: e.Center().x)
        elif axis == "y":
            return max(edges, key=lambda e: e.Center().y)
        else:
            raise ValueError(f"无效的坐标轴: {axis}")
    
    elif selection_type == "nearest":
        # 选择距离参考值最近的边
        if reference_value is None:
            raise ValueError("使用nearest选择方式时必须提供reference_value")
        
        if axis == "x":
            return min(edges, key=lambda e: abs(e.Center().x - reference_value))
        elif axis == "y":
            return min(edges, key=lambda e: abs(e.Center().y - reference_value))
        else:
            raise ValueError(f"无效的坐标轴: {axis}")
    
    else:
        raise ValueError(f"不支持的选择方式: {selection_type}")

chip_assemblies = {}
chip_dies = {}
chip_final_positions = {} # 存储每个芯片的最终位置
chip_final_rotations = {} # 存储每个芯片的最终旋转角度

# 在 connection_DBC 循环之后
print("Adding created chips to DBC assembly...")
for name, assembly in chip_assemblies.items():
    position = chip_final_positions[name]
    rotation = chip_final_rotations[name]

    # 应用最终的位置和旋转
    translated_assembly = translate_assembly(assembly, rotation, position)

    # 添加到主 DBC 模板
    # 使用更明确的名称，例如 assembly_IGBT_0, assembly_FWD_0
    DBC.add(translated_assembly, name=f"assembly_{name}", color=get_color("die"))
print("Finished adding chips.")


def create_optimal_gate_bondwires(
    die_IGBT_dict,
    assembly_IGBT_dict,
    rotations_IGBT, # 对应 all_rotations_IGBT
    positions_IGBT, # 对应 all_positions_IGBT
    labeled_upper_Cu_Gates: list[tuple], # 带标签的门极列表 [(obj, type), ...]
    igbt_index_to_type: dict, # 新增：IGBT索引到类型的映射
    length_die_IGBT,
    num_IGBT, # 对应 num_total_IGBT
    sample_density=15,
    allow_gate_to_gate=True,
    sampling_method="combined"
):
    """
    创建最优的门极键合线，支持类型匹配：
    - IGBT 只连接到相同类型的门极。
    - 门极只与相同类型的门极互连。
    - 使用高级Shapely采样方法进行轮廓内缩和点采样
    """
    print(f"Creating optimal gate bond wires with advanced sampling...")
    bond_wires = []

    # 1. 收集所有 IGBT 的门极连接点 (不变)
    igbt_points = []
    igbt_ids = []
    actual_num_igbt = num_IGBT # 使用传入的总数
    if actual_num_igbt != len(rotations_IGBT) or actual_num_igbt != len(positions_IGBT):
        print("警告：num_IGBT 与传入的 rotations/positions 列表长度不一致！")
        actual_num_igbt = min(num_IGBT, len(rotations_IGBT), len(positions_IGBT))

    for j in range(actual_num_igbt):
        igbt_die_name = f"IGBT_{j}" # 键名保持基于索引
        igbt_assembly_name = f"IGBT_{j}"
        if igbt_die_name in die_IGBT_dict and igbt_assembly_name in assembly_IGBT_dict:
            die_obj = die_IGBT_dict[igbt_die_name]
            assembly_obj = assembly_IGBT_dict[igbt_assembly_name]

            index = die_obj.faces("+Z").val().Center()
            connect_igbt_point = [(index.x, index.y + length_die_IGBT / 2 - 1, index.z)]

            connection_points = calculate_connection_points(
                assembly_obj, rotations_IGBT[j], positions_IGBT[j], connect_igbt_point
            )
            point = connection_points[0]
            igbt_points.append(point)
            igbt_ids.append(igbt_assembly_name)
            print(f"  Collected gate connection point from {igbt_assembly_name}")

    # 2. 为所有传入的门极对象生成采样点，按对象 ID 存储
    gate_sample_points = {}
    print(f"  Generating sample points with Shapely for {len(labeled_upper_Cu_Gates)} labeled gates...")
    for gate_idx, (gate_obj, gate_type) in enumerate(labeled_upper_Cu_Gates):
        try:
            gate_face = gate_obj.faces("+Z").first().val()
            # 使用新的Shapely采样方法
            samples = sample_points_on_face_shapely(
                face=gate_face, 
                num_points=sample_density,
                offset_distance=0.5,  # 轮廓内缩距离0.5mm
                sampling_method=sampling_method,
                grid_density=5,
                preserve_corners=True
            )
            gate_sample_points[id(gate_obj)] = samples
            print(f"    Gate {gate_idx} (Type {gate_type}): Generated {len(samples)} sample points")
        except Exception as e:
            print(f"  警告：为门极索引 {gate_idx} (类型 {gate_type}) 生成采样点时出错: {e}")
            # 回退到简单中心点方法
            try:
                center = gate_face.Center()
                gate_sample_points[id(gate_obj)] = [center]
                print(f"    Fallback: Using center point for Gate {gate_idx}")
            except:
                print(f"    Critical: Cannot get any sample points for Gate {gate_idx}")

    # 3. 第一阶段：为每个 IGBT 找到最优的 *同类型* 门极连接点
    igbt_to_gate_connections = []
    gate_connection_points = {}

    print(f"  Finding best connections for {actual_num_igbt} IGBTs to matching gates...")
    for igbt_idx, igbt_point in enumerate(igbt_points):
        
        # --- 获取当前 IGBT 的类型 --- 
        current_igbt_type = igbt_index_to_type.get(igbt_idx)
        if current_igbt_type is None:
             print(f"警告：无法找到 IGBT 索引 {igbt_idx} 的类型，跳过此 IGBT 的门极连接。")
             continue
        # --- 类型获取结束 ---
        
        print(f"    Processing IGBT_{igbt_idx} (Type {current_igbt_type})...")
        best_gate_original_idx = None
        best_distance = float('inf')
        best_point_on_gate = None
        best_gate_obj = None 

        # 遍历带标签的门极列表
        for gate_original_idx, (gate_obj, gate_type) in enumerate(labeled_upper_Cu_Gates):
            # --- 类型匹配过滤 --- 
            if gate_type != current_igbt_type:
                continue
            # --- 过滤结束 ---
            
            # 检查此门极是否有采样点
            obj_id = id(gate_obj)
            if obj_id not in gate_sample_points or not gate_sample_points[obj_id]:
                continue

            # 在这个门极的采样点中找到最近的点
            samples = gate_sample_points[obj_id]
            current_gate_best_dist = float('inf')
            current_gate_best_point = None

            for sample_point in samples:
                 dist = math.sqrt(
                    (igbt_point.x - sample_point.x)**2 +
                    (igbt_point.y - sample_point.y)**2 +
                    (igbt_point.z - sample_point.z)**2
                 )
                 if dist < current_gate_best_dist:
                     current_gate_best_dist = dist
                     current_gate_best_point = sample_point

            # 更新全局最佳连接
            if current_gate_best_dist < best_distance:
                best_distance = current_gate_best_dist
                best_gate_original_idx = gate_original_idx
                best_point_on_gate = current_gate_best_point
                best_gate_obj = gate_obj

        # 如果找到了合适的同类型门极连接点
        if best_gate_original_idx is not None and best_gate_obj is not None:
            igbt_to_gate_connections.append((igbt_idx, best_gate_original_idx, best_gate_obj, best_point_on_gate, best_distance))

            # 记录这个门极对象上被选中的连接点 (使用对象 ID 作为键)
            best_gate_id = id(best_gate_obj)
            if best_gate_id not in gate_connection_points:
                gate_connection_points[best_gate_id] = []
            gate_connection_points[best_gate_id].append(best_point_on_gate)

            # 创建IGBT到门极的键合线
            try:
                wire = create_bond_wire(igbt_point, best_point_on_gate, 0.5)
                wire_name = f"bond_wire_IGBT_{igbt_ids[igbt_idx]}_to_Gate_{best_gate_original_idx}"
                bond_wires.append((wire, wire_name))
                print(f"    Created bond wire: {igbt_ids[igbt_idx]} -> Gate_{best_gate_original_idx}, length: {best_distance:.2f}")
            except Exception as e:
                 print(f"    Error creating bond wire {igbt_ids[igbt_idx]} -> Gate_{best_gate_original_idx}: {e}")
        else:
            print(f"  警告：未能为 IGBT_{igbt_ids[igbt_idx]} (Type {current_igbt_type}) 找到合适的同类型门极连接点。")

    # 4. 第二阶段：为 *每种* 门极类型独立运行 MST (逻辑基本不变)
    if allow_gate_to_gate:
        print("  Finding optimal connections between gates of the SAME type...")
        unique_gate_types = sorted(list(set(gt for _, gt in labeled_upper_Cu_Gates)))
        total_gate_to_gate_length = 0

        for current_gate_type in unique_gate_types:
            print(f"    Processing connections for gate type: {current_gate_type}")
            # 筛选出当前类型的门极及其原始索引
            gates_of_current_type = [
                (gate_obj, original_idx)
                for original_idx, (gate_obj, gate_type) in enumerate(labeled_upper_Cu_Gates)
                if gate_type == current_gate_type
            ]

            num_gates_this_type = len(gates_of_current_type)
            if num_gates_this_type < 2:
                print(f"      Skipping type {current_gate_type}: only {num_gates_this_type} gate(s) found.")
                continue

            # 收集中心点，用于构建距离矩阵 (仅针对当前类型的门极)
            centers_this_type = []
            original_indices_map = {} # Map subset index -> original index
            gate_objects_map = {} # Map subset index -> gate_obj
            valid_subset_indices = [] # Track indices with valid centers
            for subset_idx, (gate_obj, original_idx) in enumerate(gates_of_current_type):
                 try:
                     center = gate_obj.faces("+Z").first().val().Center()
                     centers_this_type.append(center)
                     original_indices_map[subset_idx] = original_idx
                     gate_objects_map[subset_idx] = gate_obj
                     valid_subset_indices.append(subset_idx) # Record valid index
                 except Exception as e:
                      print(f"      警告：无法获取门极原始索引 {original_idx} (类型 {current_gate_type}) 的中心点: {e}")
                      continue # Skip this gate if center fails
            
            n = len(centers_this_type) # Number of gates with valid centers
            if n < 2:
                 print(f"      Skipping type {current_gate_type}: less than 2 valid gates for MST.")
                 continue
            
            # Build distance matrix using only valid centers
            distance_matrix = [[float('inf') for _ in range(n)] for _ in range(n)]
            for i in range(n):
                for j in range(i + 1, n):
                    # i, j now refer to indices in centers_this_type
                    dist = math.sqrt(
                        (centers_this_type[i].x - centers_this_type[j].x)**2 +
                        (centers_this_type[i].y - centers_this_type[j].y)**2 +
                        (centers_this_type[i].z - centers_this_type[j].z)**2
                    )
                    distance_matrix[i][j] = dist
                    distance_matrix[j][i] = dist

            # --- Prim MST function definition --- 
            def prim_mst(graph):
                n_nodes = len(graph)
                if n_nodes == 0: return [] # Return empty list for empty graph
                visited = [False] * n_nodes
                parent = [-1] * n_nodes
                key = [float('inf')] * n_nodes
                start_idx = 0
                key[start_idx] = 0
                for _ in range(n_nodes):
                    min_key = float('inf')
                    min_idx = -1
                    for i in range(n_nodes):
                        if not visited[i] and key[i] < min_key:
                            min_key = key[i]
                            min_idx = i
                    if min_idx == -1: break
                    visited[min_idx] = True
                    for j in range(n_nodes):
                        if (not visited[j] and graph[min_idx][j] < key[j]): # Check if edge exists implicitly via < inf
                            key[j] = graph[min_idx][j]
                            parent[j] = min_idx
                return parent
            # --- End Prim MST definition --- 

            parents = prim_mst(distance_matrix)

            # 创建当前类型门极之间的连接
            type_gate_to_gate_length = 0
            # Iterate using the indices of the valid centers (0 to n-1)
            for subset_i, parent_subset_idx in enumerate(parents):
                if parent_subset_idx != -1:
                    # parent_subset_idx and subset_i are indices within the valid subset (0 to n-1)
                    # Map back to the original subset index using valid_subset_indices
                    actual_subset_idx_i = valid_subset_indices[subset_i]
                    actual_subset_idx_parent = valid_subset_indices[parent_subset_idx]

                    # Get objects and original indices using the actual subset indices
                    gate1_obj = gate_objects_map[actual_subset_idx_parent]
                    gate2_obj = gate_objects_map[actual_subset_idx_i]
                    gate1_orig_idx = original_indices_map[actual_subset_idx_parent]
                    gate2_orig_idx = original_indices_map[actual_subset_idx_i]

                    # 获取这两个门极的采样点
                    gate1_id = id(gate1_obj)
                    gate2_id = id(gate2_obj)
                    if gate1_id not in gate_sample_points or gate2_id not in gate_sample_points:
                        print(f"      警告：门极 {gate1_orig_idx} 或 {gate2_orig_idx} (类型 {current_gate_type}) 缺少采样点，无法创建连接。")
                        continue
                    points1 = gate_sample_points[gate1_id]
                    points2 = gate_sample_points[gate2_id]
                    if not points1 or not points2:
                         print(f"      警告：门极 {gate1_orig_idx} 或 {gate2_orig_idx} (类型 {current_gate_type}) 采样点列表为空，无法创建连接。")
                         continue
                    
                    # --- (寻找最佳/次优连接点逻辑 - 基本不变) --- 
                    best_point1, best_point2, best_dist = None, None, float('inf')
                    all_pairs = [] 
                    for p1 in points1:
                        for p2 in points2:
                            try:
                                dist = math.sqrt((p1.x - p2.x)**2 + (p1.y - p2.y)**2 + (p1.z - p2.z)**2)
                                all_pairs.append(((p1, p2), dist))
                                if dist < best_dist:
                                    best_dist = dist
                                    best_point1 = p1
                                    best_point2 = p2
                            except AttributeError: continue
                    
                    wire_created = False
                    min_dist_threshold = 0.5 / 2
                    if best_point1 and best_point2:
                        try:
                            if best_dist < min_dist_threshold:
                                raise ValueError("Shortest distance too small")
                            wire = create_bond_wire(best_point1, best_point2, 0.5)
                            wire_name = f"bond_wire_Gate_{gate1_orig_idx}_to_Gate_{gate2_orig_idx}"
                            bond_wires.append((wire, wire_name))
                            type_gate_to_gate_length += best_dist
                            print(f"        Created wire (shortest): {wire_name}, len: {best_dist:.2f}")
                            wire_created = True
                        except Exception:
                            pass # Fall through

                    if not wire_created and all_pairs:
                        all_pairs.sort(key=lambda item: item[1])
                        for (p1_alt, p2_alt), dist_alt in all_pairs:
                            if dist_alt >= min_dist_threshold:
                                try:
                                    wire = create_bond_wire(p1_alt, p2_alt, 0.5)
                                    wire_name = f"bond_wire_Gate_{gate1_orig_idx}_to_Gate_{gate2_orig_idx}_alt"
                                    bond_wires.append((wire, wire_name))
                                    type_gate_to_gate_length += dist_alt
                                    print(f"        Created wire (alternative): {wire_name}, len: {dist_alt:.2f}")
                                    wire_created = True
                                    break
                                except Exception:
                                    pass # Try next alternative
                            else:
                                pass

                    if not wire_created:
                        print(f"      最终警告: 未能在门极 {gate1_orig_idx} 和 {gate2_orig_idx} (类型 {current_gate_type}) 之间创建连接。")
            # --- 单个类型 MST 结束 --- 
            print(f"      Total gate-to-gate length for type {current_gate_type}: {type_gate_to_gate_length:.2f}")
            total_gate_to_gate_length += type_gate_to_gate_length
        # --- 所有类型 Gate-to-Gate 结束 --- 

    # 计算总线长
    igbt_gate_total_length = sum(conn[4] for conn in igbt_to_gate_connections)
    print(f"Total IGBT-to-Gate length: {igbt_gate_total_length:.2f}")
    if allow_gate_to_gate:
        print(f"Total Gate-to-Gate length (all types combined): {total_gate_to_gate_length:.2f}")
    
    print(f"Created {len(bond_wires)} optimal gate bond wires in total.")
    return bond_wires

# Note: find_optimal_gate_bondwires might need adjustment or removal if no longer used
def find_optimal_gate_bondwires(*args, **kwargs):
    # 旧函数签名与新函数差异较大，直接调用可能出错
    # 建议：如果不再需要旧行为，可以移除此函数
    # 或者，需要更复杂的适配器逻辑
    print("Warning: Deprecated find_optimal_gate_bondwires called. Functionality may be incorrect or removed.")
    # 尝试基于参数名进行基本映射（可能不完整或错误）
    die_IGBT_dict = kwargs.get('die_IGBT_dict')
    assembly_IGBT_dict = kwargs.get('assembly_IGBT_dict')
    rotations_IGBT = kwargs.get('rotations_IGBT')
    positions_IGBT = kwargs.get('positions_IGBT')
    upper_Cu_Gates_objs = kwargs.get('upper_Cu_Gates') # 旧函数接收对象列表
    length_die_IGBT = kwargs.get('length_die_IGBT')
    num_IGBT = kwargs.get('num_IGBT')
    sample_density = kwargs.get('sample_density', 10)

    if not all([die_IGBT_dict, assembly_IGBT_dict, rotations_IGBT, positions_IGBT, upper_Cu_Gates_objs, length_die_IGBT, num_IGBT is not None]):
        print("Error: Missing required arguments for compatibility call.")
        return []

    # 为兼容性创建假设的标签和类型映射
    labeled_gates_compat = [(gate, 0) for gate in upper_Cu_Gates_objs]
    igbt_index_map_compat = {i: 0 for i in range(num_IGBT)}

    return create_optimal_gate_bondwires(
        die_IGBT_dict,
        assembly_IGBT_dict,
        rotations_IGBT,
        positions_IGBT,
        labeled_upper_Cu_Gates=labeled_gates_compat,
        igbt_index_to_type=igbt_index_map_compat,
        length_die_IGBT=length_die_IGBT,
        num_IGBT=num_IGBT,
        sample_density=sample_density,
        allow_gate_to_gate=False, # 禁用门极互连以模拟旧行为
        sampling_method="grid"
    )

def identify_input_output_zones(connection_DBC):
    """
    分析connection_DBC矩阵找出所有DBC的输入Zone（列全为0）和输出Zone（行全为0）
    
    Args:
        connection_DBC: 连接矩阵字典
        
    Returns:
        dict: 包含各种区域类型的索引的字典:
              {'output_zones': [索引列表], 'input_zones': [索引列表]}
    """
    zone_info = {
        'output_zones': [],  # 输出区域索引列表
        'input_zones': []    # 输入区域索引列表
    }
    
    zone_names = [name for name in connection_DBC.keys() if name.startswith("Zone_")]
    
    # 查找所有输出Zone (行全为0)
    for zone_name in zone_names:
        if all(value == 0 for value in connection_DBC[zone_name].values()):
            zone_index = int(zone_name.split('_')[-1])
            zone_info['output_zones'].append(zone_index)
            print(f"  Identified Output Zone: Zone_{zone_index}")

    # 查找所有输入Zone (列全为0)
    zone_column_sums = {name: 0 for name in zone_names}
    for row_name, columns in connection_DBC.items():
        for col_name, value in columns.items():
            if col_name in zone_column_sums:
                zone_column_sums[col_name] += value

    for zone_name, total in zone_column_sums.items():
        if total == 0:
            zone_index = int(zone_name.split('_')[-1])
            zone_info['input_zones'].append(zone_index)
            print(f"  Identified Input Zone: Zone_{zone_index}")
            
    # 确保至少有一个输入区域和一个输出区域
    if not zone_info['output_zones']:
        raise ValueError("Could not identify any output zones (rows with all zeros) from connection_DBC")
    if not zone_info['input_zones']:
        raise ValueError("Could not identify any input zones (columns with all zeros) from connection_DBC")
        
    return zone_info

def create_dbc_connections(DBCs, connection_module, module, # Removed zone_info, num_Zone
                          create_connection_func=None, connection_params_info=None,
                          positions_DBC: list[Vector] = None):
    """
    根据connection_module创建DBC之间的连接。
    直接处理 connection_module 中定义的 Zone_X_Y 到 Zone_A_B 的连接。
    如果详细连接方法失败，将尝试回退到旧的映射连接方法。
    """
    print("Creating connections between DBCs (New Format Handling)...")
    
    # 首先创建边缘选择器字典
    selectors = create_edge_selectors(tolerance=0.01)
    
    default_params = {
        'num_points': 5,
        'distance_interval': 1.5, # Used by old mapped connection
        'max_angle': 30,         # Used by old mapped connection
        'bond_wire_diameter': 0.5,
        'offset_distance': -2,    # Used by old mapped connection (via get_offset_top_face)
        'tolerance': 0.01,
        'endpoint_offset': 1.0,    # New parameter for parallel connections
        'try_y_axis_first': True,   # 添加新参数的默认值
        'edge_selector1': selectors['min_y_horizontal'], # 使用函数对象而不是字符串
        'edge_selector2': selectors['auto_match']        # 使用函数对象而不是字符串
    }
    if connection_params_info is None: connection_params_info = {'default': {}}
    if 'default' not in connection_params_info: connection_params_info['default'] = {}
    
    # 处理connection_params_info中可能存在的字符串类型边缘选择器
    for section in ['global', 'default']:
        if section in connection_params_info:
            # 转换边缘选择器字符串为实际函数对象
            for key in ['edge_selector1', 'edge_selector2']:
                if key in connection_params_info[section] and isinstance(connection_params_info[section][key], str):
                    selector_name = connection_params_info[section][key]
                    if selector_name in selectors:
                        connection_params_info[section][key] = selectors[selector_name]
                    else:
                        print(f"  警告: 无法识别的选择器名称 '{selector_name}'，使用默认选择器。")
                        connection_params_info[section][key] = default_params[key]
    
    # 处理特定连接参数的边缘选择器
    for conn_key in connection_params_info:
        if conn_key not in ['global', 'default']:
            for key in ['edge_selector1', 'edge_selector2']:
                if key in connection_params_info[conn_key] and isinstance(connection_params_info[conn_key][key], str):
                    selector_name = connection_params_info[conn_key][key]
                    if selector_name in selectors:
                        connection_params_info[conn_key][key] = selectors[selector_name]
                    else:
                        print(f"  警告: 无法识别的选择器名称 '{selector_name}'，使用默认选择器。")
                        connection_params_info[conn_key][key] = default_params[key]
    
    if 'global' in connection_params_info: default_params.update(connection_params_info['global'])
    default_params.update(connection_params_info['default'])
    
    # 移除旧的并联DBC处理逻辑 (基于 -1 flag)
    # parallel_connections_made_indices set and related logic removed.

    print("\n  Processing explicit connections from connection_module...")
    for source_zone_full_name, target_zones_map in connection_module.items():
        # 验证源名称格式
        if not (source_zone_full_name.startswith("Zone_") and len(source_zone_full_name.split('_')) == 3):
            print(f"    警告: 源连接名称 '{source_zone_full_name}' 格式不符合 'Zone_X_Y'。跳过此源。")
            continue

        for target_zone_full_name, connect_flag in target_zones_map.items():
            if connect_flag == 1: 
                # 验证目标名称格式
                if not (target_zone_full_name.startswith("Zone_") and len(target_zone_full_name.split('_')) == 3):
                    print(f"    警告: 目标连接名称 '{target_zone_full_name}' (来自源 '{source_zone_full_name}') 格式不符合 'Zone_A_B'。跳过此连接。")
                    continue
                
                try:
                    src_parts = source_zone_full_name.split('_')
                    # src_zone_idx = int(src_parts[1]) # Not directly used for finding part by name
                    src_dbc_idx = int(src_parts[2])

                    tgt_parts = target_zone_full_name.split('_')
                    # tgt_zone_idx = int(tgt_parts[1]) # Not directly used for finding part by name
                    tgt_dbc_idx = int(tgt_parts[2])
                except (IndexError, ValueError) as e:
                    print(f"    错误: 解析Zone/DBC索引时出错: {source_zone_full_name} 或 {target_zone_full_name}: {e}。跳过。")
                    continue

                # 检查DBC索引是否有效
                if not (0 <= src_dbc_idx < len(DBCs) and 0 <= tgt_dbc_idx < len(DBCs)):
                    print(f"    错误: DBC索引 {src_dbc_idx} (源'{source_zone_full_name}') 或 {tgt_dbc_idx} (目标'{target_zone_full_name}') 超出DBC列表范围 ({len(DBCs)} 个DBC)。跳过。")
                    continue
                
                source_part = DBCs[src_dbc_idx].find(source_zone_full_name)
                target_part = DBCs[tgt_dbc_idx].find(target_zone_full_name)
                
                if source_part is None:
                    print(f"      跳过: 未找到源组件 '{source_zone_full_name}' in DBC_{src_dbc_idx}.")
                    continue
                if target_part is None:
                    print(f"      跳过: 未找到目标组件 '{target_zone_full_name}' in DBC_{tgt_dbc_idx}.")
                    continue
                
                print(f"    Attempting connection: {source_zone_full_name} to {target_zone_full_name}...")
                
                # 获取此特定连接的参数
                connection_key = f"{source_zone_full_name}->{target_zone_full_name}"
                this_connection_params = default_params.copy()
                if connection_key in connection_params_info:
                    this_connection_params.update(connection_params_info[connection_key])
                
                # 使用详细连接方法
                if create_connection_func:
                    try:
                        create_connection_func(source_part, target_part, module, this_connection_params)
                    except Exception as e:
                        print(f"      Error using custom connection func for {source_zone_full_name} -> {target_zone_full_name}: {e}")
                else:
                    try:
                        edge_selector1 = this_connection_params.get('edge_selector1')
                        edge_selector2 = this_connection_params.get('edge_selector2')
                        
                        if isinstance(edge_selector1, str):
                            selector_name1 = edge_selector1
                            if selector_name1 in selectors:
                                edge_selector1 = selectors[selector_name1]
                            else:
                                print(f"  警告: 无法识别的选择器名称 '{selector_name1}'，使用默认。")
                                edge_selector1 = selectors['min_y_horizontal']
                                
                        if isinstance(edge_selector2, str):
                            selector_name2 = edge_selector2
                            if selector_name2 in selectors:
                                edge_selector2 = selectors[selector_name2]
                            else:
                                print(f"  警告: 无法识别的选择器名称 '{selector_name2}'，使用默认。")
                                edge_selector2 = selectors['auto_match']
                        
                        connection_prefix = f"{source_zone_full_name}_to_{target_zone_full_name}"
                        print(f"      Using edge selectors: edge_selector1='{type(edge_selector1).__name__ if callable(edge_selector1) else edge_selector1}', edge_selector2='{type(edge_selector2).__name__ if callable(edge_selector2) else edge_selector2}'")
                        create_parallel_zone_connection_detailed(
                            zone1=source_part,
                            zone2=target_part,
                            module=module,
                            positions_DBC=positions_DBC, # positions_DBC might still be useful for ordering inside create_parallel_zone_connection_detailed
                            num_points=this_connection_params.get('num_points', 5),
                            endpoint_offset=this_connection_params.get('endpoint_offset', 1.0),
                            wire_diameter=this_connection_params.get('bond_wire_diameter', 0.5),
                            connection_name_prefix=connection_prefix,
                            try_y_axis_first=this_connection_params.get('try_y_axis_first', True),
                            edge_selector1=edge_selector1,
                            edge_selector2=edge_selector2
                        )
                    except Exception as e:
                        print(f"      Error using create_parallel_zone_connection_detailed for {source_zone_full_name} -> {target_zone_full_name}: {e}")
                                 
    print("Finished creating connections between DBCs.")
# --- End of create_dbc_connections ---


def process_chip_connections(connection_DBC, Zone, DBC, positions_IGBT, rotations_IGBT, positions_FWD, rotations_FWD,
                           width_die_IGBT, length_die_IGBT, width_die_FWD, length_die_FWD, 
                           thickness_die, thickness_die_solder, num_IGBT, num_FWD, 
                           num_bond_wire_IGBT, num_bond_wire_FWD,
                           connection_direction=(0,1), connection_offset=2):  # 新增参数
    """
    处理芯片连接，分为两个阶段：创建芯片和创建键合线
    
    Args:
        connection_DBC: 连接矩阵
        Zone: Zone对象列表
        DBC: DBC装配对象
        positions_IGBT/FWD: IGBT/FWD位置列表
        rotations_IGBT/FWD: IGBT/FWD旋转角度列表
        width_die_IGBT/FWD: IGBT/FWD芯片宽度
        length_die_IGBT/FWD: IGBT/FWD芯片长度 
        thickness_die: 芯片厚度
        thickness_die_solder: 芯片焊料厚度
        num_IGBT/FWD: IGBT/FWD数量
        num_bond_wire_IGBT/FWD: IGBT/FWD键合线数量
        connection_direction: 连接方向，默认(0,1)
        connection_offset: 连接偏移量，默认2
        
    Returns:
        tuple: (die_IGBT_dict, assembly_IGBT_dict, die_FWD_dict, assembly_FWD_dict)
    """
    # 初始化存储字典
    die_IGBT_dict = {}
    assembly_IGBT_dict = {}
    die_FWD_dict = {}
    assembly_FWD_dict = {}

    # 第一阶段：处理Zone->IGBT/FWD连接，创建芯片
    print("Phase 1: Processing Zone->Chip connections to create dies...")
    for row_name, columns in connection_DBC.items():
        for col_name, value in columns.items():
            if value == 1:
                is_row_zone = "Zone" in row_name
                is_col_igbt = "IGBT" in col_name
                is_col_fwd = "FWD" in col_name

                # 仅处理 Zone->Chip 类型的连接
                if is_row_zone and (is_col_igbt or is_col_fwd):
                    print(f"Found Zone -> Chip connection: {row_name} to {col_name}")
                    
                    # Parse indices and type
                    try:
                        zone_index = int(row_name.split('_')[-1])
                        chip_index = int(col_name.split('_')[-1])
                        chip_type = col_name.split('_')[0] 
                    except (IndexError, ValueError):
                        print(f"  Error parsing indices from {row_name} or {col_name}. Skipping.")
                        continue
                    
                    # Check if indices are valid for position/rotation lists
                    if chip_type == "IGBT":
                        if not (0 <= chip_index < len(positions_IGBT) and 0 <= chip_index < len(rotations_IGBT)):
                            print(f"  Error: IGBT index {chip_index} out of bounds. Skipping.")
                            continue
                        positions_list = positions_IGBT
                        rotations_list = rotations_IGBT
                        width = width_die_IGBT
                        length = length_die_IGBT
                        die_dict = die_IGBT_dict
                        assembly_dict = assembly_IGBT_dict
                    elif chip_type == "FWD":
                        if not (0 <= chip_index < len(positions_FWD) and 0 <= chip_index < len(rotations_FWD)):
                            print(f"  Error: FWD index {chip_index} out of bounds. Skipping.")
                            continue
                        positions_list = positions_FWD
                        rotations_list = rotations_FWD
                        width = width_die_FWD
                        length = length_die_FWD
                        die_dict = die_FWD_dict
                        assembly_dict = assembly_FWD_dict
                    else:
                         print(f"  Error: Unknown chip type {chip_type}. Skipping.")
                         continue
                    
                    # Check if zone_index is valid
                    if not (0 <= zone_index < len(Zone)):
                        print(f"  Error: Zone index {zone_index} out of bounds. Skipping.")
                        continue

                    # Create the die assembly if it hasn't been created yet for this name
                    if col_name not in assembly_dict:
                        print(f"  Creating {chip_type} die assembly: {col_name} on Zone {zone_index}")
                        assembly_chip, die_chip = create_die_assembly(
                            die_type=chip_type,
                            zone=Zone[zone_index],
                            die_width=width,
                            die_length=length,
                            die_thickness=thickness_die,
                            solder_thickness=thickness_die_solder,
                            position=positions_list[chip_index]
                        )
                        
                        # Translate the newly created assembly
                        translated_assembly_chip = translate_assembly(assembly_chip, rotations_list[chip_index], positions_list[chip_index])
                        
                        # Add the translated assembly to the main DBC
                        dbc_assembly_name = f"assembly_die_{col_name}"
                        DBC.add(translated_assembly_chip, name=dbc_assembly_name, color=get_color("die"))
                        
                        # Store the original die object and the translated assembly object
                        die_dict[col_name] = die_chip
                        assembly_dict[col_name] = translated_assembly_chip
                        print(f"    Added {dbc_assembly_name} to DBC and stored objects.")
                    else:
                        print(f"  Skipping creation for {col_name}, already exists.")

    print("Phase 1 completed: All dies created.\n")

    # 第二阶段：处理IGBT/FWD->Zone连接，创建键合线
    print("Phase 2: Processing Chip->Zone connections to create bond wires...")

    # --- 修改后的辅助函数：获取芯片的全局连接点 (调用 calculate_connection_points) ---
    def get_global_chip_connection_points(chip_name):
        print(f"    Getting global connection points for {chip_name} using calculate_connection_points")
        try:
            chip_type, chip_index_str = chip_name.split('_')
            chip_index = int(chip_index_str)
        except (ValueError, IndexError):
            print(f"      Error parsing chip name {chip_name}. Cannot get points.")
            return None

        die_obj = None
        assembly_obj = None
        num_bond_wires = 0
        length_die = 0.0
        position = None
        rotation = 0.0 # Default rotation

        if chip_type == "IGBT":
            if (chip_name in die_IGBT_dict and chip_name in assembly_IGBT_dict and
                    0 <= chip_index < len(positions_IGBT) and 0 <= chip_index < len(rotations_IGBT)):
                die_obj = die_IGBT_dict[chip_name]
                assembly_obj = assembly_IGBT_dict[chip_name]
                num_bond_wires = num_bond_wire_IGBT
                length_die = length_die_IGBT
                position = positions_IGBT[chip_index]
                rotation = rotations_IGBT[chip_index]
            else:
                 print(f"      Error: IGBT {chip_name} or its position/rotation data not found/valid.")
                 return None
        elif chip_type == "FWD":
            if (chip_name in die_FWD_dict and chip_name in assembly_FWD_dict and
                    0 <= chip_index < len(positions_FWD) and 0 <= chip_index < len(rotations_FWD)):
                die_obj = die_FWD_dict[chip_name]
                assembly_obj = assembly_FWD_dict[chip_name]
                num_bond_wires = num_bond_wire_FWD
                length_die = length_die_FWD
                position = positions_FWD[chip_index]
                rotation = rotations_FWD[chip_index]
            else:
                print(f"      Error: FWD {chip_name} or its position/rotation data not found/valid.")
                return None
        else:
            print(f"      Error: Unknown chip type {chip_type} for {chip_name}.")
            return None

        if not die_obj or not assembly_obj or num_bond_wires <= 0 or length_die <= 0 or position is None:
             print(f"      Error: Missing data for {chip_name}. Cannot calculate points.")
             return None

        # 1. 计算局部坐标点 (与之前逻辑相同)
        local_points_coords = [] # Use list of tuples/lists for calculate_connection_points
        try:
            if not hasattr(die_obj, 'faces'):
                 raise TypeError(f"die_obj for {chip_name} is not a Workplane or similar object.")
            top_face = die_obj.faces("+Z").val()
            if not top_face:
                 raise ValueError(f"Could not get +Z face for {chip_name}.")
            center = top_face.Center()

            spacing_factor = 2 if chip_type == "FWD" else 4
            y_offset = 1

            if num_bond_wires == 1:
                 local_points_coords.append((center.x, center.y, center.z))
            else:
                 y_start = center.y - length_die / 2 + y_offset
                 y_range = length_die - spacing_factor
                 y_step = y_range / (num_bond_wires - 1)
                 for i in range(num_bond_wires):
                     y_coord = y_start + i * y_step
                     local_points_coords.append((center.x, y_coord, center.z))

        except Exception as e:
            print(f"      Error calculating local points for {chip_name}: {e}")
            return None

        # 2. 调用 calculate_connection_points 获取全局坐标
        try:
            # Pass the ORIGINAL die geometry object, rotation, position,
            # and the list of local coordinates.
            global_points = calculate_connection_points(
                die_obj,  # <--- 修改：传递原始的 die_obj (Workplane/Solid)
                rotation,
                position,
                local_points_coords # Pass the list of coordinates
            )

            if not global_points:
                 print(f"      Error: calculate_connection_points returned empty or None for {chip_name}.")
                 return None

            # Ensure the result is a list of Vectors
            if not isinstance(global_points, list) or not all(isinstance(p, Vector) for p in global_points):
                 print(f"      Warning: calculate_connection_points did not return a list of Vectors for {chip_name}. Attempting conversion.")
                 try:
                     global_points = [Vector(p) if not isinstance(p, Vector) else p for p in global_points] # Convert non-Vectors
                 except Exception as conv_e:
                     print(f"      Error: Could not convert result from calculate_connection_points to Vectors for {chip_name}: {conv_e}")
                     return None

            print(f"      Calculated {len(global_points)} global points for {chip_name} via calculate_connection_points.")
            return global_points

        except NameError:
             print(f"      Error: The function 'calculate_connection_points' is not defined or accessible.")
             return None
        except Exception as e:
             # 打印更详细的错误信息，包括类型
             print(f"      Error calling calculate_connection_points for {chip_name} with die_obj type {type(die_obj)}: {e}")
             # 可以考虑在这里打印 die_obj, rotation, position, local_points_coords 的值进行调试
             # print(f"        die_obj: {die_obj}")
             # print(f"        rotation: {rotation}, position: {position}")
             # print(f"        local_points_coords: {local_points_coords}")
             return None
    # --- 结束：获取全局连接点辅助函数 ---

    # 辅助函数：查找连接到同一 Zone 的类型 2 芯片
    def find_type2_partner(target_zone_name, connection_matrix):
        for r_name, cols in connection_matrix.items():
            if target_zone_name in cols and cols[target_zone_name] == 2:
                return r_name
        return None

    # 辅助函数：创建芯片到芯片的顺序键合线 (修改后)
    def create_chip_to_chip_sequential_bondwires(chip_A_name, chip_B_name, DBC_assembly):
        print(f"  Attempting sequential connection: {chip_A_name} -> {chip_B_name}")
        points_A = None
        points_B = None

        # --- 获取芯片 A 的全局连接点 (复制自 _create_chip_to_zone_bondwires 逻辑) --- 
        try:
            print(f"    Getting points for Chip A: {chip_A_name}")
            chip_type_A, chip_index_A_str = chip_A_name.split('_')
            chip_index_A = int(chip_index_A_str)

            die_dict_A = die_IGBT_dict if chip_type_A == "IGBT" else die_FWD_dict
            assembly_dict_A = assembly_IGBT_dict if chip_type_A == "IGBT" else assembly_FWD_dict
            rotations_list_A = rotations_IGBT if chip_type_A == "IGBT" else rotations_FWD
            positions_list_A = positions_IGBT if chip_type_A == "IGBT" else positions_FWD
            num_bond_wires_A = num_bond_wire_IGBT if chip_type_A == "IGBT" else num_bond_wire_FWD
            length_die_A = length_die_IGBT if chip_type_A == "IGBT" else length_die_FWD
            num_chips_A = num_IGBT if chip_type_A == "IGBT" else num_FWD

            # 检查和获取对象 (类似 _create_chip_to_zone_bondwires)
            if not (0 <= chip_index_A < num_chips_A):
                 raise ValueError(f"Index {chip_index_A} out of bounds for {chip_type_A}")
            if chip_A_name not in die_dict_A or chip_A_name not in assembly_dict_A:
                 raise ValueError(f"{chip_A_name} not found in dictionaries.")
            
            die_obj_A = die_dict_A[chip_A_name]
            assembly_obj_A = assembly_dict_A[chip_A_name]
            rotation_A = rotations_list_A[chip_index_A]
            position_A = positions_list_A[chip_index_A]

            # 计算局部点 (connect_points 列表)
            local_points_coords_A = []
            index_A = die_obj_A.faces("+Z").val().Center()
            spacing_factor_A = 2 if chip_type_A == "FWD" else 4
            y_offset_A = 1
            if num_bond_wires_A == 1:
                local_points_coords_A.append((index_A.x, index_A.y, index_A.z))
            else:
                y_start_A = index_A.y - length_die_A / 2 + y_offset_A
                y_range_A = length_die_A - spacing_factor_A
                y_step_A = y_range_A / (num_bond_wires_A - 1)
                for i in range(num_bond_wires_A):
                    y_coord_A = y_start_A + i * y_step_A
                    local_points_coords_A.append((index_A.x, y_coord_A, index_A.z))
            
            # 调用 calculate_connection_points 获取全局点
            # 严格按照 _create_chip_to_zone_bondwires 传递 assembly_obj_A
            points_A = calculate_connection_points(assembly_obj_A, rotation_A, position_A, local_points_coords_A)
            
            if not points_A:
                 raise ValueError("calculate_connection_points returned None or empty for Chip A")
            # 确保是 Vector 列表
            points_A = [Vector(p) if not isinstance(p, Vector) else p for p in points_A]
            print(f"      Got {len(points_A)} points for Chip A.")

        except Exception as e:
             print(f"    Error getting points for Chip A ({chip_A_name}): {e}")
             points_A = None # 标记失败

        # --- 获取芯片 B 的全局连接点 (复制自 _create_chip_to_zone_bondwires 逻辑) --- 
        try:
            print(f"    Getting points for Chip B: {chip_B_name}")
            chip_type_B, chip_index_B_str = chip_B_name.split('_')
            chip_index_B = int(chip_index_B_str)

            die_dict_B = die_IGBT_dict if chip_type_B == "IGBT" else die_FWD_dict
            assembly_dict_B = assembly_IGBT_dict if chip_type_B == "IGBT" else assembly_FWD_dict
            rotations_list_B = rotations_IGBT if chip_type_B == "IGBT" else rotations_FWD
            positions_list_B = positions_IGBT if chip_type_B == "IGBT" else positions_FWD
            num_bond_wires_B = num_bond_wire_IGBT if chip_type_B == "IGBT" else num_bond_wire_FWD
            length_die_B = length_die_IGBT if chip_type_B == "IGBT" else length_die_FWD
            num_chips_B = num_IGBT if chip_type_B == "IGBT" else num_FWD

            # 检查和获取对象 (类似 _create_chip_to_zone_bondwires)
            if not (0 <= chip_index_B < num_chips_B):
                 raise ValueError(f"Index {chip_index_B} out of bounds for {chip_type_B}")
            if chip_B_name not in die_dict_B or chip_B_name not in assembly_dict_B:
                 raise ValueError(f"{chip_B_name} not found in dictionaries.")
            
            die_obj_B = die_dict_B[chip_B_name]
            assembly_obj_B = assembly_dict_B[chip_B_name]
            rotation_B = rotations_list_B[chip_index_B]
            position_B = positions_list_B[chip_index_B]

            # 计算局部点 (connect_points 列表)
            local_points_coords_B = []
            index_B = die_obj_B.faces("+Z").val().Center()
            spacing_factor_B = 2 if chip_type_B == "FWD" else 4
            y_offset_B = 1
            if num_bond_wires_B == 1:
                local_points_coords_B.append((index_B.x, index_B.y, index_B.z))
            else:
                y_start_B = index_B.y - length_die_B / 2 + y_offset_B
                y_range_B = length_die_B - spacing_factor_B
                y_step_B = y_range_B / (num_bond_wires_B - 1)
                for i in range(num_bond_wires_B):
                    y_coord_B = y_start_B + i * y_step_B
                    local_points_coords_B.append((index_B.x, y_coord_B, index_B.z))
            
            # 调用 calculate_connection_points 获取全局点
            # 严格按照 _create_chip_to_zone_bondwires 传递 assembly_obj_B
            points_B = calculate_connection_points(assembly_obj_B, rotation_B, position_B, local_points_coords_B)
            
            if not points_B:
                 raise ValueError("calculate_connection_points returned None or empty for Chip B")
            # 确保是 Vector 列表
            points_B = [Vector(p) if not isinstance(p, Vector) else p for p in points_B]
            print(f"      Got {len(points_B)} points for Chip B.")

        except Exception as e:
             print(f"    Error getting points for Chip B ({chip_B_name}): {e}")
             points_B = None # 标记失败

        # --- 创建键合线 --- 
        if points_A and points_B:
            # --- 新增：基于质心连接向量的投影排序 --- >
            try:
                print(f"      Calculating centroids and connection vector for {len(points_A)} points (A) and {len(points_B)} points (B).")
                
                # 计算质心
                if not points_A or not points_B: # 再次检查以防万一
                     raise ValueError("Point lists are empty, cannot calculate centroids.")
                     
                sum_A = Vector(0,0,0)
                for p in points_A: sum_A += p
                centroid_A = sum_A / len(points_A)
                
                sum_B = Vector(0,0,0)
                for p in points_B: sum_B += p
                centroid_B = sum_B / len(points_B)
                
                # 计算连接向量并归一化
                vec_AB = centroid_B - centroid_A
                if vec_AB.Length < 1e-6: # 检查向量长度，避免除零
                    print("        Warning: Centroids are too close. Falling back to Y-coordinate sorting.")
                    # 退回 Y 排序作为备选方案
                    sorted_points_A = sorted(points_A, key=lambda p: p.y)
                    sorted_points_B = sorted(points_B, key=lambda p: p.y)
                else:
                    vec_AB_normalized = vec_AB.normalized()
                    print(f"        Connection vector (normalized): {vec_AB_normalized}")

                    # 计算投影并排序 A
                    projections_A = []
                    for i, p_A in enumerate(points_A):
                        projection_A = (p_A - centroid_A).dot(vec_AB_normalized)
                        projections_A.append((projection_A, i)) # 存储投影值和原始索引
                    
                    projections_A.sort() # 按投影值排序
                    sorted_points_A = [points_A[i] for proj, i in projections_A] # 按排序后的索引重新组织点列表
                    print(f"        Sorted points A based on projection.")

                    # 计算投影并排序 B (使用相同的参考点 centroid_A 和向量 vec_AB_normalized)
                    projections_B = []
                    for i, p_B in enumerate(points_B):
                        projection_B = (p_B - centroid_A).dot(vec_AB_normalized)
                        projections_B.append((projection_B, i))
                    
                    projections_B.sort()
                    sorted_points_B = [points_B[i] for proj, i in projections_B]
                    print(f"        Sorted points B based on projection.")

            except Exception as e:
                print(f"      Error during projection sorting: {e}. Falling back to original order.")
                # 如果排序出错，则退回使用原始顺序
                sorted_points_A = points_A
                sorted_points_B = points_B
            # <--- 结束排序 ---
            
            min_points = min(len(sorted_points_A), len(sorted_points_B))
            # 更新打印信息和键合线名称
            print(f"    Creating {min_points} non-crossing bond wires between {chip_A_name} and {chip_B_name} (Projection-sorted).")
            for k in range(min_points):
                try:
                    p1 = sorted_points_A[k]
                    p2 = sorted_points_B[k]
                    wire_diameter = 0.5 # 示例值
                    wire = create_bond_wire(p1, p2, wire_diameter=wire_diameter)
                    # 更新名称以反映排序方法
                    DBC_assembly.add(wire, name=f"Wire_{chip_A_name}_to_{chip_B_name}_sortedProj_{k}", color=get_color("bond_wire_2"))
                except Exception as e:
                    print(f"      Error creating projection-sorted bond wire {k} between {p1} and {p2}: {e}")
            print(f"  Finished non-crossing connection (Projection-sorted): {chip_A_name} -> {chip_B_name}")
        else:
            print(f"    Skipping bond wire creation between {chip_A_name} and {chip_B_name} due to previous errors.")

    # 记录已处理的类型 1 连接 (避免重复创建 A->X)
    handled_type1_connections = set() # 存储 (chip_A_name, zone_name)

    # 第一次遍历：处理 A(1)->B(2) 的重定向连接
    print("Phase 2a: Processing Chip(1)->Chip(2) redirections...")
    for row_name, columns in connection_DBC.items():
        is_row_igbt = "IGBT" in row_name
        is_row_fwd = "FWD" in row_name

        if is_row_igbt or is_row_fwd: # 确保行是芯片
            for col_name, value in columns.items():
                is_col_zone = "Zone" in col_name
                if is_col_zone and value == 1: # 找到一个 Chip -> Zone (Type 1) 连接
                    chip_B_name = find_type2_partner(col_name, connection_DBC)
                    if chip_B_name:
                        print(f"Redirecting connection: Found {row_name}(1)->{col_name} and {chip_B_name}(2)->{col_name}. Creating {row_name}->{chip_B_name}.")
                        # create_chip_to_chip_sequential_bondwires(row_name, chip_B_name, DBC) # 调用修改后的函数
                        handled_type1_connections.add((row_name, col_name))

        # 新增阶段：处理直接的 Chip-to-Chip 连接值为 1 的情况
    print("Phase 2b: Processing direct Chip(1)->Chip(1) connections...")
    handled_chip_to_chip_1 = set() # 记录已处理的 Chip-Chip(1) 连接对，避免 A->B 和 B->A 重复
    for row_name, columns in connection_DBC.items():
        is_row_chip = "IGBT" in row_name or "FWD" in row_name
        if not is_row_chip:
            continue

        for col_name, value in columns.items():
            is_col_chip = "IGBT" in col_name or "FWD" in col_name
            if is_col_chip and value == 1:
                # 检查是否已被重定向逻辑处理 (不太可能，但以防万一)
                if (row_name, col_name) in handled_type1_connections:
                    continue

                # 检查这对芯片是否已被处理 (忽略顺序)
                pair = tuple(sorted((row_name, col_name)))
                if pair in handled_chip_to_chip_1:
                    continue

                print(f"Found direct Chip -> Chip connection (value 1): {row_name} to {col_name}. Creating wires.")
                # create_chip_to_chip_sequential_bondwires(row_name, col_name, DBC)
                handled_chip_to_chip_1.add(pair) # 记录已处理


    print("Phase 2c: Processing remaining Chip->Zone connections...") # 原 Phase 2b 改为 Phase 2c
    # 第二次遍历：创建剩余的 Chip -> Zone 连接
    for row_name, columns in connection_DBC.items():
        for col_name, value in columns.items():
            is_row_igbt = "IGBT" in row_name
            is_row_fwd = "FWD" in row_name
            is_col_zone = "Zone" in col_name

            if is_col_zone and (is_row_igbt or is_row_fwd):
                if (value == 1 and (row_name, col_name) not in handled_type1_connections) or value != 1:
                    if value > 0:
                         print(f"Creating direct connection: {row_name} -> {col_name} (Value: {value})")
                         chip_type = row_name.split('_')[0]
                         if chip_type == "IGBT":
                             # _create_chip_to_zone_bondwires(
                             #     row_name, col_name, Zone, DBC,
                             #     die_IGBT_dict, assembly_IGBT_dict,
                             #     rotations_IGBT, positions_IGBT,
                             #     num_IGBT, num_bond_wire_IGBT, length_die_IGBT,
                             #     "IGBT",
                             #     connection_direction, connection_offset
                             # )
                             pass
                         elif chip_type == "FWD":
                             # _create_chip_to_zone_bondwires(
                             #     row_name, col_name, Zone, DBC,
                             #     die_FWD_dict, assembly_FWD_dict,
                             #     rotations_FWD, positions_FWD,
                             #     num_FWD, num_bond_wire_FWD, length_die_FWD,
                             #     "FWD",
                             #     connection_direction, connection_offset
                             # )
                             pass
                         else:
                              print(f"  Warning: Unknown chip type '{chip_type}' for row {row_name}. Cannot create bond wires.")

    print("Phase 2 completed: All bond wires created.\n")

    return die_IGBT_dict, assembly_IGBT_dict, die_FWD_dict, assembly_FWD_dict

# 添加选择器工厂函数
def create_edge_selectors(tolerance=0.01):
    """
    创建常用的边选择器函数，返回工厂函数字典
    
    Args:
        tolerance: 边方向判断的容差值
        
    Returns:
        dict: 包含常用选择器工厂函数的字典
    """
    selectors = {}
    
    # 选择最小Y坐标的水平边
    def min_y_horizontal_selector(face):
        # 获取所有平行Y轴的边
        edges = [e for e in face.Edges() 
                if abs(e.tangentAt(0).normalized().y) < tolerance]
        if not edges:
            raise ValueError("没有平行Y轴的边")
        return min(edges, key=lambda e: abs(e.Center().y))
    
    # 选择最大Y坐标的水平边
    def max_y_horizontal_selector(face):
        # 获取所有平行Y轴的边
        edges = [e for e in face.Edges() 
                if abs(e.tangentAt(0).normalized().y) < tolerance]
        if not edges:
            raise ValueError("没有平行Y轴的边")
        return max(edges, key=lambda e: abs(e.Center().y))
    
    # 选择最小X坐标的垂直边
    def min_x_vertical_selector(face):
        # 获取所有平行X轴的边
        edges = [e for e in face.Edges() 
                if abs(e.tangentAt(0).normalized().x) < tolerance]
        if not edges:
            raise ValueError("没有平行X轴的边")
        return min(edges, key=lambda e: abs(e.Center().x))
    
    # 选择最大X坐标的垂直边
    def max_x_vertical_selector(face):
        # 获取所有平行X轴的边
        edges = [e for e in face.Edges() 
                if abs(e.tangentAt(0).normalized().x) < tolerance]
        if not edges:
            raise ValueError("没有平行X轴的边")
        return max(edges, key=lambda e: abs(e.Center().x))
    
    # 创建参考边选择器工厂函数（闭包）
    def create_reference_selector(reference_edge, axis='auto'):
        """
        创建参考边选择器，根据参考边的方向自动选择相同类型的边
        
        Args:
            reference_edge: 参考边
            axis: 匹配坐标轴，'auto'表示自动检测边的方向，'x'或'y'表示指定方向
            
        Returns:
            function: 边选择器函数
        """
        # 获取参考边的X和Y坐标
        ref_center = reference_edge.Center()
        ref_tangent = reference_edge.tangentAt(0).normalized()
        
        # 自动检测边的方向
        if axis == 'auto':
            is_horizontal = abs(ref_tangent.y) < tolerance
            is_vertical = abs(ref_tangent.x) < tolerance
        else:
            is_horizontal = axis == 'x'
            is_vertical = axis == 'y'
        
        def selector(face):
            # 获取所有适当方向的边
            if is_horizontal:
                # 寻找水平边（平行于X轴的边）
                edges = [e for e in face.Edges() 
                        if abs(e.tangentAt(0).normalized().y) < tolerance]
                if not edges:
                    raise ValueError("没有平行Y轴的边")
                # 找出最接近参考点的边（同时考虑X和Y坐标）
                return min(edges, key=lambda e: ((e.Center().x - ref_center.x)**2 + 
                                                (e.Center().y - ref_center.y)**2))
            elif is_vertical:
                # 寻找垂直边（平行于Y轴的边）
                edges = [e for e in face.Edges() 
                        if abs(e.tangentAt(0).normalized().x) < tolerance]
                if not edges:
                    raise ValueError("没有平行X轴的边")
                # 找出最接近参考点的边（同时考虑X和Y坐标）
                return min(edges, key=lambda e: ((e.Center().x - ref_center.x)**2 + 
                                                (e.Center().y - ref_center.y)**2))
            else:
                # 默认回退到原来的逻辑
                edges = face.Edges()
                if not edges:
                    raise ValueError("没有边可选择")
                return min(edges, key=lambda e: ((e.Center().x - ref_center.x)**2 + 
                                               (e.Center().y - ref_center.y)**2))
        
        return selector
    
    # 创建自动匹配选择器的工厂函数（仅需在使用时提供参考边）
    def auto_match_factory(first_selector_result=None):
        """
        创建一个自动匹配第一个选择器的选择器
        当调用create_mapped_connection时，会自动将其替换为实际的匹配选择器
        
        Args:
            first_selector_result: 第一个选择器的结果，通常在函数内部设置
            
        Returns:
            str: 标识字符串，提示需要动态创建选择器
        """
        # 这是一个占位符，在实际使用时会被替换
        return "AUTO_MATCH_SELECTOR"
    
    # 添加到选择器字典
    selectors['min_y_horizontal'] = min_y_horizontal_selector
    selectors['max_y_horizontal'] = max_y_horizontal_selector
    selectors['min_x_vertical'] = min_x_vertical_selector
    selectors['max_x_vertical'] = max_x_vertical_selector
    selectors['create_reference_selector'] = create_reference_selector
    selectors['auto_match'] = auto_match_factory  # 添加自动匹配选择器
    
    return selectors

def create_mapped_connection(source_part, target_part, module, 
                           num_points=5, distance_interval=1.5, 
                           edge_selector1=None, edge_selector2=None,
                           max_angle=30, bond_wire_diameter=0.5,
                           offset_distance=-2, tolerance=0.01):
    """
    创建两个组件之间的映射连接，支持自定义或默认选择器
    
    Args:
        source_part: 源组件
        target_part: 目标组件
        module: 要添加连接的主Assembly对象
        num_points: 创建的连接点数
        distance_interval: 点间距
        edge_selector1: 源组件的边选择函数，如果为None则使用默认选择器
        edge_selector2: 目标组件的边选择函数，如果为None或'auto_match'则自动创建匹配选择器
        max_angle: 最大允许投影角度
        bond_wire_diameter: 键合线直径
        offset_distance: 边缘偏移距离
        tolerance: 容差值
    """
    # 如果没有提供选择器，使用默认选择器
    selectors = create_edge_selectors(tolerance)
    
    if edge_selector1 is None:
        edge_selector1 = selectors['min_y_horizontal']
    
    # 动态确定参考边，基于找到的 source_part
    try:
        # 获取源部件的参考边
        ref_edge = edge_selector1(source_part.obj.faces(">Z").first().val().located(source_part.loc))
        
        # 处理第二个选择器
        # 如果edge_selector2是None或者是auto_match选择器或其结果是"AUTO_MATCH_SELECTOR"占位符
        if edge_selector2 is None or edge_selector2 == selectors['auto_match'] or (callable(edge_selector2) and edge_selector2() == "AUTO_MATCH_SELECTOR"):
            # 自动创建匹配选择器
            edge_selector2 = selectors['create_reference_selector'](ref_edge, axis='auto')
    except Exception as e:
        print(f"    Error determining reference edge: {e}. Skipping connection.")
        return
        
    # 生成映射点
    try:
        src, tgt = generate_mapped_points(
            source_part, target_part,
            edge_selector1=edge_selector1,
            edge_selector2=edge_selector2,
            num_points=num_points,
            distance_interval=distance_interval,
            max_angle=max_angle,
            offset_distance=offset_distance
        )
    except Exception as e:
        print(f"    Error generating mapped points: {e}. Skipping connection.")
        return
    
    # 创建键合线
    source_name = source_part.name
    target_name = target_part.name
    created_wires = []
    
    for i in range(len(src)):
        p1_tuple = src[i].toTuple()
        p2_tuple = tgt[i].toTuple()
        wire = create_bond_wire(p1_tuple, p2_tuple, bond_wire_diameter)
        wire_name = f"bond_wire_{i}_{source_name}_to_{target_name}"
        module.add(wire, name=wire_name, color=get_color("bond_wire"))
        created_wires.append((wire, wire_name))
        print(f"    Created bond wire {i} from {source_name} to {target_name}")
    
    return created_wires

def extract_connection_pairs(connection_module):
    """
    从connection_module矩阵中提取所有有效的连接对
    
    Args:
        connection_module: DBC间连接矩阵
        
    Returns:
        list: 连接对列表，格式为[(source_dbc_name, target_dbc_name), ...]
    """
    connection_pairs = []
    
    for source_dbc_name, targets in connection_module.items():
        for target_dbc_name, connect_flag in targets.items():
            if connect_flag == 1:
                connection_pairs.append((source_dbc_name, target_dbc_name))
                print(f"Extracted connection pair: {source_dbc_name} -> {target_dbc_name}")
    
    return connection_pairs

def generate_connection_params_template(connection_pairs, use_detailed_format=True):
    """
    根据连接对列表生成连接参数模板
    
    Args:
        connection_pairs: 连接对列表，格式为[(source_dbc_name, target_dbc_name), ...]
        use_detailed_format: 是否使用详细格式（包含注释和示例值）
        
    Returns:
        dict: 连接参数模板
    """
    template = {
        'global': {},
        'default': {}
    }
    
    if use_detailed_format:
        # 添加注释和示例值
        template['global'] = {
            'tolerance': 0.01,  # 公差
            'bond_wire_diameter': 0.5  # 键合线直径
        }
        template['default'] = {
            'num_points': 5,  # 连接点数量
            'distance_interval': 1.5,  # 点间距
            'max_angle': 30,  # 最大投影角度
            # 注意：实际使用时，应该传递函数对象而不是字符串
            # 例如：'edge_selector1': selectors['min_y_horizontal']
            # 例如：'edge_selector2': selectors['auto_match']
        }
    
    # 为每个连接对添加条目
    for source, target in connection_pairs:
        key = f"{source}->{target}"
        if use_detailed_format:
            template[key] = {
                '# 以下参数会覆盖默认参数': '',
                'num_points': 5,  # 连接点数量
                'distance_interval': 1.5,  # 可选：点间距
                # 注意：实际使用时，应该传递函数对象而不是字符串
                # 例如：'edge_selector1': selectors['min_y_horizontal']
                # 例如：'edge_selector2': selectors['auto_match']
            }
        else:
            template[key] = {}
    
    return template

def calculate_dbcs_boundary(DBCs, ceramics_width, ceramics_length, positions, rotations):
    """
    计算多个DBC的总边界框
    
    Args:
        DBCs: DBC对象列表
        ceramics_width: 陶瓷板宽度
        ceramics_length: 陶瓷板长度
        positions: DBC位置列表
        rotations: DBC旋转角度列表
        
    Returns:
        tuple: (min_x, min_y, max_x, max_y) 边界坐标
    """
    # 初始化边界值
    min_x, min_y = float('inf'), float('inf')
    max_x, max_y = float('-inf'), float('-inf')
    
    # 遍历所有DBC
    for i, (dbc, position, rotation) in enumerate(zip(DBCs, positions, rotations)):
        # 计算DBC的四个角点（考虑旋转）
        corners = []
        
        # 陶瓷板的四个角点（相对于原点）
        if rotation == 0:
            corners = [
                (0, 0),  # 左下
                (ceramics_width, 0),  # 右下
                (0, ceramics_length),  # 左上
                (ceramics_width, ceramics_length)  # 右上
            ]
        elif rotation == 90:
            corners = [
                (0, 0),  # 左下
                (0, ceramics_width),  # 左上
                (-ceramics_length, 0),  # 右下
                (-ceramics_length, ceramics_width)  # 右上
            ]
        elif rotation == 180:
            corners = [
                (0, 0),  # 左下
                (-ceramics_width, 0),  # 右下
                (0, -ceramics_length),  # 左上
                (-ceramics_width, -ceramics_length)  # 右上
            ]
        elif rotation == 270:
            corners = [
                (0, 0),  # 左下
                (0, -ceramics_width),  # 左上
                (ceramics_length, 0),  # 右下
                (ceramics_length, -ceramics_width)  # 右上
            ]
        else:
            # 处理任意角度旋转
            rad = math.radians(rotation)
            cos_r, sin_r = math.cos(rad), math.sin(rad)
            corners = [
                (0, 0),  # 原点
                (ceramics_width * cos_r, ceramics_width * sin_r),  # 右边点
                (-ceramics_length * sin_r, ceramics_length * cos_r),  # 上边点
                (ceramics_width * cos_r - ceramics_length * sin_r, 
                 ceramics_width * sin_r + ceramics_length * cos_r)  # 右上角点
            ]
        
        # 将角点转换为绝对坐标
        for corner_x, corner_y in corners:
            abs_x = corner_x + position.x
            abs_y = corner_y + position.y
            
            # 更新边界
            min_x = min(min_x, abs_x)
            min_y = min(min_y, abs_y)
            max_x = max(max_x, abs_x)
            max_y = max(max_y, abs_y)
    
    print(f"Calculated DBC boundary: ({min_x}, {min_y}) to ({max_x}, {max_y})")
    return min_x, min_y, max_x, max_y

def create_substrate_for_dbcs(DBCs: List[Assembly], 
                             thickness_substrate: float, 
                             distance_substrate: float, 
                             substrate_reference_z: float) -> cq.Workplane:
    """
    创建基于多个实际DBC装配体位置的基板。
    
    Args:
        DBCs: 包含已定位和旋转的DBC Assembly对象的列表。
        thickness_substrate: 基板厚度。
        distance_substrate: 基板扩展距离。
        substrate_reference_z: 基板Z轴参考位置。
        
    Returns:
        cq.Workplane: 基板对象。
    """
    # 计算所有DBC的实际边界
    min_x, min_y, max_x, max_y = float('inf'), float('inf'), float('-inf'), float('-inf')
    
    print(f"计算基板边界 - 基于 {len(DBCs)} 个实际DBC装配体")
    
    if not DBCs:
        raise ValueError("DBCs列表不能为空")

    # 遍历所有实际的DBC装配体
    for i, dbc_assembly in enumerate(DBCs):
        try:
            # 获取每个DBC装配体在世界坐标系中的边界框
            # toCompound() 将装配体视为一个整体来计算边界
            compound = dbc_assembly.toCompound()
            if compound is None:
                 print(f"警告：DBC #{i} ({dbc_assembly.name}) 无法转换为Compound，跳过。")
                 continue
                 
            bbox = compound.BoundingBox()
            
            # 更新全局边界
            min_x = min(min_x, bbox.xmin)
            min_y = min(min_y, bbox.ymin)
            max_x = max(max_x, bbox.xmax)
            max_y = max(max_y, bbox.ymax)
            
            print(f"  DBC #{i} ({dbc_assembly.name}) 边界: X=({bbox.xmin:.2f}, {bbox.xmax:.2f}), Y=({bbox.ymin:.2f}, {bbox.ymax:.2f})")

        except Exception as e:
            print(f"处理 DBC #{i} ({dbc_assembly.name}) 时出错: {e}")
            continue # 跳过有问题的DBC

    # 检查是否成功计算了边界
    if min_x == float('inf') or max_x == float('-inf'):
        raise ValueError("无法计算任何DBC的有效边界。")

    print(f"所有DBC组合后的原始边界: ({min_x:.2f}, {min_y:.2f}) 到 ({max_x:.2f}, {max_y:.2f})")
    print(f"原始尺寸: {max_x - min_x:.2f} x {max_y - min_y:.2f}")
    
    # 添加扩展距离
    min_x -= distance_substrate
    min_y -= distance_substrate
    max_x += distance_substrate
    max_y += distance_substrate
    
    # 计算宽度和高度
    width = max_x - min_x
    height = max_y - min_y
    
    print(f"最终边界 (含扩展): ({min_x:.2f}, {min_y:.2f}) 到 ({max_x:.2f}, {max_y:.2f})")
    print(f"最终基板尺寸: {width:.2f} x {height:.2f}")
    
    # 创建基板工作平面（以左下角为基准点）
    # Z 偏移参考点现在由参数 substrate_reference_z 控制
    substrate_workplane = cq.Workplane("XY").workplane(offset=substrate_reference_z)
    
    # 创建基板盒体
    # 使用计算出的 min_x, min_y 进行平移
    substrate = (
        substrate_workplane
        .box(width, height, thickness_substrate, centered=(False, False, False))
        # 注意：Z坐标也需要调整，因为 substrate_reference_z 是基板的上表面 Z 坐标
        .translate((min_x, min_y, -thickness_substrate)) 
    )
    
    return substrate

def collect_all_gate_geometries(gate_configs: dict) -> tuple[list, list]:
    """
    从门极配置字典中收集所有类型的几何定义。

    Args:
        gate_configs: 包含门极类型及其几何定义的字典。
                      格式: {type_key: {"start_points": [...], "relative_moves": [...]}}

    Returns:
        tuple: 包含两个列表 (all_start_points, all_relative_moves)
    """
    all_start_points = []
    all_relative_moves = []
    print("Collecting all gate geometries from configurations...")
    for gate_type_key, config in gate_configs.items():
        start_points = config.get("start_points")
        relative_moves = config.get("relative_moves")

        if start_points and relative_moves and len(start_points) == len(relative_moves):
            all_start_points.extend(start_points)
            all_relative_moves.extend(relative_moves)
            print(f"  Collected {len(start_points)} geometries for type {gate_type_key}.")
        else:
            print(f"警告: type_Gate={gate_type_key} 的配置不完整或不匹配，跳过几何收集。")

    if not all_start_points or not all_relative_moves:
         print("警告：未能从 gate_configs 收集到任何有效的门极几何定义。")
         # 返回空列表，让调用方处理

    return all_start_points, all_relative_moves


def create_labeled_gates(gate_configs: dict,
                         ceramics: cq.Workplane,
                         thickness: float,
                         fillet: float,
                         margin: float) -> list[tuple[cq.Workplane, any]]:
    """
    根据配置字典创建所有类型的门极，并返回带类型标签的对象列表。

    Args:
        gate_configs: 包含门极类型及其几何定义的字典。
        ceramics: 陶瓷基板对象。
        thickness: 门极厚度。
        fillet: 圆角值。
        margin: 边距。

    Returns:
        list: 包含元组的列表 [(gate_object, gate_type_key), ...]
    """
    labeled_gates = []
    print("Creating and labeling all defined gate types...")
    gate_counter = 0
    for gate_type_key, config in gate_configs.items():
        start_points = config.get("start_points", [])
        relative_moves = config.get("relative_moves", [])

        if not start_points or not relative_moves or len(start_points) != len(relative_moves):
            print(f"警告: 跳过创建 type_Gate={gate_type_key}，配置不完整。")
            continue

        print(f"  Creating gates for type {gate_type_key}...")
        for i in range(len(start_points)):
            try:
                gate_obj = create_upper_Cu( # Assuming create_upper_Cu is defined above in utils.py
                    layer_type='gate',
                    ceramics=ceramics,
                    start_point=start_points[i],
                    relative_moves=relative_moves[i],
                    thickness=thickness,
                    fillet_value=fillet,
                    margin=margin
                )
                labeled_gates.append((gate_obj, gate_type_key))
                gate_counter += 1
                print(f"    Created gate object {gate_counter} (type {gate_type_key}, index {i}).")
            except Exception as e:
                 print(f"    创建 gate (type {gate_type_key}, index {i}) 时出错: {e}")

    if not labeled_gates:
        print("警告：未能成功创建任何门极对象。")

    print(f"Finished creating labeled gates. Total: {len(labeled_gates)}")
    return labeled_gates
def _get_dbc_index_from_name(name: str) -> int:
    try:
        return int(name.split('_')[-1])
    except Exception:
        return -1
# --- REFACTORED: New function for parallel Zone-Zone connections ---
def create_parallel_zone_connection_detailed(
    zone1: Union[cq.Workplane, cq.Assembly],
    zone2: Union[cq.Workplane, cq.Assembly],
    module: Assembly,
    positions_DBC: list[Vector] = None,
    num_points: int = 5,
    endpoint_offset: float = 1.0, # How far along the connection line to offset endpoints outwards
    wire_diameter: float = 0.5,
    min_spacing_factor: float = 1.2, # Multiplier for wire diameter for minimum spacing
    sampling_percentage: float = 0.8, # Sample on the central 80% of the shorter edge
    min_points: int = 2, # Minimum number of bond wires allowed
    connection_name_prefix: str = "parallel_zone",
    try_y_axis_first: bool = True, # 这个参数现在可能不再直接相关，但可以保留或移除
    edge_selector1: Callable = None, # 新增：用于zone1的边缘选择器
    edge_selector2: Callable = None  # 新增：用于zone2的边缘选择器
):
    """
    创建两个Zone之间的键合线 (使用传入的边缘选择器)。V3 - 改进版
    - 使用提供的 edge_selector1 和 edge_selector2 确定连接边。
    - 在较短的边中心区域进行等距采样。
    - 将点投影/映射到长边。
    - 检查间距，如果小于线径*系数，则减少点数重新计算。
    - 对两边的点沿连接线向外偏移，然后连接。
    """
    created_wires = []
    try:
        # 1. 获取顶面
        face1 = get_top_face(zone1)
        face2 = get_top_face(zone2)
        if not face1 or not face2:
            print(f"    错误: 无法获取 {connection_name_prefix} 的一个或两个顶面。")
            return []
        try:
            if positions_DBC:
                idx1 = _get_dbc_index_from_name(getattr(zone1, "name", ""))
                idx2 = _get_dbc_index_from_name(getattr(zone2, "name", ""))
                if (
                    0 <= idx1 < len(positions_DBC) and
                    0 <= idx2 < len(positions_DBC)
                ):
                    center1_y = positions_DBC[idx1].y
                    center2_y = positions_DBC[idx2].y

            if center1_y > center2_y:
                # 交换 zone1 和 zone2，使 zone1 是 lower，zone2 是 higher
                zone1, zone2 = zone2, zone1
                face1, face2 = face2, face1
                print(f"    Info: Swapped zones based on Y coord: "
                      f"lower={getattr(zone1, 'name', 'zone1')} ({center1_y:.3f}), "
                      f"higher={getattr(zone2, 'name', 'zone2')} ({center2_y:.3f}).")
            else:
                print(f"    Info: Keeping zones order based on Y coord: "
                      f"lower={getattr(zone1, 'name', 'zone1')} ({center1_y:.3f}), "
                      f"higher={getattr(zone2, 'name', 'zone2')} ({center2_y:.3f}).")

        except Exception as e:
            print(f"          警告: 检查 Zone Y 坐标时出错: {e}. 保持原始传入顺序。")

        # 1.5 处理auto_match和AUTO_MATCH_SELECTOR的情况
        selectors = create_edge_selectors()
        if edge_selector2 == selectors['auto_match'] or (callable(edge_selector2) and edge_selector2() == "AUTO_MATCH_SELECTOR"):
            # 先尝试调用edge_selector1获取参考边
            try:
                test_edge1 = edge_selector1(face1)
                # 确保得到的不是字符串
                if isinstance(test_edge1, str):
                    print(f"    错误: edge_selector1返回了字符串而不是边缘对象: '{test_edge1}'")
                    return []
                    
                # 使用edge1创建匹配选择器
                edge_selector2 = selectors['create_reference_selector'](test_edge1, axis='auto')
                print(f"    信息: 为'{connection_name_prefix}'创建了自动匹配选择器")
            except Exception as e:
                print(f"    错误: 创建自动匹配选择器时出错 ({connection_name_prefix}): {e}")
                # 退回到默认的选择器
                edge_selector2 = selectors['max_y_horizontal']
                print(f"    警告: 使用默认选择器'max_y_horizontal'代替")

        # 2. 使用传入的选择器获取边
        if not edge_selector1 or not edge_selector2:
            print(f"    错误: {connection_name_prefix} 未提供 edge_selector1 或 edge_selector2。")
            return []

        try:
            edge1 = edge_selector1(face1)
            edge2 = edge_selector2(face2)
            
            # 添加类型检查，确保返回的是边缘对象而不是字符串
            if isinstance(edge1, str):
                print(f"    错误: edge_selector1返回了字符串而不是边缘对象: '{edge1}'")
                return []
            if isinstance(edge2, str):
                print(f"    错误: edge_selector2返回了字符串而不是边缘对象: '{edge2}'")
                return []
        except Exception as e:
            print(f"    错误: 执行边缘选择器时出错 ({connection_name_prefix}): {e}")
            return []

        if not edge1 or not edge2:
            print(f"    错误: 一个或两个边缘选择器未能找到边 ({connection_name_prefix})。")
            return []

        # 3. 确定短边和长边
        len1 = edge1.Length()
        len2 = edge2.Length()
        if len1 <= len2:
            short_edge, long_edge = edge1, edge2
            short_face, long_face = face1, face2
            zone_short, zone_long = zone1, zone2 # Keep track of original zones
            proj_dir = Vector(0, 1, 0) # Project from short (bottom) to long (top)
        else:
            short_edge, long_edge = edge2, edge1
            short_face, long_face = face2, face1
            zone_short, zone_long = zone2, zone1 # Keep track of original zones
            proj_dir = Vector(0, -1, 0) # Project from short (top) to long (bottom)
            
        print(f"    Short edge length: {short_edge.Length():.2f}, Long edge length: {long_edge.Length():.2f}")



        # 4. 调整点数以满足最小间距要求
        actual_num_points = num_points
        min_required_spacing = wire_diameter * min_spacing_factor
        
        while actual_num_points >= min_points:
            if actual_num_points == 1:
                 # If only one point, spacing is not an issue
                 t_values_short = [0.5] # Sample at the center
                 calculated_spacing = float('inf')
                 break 
                 
            # Calculate spacing based on sampling the central percentage of the short edge
            sample_length = short_edge.Length() * sampling_percentage
            calculated_spacing = sample_length / (actual_num_points - 1)
            
            print(f"    Trying {actual_num_points} points: sample_length={sample_length:.2f}, calculated_spacing={calculated_spacing:.2f}, required={min_required_spacing:.2f}")

            if calculated_spacing >= min_required_spacing:
                # Generate t_values for the central part of the short edge
                t_start = (1.0 - sampling_percentage) / 2.0
                t_end = 1.0 - t_start
                t_values_short = np.linspace(t_start, t_end, actual_num_points).tolist()
                print(f"    Using {actual_num_points} points.")
                break # Spacing is sufficient
            
            actual_num_points -= 1 # Reduce points and recalculate spacing
        else:
            # Loop finished without finding suitable spacing (reached min_points)
            actual_num_points = min_points
            print(f"    警告: 无法在短边上满足最小间距要求。使用最小点数: {actual_num_points}")
            if actual_num_points == 1:
                 t_values_short = [0.5]
            else:
                 t_start = (1.0 - sampling_percentage) / 2.0
                 t_end = 1.0 - t_start
                 t_values_short = np.linspace(t_start, t_end, actual_num_points).tolist()
                 sample_length = short_edge.Length() * sampling_percentage
                 final_spacing = sample_length / (actual_num_points - 1) if actual_num_points > 1 else float('inf')
                 print(f"    Final calculated spacing with {actual_num_points} points: {final_spacing:.2f}")

        # 5. 获取短边上的采样点
        points_short = [short_edge.positionAt(t) for t in t_values_short]

        # 6. 在长边上找到对应的等距点集
        points_long = []
        # normal_projection_successful = False # Removed flag

        # --- Removed: Simple projection based on edge center vector ---
        # try: ... except ... block removed here (Original lines 4226-4278)

        # --- Start of fallback logic (now the primary logic) ---
        # 新增: 先尝试沿y轴投影匹配点 (原始逻辑保留作为回退)
        if try_y_axis_first:
            # print(f"    尝试沿y轴投影方法匹配点...") # Modified print statement
            print(f"    尝试几何投影到长边线段方法...")
            points_long = [] # 重置 points_long
            temp_points_long = [] # Temporary list for projected points
            # y_projection_valid = True # Replaced by all_projections_on_segment
            all_projections_on_segment = True # New flag
            projection_tolerance = 1e-6 # Tolerance for checking if parameter is within [0, 1]
            
            # Pre-calculate long edge properties needed for projection
            try:
                p_start_long = long_edge.startPoint()
                p_end_long = long_edge.endPoint()
                dir_long_vec = p_end_long - p_start_long
                long_edge_len_sq = dir_long_vec.Length**2
                if long_edge_len_sq < 1e-12: # Avoid division by zero for very short edges
                     raise ValueError("Long edge length is too small for projection.")
            except Exception as e:
                 print(f"    错误: 无法获取长边属性进行投影: {e}")
                 all_projections_on_segment = False # Mark method as failed

            if all_projections_on_segment: # Proceed only if long edge properties are valid
                for i, p_short in enumerate(points_short):
                    try:
                        # --- New Projection Logic --- 
                        # Vector from long edge start to the short point
                        vec_to_short = p_short - p_start_long
                        
                        # Project vec_to_short onto the direction vector of the long edge
                        # t = dot(vec_to_short, dir_long_vec) / |dir_long_vec|^2
                        t = vec_to_short.dot(dir_long_vec) / long_edge_len_sq
                        
                        # Check if the projection parameter t is within the segment [0, 1]
                        if -projection_tolerance <= t <= 1 + projection_tolerance:
                            # Calculate the actual projected point on the infinite line
                            projected_point = p_start_long + dir_long_vec * t
                            temp_points_long.append(projected_point)
                            # print(f"      点 {i} 投影成功，参数 t = {t:.4f}")
                        else:
                            # Projection falls outside the segment
                            print(f"      点 {i} 投影在线段外部 (t={t:.4f})。放弃此方法。")
                            all_projections_on_segment = False
                            break # Exit loop, this method failed
                            
                    except Exception as e:
                        print(f"    警告: 点 {i} 几何投影计算出错: {e}")
                        all_projections_on_segment = False
                        break # Exit loop, this method failed
            
            # --- Decision based on the flag --- 
            if all_projections_on_segment:
                # All points projected successfully onto the segment
                points_long = temp_points_long # Assign the results
                print(f"    成功使用几何投影到长边线段方法匹配了 {len(points_long)} 个点")
            else:
                # Method failed for at least one point
                print(f"    几何投影到长边线段方法失败，切换到长边参数化分布方法")
                points_long = []  # Ensure list is empty for the next method

        # 如果没有启用 try_y_axis_first 或上述几何投影方法失败，使用参数化分布方法
        # The condition implicitly handles the failure of the previous method because points_long will be empty
        if not try_y_axis_first or len(points_long) == 0:
             # --- Start: New Parameterization Logic with Equal Physical Margins --- 
             print(f"    使用基于等效物理边距的长边参数化分布方法...")
             points_long = [] # 再次确保清空

             # Calculate the geometric length covered by points on the short edge
             short_pattern_length = short_edge.Length() * sampling_percentage
             if short_edge.Length() < 1e-9: # Avoid issues if short edge has zero length
                 print(f"    错误: 短边长度过小 ({short_edge.Length():.2e})。")
                 return []
                 
             # Calculate the required *physical* margin on the short edge
             t_start_short = (1.0 - sampling_percentage) / 2.0
             short_edge_margin_physical = short_edge.Length() * t_start_short
             print(f"    短边所需物理边距: {short_edge_margin_physical:.3f} (参数 t={t_start_short:.3f})")

             # Calculate the corresponding *parametric* margin required on the long edge
             long_edge_length = long_edge.Length()
             if long_edge_length < 1e-9:
                 print(f"    错误: 长边长度过小 ({long_edge_length:.2e})，无法计算边距。")
                 return []
                 
             long_edge_margin_parametric = short_edge_margin_physical / long_edge_length
             
             # Determine the allowable parametric range on the long edge respecting the margin
             t_long_allowed_start = long_edge_margin_parametric
             t_long_allowed_end = 1.0 - long_edge_margin_parametric
             
             # Check if margins consume the entire edge or overlap
             if t_long_allowed_start >= t_long_allowed_end - 1e-9: # Add tolerance
                 print(f"    错误: 所需的等效物理边距 ({short_edge_margin_physical:.3f} * 2) 大于或等于长边长度 ({long_edge_length:.3f})。无法放置点。")
                 # Optional: Fallback to placing a single point at the center? 
                 # For now, return empty as it's an impossible constraint.
                 return []
             print(f"    长边允许参数范围 (基于边距): [{t_long_allowed_start:.3f}, {t_long_allowed_end:.3f}]")

             # Calculate the parametric width of the pattern on the long edge
             delta_t_long = short_pattern_length / long_edge_length

             # Find the target center parameter on the long edge (closest to short edge physical center)
             p_short_center = short_edge.positionAt(0.5)
             try:
                 t_long_center_approx = long_edge.paramAt(p_short_center)
             except Exception as e:
                 t_long_center_approx = 0.5
                 print(f"    警告: long_edge.paramAt(p_short_center) 失败，使用长边中心 (t=0.5) 作为对齐参考。")
             print(f"    长边目标中心参数: {t_long_center_approx:.3f}")
             
             # Calculate the ideal start parameter on the long edge to center the pattern
             t_long_pattern_ideal_start = t_long_center_approx - delta_t_long / 2.0

             # Check if the pattern fits within the allowed range based on margins
             allowed_range_width = t_long_allowed_end - t_long_allowed_start
             pattern_fits_in_margins = delta_t_long <= allowed_range_width + 1e-9 # Tolerance

             # Determine the final generation range [t_gen_start, t_gen_end]
             if pattern_fits_in_margins:
                 # Pattern fits, clamp its ideal position within the allowed range
                 t_gen_start = max(t_long_allowed_start, min(t_long_pattern_ideal_start, t_long_allowed_end - delta_t_long))
                 t_gen_end = t_gen_start + delta_t_long
                 print(f"    图案可放入边距内。最终生成范围: [{t_gen_start:.3f}, {t_gen_end:.3f}]")
             else:
                 # Pattern is wider than the allowed range, use the full allowed range (compressing spacing)
                 print(f"    警告: 图案宽度 ({delta_t_long:.3f}) 大于边距允许的宽度 ({allowed_range_width:.3f})。将在允许范围内生成点，间距可能被压缩。")
                 t_gen_start = t_long_allowed_start
                 t_gen_end = t_long_allowed_end
                 print(f"    最终生成范围 (受边距限制): [{t_gen_start:.3f}, {t_gen_end:.3f}]")

             # Generate the corresponding parameter values on the long edge
             if actual_num_points == 1:
                 # Place single point at the center of the final generation range
                 t_values_long = [(t_gen_start + t_gen_end) / 2.0]
             else:
                 t_values_long = np.linspace(t_gen_start, t_gen_end, actual_num_points).tolist()
                 
             # Get the corresponding points on the long edge
             points_long = [long_edge.positionAt(t) for t in t_values_long]
             # --- End: New Parameterization Logic --- 



        if len(points_short) == 0 or len(points_long) == 0 or len(points_short) != len(points_long):
            print("    错误: 短边和长边上的点数量不匹配或为零，无法创建连接。")
            return []

        # 7. 计算内法线并偏移两边的点
        offset_points_short = []
        offset_points_long = []
        skipped_indices = set()

        for i in range(actual_num_points):
            p_short = points_short[i]
            p_long = points_long[i]
            # t_short = t_values_short[i] # No longer needed for offset

            # --- Start: Replace Inward Normal Offset with Outward Along Connection Line ---
            try:
                # Calculate the direction vector between points
                direction_sl = p_long - p_short # Vector from short point to long point
                dist = direction_sl.Length
                
                if dist < 1e-6: # Check if points are coincident
                    print(f"    警告: 点 {i} 的原始位置重合 (距离={dist:.2e})。跳过此键合线。")
                    skipped_indices.add(i)
                    continue
                    
                # Normalize the direction vector
                norm_dir_sl = direction_sl / dist # Normalized vector from short to long
                
                # Offset points outwards along the connection line
                # p_short is offset in the direction OPPOSITE to norm_dir_sl (p_short - p_long direction)
                offset_p_short = p_short - norm_dir_sl * endpoint_offset
                # p_long is offset in the direction of norm_dir_sl (p_long - p_short direction)
                offset_p_long = p_long + norm_dir_sl * endpoint_offset
                 
                # --- Verification Step: Check if offset points are inside their faces ---
                is_short_inside = is_point_on_face_2d(short_face, offset_p_short)
                is_long_inside = is_point_on_face_2d(long_face, offset_p_long)
                
                if is_short_inside and is_long_inside:
                    offset_points_short.append(offset_p_short)
                    offset_points_long.append(offset_p_long)
                else:
                    print(f"    偏移后坐标: 短边点={offset_p_short.toTuple()}, 长边点={offset_p_long.toTuple()}")
                    print(f"    警告: 点 {i} 偏移后落在面外 (Short: {is_short_inside}, Long: {is_long_inside})。跳过此键合线。")
                    # Mark this index to be skipped when creating wires
                    skipped_indices.add(i)
                # --- End Verification --- 
                 
            except Exception as e:
                print(f"    警告: 计算点 {i} 的连接线偏移或检查时出错: {e}. 跳过此键合线。")
        
            # --- End: Replace Inward Normal Offset ---

        # --- Add Print Statements for Final Offset Points --- 
        print(f"    Final offset points on short side ({len(offset_points_short)} points):")
        for i, p in enumerate(offset_points_short):
            # Find original index if needed (complex if many skips) - simple print for now
            print(f"      Offset Short Point (idx {i}): {p.toTuple()}") 
            
        print(f"    Final offset points on long side ({len(offset_points_long)} points):")
        for i, p in enumerate(offset_points_long):
            print(f"      Offset Long Point (idx {i}): {p.toTuple()}")
        # --- End Print Statements ---

        # 8. 创建键合线 (连接未跳过的偏移点)
        final_wire_count = 0
        for i in range(actual_num_points):
            if i in skipped_indices:
                continue
                
            # Find the corresponding index in the potentially shorter offset lists
            # Since we only skip, the relative order is maintained. We need to count how many were skipped before i.
            offset_list_index = i - sum(1 for skipped_idx in skipped_indices if skipped_idx < i)
            
            if offset_list_index >= len(offset_points_short) or offset_list_index >= len(offset_points_long):
                print(f"    警告: 计算偏移列表索引时出错，跳过键合线 {i}。")
                continue

            op_short = offset_points_short[offset_list_index]
            op_long = offset_points_long[offset_list_index]
            
            # Determine which point belongs to zone1 and zone2 based on original assignment
            if zone_short == zone1:
                 point1, point2 = op_short, op_long
            else:
                 point1, point2 = op_long, op_short

            wire = create_bond_wire(point1, point2, wire_diameter)
            # Use more descriptive name including original zone names if available
            zone1_name = getattr(zone1, 'name', 'zone1')
            zone2_name = getattr(zone2, 'name', 'zone2')
            wire_name = f"bond_wire_p_{connection_name_prefix}_{zone1_name}_to_{zone2_name}_{final_wire_count}"
            module.add(wire, name=wire_name, color=get_color("bond_wire_2"))
            created_wires.append((wire, wire_name))
            final_wire_count += 1
            
        print(f"    Successfully created {len(created_wires)} wires for connection '{connection_name_prefix}'")

    except Exception as e:
        import traceback
        print(f"    创建Zone 连接 '{connection_name_prefix}' 时出错: {e}")
        print(traceback.format_exc())
        
    return created_wires


def _create_chip_to_zone_bondwires(row_name, col_name, Zone, DBC, die_dict, assembly_dict, 
                                 rotations_list, positions_list, num_chips, num_bond_wires, 
                                 length_die, chip_type,
                                 connection_direction=(0,1), connection_offset=2):  # 新增参数
    """
    创建芯片到Zone的键合线（内部辅助函数）
    """
    print(f"Creating bond wires for {chip_type}->Zone connection: {row_name} to {col_name}")
    
    # 解析芯片索引和Zone索引
    try:
        chip_index = int(row_name.split('_')[-1])
        zone_index = int(col_name.split('_')[-1])
    except (IndexError, ValueError):
        print(f"  Error parsing indices from {row_name} or {col_name}. Skipping bond wires.")
        return
    
    # 验证索引有效性
    if not 0 <= chip_index < num_chips:
        print(f"  Error: {chip_type} index {chip_index} out of bounds. Skipping bond wires.")
        return
    
    if not 0 <= zone_index < len(Zone):
        print(f"  Error: Zone index {zone_index} out of bounds. Skipping bond wires.")
        return
    
    # 检查需要的对象是否存在
    chip_die_name = row_name  # 例如 "IGBT_0"
    chip_assembly_name = row_name  # 同名
    
    if chip_die_name not in die_dict or chip_assembly_name not in assembly_dict:
        print(f"  Cannot create bond wires: {chip_die_name} not found in dictionaries.")
        return
    
    # 获取所需的对象
    die_obj = die_dict[chip_die_name]
    assembly_obj = assembly_dict[chip_assembly_name]
    
    # 创建键合线
    connect_points = []
    bond_wires = []
    
    index = die_obj.faces("+Z").val().Center()
    # 对于IGBT和FWD，都创建连接点
    for i in range(num_bond_wires):
        connect_points.append((
            index.x,
            index.y - length_die / 2 + i * (length_die - (2 if chip_type=="FWD" else 4)) / (num_bond_wires - 1) + 1,
            index.z
        ))
    
    # 计算连接点
    connection_points = calculate_connection_points(assembly_obj, rotations_list[chip_index], positions_list[chip_index], connect_points)
    
    # 创建键合线 - 使用传入的方向和偏移量参数
    for i in range(len(connection_points)):
        connection_index = get_coordinate(Zone[zone_index].faces("+Z").first(), 
                                        (connection_points[i].x, connection_points[i].y),
                                        connection_direction,  # 使用传入的参数
                                        offset=connection_offset)  # 使用传入的参数
        wire = create_bond_wire(connection_points[i], connection_index, 0.5)
        DBC.add(wire, name=f"bond_wire_{chip_type}_{i}_{chip_assembly_name}_to_Zone_{zone_index}", color=get_color("bond_wire_2"))
        print(f"  Created bond wire {i} from {chip_assembly_name} to Zone_{zone_index}")

# 添加选择器工厂函数
def create_edge_selectors(tolerance=0.01):
    """
    创建常用的边选择器函数，返回工厂函数字典
    
    Args:
        tolerance: 边方向判断的容差值
        
    Returns:
        dict: 包含常用选择器工厂函数的字典
    """
    selectors = {}
    
    # 选择最小Y坐标的水平边
    def min_y_horizontal_selector(face):
        # 获取所有平行Y轴的边
        edges = [e for e in face.Edges() 
                if abs(e.tangentAt(0).normalized().y) < tolerance]
        if not edges:
            raise ValueError("没有平行Y轴的边")
        return min(edges, key=lambda e: abs(e.Center().y))
    
    # 选择最大Y坐标的水平边
    def max_y_horizontal_selector(face):
        # 获取所有平行Y轴的边
        edges = [e for e in face.Edges() 
                if abs(e.tangentAt(0).normalized().y) < tolerance]
        if not edges:
            raise ValueError("没有平行Y轴的边")
        return max(edges, key=lambda e: abs(e.Center().y))
    
    # 选择最小X坐标的垂直边
    def min_x_vertical_selector(face):
        # 获取所有平行X轴的边
        edges = [e for e in face.Edges() 
                if abs(e.tangentAt(0).normalized().x) < tolerance]
        if not edges:
            raise ValueError("没有平行X轴的边")
        return min(edges, key=lambda e: abs(e.Center().x))
    
    # 选择最大X坐标的垂直边
    def max_x_vertical_selector(face):
        # 获取所有平行X轴的边
        edges = [e for e in face.Edges() 
                if abs(e.tangentAt(0).normalized().x) < tolerance]
        if not edges:
            raise ValueError("没有平行X轴的边")
        return max(edges, key=lambda e: abs(e.Center().x))
    
    # 创建参考边选择器工厂函数（闭包）
    def create_reference_selector(reference_edge, axis='auto'):
        """
        创建参考边选择器，根据参考边的方向自动选择相同类型的边
        
        Args:
            reference_edge: 参考边
            axis: 匹配坐标轴，'auto'表示自动检测边的方向，'x'或'y'表示指定方向
            
        Returns:
            function: 边选择器函数
        """
        # 获取参考边的X和Y坐标
        ref_center = reference_edge.Center()
        ref_tangent = reference_edge.tangentAt(0).normalized()
        
        # 自动检测边的方向
        if axis == 'auto':
            is_horizontal = abs(ref_tangent.y) < tolerance
            is_vertical = abs(ref_tangent.x) < tolerance
        else:
            is_horizontal = axis == 'x'
            is_vertical = axis == 'y'
        
        def selector(face):
            # 获取所有适当方向的边
            if is_horizontal:
                # 寻找水平边（平行于X轴的边）
                edges = [e for e in face.Edges() 
                        if abs(e.tangentAt(0).normalized().y) < tolerance]
                if not edges:
                    raise ValueError("没有平行Y轴的边")
                # 找出最接近参考点的边（同时考虑X和Y坐标）
                return min(edges, key=lambda e: ((e.Center().x - ref_center.x)**2 + 
                                                (e.Center().y - ref_center.y)**2))
            elif is_vertical:
                # 寻找垂直边（平行于Y轴的边）
                edges = [e for e in face.Edges() 
                        if abs(e.tangentAt(0).normalized().x) < tolerance]
                if not edges:
                    raise ValueError("没有平行X轴的边")
                # 找出最接近参考点的边（同时考虑X和Y坐标）
                return min(edges, key=lambda e: ((e.Center().x - ref_center.x)**2 + 
                                                (e.Center().y - ref_center.y)**2))
            else:
                # 默认回退到原来的逻辑
                edges = face.Edges()
                if not edges:
                    raise ValueError("没有边可选择")
                return min(edges, key=lambda e: ((e.Center().x - ref_center.x)**2 + 
                                               (e.Center().y - ref_center.y)**2))
        
        return selector
    
    # 创建自动匹配选择器的工厂函数（仅需在使用时提供参考边）
    def auto_match_factory(first_selector_result=None):
        """
        创建一个自动匹配第一个选择器的选择器
        当调用create_mapped_connection时，会自动将其替换为实际的匹配选择器
        
        Args:
            first_selector_result: 第一个选择器的结果，通常在函数内部设置
            
        Returns:
            str: 标识字符串，提示需要动态创建选择器
        """
        # 这是一个占位符，在实际使用时会被替换
        return "AUTO_MATCH_SELECTOR"
    
    # 添加到选择器字典
    selectors['min_y_horizontal'] = min_y_horizontal_selector
    selectors['max_y_horizontal'] = max_y_horizontal_selector
    selectors['min_x_vertical'] = min_x_vertical_selector
    selectors['max_x_vertical'] = max_x_vertical_selector
    selectors['create_reference_selector'] = create_reference_selector
    selectors['auto_match'] = auto_match_factory  # 添加自动匹配选择器
    
    return selectors

def create_mapped_connection(source_part, target_part, module, 
                           num_points=5, distance_interval=1.5, 
                           edge_selector1=None, edge_selector2=None,
                           max_angle=30, bond_wire_diameter=0.5,
                           offset_distance=-2, tolerance=0.01):
    """
    创建两个组件之间的映射连接，支持自定义或默认选择器
    
    Args:
        source_part: 源组件
        target_part: 目标组件
        module: 要添加连接的主Assembly对象
        num_points: 创建的连接点数
        distance_interval: 点间距
        edge_selector1: 源组件的边选择函数，如果为None则使用默认选择器
        edge_selector2: 目标组件的边选择函数，如果为None或'auto_match'则自动创建匹配选择器
        max_angle: 最大允许投影角度
        bond_wire_diameter: 键合线直径
        offset_distance: 边缘偏移距离
        tolerance: 容差值
    """
    # 如果没有提供选择器，使用默认选择器
    selectors = create_edge_selectors(tolerance)
    
    if edge_selector1 is None:
        edge_selector1 = selectors['min_y_horizontal']
    
    # 动态确定参考边，基于找到的 source_part
    try:
        # 获取源部件的参考边
        ref_edge = edge_selector1(source_part.obj.faces(">Z").first().val().located(source_part.loc))
        
        # 处理第二个选择器
        # 如果edge_selector2是None或者是auto_match选择器或其结果是"AUTO_MATCH_SELECTOR"占位符
        if edge_selector2 is None or edge_selector2 == selectors['auto_match'] or (callable(edge_selector2) and edge_selector2() == "AUTO_MATCH_SELECTOR"):
            # 自动创建匹配选择器
            edge_selector2 = selectors['create_reference_selector'](ref_edge, axis='auto')
    except Exception as e:
        print(f"    Error determining reference edge: {e}. Skipping connection.")
        return
        
    # 生成映射点
    try:
        src, tgt = generate_mapped_points(
            source_part, target_part,
            edge_selector1=edge_selector1,
            edge_selector2=edge_selector2,
            num_points=num_points,
            distance_interval=distance_interval,
            max_angle=max_angle,
            offset_distance=offset_distance
        )
    except Exception as e:
        print(f"    Error generating mapped points: {e}. Skipping connection.")
        return
    
    # 创建键合线
    source_name = source_part.name
    target_name = target_part.name
    created_wires = []
    
    for i in range(len(src)):
        p1_tuple = src[i].toTuple()
        p2_tuple = tgt[i].toTuple()
        wire = create_bond_wire(p1_tuple, p2_tuple, bond_wire_diameter)
        wire_name = f"bond_wire_{i}_{source_name}_to_{target_name}"
        module.add(wire, name=wire_name, color=get_color("bond_wire_2"))
        created_wires.append((wire, wire_name))
        print(f"    Created bond wire {i} from {source_name} to {target_name}")
    
    return created_wires

def extract_connection_pairs(connection_module):
    """
    从connection_module矩阵中提取所有有效的连接对
    
    Args:
        connection_module: DBC间连接矩阵
        
    Returns:
        list: 连接对列表，格式为[(source_dbc_name, target_dbc_name), ...]
    """
    connection_pairs = []
    
    for source_dbc_name, targets in connection_module.items():
        for target_dbc_name, connect_flag in targets.items():
            if connect_flag == 1:
                connection_pairs.append((source_dbc_name, target_dbc_name))
                print(f"Extracted connection pair: {source_dbc_name} -> {target_dbc_name}")
    
    return connection_pairs

def generate_connection_params_template(connection_pairs, use_detailed_format=True):
    """
    根据连接对列表生成连接参数模板
    
    Args:
        connection_pairs: 连接对列表，格式为[(source_dbc_name, target_dbc_name), ...]
        use_detailed_format: 是否使用详细格式（包含注释和示例值）
        
    Returns:
        dict: 连接参数模板
    """
    template = {
        'global': {},
        'default': {}
    }
    
    if use_detailed_format:
        # 添加注释和示例值
        template['global'] = {
            'tolerance': 0.01,  # 公差
            'bond_wire_diameter': 0.5  # 键合线直径
        }
        template['default'] = {
            'num_points': 5,  # 连接点数量
            'distance_interval': 1.5,  # 点间距
            'max_angle': 30,  # 最大投影角度
            # 注意：实际使用时，应该传递函数对象而不是字符串
            # 例如：'edge_selector1': selectors['min_y_horizontal']
            # 例如：'edge_selector2': selectors['auto_match']
        }
    
    # 为每个连接对添加条目
    for source, target in connection_pairs:
        key = f"{source}->{target}"
        if use_detailed_format:
            template[key] = {
                '# 以下参数会覆盖默认参数': '',
                'num_points': 5,  # 连接点数量
                'distance_interval': 1.5,  # 可选：点间距
                # 注意：实际使用时，应该传递函数对象而不是字符串
                # 例如：'edge_selector1': selectors['min_y_horizontal']
                # 例如：'edge_selector2': selectors['auto_match']
            }
        else:
            template[key] = {}
    
    return template

def calculate_dbcs_boundary(DBCs, ceramics_width, ceramics_length, positions, rotations):
    """
    计算多个DBC的总边界框
    
    Args:
        DBCs: DBC对象列表
        ceramics_width: 陶瓷板宽度
        ceramics_length: 陶瓷板长度
        positions: DBC位置列表
        rotations: DBC旋转角度列表
        
    Returns:
        tuple: (min_x, min_y, max_x, max_y) 边界坐标
    """
    # 初始化边界值
    min_x, min_y = float('inf'), float('inf')
    max_x, max_y = float('-inf'), float('-inf')
    
    # 遍历所有DBC
    for i, (dbc, position, rotation) in enumerate(zip(DBCs, positions, rotations)):
        # 计算DBC的四个角点（考虑旋转）
        corners = []
        
        # 陶瓷板的四个角点（相对于原点）
        if rotation == 0:
            corners = [
                (0, 0),  # 左下
                (ceramics_width, 0),  # 右下
                (0, ceramics_length),  # 左上
                (ceramics_width, ceramics_length)  # 右上
            ]
        elif rotation == 90:
            corners = [
                (0, 0),  # 左下
                (0, ceramics_width),  # 左上
                (-ceramics_length, 0),  # 右下
                (-ceramics_length, ceramics_width)  # 右上
            ]
        elif rotation == 180:
            corners = [
                (0, 0),  # 左下
                (-ceramics_width, 0),  # 右下
                (0, -ceramics_length),  # 左上
                (-ceramics_width, -ceramics_length)  # 右上
            ]
        elif rotation == 270:
            corners = [
                (0, 0),  # 左下
                (0, -ceramics_width),  # 左上
                (ceramics_length, 0),  # 右下
                (ceramics_length, -ceramics_width)  # 右上
            ]
        else:
            # 处理任意角度旋转
            rad = math.radians(rotation)
            cos_r, sin_r = math.cos(rad), math.sin(rad)
            corners = [
                (0, 0),  # 原点
                (ceramics_width * cos_r, ceramics_width * sin_r),  # 右边点
                (-ceramics_length * sin_r, ceramics_length * cos_r),  # 上边点
                (ceramics_width * cos_r - ceramics_length * sin_r, 
                 ceramics_width * sin_r + ceramics_length * cos_r)  # 右上角点
            ]
        
        # 将角点转换为绝对坐标
        for corner_x, corner_y in corners:
            abs_x = corner_x + position.x
            abs_y = corner_y + position.y
            
            # 更新边界
            min_x = min(min_x, abs_x)
            min_y = min(min_y, abs_y)
            max_x = max(max_x, abs_x)
            max_y = max(max_y, abs_y)
    
    print(f"Calculated DBC boundary: ({min_x}, {min_y}) to ({max_x}, {max_y})")
    return min_x, min_y, max_x, max_y

def create_substrate_for_dbcs(DBCs: List[Assembly], 
                             thickness_substrate: float, 
                             distance_substrate: float, 
                             substrate_reference_z: float) -> cq.Workplane:
    """
    创建基于多个实际DBC装配体位置的基板。
    
    Args:
        DBCs: 包含已定位和旋转的DBC Assembly对象的列表。
        thickness_substrate: 基板厚度。
        distance_substrate: 基板扩展距离。
        substrate_reference_z: 基板Z轴参考位置。
        
    Returns:
        cq.Workplane: 基板对象。
    """
    # 计算所有DBC的实际边界
    min_x, min_y, max_x, max_y = float('inf'), float('inf'), float('-inf'), float('-inf')
    
    print(f"计算基板边界 - 基于 {len(DBCs)} 个实际DBC装配体")
    
    if not DBCs:
        raise ValueError("DBCs列表不能为空")

    # 遍历所有实际的DBC装配体
    for i, dbc_assembly in enumerate(DBCs):
        try:
            # 获取每个DBC装配体在世界坐标系中的边界框
            # toCompound() 将装配体视为一个整体来计算边界
            compound = dbc_assembly.toCompound()
            if compound is None:
                 print(f"警告：DBC #{i} ({dbc_assembly.name}) 无法转换为Compound，跳过。")
                 continue
                 
            bbox = compound.BoundingBox()
            
            # 更新全局边界
            min_x = min(min_x, bbox.xmin)
            min_y = min(min_y, bbox.ymin)
            max_x = max(max_x, bbox.xmax)
            max_y = max(max_y, bbox.ymax)
            
            print(f"  DBC #{i} ({dbc_assembly.name}) 边界: X=({bbox.xmin:.2f}, {bbox.xmax:.2f}), Y=({bbox.ymin:.2f}, {bbox.ymax:.2f})")

        except Exception as e:
            print(f"处理 DBC #{i} ({dbc_assembly.name}) 时出错: {e}")
            continue # 跳过有问题的DBC

    # 检查是否成功计算了边界
    if min_x == float('inf') or max_x == float('-inf'):
        raise ValueError("无法计算任何DBC的有效边界。")

    print(f"所有DBC组合后的原始边界: ({min_x:.2f}, {min_y:.2f}) 到 ({max_x:.2f}, {max_y:.2f})")
    print(f"原始尺寸: {max_x - min_x:.2f} x {max_y - min_y:.2f}")
    
    # 添加扩展距离
    min_x -= distance_substrate
    min_y -= distance_substrate
    max_x += distance_substrate
    max_y += distance_substrate
    
    # 计算宽度和高度
    width = max_x - min_x
    height = max_y - min_y
    
    print(f"最终边界 (含扩展): ({min_x:.2f}, {min_y:.2f}) 到 ({max_x:.2f}, {max_y:.2f})")
    print(f"最终基板尺寸: {width:.2f} x {height:.2f}")
    
    # 创建基板工作平面（以左下角为基准点）
    # Z 偏移参考点现在由参数 substrate_reference_z 控制
    substrate_workplane = cq.Workplane("XY").workplane(offset=substrate_reference_z)
    
    # 创建基板盒体
    # 使用计算出的 min_x, min_y 进行平移
    substrate = (
        substrate_workplane
        .box(width, height, thickness_substrate, centered=(False, False, False))
        # 注意：Z坐标也需要调整，因为 substrate_reference_z 是基板的上表面 Z 坐标
        .translate((min_x, min_y, -thickness_substrate)) 
    )
    
    return substrate


def create_labeled_gates(gate_configs: dict,
                         ceramics: cq.Workplane,
                         thickness: float,
                         fillet: float,
                         margin: float) -> list[tuple[cq.Workplane, any]]:
    """
    根据配置字典创建所有类型的门极，并返回带类型标签的对象列表。

    Args:
        gate_configs: 包含门极类型及其几何定义的字典。
        ceramics: 陶瓷基板对象。
        thickness: 门极厚度。
        fillet: 圆角值。
        margin: 边距。

    Returns:
        list: 包含元组的列表 [(gate_object, gate_type_key), ...]
    """
    labeled_gates = []
    print("Creating and labeling all defined gate types...")
    gate_counter = 0
    for gate_type_key, config in gate_configs.items():
        start_points = config.get("start_points", [])
        relative_moves = config.get("relative_moves", [])

        if not start_points or not relative_moves or len(start_points) != len(relative_moves):
            print(f"警告: 跳过创建 type_Gate={gate_type_key}，配置不完整。")
            continue

        print(f"  Creating gates for type {gate_type_key}...")
        for i in range(len(start_points)):
            try:
                gate_obj = create_upper_Cu( # Assuming create_upper_Cu is defined above in utils.py
                    layer_type='gate',
                    ceramics=ceramics,
                    start_point=start_points[i],
                    relative_moves=relative_moves[i],
                    thickness=thickness,
                    fillet_value=fillet,
                    margin=margin
                )
                labeled_gates.append((gate_obj, gate_type_key))
                gate_counter += 1
                print(f"    Created gate object {gate_counter} (type {gate_type_key}, index {i}).")
            except Exception as e:
                 print(f"    创建 gate (type {gate_type_key}, index {i}) 时出错: {e}")

    if not labeled_gates:
        print("警告：未能成功创建任何门极对象。")

    print(f"Finished creating labeled gates. Total: {len(labeled_gates)}")
    return labeled_gates

# --- Function to process IGBT configurations ---
def process_igbt_configs(igbt_configs: dict) -> tuple[list[Vector], list[float], dict[int, any]]:
    """
    Processes the IGBT configuration dictionary to extract positions, rotations, 
    and create a mapping from index to type.

    Args:
        igbt_configs: Dictionary containing IGBT configurations by type. 
                      Example: {1: {"positions": [Vector(...)], "rotations": [180]}}

    Returns:
        tuple: (all_positions_IGBT, all_rotations_IGBT, igbt_index_to_type)
               - all_positions_IGBT: List of Vector positions for all IGBTs.
               - all_rotations_IGBT: List of rotation angles for all IGBTs.
               - igbt_index_to_type: Dictionary mapping the combined index to its original type key.
               
    Raises:
        ValueError: If no valid IGBT configurations are found.
    """
    all_positions_IGBT = []
    all_rotations_IGBT = []
    igbt_index_to_type = {}
    current_igbt_index = 0
    
    if not igbt_configs:
        print("警告：igbt_configs 字典为空。")
        return [], [], {} # Return empty structures

    for type_key, config in igbt_configs.items():
        positions = config.get("positions", [])
        rotations = config.get("rotations", [])
        
        if not isinstance(positions, list) or not isinstance(rotations, list):
             print(f"警告：igbt_configs 中类型 {type_key} 的 positions 或 rotations 不是列表。跳过。")
             continue
             
        if positions and rotations and len(positions) == len(rotations):
            # Validate positions are Vectors and rotations are numbers
            valid_positions = [p for p in positions if isinstance(p, Vector)]
            valid_rotations = [r for r in rotations if isinstance(r, (int, float))]

            if len(valid_positions) != len(positions) or len(valid_rotations) != len(rotations):
                 print(f"警告：igbt_configs 中类型 {type_key} 包含无效的位置 (非Vector) 或旋转角度 (非数值)。")
                 # Proceed only with valid pairs if lengths still match after filtering
                 if len(valid_positions) == len(valid_rotations) and len(valid_positions) > 0:
                      positions_to_add = valid_positions
                      rotations_to_add = valid_rotations
                 else:
                      print(f"  -> 由于验证失败且长度不匹配，完全跳过类型 {type_key}。")
                      continue # Skip this type if validation fails fundamentally
            else:
                 positions_to_add = positions
                 rotations_to_add = rotations
                 
            print(f"  处理 IGBT 类型 {type_key}，找到 {len(positions_to_add)} 个有效配置。")
            all_positions_IGBT.extend(positions_to_add)
            all_rotations_IGBT.extend(rotations_to_add)
            for i in range(len(positions_to_add)):
                igbt_index_to_type[current_igbt_index] = type_key
                current_igbt_index += 1
        elif not positions and not rotations:
             print(f"信息：igbt_configs 中类型 {type_key} 的 positions 和 rotations 均为空。")
        else:
            print(f"警告：igbt_configs 中类型 {type_key} 的 positions ({len(positions)}个) 和 rotations ({len(rotations)}个) 数量不匹配或为空。跳过。")

    if not all_positions_IGBT:
        # Raise error only if the input config was not empty but resulted in no valid IGBTs
        if igbt_configs: 
             raise ValueError("未能从 igbt_configs 中加载任何有效的 IGBT 配置。检查配置格式和内容。")
        else: # If input was empty, just return empty structures quietly
             pass 

    print(f"总共加载了 {len(all_positions_IGBT)} 个 IGBT 配置，类型映射: {igbt_index_to_type}")
    return all_positions_IGBT, all_rotations_IGBT, igbt_index_to_type
# --- End of process_igbt_configs ---
def count_zones(connection_dict):
    """
    计算 connection_DBC 字典中以 'Zone_' 开头的键的数量。

    Args:
        connection_dict (dict): 连接字典，例如 connection_DBC。

    Returns:
        int: Zone 的数量。
    """
    zone_count = 0
    for key in connection_dict.keys():
        if key.startswith("Zone_"):
            zone_count += 1
    return zone_count

def extract_connection_pairs(connection_module):
    """
    从connection_module矩阵中提取所有有效的Zone-to-Zone连接对
    
    Args:
        connection_module: DBC间连接矩阵 (新格式)
        
    Returns:
        list: 连接对列表，格式为[(source_zone_name, target_zone_name), ...]
              其中名称格式为 "Zone_X_Y"
    """
    connection_pairs = []
    print("Extracting Zone-to-Zone connection pairs (New Format Handling)...")
    for source_zone_full_name, target_zones_map in connection_module.items():
        # 验证源名称格式
        if not (source_zone_full_name.startswith("Zone_") and len(source_zone_full_name.split('_')) == 3):
            # print(f"    Skipping source '{source_zone_full_name}' in extract_connection_pairs, not in Zone_X_Y format.")
            continue

        for target_zone_full_name, connect_flag in target_zones_map.items():
            # 验证目标名称格式
            if not (target_zone_full_name.startswith("Zone_") and len(target_zone_full_name.split('_')) == 3):
                # print(f"    Skipping target '{target_zone_full_name}' for source '{source_zone_full_name}' in extract_connection_pairs, not in Zone_X_Y format.")
                continue
            
            if connect_flag == 1:
                connection_pairs.append((source_zone_full_name, target_zone_full_name))
                print(f"  Extracted connection pair: {source_zone_full_name} -> {target_zone_full_name}")
    
    if not connection_pairs:
        print("  No valid Zone-to-Zone connection pairs with flag 1 found.")
    return connection_pairs
def generate_positions_DBC(num_positions, length_ceramics, distance_DBC_DBC):
    """
    根据给定的DBC数量生成DBC的位置列表
    
    Args:
        num_positions (int): DBC的数量
        length_ceramics (float): 陶瓷基板的长度
        distance_DBC_DBC (float): DBC之间的距离
        
    Returns:
        list: 包含Vector对象的列表，表示每个DBC的位置
    """
    positions = []
    for i in range(num_positions):
        y_position = i * (-length_ceramics-distance_DBC_DBC)
        positions.append(Vector(0, y_position, 0))
    return positions

def _transform_points_to_global(local_points_tuples: list[tuple[float, float, float]],
                                rotation_angle_deg: float,
                                translation_vector: Vector) -> list[Vector]:
    """
    Transforms points from a local coordinate system to global coordinates.
    Local points are defined relative to the chip's center.
    Rotation is applied first, then translation.
    """
    transformed_global_points = []
    angle_rad = math.radians(rotation_angle_deg)
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)

    for p_local_tuple in local_points_tuples:
        lx, ly, lz = p_local_tuple
        
        # Rotate around Z-axis (assuming standard chip orientation)
        gx_rotated = lx * cos_a - ly * sin_a
        gy_rotated = lx * sin_a + ly * cos_a
        gz_rotated = lz

        # Translate
        final_gx = gx_rotated + translation_vector.x
        final_gy = gy_rotated + translation_vector.y
        final_gz = gz_rotated + translation_vector.z
        transformed_global_points.append(Vector(final_gx, final_gy, final_gz))
    return transformed_global_points
# ---------- Independent bond-wire generator ---------- #
# ---------- Independent bond-wire generator (fixed 2025-05-18) ---------- #
def create_chip_to_chip_bondwires(
    chip_A_name: str,
    chip_B_name: str,
    DBC_assembly,
    *,
    # ========= 运行时上下文（必须以关键字传递，或提前注入 utils 全局） =========
    die_IGBT_dict, die_FWD_dict,
    assembly_IGBT_dict, assembly_FWD_dict,
    rotations_IGBT, rotations_FWD,
    positions_IGBT, positions_FWD,
    num_bond_wire_IGBT, num_bond_wire_FWD,
    length_die_IGBT, length_die_FWD,
    # ---------- 可选 ----------
    wire_diameter: float = 0.5,
    get_color=lambda *_: get_color("bond_wire"),              # 若外部没提供配色函数就返回 None
    calculate_connection_points=None,
    create_bond_wire=None,
):
    """
    在 `chip_A_name` 与 `chip_B_name` 之间生成等数量、点对点的键合线，
    所有键合线直接 add 到 `DBC_assembly` 中。

    逻辑保持与原实现一致：
    1. 依芯片类型 (IGBT / FWD) 取出 die、assembly、旋转角、位移向量等；
    2. 在芯片顶面按 Y 方向等距取局部端点；
    3. 使用 calculate_connection_points() 转到全局坐标；
    4. 逐点调用 create_bond_wire() 生成实体并挂到装配体。
    """

    # ---------- 找到工具函数 ----------
    g = globals()
    if calculate_connection_points is None:
        calculate_connection_points = g["calculate_connection_points"]
    if create_bond_wire is None:
        create_bond_wire = g["create_bond_wire"]

    # ---------- 内部帮助函数 ----------
    def _collect_points(chip_name: str):
        chip_type, idx_str = chip_name.split("_")
        idx = int(idx_str)

        if chip_type == "IGBT":
            die_dict, assy_dict = die_IGBT_dict, assembly_IGBT_dict
            rotations, positions = rotations_IGBT, positions_IGBT
            n_wires, die_len = num_bond_wire_IGBT, length_die_IGBT
        else:  # FWD
            die_dict, assy_dict = die_FWD_dict, assembly_FWD_dict
            rotations, positions = rotations_FWD, positions_FWD
            n_wires, die_len = num_bond_wire_FWD, length_die_FWD

        die_obj = die_dict[chip_name]
        assy_obj = assy_dict[chip_name]
        rot_deg = rotations[idx]
        pos_vec = positions[idx]

        # —— 1) 在芯片顶面均匀取局部端点 ——
        top_center = die_obj.faces("+Z").val().Center()
        spacing = 2 if chip_type == "FWD" else 4
        y_offset = 1
        local_pts = []
        if n_wires == 1:
            local_pts.append((top_center.x, top_center.y, top_center.z))
        else:
            y_start = top_center.y - die_len / 2 + y_offset
            y_step = (die_len - spacing) / (n_wires - 1)
            for i in range(n_wires):
                local_pts.append((top_center.x, y_start + i * y_step, top_center.z))

        # —— 2) 转到全局坐标（按 func 签名的顺序） ——
        return calculate_connection_points(
            assy_obj,          # ① assembly ⇒ toCompound() 一定存在
            rot_deg,           # ② 旋转角（°）
            pos_vec,           # ③ 平移向量
            local_pts,         # ④ 局部坐标列表
        )

    # ---------- 主流程 ----------
    print(f"  Attempting sequential connection: {chip_A_name} -> {chip_B_name}")
    try:
        pts_A = _collect_points(chip_A_name)
        pts_B = _collect_points(chip_B_name)
    except Exception as exc:
        print(f"    ⚠️  Failed to collect connection points: {exc}")
        return

    if not (pts_A and pts_B and len(pts_A) == len(pts_B)):
        print("    ⚠️  Failed: connection points mismatch or not found.")
        return

    created = 0
    for pA, pB in zip(pts_A, pts_B):
        try:
            bw = create_bond_wire(pA, pB, wire_diameter=wire_diameter)
            # color 只能在 add() 时再传，否则 create_bond_wire 不认识
            DBC_assembly.add(bw, color=get_color("bond"))
            created += 1
        except Exception as exc:
            print(f"      Error creating bond wire {pA} -> {pB}: {exc}")

    if created:
        print(f"    ✅ Created {created} bond wires between {chip_A_name} and {chip_B_name}")
    else:
        print("    ⚠️  No bond wires created.")
def find_in_dbcs(dbcs, part_name):
    for dbc in dbcs:
        part = dbc.find(part_name)
        if part:
            return part
    return None
