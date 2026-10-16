"""
Prusa Cookie Cutter & Stamp Studio - FastAPI Application
"""

import os
import time
import uuid
import shutil
import logging
from pathlib import Path
from typing import Optional

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
from fastapi.staticfiles import StaticFiles

from app.geometry import (
    preprocess_image,
    extract_contours,
    scale_and_center_polygons,
    generate_cookie_cutter_mesh,
)
from app.slicer import slice_stl_to_gcode
from app.auth import verify_user_token, is_auth_enabled, GOOGLE_CLIENT_ID

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
JOBS_DIR = Path(os.getenv("JOBS_DIR", "/tmp/cookiecutter_jobs"))
JOBS_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_INI_PATH = ROOT_DIR / "slicer_config.ini"

app = FastAPI(title="Prusa Cookie Cutter & Stamp Studio")

# Mount static and templates
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
    simplify_tol: float = Form(0.15),
    invert: bool = Form(False),
    stamp_mode: bool = Form(False),
    user_info: dict = Depends(verify_user_token),
):
    """
    1. Preprocesses image (horizontal mirror for correct stamp orientation).
    2. Extracts contours and scales to target_size_mm.
    3. Extrudes 3-tier profile + stamp plate/ribs to 3D mesh.
    4. Slices STL to G-code via PrusaSlicer CLI.
    """
    # Clean up older jobs in background
    background_tasks.add_task(cleanup_old_jobs)

    if not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Uploaded file is not an image.")

    content = await image.read()
    if len(content) > 15 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image size exceeds 15MB limit.")

    try:
        # 1. OpenCV Preprocessing
        _, binary_mask = preprocess_image(
            content,
            max_px=1000,
            invert=invert,
        )

        # 2. Extract and Scale Contours
        polygons = extract_contours(binary_mask, simplify_tol=simplify_tol)
        scaled_outer, scaled_details, scale, dims = scale_and_center_polygons(
            polygons,
            target_size_mm=target_size_mm
        )

        # 3. Extrude 3D Mesh
        mesh, stats = generate_cookie_cutter_mesh(
            outer_poly=scaled_outer,
            detail_polys=scaled_details,
            blade_height=blade_height,
            blade_width=0.86,
            collar_height=min(4.0, blade_height * 0.4),
            collar_width=1.60,
            handle_height=2.0,
            handle_width=handle_width,
            stamp_mode=stamp_mode,
            stamp_relief=2.50,
        )

        # 4. Save to temporary Job Directory
        job_id = str(uuid.uuid4())
        job_dir = JOBS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        stl_filename = "cookie_cutter.stl"
        stl_path = job_dir / stl_filename
        mesh.export(str(stl_path))

        # 5. Headless PrusaSlicer Slicing
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

        return JSONResponse(
            content={
                "job_id": job_id,
                "dimensions": dims,
                "stats": stats,
                "slicing": slicing_result,
                "downloads": {
                    "stl_url": f"/api/download/{job_id}/{stl_filename}",
                    "gcode_url": f"/api/download/{job_id}/{gcode_filename}",
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
async def download_file(
    job_id: str,
    filename: str,
    user_info: dict = Depends(verify_user_token)
):
    """Safely serves generated STL or G-code file."""
    # Prevent path traversal
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
