"""
PrusaSlicer Headless Execution Module.

Slices with Prusa Research's official printer / print / filament profiles (the vendor
bundle shipped with PrusaSlicer), so the G-code gets the same start sequence as
PrusaSlicer on the desktop: printer and nozzle checks, probing with a cool nozzle,
mesh bed levelling of the print area, purge line and the filament's temperatures.
"""

import os
import re
import shutil
import logging
import subprocess
import configparser
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger("slicer")

# Prusa vendor bundle installed with the prusa-slicer package
VENDOR_BUNDLE = Path(os.getenv("PRUSASLICER_VENDOR_BUNDLE", "/usr/share/PrusaSlicer/profiles/PrusaResearch.ini"))
# PrusaSlicer configuration folder in which the Prusa printers and filaments are "installed"
DATADIR = Path(os.getenv("PRUSASLICER_DATADIR", "/var/tmp/prusaslicer-data"))

FILAMENTS = ("pla", "petg")
DEFAULT_FILAMENT = "pla"

# Profile names are the system presets from PrusaResearch.ini (PrusaSlicer 2.9 bundle).
PRINTER_SPECS = {
    "mk4s": {
        "bed_x": 250,
        "bed_y": 210,
        "name": "Prusa MK4S",
        "center": "125,105",
        "printer_profile": "Original Prusa MK4S HF0.4 nozzle",
        "print_profile": "0.20mm SPEED @MK4S HF0.4",
        "filament_profiles": {"pla": "Generic PLA @MK4S HF0.4", "petg": "Generic PETG @MK4S HF0.4"},
    },
    "mk4": {
        "bed_x": 250,
        "bed_y": 210,
        "name": "Prusa MK4",
        "center": "125,105",
        "printer_profile": "Original Prusa MK4 Input Shaper 0.4 nozzle",
        "print_profile": "0.20mm SPEED @MK4IS 0.4",
        "filament_profiles": {"pla": "Generic PLA @PGIS", "petg": "Generic PETG @PGIS"},
    },
    "mk3s": {
        "bed_x": 250,
        "bed_y": 210,
        "name": "Prusa MK3S+",
        "center": "125,105",
        "printer_profile": "Original Prusa i3 MK3S & MK3S+",
        "print_profile": "0.20mm QUALITY @MK3",
        "filament_profiles": {"pla": "Generic PLA", "petg": "Generic PETG"},
    },
    "mini": {
        "bed_x": 180,
        "bed_y": 180,
        "name": "Prusa MINI+",
        "center": "90,90",
        "printer_profile": "Original Prusa MINI & MINI+ Input Shaper",
        "print_profile": "0.20mm SPEED @MINIIS 0.4",
        "filament_profiles": {"pla": "Generic PLA @MINIIS", "petg": "Generic PETG @MINIIS"},
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


def ensure_datadir() -> Path:
    """
    Creates a PrusaSlicer configuration folder with every Prusa printer model, nozzle
    variant and filament from the vendor bundle marked as installed. The CLI only loads
    system presets (--printer-profile etc.) that are installed in its datadir.
    """
    app_config = DATADIR / "PrusaSlicer.ini"
    if app_config.exists():
        return DATADIR
    if not VENDOR_BUNDLE.exists():
        raise FileNotFoundError(f"PrusaSlicer vendor bundle not found: {VENDOR_BUNDLE}")

    bundle = configparser.RawConfigParser(strict=False, interpolation=None)
    bundle.optionxform = str
    bundle.read(VENDOR_BUNDLE, encoding="utf-8")

    (DATADIR / "vendor").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(VENDOR_BUNDLE, DATADIR / "vendor" / VENDOR_BUNDLE.name)

    lines = [f"[vendor:{VENDOR_BUNDLE.stem}]"]
    for section in bundle.sections():
        if section.startswith("printer_model:"):
            variants = [v.strip() for v in bundle[section].get("variants", "").split(";") if v.strip()]
            lines.append(f"model:{section.split(':', 1)[1]} = {';'.join(variants)}")
    lines.append("")
    lines.append("[filaments]")
    for section in bundle.sections():
        if section.startswith("filament:") and not section.startswith("filament:*"):
            lines.append(f"{section.split(':', 1)[1]} = 1")

    tmp = app_config.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(app_config)
    return DATADIR


def slice_stl_to_gcode(
    stl_path: str,
    output_gcode_path: str,
    printer_model: str = "mk4s",
    filament: str = DEFAULT_FILAMENT,
    timeout_seconds: int = 60,
) -> Dict[str, Any]:
    """
    Slices an STL file with PrusaSlicer's official profiles for the printer and filament.
    """
    binary = find_prusa_slicer_binary()
    if not binary:
        msg = "PrusaSlicer CLI not found in environment."
        logger.warning(msg)
        return {
            "success": False,
            "error": msg,
            "error_key": "slicer_not_found",
            "gcode_generated": False,
        }

    if not os.path.exists(stl_path):
        return {"success": False, "error": f"STL file not found: {stl_path}", "gcode_generated": False}

    spec = PRINTER_SPECS.get(printer_model.lower(), PRINTER_SPECS["mk4s"])
    filament = filament if filament in FILAMENTS else DEFAULT_FILAMENT
    filament_profile = spec["filament_profiles"][filament]

    try:
        datadir = ensure_datadir()
    except Exception as e:
        logger.exception("Could not prepare PrusaSlicer profiles")
        return {"success": False, "error": str(e), "error_key": "slicer_failed", "gcode_generated": False}

    cmd = [
        binary,
        "--datadir", str(datadir),
        "--printer-profile", spec["printer_profile"],
        "--print-profile", spec["print_profile"],
        "--material-profile", filament_profile,
        "--no-binary-gcode",
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
                "error_key": "slicer_failed",
                "gcode_generated": False,
            }

        if not os.path.exists(output_gcode_path) or os.path.getsize(output_gcode_path) == 0:
            err_msg = "PrusaSlicer completed but no G-code file was produced."
            logger.error(err_msg)
            return {
                "success": False,
                "error": err_msg,
                "error_key": "slicer_no_output",
                "gcode_generated": False,
            }

        filament_used_m = None
        estimated_time = None
        used_profiles = {}
        with open(output_gcode_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if "filament used [mm] =" in line:
                    try:
                        filament_used_m = round(float(line.split("=")[1].strip()) / 1000.0, 2)
                    except Exception:
                        pass
                elif "estimated printing time (normal mode) =" in line:
                    estimated_time = line.split("=")[1].strip()
                else:
                    m = re.match(r"; (printer|print|filament)_settings_id = \"?([^\"\n]*)\"?", line)
                    if m:
                        used_profiles[m.group(1)] = m.group(2)

        # PrusaSlicer silently falls back to defaults if a preset can't be loaded
        if used_profiles.get("printer") != spec["printer_profile"]:
            err_msg = f"PrusaSlicer did not apply the printer profile '{spec['printer_profile']}' (got '{used_profiles.get('printer')}')."
            logger.error(err_msg)
            return {"success": False, "error": err_msg, "error_key": "slicer_failed", "gcode_generated": False}

        logger.info(f"G-code generated successfully: {output_gcode_path} ({filament_used_m}m, {estimated_time})")
        return {
            "success": True,
            "gcode_generated": True,
            "printer_model": printer_model,
            "printer_name": spec["name"],
            "filament": filament,
            "profiles": used_profiles,
            "gcode_path": output_gcode_path,
            "gcode_size_bytes": os.path.getsize(output_gcode_path),
            "filament_used_meters": filament_used_m,
            "estimated_print_time": estimated_time,
        }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": f"Slicing timed out after {timeout_seconds} seconds.",
            "error_key": "slicer_timeout",
            "timeout_seconds": timeout_seconds,
            "gcode_generated": False,
        }
    except Exception as e:
        logger.exception("Slicing exception")
        return {
            "success": False,
            "error": str(e),
            "error_key": "slicer_failed",
            "gcode_generated": False,
        }
