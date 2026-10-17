"""
PrusaSlicer Headless Execution Module.
Supports dynamic profile generation for Prusa MK4S, MK4, MK3S+, and MINI+.
"""

import os
import shutil
import logging
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger("slicer")

PRINTER_SPECS = {
    "mk4s": {
        "name": "Prusa MK4S",
        "bed_shape": "0x0,250x0,250x210,0x210",
        "center": "125,105",
        "max_z": 220,
    },
    "mk4": {
        "name": "Prusa MK4",
        "bed_shape": "0x0,250x0,250x210,0x210",
        "center": "125,105",
        "max_z": 220,
    },
    "mk3s": {
        "name": "Prusa MK3S+",
        "bed_shape": "0x0,250x0,250x210,0x210",
        "center": "125,105",
        "max_z": 210,
    },
    "mini": {
        "name": "Prusa MINI+",
        "bed_shape": "0x0,180x0,180x180,0x180",
        "center": "90,90",
        "max_z": 180,
    },
}


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


def generate_slicer_ini(printer_model: str, output_ini_path: str, threads: int = 2) -> str:
    """Generates a clean, 100% version-compatible PrusaSlicer config INI file."""
    spec = PRINTER_SPECS.get(printer_model.lower(), PRINTER_SPECS["mk4s"])

    ini_content = f"""# Auto-generated PrusaSlicer profile for {spec['name']}
threads = {threads}
bed_shape = {spec['bed_shape']}
max_print_height = {spec['max_z']}
layer_height = 0.2
first_layer_height = 0.2
perimeters = 3
top_solid_layers = 4
bottom_solid_layers = 4
fill_density = 20%
fill_pattern = grid
skirts = 1
skirt_distance = 3
skirt_height = 1
brim_width = 0
support_material = 0
infill_speed = 60
perimeter_speed = 45
external_perimeter_speed = 35
travel_speed = 120
first_layer_speed = 20
filament_colour = #FA6831
filament_type = PLA
filament_diameter = 1.75
temperature = 215
first_layer_temperature = 220
bed_temperature = 60
first_layer_bed_temperature = 60
cooling = 1
min_fan_speed = 100
max_fan_speed = 100
nozzle_diameter = 0.4
gcode_flavor = marlin
extruder_offset = 0x0
retract_length = 0.8
retract_speed = 35
retract_lift = 0.2
start_gcode = G90\\nM83\\nM104 S220\\nM140 S60\\nM190 S60\\nM109 S220\\nG28\\nG1 Z0.2 F720\\nG1 Y-3 F1000\\nG92 E0\\nG1 X60 E9 F1000\\nG1 X100 E12.5 F1000\\nG92 E0
end_gcode = G4\\nM104 S0\\nM140 S0\\nM107\\nG1 Z30 F3000\\nG1 X0 Y200 F3000\\nM84
"""
    with open(output_ini_path, "w", encoding="utf-8") as f:
        f.write(ini_content.strip())
    return output_ini_path


def slice_stl_to_gcode(
    stl_path: str,
    output_gcode_path: str,
    printer_model: str = "mk4s",
    threads: int = 2,
    timeout_seconds: int = 60,
) -> Dict[str, Any]:
    """
    Slices an STL file using PrusaSlicer CLI with dynamic profile generation.
    """
    binary = find_prusa_slicer_binary()
    if not binary:
        msg = "PrusaSlicer CLI not found in environment."
        logger.warning(msg)
        return {
            "success": False,
            "error": msg,
            "gcode_generated": False,
        }

    if not os.path.exists(stl_path):
        return {"success": False, "error": f"STL file not found: {stl_path}", "gcode_generated": False}

    spec = PRINTER_SPECS.get(printer_model.lower(), PRINTER_SPECS["mk4s"])
    ini_path = str(Path(stl_path).with_suffix(".ini"))
    generate_slicer_ini(printer_model, ini_path, threads=threads)

    cmd = [
        binary,
        "--load", ini_path,
        "--center", spec["center"],
        "--ensure-on-bed",
        "--export-gcode",
        "--output", output_gcode_path,
        stl_path,
    ]

    env = os.environ.copy()
    if "HOME" not in env or env["HOME"].startswith("/usr"):
        env["HOME"] = "/tmp"

    try:
        logger.info(f"Running PrusaSlicer: {' '.join(cmd)}")
        proc = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )

        if proc.returncode != 0:
            err_msg = f"PrusaSlicer exited with code {proc.returncode}: {proc.stderr or proc.stdout}"
            logger.error(err_msg)
            return {
                "success": False,
                "error": err_msg,
                "gcode_generated": False,
            }

        if not os.path.exists(output_gcode_path) or os.path.getsize(output_gcode_path) == 0:
            err_msg = "PrusaSlicer completed but no G-code file was produced."
            logger.error(err_msg)
            return {
                "success": False,
                "error": err_msg,
                "gcode_generated": False,
            }

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

        logger.info(f"G-code generated successfully: {output_gcode_path} ({filament_used_m}m, {estimated_time})")
        return {
            "success": True,
            "gcode_generated": True,
            "printer_model": printer_model,
            "printer_name": spec["name"],
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
        logger.exception("Slicing exception")
        return {
            "success": False,
            "error": str(e),
            "gcode_generated": False,
        }
