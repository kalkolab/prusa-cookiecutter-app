"""
Prusa Cookie Cutter & Stamp Studio - FastAPI Application
"""

import os
import json
import time
import uuid
import base64
import shutil
import logging
from pathlib import Path
from typing import List, Optional

from fastapi import (
    FastAPI,
    Request,
    File,
    Form,
    UploadFile,
    HTTPException,
    Depends,
    BackgroundTasks,
)
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.geometry import image_to_cookie_cutter
from app.slicer import slice_stl_to_gcode, find_prusa_slicer_binary, PRINTER_SPECS
from app.auth import verify_user_token, is_auth_enabled, GOOGLE_CLIENT_ID
from app import library, plate
from app.i18n import UserError, translate, request_language

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
JOBS_DIR = Path(os.getenv("JOBS_DIR", "/tmp/cookiecutter_jobs"))
JOBS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Prusa Cookie Cutter & Stamp Studio")

TEMPLATES_DIR = BASE_DIR / "templates"
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.exception_handler(UserError)
async def user_error_handler(request: Request, exc: UserError):
    """Renders user-facing errors in the language chosen in the UI (lang cookie)."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": translate(exc.key, request_language(request), **exc.params), "code": exc.key},
        headers=exc.headers,
    )


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
async def serve_index(request: Request):
    index_file = TEMPLATES_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="Index template not found.")
    # Set <html lang> from the saved choice so the page starts in the right language
    html = index_file.read_text(encoding="utf-8").replace(
        '<html lang="en">', f'<html lang="{request_language(request)}">', 1
    )
    return HTMLResponse(content=html)


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
        raise UserError("param_range", name=name, lo=f"{lo:g}", hi=f"{hi:g}", value=f"{value:g}")


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
        raise UserError("not_image")

    content = image.file.read()
    if len(content) > 15 * 1024 * 1024:
        raise UserError("image_too_large")

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
            raise UserError(
                "footprint_too_big",
                width=stats["print_width_mm"], length=stats["print_length_mm"],
                printer=spec["name"], max_x=f"{max_x:g}", max_y=f"{max_y:g}",
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

        # Keep the source image, mask and parameters next to the outputs so the
        # job can later be saved to the cutter library.
        original_filename = "original" + library.image_extension(image.content_type, image.filename)
        (job_dir / original_filename).write_bytes(content)
        mask_filename = None
        if preview_b64:
            mask_filename = "mask.png"
            (job_dir / mask_filename).write_bytes(base64.b64decode(preview_b64))
        thumb_filename = None
        thumb = library.make_thumbnail(content)
        if thumb:
            thumb_filename = "thumb.jpg"
            (job_dir / thumb_filename).write_bytes(thumb)

        job_meta = {
            "mode": mode_str,
            "params": {
                "target_size_mm": target_size_mm,
                "blade_height": blade_height,
                "handle_width": handle_width,
                "simplify_tol": simplify_tol,
                "invert": invert,
                "stamp_mode": stamp_mode,
                "printer_model": clean_printer,
            },
            "printer_name": spec["name"],
            "dimensions": dims,
            "stats": stats,
            "slicing": slicing_result,
            "original_filename": image.filename,
            "files": {
                "stl": stl_filename,
                "gcode": gcode_filename if slicing_result.get("gcode_generated") else None,
                "original": original_filename,
                "mask": mask_filename,
                "thumb": thumb_filename,
            },
        }
        (job_dir / library.JOB_META_FILENAME).write_text(json.dumps(job_meta, indent=2), encoding="utf-8")

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

    except UserError:
        raise
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.exception("Failed to generate model")
        raise UserError("internal_error", status_code=500, error=str(e))


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
        raise UserError("invalid_path")

    if not target_file.exists():
        raise UserError("file_expired", status_code=404)

    media_type = {".stl": "model/stl", ".3mf": "model/3mf"}.get(Path(safe_filename).suffix.lower(), "text/x.gcode")
    return FileResponse(
        path=target_file,
        media_type=media_type,
        filename=safe_filename,
    )


# ---------------------------------------------------------------------------
# Cutter Library
# ---------------------------------------------------------------------------

class SaveToLibraryRequest(BaseModel):
    job_id: str
    name: str
    source_id: Optional[str] = None


def library_item_response(meta: dict) -> dict:
    """Adds download URLs for each stored file to an item's metadata."""
    item_id = meta["id"]
    return {
        **meta,
        "urls": {
            key: f"/api/library/{item_id}/files/{filename}"
            for key, filename in meta.get("files", {}).items()
            if filename
        },
    }


def get_library_item_or_404(item_id: str) -> dict:
    meta = library.get_item(item_id)
    if not meta:
        raise UserError("library_item_not_found", status_code=404)
    return meta


@app.post("/api/library")
def save_to_library(req: SaveToLibraryRequest, user_info: dict = Depends(verify_user_token)):
    """Copies a generated job (STL, G-code, original image, params) into the persistent library."""
    try:
        job_id = str(uuid.UUID(req.job_id))
    except ValueError:
        raise UserError("invalid_job_id")
    try:
        meta = library.save_from_job(JOBS_DIR / job_id, req.name, user_info.get("email"), req.source_id)
    except UserError:
        raise
    except Exception as e:
        logger.exception("Failed to save to library")
        raise UserError("save_failed", status_code=500, error=str(e))
    return library_item_response(meta)


@app.get("/api/library")
def list_library(user_info: dict = Depends(verify_user_token)):
    return {"items": [library_item_response(m) for m in library.list_items()]}


@app.get("/api/library/{item_id}")
def get_library_item(item_id: str, user_info: dict = Depends(verify_user_token)):
    return library_item_response(get_library_item_or_404(item_id))


@app.get("/api/library/{item_id}/files/{filename}")
def get_library_file(item_id: str, filename: str, user_info: dict = Depends(verify_user_token)):
    """
    Serves a stored file. Unlike job downloads these live forever, so they require
    the Authorization header; the UI fetches them as blobs.
    """
    meta = get_library_item_or_404(item_id)
    path = library.item_file_path(item_id, filename)
    if not path:
        raise UserError("file_not_found", status_code=404)

    suffix = path.suffix.lower()
    if suffix in (".stl", ".gcode"):
        media_type = "model/stl" if suffix == ".stl" else "text/x.gcode"
        stem = library.slugify(meta.get("name", ""))
        if suffix == ".gcode":
            stem += f"_{meta.get('params', {}).get('printer_model', 'prusa')}"
        return FileResponse(path=path, media_type=media_type, filename=stem + suffix)
    return FileResponse(path=path)


@app.delete("/api/library/{item_id}")
def delete_library_item(item_id: str, user_info: dict = Depends(verify_user_token)):
    meta = get_library_item_or_404(item_id)
    if not user_info.get("auth_disabled") and meta.get("owner") != user_info.get("email"):
        raise UserError("delete_forbidden", status_code=403)
    library.delete_item(item_id)
    return {"deleted": item_id}


# ---------------------------------------------------------------------------
# Print Plate: several library cutters arranged on one bed
# ---------------------------------------------------------------------------

class PlateItem(BaseModel):
    id: str
    quantity: int = 1


class PlateRequest(BaseModel):
    items: List[PlateItem]
    printer_model: str = "mk4s"


@app.post("/api/plate")
def build_print_plate(req: PlateRequest, background_tasks: BackgroundTasks, user_info: dict = Depends(verify_user_token)):
    """
    Arranges the selected library cutters on the printer's bed without overlap and
    exports a combined STL, a PrusaSlicer 3MF project and sliced G-code.
    """
    background_tasks.add_task(cleanup_old_jobs)

    printer = req.printer_model.lower().strip()
    if printer not in PRINTER_SPECS:
        raise UserError("unknown_printer", printer=req.printer_model)
    spec = PRINTER_SPECS[printer]

    parts = []
    for item in req.items:
        if not 1 <= item.quantity <= plate.MAX_COPIES:
            raise UserError("quantity_range", max=plate.MAX_COPIES)
        meta = get_library_item_or_404(item.id)
        stl_path = library.item_file_path(item.id, meta["files"]["stl"])
        if not stl_path:
            raise UserError("plate_stl_missing", status_code=404, name=meta["name"])
        parts.append({"name": meta["name"], "stl_path": stl_path, "quantity": item.quantity})

    job_id = str(uuid.uuid4())
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = plate.build_plate(parts, spec["bed_x"], spec["bed_y"], BED_MARGIN_MM, job_dir)
    except UserError:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise
    except Exception as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        logger.exception("Failed to build print plate")
        raise UserError("plate_failed", status_code=500, error=str(e))

    gcode_filename = f"plate_{printer}.gcode"
    slicing_result = slice_stl_to_gcode(
        stl_path=str(job_dir / "plate.stl"),
        output_gcode_path=str(job_dir / gcode_filename),
        printer_model=printer,
        threads=2,
        timeout_seconds=150,
    )

    return {
        "job_id": job_id,
        **result,
        "printer_model": printer,
        "printer_name": spec["name"],
        "slicing": slicing_result,
        "downloads": {
            "stl_url": f"/api/download/{job_id}/plate.stl",
            "project_url": f"/api/download/{job_id}/plate.3mf",
            "gcode_url": f"/api/download/{job_id}/{gcode_filename}" if slicing_result.get("gcode_generated") else None,
            "gcode_filename": gcode_filename,
        },
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)
