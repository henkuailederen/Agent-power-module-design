import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import math
from shapely import affinity
from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union, nearest_points


def _ensure_repo_root_on_path():
    """保证可以从项目根目录导入 cad.merge 等模块。"""
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


_ensure_repo_root_on_path()

# 延迟导入，确保 sys.path 已更新
from cad.merge import ConfigProcessor  # noqa: E402


# ------------------------- 基础解析 ------------------------- #
def _load_flat_config(path: Path) -> Dict[str, Any]:
    """
    尝试以 JSON 读取，失败则回退为 YAML（若安装了 PyYAML）。
    """
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except Exception:
        try:
            import yaml  # type: ignore

            return yaml.safe_load(text)
        except Exception as e:
            raise ValueError(f"无法解析配置文件 {path}: {e}")


def _to_nested(flat_cfg: Dict[str, Any]) -> Dict[str, Any]:
    cp = ConfigProcessor()
    return cp.convert_flat_default_to_nested(flat_cfg)


# ------------------------- 几何构建 ------------------------- #
def _gate_polygons(nested: Dict[str, Any]) -> List[Polygon]:
    gates: List[Polygon] = []
    design = nested.get("gate_design", {})
    types = design.get("types", {}) or {}
    geom = nested["geometry"]["ceramics"]
    width = float(geom["width"])
    length = float(geom["length"])
    margin = float(nested["margins"]["cu2ceramics"])

    for type_cfg in types.values():
        starts = type_cfg.get("start_points") or []
        moves_list = type_cfg.get("moves_list") or []
        if not starts or not moves_list or len(starts) != len(moves_list):
            continue
        for start, moves in zip(starts, moves_list):
            pts = []
            x0 = float(start["x_ratio"]) * width + margin
            y0 = float(start["y_ratio"]) * length + margin
            cx, cy = x0, y0
            pts.append((cx, cy))
            for mv in moves:
                cx += float(mv["x_ratio"]) * width
                cy += float(mv["y_ratio"]) * length
                pts.append((cx, cy))
            pts.append((x0, y0))
            if len(pts) >= 4:
                gates.append(Polygon(pts))
    return gates


def _cut_polygons(nested: Dict[str, Any], extreme: float) -> List[Polygon]:
    cut_cfg = nested.get("cutting_design", {}) or {}
    paths = cut_cfg.get("paths") or []
    geom = nested["geometry"]["ceramics"]
    width = float(geom["width"])
    length = float(geom["length"])
    margin = float(nested["margins"]["cu2ceramics"])
    cuts: List[Polygon] = []

    def _coord(val: Any, span: float) -> float:
        if val == 999:
            return extreme
        if val == -999:
            return -extreme
        return float(val) * span + margin

    for path in paths:
        pts = []
        for p in path.get("points", []):
            pts.append(
                (
                    _coord(p["x_ratio"], width),
                    _coord(p["y_ratio"], length),
                )
            )
        if len(pts) < 2:
            continue
        ls = LineString(pts)
        cuts.append(ls.buffer(0.5 * float(nested["margins"]["cu2cu"]), cap_style=2, join_style=2))
    return cuts


def _build_zones(nested: Dict[str, Any], extreme: float = 1000.0) -> List[Tuple[str, Polygon]]:
    geom = nested["geometry"]["ceramics"]
    width = float(geom["width"])
    length = float(geom["length"])
    margin = float(nested["margins"]["cu2ceramics"])
    cu_slot = float(nested["margins"]["cu2cu"])

    # 基础铜层矩形
    base = box(margin, margin, width - margin, length - margin)

    # 扣除 gate 槽
    gates = _gate_polygons(nested)
    if gates:
        gate_holes = unary_union([g.buffer(cu_slot / 2.0, cap_style=2, join_style=2) for g in gates])
        base = base.difference(gate_holes)

    # 切割路径
    cuts = _cut_polygons(nested, extreme)
    for cut in cuts:
        base = base.difference(cut)

    # 拆成多个 polygon，并按质心 X 排序命名 Zone_i
    zones: List[Polygon] = []
    if base.is_empty:
        return []
    if base.geom_type == "Polygon":
        zones = [base]
    else:
        zones = [geom for geom in base.geoms if geom.geom_type == "Polygon"]

    zones_sorted = sorted(zones, key=lambda p: p.centroid.x)
    return [(f"Zone_{i}", poly) for i, poly in enumerate(zones_sorted)]


# ------------------------- 芯片构建 ------------------------- #
def _build_chips(nested: Dict[str, Any]) -> List[Tuple[str, Polygon]]:
    chips: List[Tuple[str, Polygon]] = []
    width_c = float(nested["geometry"]["ceramics"]["width"])
    length_c = float(nested["geometry"]["ceramics"]["length"])

    igbt_cfg = nested.get("igbt_positions") or {}
    igbt_rot = nested.get("igbt_rotations") or {}
    igbt_w = float(nested["dies"]["igbt"]["size"]["width"])
    igbt_l = float(nested["dies"]["igbt"]["size"]["length"])
    fwd_pos = nested.get("fwd_positions") or []
    fwd_rot = nested.get("fwd_rotations") or []
    fwd_w = float(nested["dies"]["fwd"]["size"]["width"])
    fwd_l = float(nested["dies"]["fwd"]["size"]["length"])

    # IGBT：按原始 dict 顺序累计索引
    igbt_index = 0
    for type_key, pos_list in igbt_cfg.items():
        rotations = igbt_rot.get(type_key, [])
        for idx, pos in enumerate(pos_list):
            rot = float(rotations[idx]) if idx < len(rotations) else 0.0
            x = float(pos[0]) * width_c
            y = float(pos[1]) * length_c
            poly = _chip_polygon(x, y, igbt_w, igbt_l, rot)
            chips.append((f"IGBT_{igbt_index}", poly))
            igbt_index += 1

    # FWD：按列表顺序
    for i, pos in enumerate(fwd_pos):
        rot = float(fwd_rot[i]) if i < len(fwd_rot) else 0.0
        x = float(pos[0]) * width_c
        y = float(pos[1]) * length_c
        poly = _chip_polygon(x, y, fwd_w, fwd_l, rot)
        chips.append((f"FWD_{i}", poly))

    return chips


def _chip_polygon(x: float, y: float, w: float, l: float, rot_deg: float) -> Polygon:
    rect = box(x, y, x + w, y + l)
    cx = x + w * 0.5
    cy = y + l * 0.5
    return affinity.rotate(rect, rot_deg, origin=(cx, cy))


def _zone_binding(nested: Dict[str, Any]) -> Dict[str, str]:
    """
    仅使用 Zone_* -> IGBT_/FWD_ 的连接；首个命中即为归属。
    """
    bindings: Dict[str, str] = {}
    topo = nested.get("topology", {}) or {}
    conns = topo.get("dbc_connections") or []
    for conn in conns:
        src = str(conn.get("source", ""))
        tgt = str(conn.get("target", ""))
        val = conn.get("value", 1)
        if val != 1:
            continue
        if src.startswith("Zone_") and (tgt.startswith("IGBT_") or tgt.startswith("FWD_")):
            if tgt not in bindings:
                bindings[tgt] = src
    return bindings


# ------------------------- 检查逻辑 ------------------------- #
def run_precheck(json_path: str, extreme: float = 1000.0) -> Dict[str, Any]:
    path = Path(json_path)
    flat = _load_flat_config(path)
    nested = _to_nested(flat)

    zones = _build_zones(nested, extreme)
    zone_map = {name: poly for name, poly in zones}
    chips = _build_chips(nested)
    bindings = _zone_binding(nested)

    errors: List[Dict[str, Any]] = []

    def _bbox(poly: Polygon) -> Tuple[float, float, float, float]:
        return tuple(poly.bounds)

    def _vector_suggestion(src_centroid, tgt_centroid, min_shift: float) -> Tuple[float, float]:
        dx = tgt_centroid.x - src_centroid.x
        dy = tgt_centroid.y - src_centroid.y
        norm = math.hypot(dx, dy)
        if norm < 1e-9:
            # 退化情况，给一个最小位移
            return (min_shift, 0.0)
        scale = max(min_shift / norm, 1.0)
        return (dx * scale, dy * scale)

    # 越界检查
    for chip_name, poly in chips:
        zone_name = bindings.get(chip_name)
        if not zone_name:
            errors.append(
                {
                    "type": "missing_zone_binding",
                    "chip": chip_name,
                    "detail": "未在 dbc_connections 中找到 Zone->芯片 的绑定",
                }
            )
            continue
        zone_poly = zone_map.get(zone_name)
        if not zone_poly:
            errors.append(
                {
                    "type": "missing_zone_geometry",
                    "chip": chip_name,
                    "zone": zone_name,
                    "detail": "未生成对应 Zone 的几何，无法校验越界",
                }
            )
            continue
        if not poly.within(zone_poly.buffer(1e-6)):
            outside = poly.difference(zone_poly)
            outside_area = outside.area
            ratio = outside_area / max(poly.area, 1e-9)
            # 计算最近点建议位移
            p_zone, p_chip = nearest_points(zone_poly, poly)
            suggest_dx = p_zone.x - p_chip.x
            suggest_dy = p_zone.y - p_chip.y
            errors.append(
                {
                    "type": "chip_out_of_zone",
                    "chip": chip_name,
                    "zone": zone_name,
                    "detail": (
                        f"芯片有部分超出 Zone 边界，超出面积占芯片面积约 {ratio:.3%}；"
                        f"芯片包络 {list(_bbox(poly))}，Zone 包络 {list(_bbox(zone_poly))}。"
                        f"可尝试按向量 ({suggest_dx:.3f}, {suggest_dy:.3f}) 调整靠近 Zone。"
                    ),
                    "metrics": {
                        "outside_area": outside_area,
                        "outside_ratio": ratio,
                        "chip_bbox": list(_bbox(poly)),
                        "zone_bbox": list(_bbox(zone_poly)),
                    },
                    "suggest_move": [suggest_dx, suggest_dy],
                }
            )

    # 重叠检查
    for i in range(len(chips)):
        name_i, poly_i = chips[i]
        for j in range(i + 1, len(chips)):
            name_j, poly_j = chips[j]
            if poly_i.intersects(poly_j) and poly_i.intersection(poly_j).area > 1e-6:
                inter = poly_i.intersection(poly_j)
                inter_area = inter.area
                overlap_ratio = inter_area / max(min(poly_i.area, poly_j.area), 1e-9)
                # 建议位移：从 j 指向 i 的方向，移动较小面积的芯片
                ci = poly_i.centroid
                cj = poly_j.centroid
                dx = ci.x - cj.x
                dy = ci.y - cj.y
                norm = math.hypot(dx, dy)
                # 以重叠外接矩形的较大边为基准，加少量冗余
                ob = inter.bounds
                min_shift = max(ob[2] - ob[0], ob[3] - ob[1]) + 1e-3
                if norm < 1e-9:
                    suggest_dx, suggest_dy = (min_shift, 0.0)
                else:
                    suggest_dx, suggest_dy = (dx / norm * min_shift, dy / norm * min_shift)
                errors.append(
                    {
                        "type": "chip_overlap",
                        "chips": [name_i, name_j],
                        "detail": (
                            f"两个芯片发生交叠，重叠面积 {inter_area:.4f}，"
                            f"占较小芯片面积约 {overlap_ratio:.3%}；"
                            f"重叠包络 {list(ob)}。可将较小芯片沿向量 "
                            f"({suggest_dx:.3f}, {suggest_dy:.3f}) 平移以消除重叠。"
                        ),
                        "metrics": {
                            "overlap_area": inter_area,
                            "overlap_ratio_min": overlap_ratio,
                            "chip_i_bbox": list(_bbox(poly_i)),
                            "chip_j_bbox": list(_bbox(poly_j)),
                            "overlap_bbox": list(ob),
                        },
                        "suggest_move": [suggest_dx, suggest_dy],
                    }
                )

    ok = len(errors) == 0
    return {
        "ok": ok,
        "errors": errors,
        "warnings": [],
        "summary": "检测通过" if ok else f"发现 {len(errors)} 个问题",
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="在 merge 前对 CAD JSON 进行越界/重叠预检")
    parser.add_argument("--input", required=True, help="待检测的 JSON/YAML 配置路径")
    parser.add_argument("--extreme", type=float, default=1000.0, help="EXTREME 值，默认为 1000")
    args = parser.parse_args(argv)

    result = run_precheck(args.input, extreme=args.extreme)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

