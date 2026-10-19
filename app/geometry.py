"""
2D-to-3D Geometry Engine for Cookie Cutter & Stamp Generation.
Stack: OpenCV + Shapely + Trimesh + mapbox-earcut

Ported from the proven standalone cookiecutter_app.py script.
"""

import base64
import logging
from typing import Dict, Any, Tuple, List, Optional
import cv2
import numpy as np
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union
import shapely.affinity
import trimesh

from app.i18n import UserError

logger = logging.getLogger("geometry")


def preprocess_image(
    image_bytes: bytes,
    max_px: int = 1000,
    invert: bool = False,
    blur_kernel: int = 5,
) -> Tuple[np.ndarray, np.ndarray, str]:
    """
    Decodes and preprocesses the uploaded image.
    Returns:
        (bgr_preview, binary_mask, preview_b64_png)
    """
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise UserError("image_decode_failed")

    # Handle transparent PNGs by compositing onto white background
    if len(img.shape) == 3 and img.shape[2] == 4:
        alpha = img[:, :, 3] / 255.0
        bgr = img[:, :, :3]
        white_bg = np.ones_like(bgr, dtype=np.uint8) * 255
        img = (bgr * alpha[:, :, None] + white_bg * (1.0 - alpha[:, :, None])).astype(np.uint8)

    # Convert to grayscale
    if len(img.shape) == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img

    # Resize maintaining aspect ratio
    h, w = gray.shape
    if max(h, w) > max_px:
        scale = max_px / float(max(h, w))
        new_w, new_h = max(10, int(w * scale)), max(10, int(h * scale))
        gray = cv2.resize(gray, (new_w, new_h), interpolation=cv2.INTER_AREA)

    # Mirror horizontally so text/asymmetric shapes stamp right-side-up on dough
    img_mirrored = cv2.flip(gray, 1)

    # Blur + Threshold
    blurred = cv2.GaussianBlur(img_mirrored, (blur_kernel, blur_kernel), 0)

    t_type = cv2.THRESH_BINARY if invert else cv2.THRESH_BINARY_INV
    _, binary = cv2.threshold(blurred, 0, 255, t_type + cv2.THRESH_OTSU)

    # Morphological closing to seal jagged JPEG artifacts
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

    # Encode 2D un-mirrored preview for the UI
    preview_img = cv2.flip(closed, 1)
    _, buf = cv2.imencode(".png", preview_img)
    preview_b64 = base64.b64encode(buf).decode("utf-8")

    return img_mirrored, closed, preview_b64


def extrude_shapely_geom(
    geom, height: float, z_offset: float = 0.0
) -> List[trimesh.Trimesh]:
    """Extrudes any Shapely Polygon or MultiPolygon into 3D Trimesh objects."""
    meshes = []
    if geom.is_empty:
        return meshes
    polys = [geom] if isinstance(geom, Polygon) else list(geom.geoms)
    for poly in polys:
        if poly.is_empty or poly.area < 0.5:
            continue
        try:
            mesh = trimesh.creation.extrude_polygon(poly, height=height)
            if z_offset != 0.0:
                mesh.apply_translation([0, 0, z_offset])
            meshes.append(mesh)
        except Exception as e:
            logger.warning(f"Extrusion failed for polygon (area={poly.area:.1f}): {e}")
            continue
    return meshes


def image_to_cookie_cutter(
    image_bytes: bytes,
    target_size_mm: float = 80.0,
    blade_height_mm: float = 12.0,
    blade_thick_mm: float = 0.86,
    handle_width_mm: float = 4.0,
    mode: str = "outline",
    invert: bool = False,
    simplify_tol: float = 0.12,
) -> Tuple[trimesh.Trimesh, Dict[str, Any], str]:
    """
    Full pipeline: image bytes -> 3D cookie cutter mesh.
    Returns (mesh, stats_dict, preview_b64_png).
    """
    # 1. Preprocess
    _, binary, preview_b64 = preprocess_image(image_bytes, invert=invert)

    # 2. Extract contours (RETR_LIST: simple flat list, most robust)
    contours, _ = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    raw_polys = []
    for cnt in contours:
        if cv2.contourArea(cnt) < 50:
            continue
        pts = cnt.squeeze()
        if pts.ndim == 2 and len(pts) >= 3:
            p = Polygon(pts).buffer(0)
            if not p.is_empty and p.area >= 50:
                raw_polys.append(p)

    if not raw_polys:
        raise UserError("no_shapes")

    # Sort polygons largest to smallest; largest is outer cookie silhouette
    raw_polys.sort(key=lambda p: p.area, reverse=True)
    outer_silhouette = Polygon(raw_polys[0].exterior).buffer(0)

    # 3. Normalize & scale so the longest dimension equals target_size_mm
    minx, miny, maxx, maxy = outer_silhouette.bounds
    span = max(maxx - minx, maxy - miny)
    if span <= 0:
        raise UserError("zero_bbox")
    scale_factor = target_size_mm / span
    cx, cy = (minx + maxx) / 2.0, (miny + maxy) / 2.0

    def transform_poly(p):
        shifted = shapely.affinity.translate(p, xoff=-cx, yoff=-cy)
        # Negate Y: image rows grow downward, mesh Y grows toward the back of the bed.
        # Without this the implicit vertical flip cancels the horizontal mirror above.
        scaled = shapely.affinity.scale(
            shifted, xfact=scale_factor, yfact=-scale_factor, origin=(0, 0)
        )
        return scaled.simplify(simplify_tol, preserve_topology=True).buffer(0)

    outer_2d = transform_poly(outer_silhouette)

    # 4. Build 3-Tiered Outer Cutter
    blade_shell = outer_2d.buffer(blade_thick_mm, join_style=1).difference(outer_2d)
    collar_shell = outer_2d.buffer(1.6, join_style=1).difference(outer_2d)
    handle_shell = outer_2d.buffer(handle_width_mm, join_style=1).difference(outer_2d)

    meshes = []
    meshes.extend(extrude_shapely_geom(blade_shell, height=blade_height_mm))
    meshes.extend(extrude_shapely_geom(collar_shell, height=4.0))
    meshes.extend(extrude_shapely_geom(handle_shell, height=2.0))

    # 5. Stamp mode: fuse a solid backing plate + inner detail embossers
    if mode == "stamp":
        # Solid backing plate at Z=0..2mm so floating details stay attached
        meshes.extend(extrude_shapely_geom(outer_2d, height=2.0))

        inner_polys = [transform_poly(Polygon(p.exterior)) for p in raw_polys[1:]]
        if inner_polys:
            inner_union = unary_union(inner_polys).intersection(outer_2d.buffer(-1.2))
            inner_rings = inner_union.boundary.buffer(blade_thick_mm / 2.0)
            stamp_height = max(4.0, blade_height_mm - 2.5)
            meshes.extend(extrude_shapely_geom(inner_rings, height=stamp_height))

    if not meshes:
        raise UserError("mesh_failed")

    combined_mesh = trimesh.util.concatenate(meshes)

    # Compute stats
    extents = combined_mesh.extents
    ob = outer_2d.bounds
    stats = {
        "print_width_mm": round(float(extents[0]), 2),
        "print_length_mm": round(float(extents[1]), 2),
        "print_height_mm": round(float(extents[2]), 2),
        "is_watertight": bool(combined_mesh.is_watertight),
        "triangle_count": int(len(combined_mesh.faces)),
        "inner_width_mm": round(ob[2] - ob[0], 2),
        "inner_height_mm": round(ob[3] - ob[1], 2),
    }

    return combined_mesh, stats, preview_b64
