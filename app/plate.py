"""
Print Plate: combines several library cutters onto one print bed.

Each copy's footprint (XY bounding box) is packed onto the usable bed area with a
MaxRects algorithm, rotating parts by 90° when that helps. The placed meshes are written
as one STL (sliced as a single object, so PrusaSlicer keeps the layout) and as a 3MF
project with one named object per cutter for opening in PrusaSlicer.
"""

import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import trimesh

PART_SPACING_MM = 5.0
MAX_COPIES = 30

# (x, y, rotated) of each rectangle's lower-left corner within the usable area
Placement = Tuple[float, float, bool]


Rect = Tuple[float, float, float, float]  # x, y, width, height


def _maxrects_pack(
    sizes: List[Tuple[float, float]], order: List[int], width: float, height: float, spacing: float
) -> Optional[List[Placement]]:
    """
    MaxRects packing with the best-short-side-fit rule, trying both orientations of
    every part. Spacing is applied by growing each part and the bin by `spacing`, so
    neighbours end up exactly `spacing` apart. Returns None if a part does not fit.
    """
    bin_w, bin_h = width + spacing, height + spacing
    free: List[Rect] = [(0.0, 0.0, bin_w, bin_h)]
    placements: List[Optional[Placement]] = [None] * len(sizes)

    for i in order:
        w0, h0 = sizes[i][0] + spacing, sizes[i][1] + spacing
        best = None  # (short_side_leftover, long_side_leftover, x, y, w, h, rotated)
        for fx, fy, fw, fh in free:
            for w, h, rotated in ((w0, h0, False), (h0, w0, True)):
                if w <= fw + 1e-9 and h <= fh + 1e-9:
                    score = (min(fw - w, fh - h), max(fw - w, fh - h), fy, fx)
                    if best is None or score < best[0]:
                        best = (score, fx, fy, w, h, rotated)
        if best is None:
            return None
        _, x, y, w, h, rotated = best
        placements[i] = (x, y, rotated)

        # Split every free rectangle that overlaps the placed one into its leftover parts
        next_free: List[Rect] = []
        for fx, fy, fw, fh in free:
            if x >= fx + fw or x + w <= fx or y >= fy + fh or y + h <= fy:
                next_free.append((fx, fy, fw, fh))
                continue
            if x > fx:
                next_free.append((fx, fy, x - fx, fh))
            if x + w < fx + fw:
                next_free.append((x + w, fy, fx + fw - (x + w), fh))
            if y > fy:
                next_free.append((fx, fy, fw, y - fy))
            if y + h < fy + fh:
                next_free.append((fx, y + h, fw, fy + fh - (y + h)))
        # Drop free rectangles fully contained in another one
        free = [
            a for k, a in enumerate(next_free)
            if not any(
                k != j and b[0] <= a[0] and b[1] <= a[1]
                and a[0] + a[2] <= b[0] + b[2] and a[1] + a[3] <= b[1] + b[3]
                and (a != b or j < k)
                for j, b in enumerate(next_free)
            )
        ]
    return placements


def arrange_footprints(
    sizes: List[Tuple[float, float]], width: float, height: float, spacing: float = PART_SPACING_MM
) -> List[Placement]:
    """
    Packs rectangles into width × height without overlap. Several insertion orders are
    tried and the most compact successful layout is kept. Raises ValueError if none fits.
    """
    idx = list(range(len(sizes)))
    orders = [
        sorted(idx, key=lambda i: sizes[i][0] * sizes[i][1], reverse=True),
        sorted(idx, key=lambda i: max(sizes[i]), reverse=True),
        sorted(idx, key=lambda i: min(sizes[i]), reverse=True),
        sorted(idx, key=lambda i: sizes[i][0] + sizes[i][1], reverse=True),
    ]
    best = None
    for order in orders:
        placements = _maxrects_pack(sizes, order, width, height, spacing)
        if placements is None:
            continue
        extent = 0.0
        for (w, h), (x, y, rotated) in zip(sizes, placements):
            fw, fh = (h, w) if rotated else (w, h)
            extent = max(extent, x + fw, y + fh)
        if best is None or extent < best[0]:
            best = (extent, placements)
    if best is None:
        raise ValueError("doesn't fit")
    return best[1]


def build_plate(
    parts: List[Dict[str, Any]], bed_x: float, bed_y: float, margin: float, out_dir: Path
) -> Dict[str, Any]:
    """
    parts: [{"name": str, "stl_path": Path, "quantity": int}]
    Writes plate.stl and plate.3mf into out_dir and returns the layout (bed coordinates, mm).
    """
    copies = []
    for part in parts:
        mesh = trimesh.load(str(part["stl_path"]), force="mesh")
        for n in range(part["quantity"]):
            label = part["name"] if part["quantity"] == 1 else f"{part['name']} #{n + 1}"
            copies.append((label, mesh))
    if not copies:
        raise ValueError("Select at least one cutter.")
    if len(copies) > MAX_COPIES:
        raise ValueError(f"A plate can hold at most {MAX_COPIES} cutters (got {len(copies)}).")

    sizes = [(float(mesh.extents[0]), float(mesh.extents[1])) for _, mesh in copies]
    usable_w, usable_h = bed_x - 2 * margin, bed_y - 2 * margin
    try:
        placements = arrange_footprints(sizes, usable_w, usable_h)
    except ValueError:
        area = sum(w * h for w, h in sizes)
        raise ValueError(
            f"These {len(copies)} cutters don't fit on the bed together "
            f"({area / (usable_w * usable_h):.0%} of the usable area before spacing). "
            "Remove some cutters or lower the quantities."
        )

    # Center the packed block on the bed
    footprints = []
    for (w, h), (x, y, rotated) in zip(sizes, placements):
        fw, fh = (h, w) if rotated else (w, h)
        footprints.append((x, y, fw, fh))
    block_w = max(x + w for x, _, w, _ in footprints)
    block_h = max(y + h for _, y, _, h in footprints)
    offset_x = (bed_x - block_w) / 2
    offset_y = (bed_y - block_h) / 2

    scene = trimesh.Scene()
    placed_meshes = []
    layout = []
    for (label, mesh), (x, y, w, h), (_, _, rotated) in zip(copies, footprints, placements):
        m = mesh.copy()
        if rotated:
            m.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [0, 0, 1]))
        lo = m.bounds[0]
        m.apply_translation([offset_x + x - lo[0], offset_y + y - lo[1], -lo[2]])
        placed_meshes.append(m)
        node = f"{label} ({len(layout) + 1})" if any(e["name"] == label for e in layout) else label
        scene.add_geometry(m, node_name=node, geom_name=node)
        layout.append({
            "name": label,
            "x": round(offset_x + x, 2),
            "y": round(offset_y + y, 2),
            "width": round(w, 2),
            "length": round(h, 2),
            "rotated": rotated,
        })

    combined = trimesh.util.concatenate(placed_meshes)
    combined.export(str(out_dir / "plate.stl"))
    (out_dir / "plate.3mf").write_bytes(scene.export(file_type="3mf"))

    bounds = combined.bounds
    return {
        "layout": layout,
        "copies": len(copies),
        "bed": {"x": bed_x, "y": bed_y, "margin": margin},
        "extents_mm": [round(float(v), 1) for v in np.ptp(bounds, axis=0)],
        "triangle_count": int(len(combined.faces)),
    }
