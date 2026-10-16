#!/usr/bin/env python3
"""
CLI Debugging & Test Script for Cookie Cutter Engine.
Allows diagnosing any image locally step-by-step:
  python3 debug_cutter.py <image_path> [--target-size 80] [--stamp] [--invert]
"""

import sys
import os
import argparse
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cv2
import numpy as np
from app.geometry import (
    preprocess_image,
    extract_contours,
    scale_and_center_polygons,
    generate_cookie_cutter_mesh,
)
from app.slicer import slice_stl_to_gcode, find_prusa_slicer_binary


def debug_image(image_path: str, target_size_mm: float = 80.0, stamp_mode: bool = False, invert: bool = False):
    print(f"\n=======================================================")
    print(f" DEBUGGING IMAGE: {image_path}")
    print(f" Target size: {target_size_mm} mm | Stamp Mode: {stamp_mode} | Invert: {invert}")
    print(f"=======================================================\n")

    if not os.path.exists(image_path):
        print(f"❌ Error: Image file not found: {image_path}")
        return False

    with open(image_path, "rb") as f:
        content = f.read()
    print(f"✓ Loaded file: {len(content)} bytes")

    # Step 1: Preprocessing
    print("\n[Step 1] OpenCV Preprocessing...")
    try:
        preview_bgr, mask = preprocess_image(content, max_px=1000, invert=invert)
        cv2.imwrite("debug_preview_mirrored.png", preview_bgr)
        cv2.imwrite("debug_binary_mask.png", mask)
        print(f"  → Preview shape: {preview_bgr.shape}")
        print(f"  → Binary mask shape: {mask.shape} (Saved: debug_binary_mask.png)")
        white_pixels = np.count_nonzero(mask)
        total_pixels = mask.size
        print(f"  → Mask foreground coverage: {white_pixels}/{total_pixels} ({white_pixels/total_pixels*100:.1f}%)")
    except Exception as e:
        print(f"❌ Step 1 Failed: {e}")
        return False

    # Step 2: Contour Extraction
    print("\n[Step 2] Contour Extraction...")
    try:
        polygons = extract_contours(mask, simplify_tol=0.15)
        print(f"  → Extracted {len(polygons)} valid closed polygon(s)")
        for idx, p in enumerate(polygons):
            print(f"    - Polygon {idx}: area={p.area:.1f} px², vertices={len(p.exterior.coords)}, holes={len(p.interiors)}")
    except Exception as e:
        print(f"❌ Step 2 Failed: {e}")
        return False

    # Step 3: Scaling & Centering
    print("\n[Step 3] Sizing & Positioning...")
    try:
        scaled_outer, scaled_details, scale, dims = scale_and_center_polygons(polygons, target_size_mm)
        print(f"  → Scale factor: {scale:.4f} mm/px")
        print(f"  → Inner Cut Size: {dims['inner_width_mm']} mm × {dims['inner_height_mm']} mm")
        print(f"  → Scaled detail contours: {len(scaled_details)}")
    except Exception as e:
        print(f"❌ Step 3 Failed: {e}")
        return False

    # Step 4: 3D Extrusion
    print("\n[Step 4] 3D Mesh Extrusion (Trimesh + Earcut)...")
    try:
        mesh, stats = generate_cookie_cutter_mesh(
            outer_poly=scaled_outer,
            detail_polys=scaled_details,
            blade_height=12.0,
            blade_width=0.86,
            collar_height=4.0,
            collar_width=1.60,
            handle_height=2.0,
            handle_width=4.0,
            stamp_mode=stamp_mode,
        )
        stl_output = "debug_output.stl"
        mesh.export(stl_output)
        print(f"  ✓ Mesh generated successfully: {stl_output}")
        print(f"    - Print Footprint: {stats['print_width_mm']} × {stats['print_length_mm']} × {stats['print_height_mm']} mm")
        print(f"    - Triangles: {stats['triangle_count']}")
        print(f"    - Is Watertight (Manifold): {stats['is_watertight']}")
    except Exception as e:
        print(f"❌ Step 4 Failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    # Step 5: Slicing Check
    print("\n[Step 5] Headless PrusaSlicer Test...")
    slicer_bin = find_prusa_slicer_binary()
    if not slicer_bin:
        print("  ⚠️ PrusaSlicer binary not installed on this local system. (Will run inside Docker on Cloud Run)")
    else:
        config_file = "slicer_config.ini"
        gcode_output = "debug_output.gcode"
        res = slice_stl_to_gcode(stl_output, gcode_output, config_file)
        if res.get("success"):
            print(f"  ✓ Sliced G-code: {gcode_output}")
            print(f"    - Print Time: {res.get('estimated_print_time')}")
            print(f"    - Filament: {res.get('filament_used_meters')} meters")
        else:
            print(f"  ⚠️ Slicing error: {res.get('error')}")

    print("\n=======================================================")
    print(" ✅ DIAGNOSTIC TEST COMPLETE")
    print("=======================================================\n")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cookie Cutter Diagnostic Tool")
    parser.add_argument("image", help="Path to image file")
    parser.add_argument("--target-size", type=float, default=80.0, help="Target inner size in mm (default: 80)")
    parser.add_argument("--stamp", action="store_true", help="Enable stamp mode")
    parser.add_argument("--invert", action="store_true", help="Invert image colors")
    args = parser.parse_args()

    debug_image(args.image, args.target_size, args.stamp, args.invert)
