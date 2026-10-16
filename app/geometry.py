"""
2D-to-3D Geometry Engine for Cookie Cutter & Stamp Generation.
Stack: OpenCV + Shapely + Trimesh + mapbox-earcut
"""

import io
import math
import logging
from typing import Dict, Any, Tuple, List, Optional
import cv2
import numpy as np
from shapely.geometry import Polygon, MultiPolygon, GeometryCollection
from shapely.ops import unary_union, orient
import trimesh

logger = logging.getLogger("geometry")


def preprocess_image(
    image_bytes: bytes,
    max_px: int = 1000,
    invert: bool = False,
    blur_kernel: int = 5,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Decodes and preprocesses the uploaded image:
    1. Mirrors horizontally so asymmetric shapes / text stamp right-side-up into dough.
    2. Resizes to max_px maintaining aspect ratio.
    3. Converts to grayscale, applies Gaussian blur, Otsu thresholding, and morphological closing.
    Returns:
        (bgr_preview, binary_mask)
    """
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError("Could not decode image file. Please provide a valid PNG, JPG, or WebP.")

    # Handle transparent PNGs by compositing onto white background
    if len(img.shape) == 3 and img.shape[2] == 4:
        alpha = img[:, :, 3] / 255.0
        bgr = img[:, :, :3]
        white_bg = np.ones_like(bgr, dtype=np.uint8) * 255
        img = (bgr * alpha[:, :, None] + white_bg * (1.0 - alpha[:, :, None])).astype(np.uint8)
    elif len(img.shape) == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

    # 1. Mirror horizontally for stamp / cookie cutter imprint correctness
    img = cv2.flip(img, 1)

    # 2. Resize maintaining aspect ratio
    h, w = img.shape[:2]
    if max(h, w) > max_px:
        scale = max_px / float(max(h, w))
        new_w, new_h = max(10, int(w * scale)), max(10, int(h * scale))
        img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)

    # 3. Grayscale, Blur, Threshold
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (blur_kernel, blur_kernel), 0)

    # Otsu automatic thresholding
    _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    if invert:
        thresh = cv2.bitwise_not(thresh)

    # Morphological closing to seal small boundary gaps
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    closed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=2)

    return img, closed


def extract_contours(binary_mask: np.ndarray, simplify_tol: float = 0.20) -> List[Polygon]:
    """
    Finds outer and inner contours from the binary mask and converts them to Shapely Polygons.
    """
    contours, hierarchy = cv2.findContours(binary_mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_TC89_KCOS)
    if not contours or hierarchy is None:
        raise ValueError("No contours detected in image. Try toggling 'Invert Colors' or using an image with higher contrast.")

    hierarchy = hierarchy[0]
    polygons: List[Polygon] = []

    for i, cnt in enumerate(contours):
        # Top-level contours (hierarchy[i][3] == -1) as outer boundaries
        if hierarchy[i][3] == -1:
            if len(cnt) < 3:
                continue
            outer_pts = cnt.reshape(-1, 2)
            holes: List[np.ndarray] = []

            # Check for inner child contours (holes)
            child_idx = hierarchy[i][2]
            while child_idx != -1:
                child_cnt = contours[child_idx]
                if len(child_cnt) >= 3:
                    holes.append(child_cnt.reshape(-1, 2))
                child_idx = hierarchy[child_idx][0]

            try:
                poly = Polygon(shell=outer_pts, holes=holes)
                if not poly.is_valid:
                    poly = poly.buffer(0)
                if poly.is_empty:
                    continue

                if simplify_tol > 0:
                    poly = poly.simplify(simplify_tol, preserve_topology=True)

                if isinstance(poly, Polygon) and poly.area > 50:
                    polygons.append(orient(poly, sign=1.0))
                elif isinstance(poly, MultiPolygon):
                    for sub_poly in poly.geoms:
                        if sub_poly.area > 50:
                            polygons.append(orient(sub_poly, sign=1.0))
            except Exception as e:
                logger.warning(f"Skipping degenerate contour: {e}")
                continue

    if not polygons:
        raise ValueError(
            "Found shapes in the image, but none were large or clean enough to form a cookie cutter. "
            "Please check contrast or reduce mesh smoothing."
        )

    return polygons


def scale_and_center_polygons(
    polygons: List[Polygon],
    target_size_mm: float
) -> Tuple[Polygon, List[Polygon], float, Dict[str, float]]:
    """
    Identifies the largest polygon as the main outer cutter contour,
    scales all polygons so the longest inner cut dimension matches target_size_mm,
    and centers them at (0, 0).
    """
    # Main outer contour is the largest by area
    main_poly = max(polygons, key=lambda p: p.area)
    outer_contour = Polygon(shell=main_poly.exterior)
    if not outer_contour.is_valid:
        outer_contour = outer_contour.buffer(0)

    minx, miny, maxx, maxy = outer_contour.bounds
    width_px = maxx - minx
    height_px = maxy - miny
    max_dim_px = max(width_px, height_px)

    if max_dim_px <= 0:
        raise ValueError("Invalid contour bounding box (zero width or height).")

    scale = target_size_mm / max_dim_px

    # Scale outer contour and center at (0, 0)
    center_x = (minx + maxx) / 2.0
    center_y = (miny + maxy) / 2.0

    def transform_poly(p: Polygon) -> Polygon:
        exterior_coords = [
            ((x - center_x) * scale, -(y - center_y) * scale)
            for x, y in p.exterior.coords
        ]
        holes_coords = [
            [((x - center_x) * scale, -(y - center_y) * scale) for x, y in hole.coords]
            for hole in p.interiors
        ]
        res = Polygon(shell=exterior_coords, holes=holes_coords)
        res = res.buffer(0) if not res.is_valid else res
        return orient(res, sign=1.0)

    scaled_main = transform_poly(outer_contour)

    # Transform internal features (holes from main + secondary smaller polygons)
    scaled_details: List[Polygon] = []
    for hole in main_poly.interiors:
        hole_poly = Polygon(shell=hole.coords)
        scaled_details.append(transform_poly(hole_poly))

    for p in polygons:
        if p != main_poly:
            scaled_details.append(transform_poly(p))

    s_minx, s_miny, s_maxx, s_maxy = scaled_main.bounds
    dims = {
        "inner_width_mm": round(s_maxx - s_minx, 2),
        "inner_height_mm": round(s_maxy - s_miny, 2),
        "target_size_mm": target_size_mm,
    }

    return scaled_main, scaled_details, scale, dims


def generate_cookie_cutter_mesh(
    outer_poly: Polygon,
    detail_polys: List[Polygon],
    blade_height: float = 12.0,
    blade_width: float = 0.86,
    collar_height: float = 4.0,
    collar_width: float = 1.60,
    handle_height: float = 2.0,
    handle_width: float = 4.00,
    stamp_mode: bool = False,
    stamp_relief: float = 2.50,
) -> Tuple[trimesh.Trimesh, Dict[str, float]]:
    """
    Extrudes the 3D mesh model flat on the bed at Z=0 (handle base):
    - Tier 1: Ergonomic Handle Rim (outer offset +handle_width, Z=0..handle_height)
    - Tier 2: Support Collar (outer offset +collar_width, Z=0..collar_height)
    - Tier 3: Cutting Blade (outer offset +blade_width, Z=0..blade_height)
    """
    meshes: List[trimesh.Trimesh] = []
    error_log: List[str] = []

    # Ensure valid polygon geometry
    if not outer_poly.is_valid:
        outer_poly = outer_poly.buffer(0)
    outer_poly = orient(outer_poly, sign=1.0)

    # 1. Concentric non-overlapping 2D rings for the 3 tiers
    blade_outer = outer_poly.buffer(blade_width, resolution=32)
    poly_blade_ring = blade_outer.difference(outer_poly)

    collar_outer = outer_poly.buffer(collar_width, resolution=32)
    poly_collar_ring = collar_outer.difference(blade_outer)

    handle_outer = outer_poly.buffer(handle_width, resolution=32)
    poly_handle_ring = handle_outer.difference(collar_outer)

    def extrude_shape(poly_geom, height: float, z_offset: float = 0.0, label: str = "") -> Optional[trimesh.Trimesh]:
        if poly_geom.is_empty:
            return None
        geoms = poly_geom.geoms if hasattr(poly_geom, "geoms") else [poly_geom]
        sub_meshes = []
        for idx, g in enumerate(geoms):
            if not isinstance(g, Polygon) or g.is_empty or g.area < 1e-4:
                continue
            if not g.is_valid:
                g = g.buffer(0)
            g = orient(g, sign=1.0)
            try:
                # Try earcut / standard extrusion
                m = trimesh.creation.extrude_polygon(g, height=height)
                if z_offset != 0.0:
                    m.apply_translation([0, 0, z_offset])
                sub_meshes.append(m)
            except Exception as ex:
                err_msg = f"{label}[{idx}] extrusion failed: {str(ex)}"
                logger.error(err_msg)
                error_log.append(err_msg)
                continue
        if not sub_meshes:
            return None
        return sub_meshes[0] if len(sub_meshes) == 1 else trimesh.util.concatenate(sub_meshes)

    # Extrude Blade
    mesh_blade = extrude_shape(poly_blade_ring, height=blade_height, z_offset=0.0, label="Blade")
    if mesh_blade:
        meshes.append(mesh_blade)

    # Extrude Collar
    mesh_collar = extrude_shape(poly_collar_ring, height=collar_height, z_offset=0.0, label="Collar")
    if mesh_collar:
        meshes.append(mesh_collar)

    # Extrude Handle Rim
    mesh_handle = extrude_shape(poly_handle_ring, height=handle_height, z_offset=0.0, label="Handle")
    if mesh_handle:
        meshes.append(mesh_handle)

    # 2. Stamp Mode Features
    if stamp_mode:
        backing_plate_poly = outer_poly.buffer(0.2)
        mesh_backing = extrude_shape(backing_plate_poly, height=handle_height, z_offset=0.0, label="BackingPlate")
        if mesh_backing:
            meshes.append(mesh_backing)

        stamp_height = max(handle_height + 1.0, blade_height - stamp_relief)
        rib_extrusion_height = stamp_height - handle_height

        clean_details = []
        for det in detail_polys:
            if not det.is_empty and det.area > 0.1:
                rib = det.exterior.buffer(0.5, resolution=16)
                if not rib.is_empty:
                    clean_details.append(rib)

        if clean_details:
            merged_details = unary_union(clean_details)
            mesh_stamp = extrude_shape(
                merged_details,
                height=rib_extrusion_height,
                z_offset=handle_height,
                label="StampRibs"
            )
            if mesh_stamp:
                meshes.append(mesh_stamp)

    if not meshes:
        diag = "; ".join(error_log) if error_log else "No valid extrusion produced."
        raise ValueError(
            f"Failed to construct 3D mesh. "
            f"Diagnostics: {diag}. "
            f"Outer contour area: {outer_poly.area:.1f} mm², "
            f"Target size: {blade_height}mm height."
        )

    final_mesh = trimesh.util.concatenate(meshes)

    # Clean and verify mesh (compatible with trimesh 4.x+)
    final_mesh.process(validate=True)
    final_mesh.fix_normals()

    extents = final_mesh.extents

    stats = {
        "print_width_mm": round(float(extents[0]), 2),
        "print_length_mm": round(float(extents[1]), 2),
        "print_height_mm": round(float(extents[2]), 2),
        "is_watertight": bool(final_mesh.is_watertight),
        "triangle_count": int(len(final_mesh.faces)),
    }

    return final_mesh, stats
