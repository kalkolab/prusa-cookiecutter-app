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
from app.slicer import slice_stl_to_gcode, find_prusa_slicer_binary
from app.auth import verify_user_token, is_auth_enabled, GOOGLE_CLIENT_ID

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
JOBS_DIR = Path(os.getenv("JOBS_DIR", "/tmp/cookiecutter_jobs"))
JOBS_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_INI_PATH = ROOT_DIR / "slicer_config.ini"

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
    }


@app.get("/api/config")
async def get_client_config():
    return {
        "google_client_id": GOOGLE_CLIENT_ID,
        "auth_disabled": not is_auth_enabled(),
    }


@app.post("/api/generate")
async def generate_model(
    background_tasks: BackgroundTasks,
    image: UploadFile = File(...),
    target_size_mm: float = Form(80.0),
    blade_height: float = Form(12.0),
    handle_width: float = Form(4.0),
    simplify_tol: float = Form(0.12),
    invert: bool = Form(False),
    stamp_mode: bool = Form(False),
    user_info: dict = Depends(verify_user_token),
):
    """
    1. Preprocesses image (horizontal mirror for correct imprint).
    2. Extracts contours, normalizes, scales, and extrudes 3-tier cutter & optional stamp.
    3. Exports STL and runs headless PrusaSlicer.
    """
    background_tasks.add_task(cleanup_old_jobs)

    if not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Uploaded file is not an image.")

    content = await image.read()
    if len(content) > 15 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image size exceeds 15MB limit.")

    try:
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

        job_id = str(uuid.uuid4())
        job_dir = JOBS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        stl_filename = "cookie_cutter.stl"
        stl_path = job_dir / stl_filename
        mesh.export(str(stl_path))

        gcode_filename = "cookie_cutter.gcode"
        gcode_path = job_dir / gcode_filename

        slicing_result = slice_stl_to_gcode(
            stl_path=str(stl_path),
            output_gcode_path=str(gcode_path),
            config_ini_path=str(CONFIG_INI_PATH),
            center_xy="125,105",
            threads=2,
            timeout_seconds=45,
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
                "downloads": {
                    "stl_url": f"/api/download/{job_id}/{stl_filename}",
                    "gcode_url": f"/api/download/{job_id}/{gcode_filename}" if slicing_result.get("gcode_generated") else None,
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
