"""
Prusa Cookie Cutter & Stamp Studio - FastAPI Application
"""

import os
import time
import uuid
import shutil
import logging
from pathlib import Path

from fastapi import (
    FastAPI,
    File,
    Form,
    UploadFile,
    HTTPException,
    Depends,
    BackgroundTasks,
)
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse

from app.geometry import image_to_cookie_cutter
from app.slicer import slice_stl_to_gcode, find_prusa_slicer_binary, PRINTER_SPECS
from app.auth import verify_user_token, is_auth_enabled, GOOGLE_CLIENT_ID

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
JOBS_DIR = Path(os.getenv("JOBS_DIR", "/tmp/cookiecutter_jobs"))
JOBS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Prusa Cookie Cutter & Stamp Studio")

TEMPLATES_DIR = BASE_DIR / "templates"


def cleanup_old_jobs(max_age_seconds: int = 3600):
    """Deletes job folders older than max_age_seconds."""
    now = time.time()
    try:
        for job_path in JOBS_DIR.iterdir():
            if job_path.is_dir():
                mtime = job_path.stat().st_mtime
                if now - mtime > max_age_seconds:
                    shutil.rmtree(job_path, ignore_errors=True)
    except Exception as e:
        logger.error(f"Error during job cleanup: {e}")


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_file = TEMPLATES_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="Index template not found.")
    return HTMLResponse(content=index_file.read_text(encoding="utf-8"))


@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "service": "prusa-cookiecutter-studio"}


@app.get("/api/status")
async def get_system_status():
    slicer = find_prusa_slicer_binary()
    return {
        "prusaslicer_available": slicer is not None,
        "binary": slicer,
        "supported_printers": list(PRINTER_SPECS.keys()),
    }


@app.get("/api/config")
async def get_client_config():
    return {
        "google_client_id": GOOGLE_CLIENT_ID,
        "auth_disabled": not is_auth_enabled(),
    }


# Accepted ranges for user-supplied geometry parameters (mirror the UI sliders)
PARAM_LIMITS = {
    "target_size_mm": (20.0, 200.0),
    "blade_height": (5.0, 30.0),
    "handle_width": (1.0, 10.0),
    "simplify_tol": (0.01, 1.0),
}

# Clearance kept around the part for the skirt (skirt_distance + skirt line)
BED_MARGIN_MM = 5.0


def validate_param(name: str, value: float) -> None:
    lo, hi = PARAM_LIMITS[name]
    if not (lo <= value <= hi):
        raise HTTPException(
            status_code=400,
            detail=f"{name} must be between {lo:g} and {hi:g} (got {value:g}).",
        )


@app.post("/api/generate")
def generate_model(
    background_tasks: BackgroundTasks,
    image: UploadFile = File(...),
    target_size_mm: float = Form(80.0),
    blade_height: float = Form(12.0),
    handle_width: float = Form(4.0),
    simplify_tol: float = Form(0.12),
    invert: bool = Form(False),
    stamp_mode: bool = Form(False),
    printer_model: str = Form("mk4s"),
    user_info: dict = Depends(verify_user_token),
):
    """
    1. Preprocesses image (horizontal mirror for correct imprint).
    2. Extracts contours, normalizes, scales, and extrudes 3-tier cutter & optional stamp.
    3. Exports STL and runs headless PrusaSlicer for the selected printer model (MK4S, MK4, MK3S+, MINI+).

    Declared as a sync handler so FastAPI runs it in a worker thread: the geometry
    work and the PrusaSlicer subprocess would otherwise block the event loop.
    """
    background_tasks.add_task(cleanup_old_jobs)

    validate_param("target_size_mm", target_size_mm)
    validate_param("blade_height", blade_height)
    validate_param("handle_width", handle_width)
    validate_param("simplify_tol", simplify_tol)

    if not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Uploaded file is not an image.")

    content = image.file.read()
    if len(content) > 15 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image size exceeds 15MB limit.")

    try:
        clean_printer = printer_model.lower().strip()
        if clean_printer not in PRINTER_SPECS:
            clean_printer = "mk4s"

        mode_str = "stamp" if stamp_mode else "outline"
        mesh, stats, preview_b64 = image_to_cookie_cutter(
            image_bytes=content,
            target_size_mm=target_size_mm,
            blade_height_mm=blade_height,
            blade_thick_mm=0.86,
            handle_width_mm=handle_width,
            mode=mode_str,
            invert=invert,
            simplify_tol=simplify_tol,
        )

        spec = PRINTER_SPECS[clean_printer]
        max_x = spec["bed_x"] - 2 * BED_MARGIN_MM
        max_y = spec["bed_y"] - 2 * BED_MARGIN_MM
        if stats["print_width_mm"] > max_x or stats["print_length_mm"] > max_y:
            raise ValueError(
                f"Model footprint {stats['print_width_mm']} × {stats['print_length_mm']} mm "
                f"does not fit the {spec['name']} bed (max {max_x:g} × {max_y:g} mm). "
                "Reduce the target size or handle rim width."
            )

        job_id = str(uuid.uuid4())
        job_dir = JOBS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        stl_filename = "cookie_cutter.stl"
        stl_path = job_dir / stl_filename
        mesh.export(str(stl_path))

        gcode_filename = f"cookie_cutter_{clean_printer}.gcode"
        gcode_path = job_dir / gcode_filename

        slicing_result = slice_stl_to_gcode(
            stl_path=str(stl_path),
            output_gcode_path=str(gcode_path),
            printer_model=clean_printer,
            threads=2,
            timeout_seconds=50,
        )

        dims = {
            "inner_width_mm": stats.get("inner_width_mm", target_size_mm),
            "inner_height_mm": stats.get("inner_height_mm", target_size_mm),
            "target_size_mm": target_size_mm,
        }

        return JSONResponse(
            content={
                "job_id": job_id,
                "preview_b64": preview_b64,
                "dimensions": dims,
                "stats": stats,
                "slicing": slicing_result,
                "printer_model": clean_printer,
                "printer_name": PRINTER_SPECS[clean_printer]["name"],
                "downloads": {
                    "stl_url": f"/api/download/{job_id}/{stl_filename}",
                    "stl_filename": stl_filename,
                    "gcode_url": f"/api/download/{job_id}/{gcode_filename}" if slicing_result.get("gcode_generated") else None,
                    "gcode_filename": gcode_filename,
                },
                "user": user_info.get("email"),
            }
        )

    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.exception("Failed to generate model")
        raise HTTPException(status_code=500, detail=f"Internal processing error: {str(e)}")


@app.get("/api/download/{job_id}/{filename}")
async def download_file(job_id: str, filename: str):
    """
    Serves generated STL or G-code file by unguessable UUID.
    Does not require Authorization header so browser <a> tags and Three.js loader work smoothly.
    """
    safe_job_id = Path(job_id).name
    safe_filename = Path(filename).name

    target_file = (JOBS_DIR / safe_job_id / safe_filename).resolve()

    if not str(target_file).startswith(str(JOBS_DIR.resolve())):
        raise HTTPException(status_code=400, detail="Invalid path access.")

    if not target_file.exists():
        raise HTTPException(status_code=404, detail="Requested file not found or expired.")

    media_type = "model/stl" if safe_filename.endswith(".stl") else "text/x.gcode"
    return FileResponse(
        path=target_file,
        media_type=media_type,
        filename=safe_filename,
    )


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)
