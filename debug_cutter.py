#!/usr/bin/env python3
"""
CLI Debugging & Test Script for Cookie Cutter Engine.
Allows diagnosing any image locally step-by-step:
  python3 debug_cutter.py <image_path> [--target-size 80] [--stamp] [--invert] [--printer mk4s]
"""

import sys
import os
import argparse
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cv2
import numpy as np
from app.geometry import preprocess_image, image_to_cookie_cutter
from app.slicer import slice_stl_to_gcode, find_prusa_slicer_binary


def debug_image(
    image_path: str,
    target_size_mm: float = 80.0,
    stamp_mode: bool = False,
    invert: bool = False,
    printer_model: str = "mk4s",
):
    print(f"\n=======================================================")
    print(f" DEBUGGING IMAGE: {image_path}")
    print(f" Target size: {target_size_mm} mm | Stamp Mode: {stamp_mode} | Invert: {invert} | Printer: {printer_model}")
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
        mirrored_gray, mask, _ = preprocess_image(content, max_px=1000, invert=invert)
        cv2.imwrite("debug_preview_mirrored.png", mirrored_gray)
        cv2.imwrite("debug_binary_mask.png", mask)
        print(f"  → Preview shape: {mirrored_gray.shape}")
        print(f"  → Binary mask shape: {mask.shape} (Saved: debug_binary_mask.png)")
        white_pixels = np.count_nonzero(mask)
        total_pixels = mask.size
        print(f"  → Mask foreground coverage: {white_pixels}/{total_pixels} ({white_pixels/total_pixels*100:.1f}%)")
        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        print(f"  → Raw contours found: {len(contours)}")
    except Exception as e:
        print(f"❌ Step 1 Failed: {e}")
        return False

    # Step 2: Full geometry pipeline (contours → scaling → 3D extrusion)
    print("\n[Step 2] 3D Mesh Generation (Shapely + Trimesh + Earcut)...")
    try:
        mesh, stats, _ = image_to_cookie_cutter(
            image_bytes=content,
            target_size_mm=target_size_mm,
            mode="stamp" if stamp_mode else "outline",
            invert=invert,
        )
        stl_output = "debug_output.stl"
        mesh.export(stl_output)
        print(f"  ✓ Mesh generated successfully: {stl_output}")
        print(f"    - Inner Cut Size: {stats['inner_width_mm']} × {stats['inner_height_mm']} mm")
        print(f"    - Print Footprint: {stats['print_width_mm']} × {stats['print_length_mm']} × {stats['print_height_mm']} mm")
        print(f"    - Triangles: {stats['triangle_count']}")
        print(f"    - Is Watertight (Manifold): {stats['is_watertight']}")
    except Exception as e:
        print(f"❌ Step 2 Failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    # Step 3: Slicing Check
    print("\n[Step 3] Headless PrusaSlicer Test...")
    slicer_bin = find_prusa_slicer_binary()
    if not slicer_bin:
        print("  ⚠️ PrusaSlicer binary not installed on this local system. (Will run inside Docker on Cloud Run)")
    else:
        gcode_output = "debug_output.gcode"
        res = slice_stl_to_gcode(stl_output, gcode_output, printer_model=printer_model)
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
    parser.add_argument("--printer", default="mk4s", help="Printer model: mk4s, mk4, mk3s, mini (default: mk4s)")
    args = parser.parse_args()

    ok = debug_image(args.image, args.target_size, args.stamp, args.invert, args.printer)
    sys.exit(0 if ok else 1)
