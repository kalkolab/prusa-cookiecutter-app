"""
PrusaSlicer Headless Execution Module.
Invokes prusa-slicer CLI to slice STL into G-code for Prusa MK3S/MK4 printers.
"""

import os
import shutil
import subprocess
from typing import Dict, Any, Optional


def find_prusa_slicer_binary() -> Optional[str]:
    """Finds available prusa-slicer executable."""
    candidates = [
        "prusa-slicer",
        "/usr/bin/prusa-slicer",
        "/usr/local/bin/prusa-slicer",
        "PrusaSlicer",
    ]
    for c in candidates:
        path = shutil.which(c)
        if path:
            return path
    return None


def slice_stl_to_gcode(
    stl_path: str,
    output_gcode_path: str,
    config_ini_path: str,
    center_xy: str = "125,105",
    threads: int = 2,
    timeout_seconds: int = 60,
) -> Dict[str, Any]:
    """
    Slices an STL file using PrusaSlicer CLI.
    Command:
      prusa-slicer --load <config> --center <center_xy> --ensure-on-bed --threads <threads> --export-gcode --output <gcode> <stl>
    """
    binary = find_prusa_slicer_binary()
    if not binary:
        return {
            "success": False,
            "error": "prusa-slicer CLI not found on system. STL is available for download.",
            "gcode_generated": False,
        }

    if not os.path.exists(stl_path):
        return {"success": False, "error": f"STL file not found: {stl_path}", "gcode_generated": False}

    if not os.path.exists(config_ini_path):
        return {"success": False, "error": f"Config file not found: {config_ini_path}", "gcode_generated": False}

    cmd = [
        binary,
        "--load", config_ini_path,
        "--center", center_xy,
        "--ensure-on-bed",
        "--threads", str(threads),
        "--export-gcode",
        "--output", output_gcode_path,
        stl_path,
    ]

    # Handle Flatpak environment variables if running inside container or Flatpak
    env = os.environ.copy()
    if "HOME" not in env or env["HOME"].startswith("/usr"):
        env["HOME"] = "/tmp"

    try:
        proc = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )

        if proc.returncode != 0:
            return {
                "success": False,
                "error": f"PrusaSlicer exited with code {proc.returncode}: {proc.stderr}",
                "gcode_generated": False,
            }

        if not os.path.exists(output_gcode_path) or os.path.getsize(output_gcode_path) == 0:
            return {
                "success": False,
                "error": "PrusaSlicer completed but no G-code file was created.",
                "gcode_generated": False,
            }

        # Parse estimated print time and filament used from G-code comments
        filament_used_m = None
        estimated_time = None
        with open(output_gcode_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if "filament used [mm] =" in line:
                    try:
                        filament_used_m = round(float(line.split("=")[1].strip()) / 1000.0, 2)
                    except Exception:
                        pass
                elif "estimated printing time (normal mode) =" in line:
                    estimated_time = line.split("=")[1].strip()

        return {
            "success": True,
            "gcode_generated": True,
            "gcode_path": output_gcode_path,
            "gcode_size_bytes": os.path.getsize(output_gcode_path),
            "filament_used_meters": filament_used_m,
            "estimated_print_time": estimated_time,
        }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": f"Slicing timed out after {timeout_seconds} seconds.",
            "gcode_generated": False,
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "gcode_generated": False,
        }
