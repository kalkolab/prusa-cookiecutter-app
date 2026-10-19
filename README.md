# Prusa Cookie Cutter & Stamp Studio 🍪🖨️

A self-contained web application that transforms 2D images (JPEG, PNG, WebP) into production-ready 3D cookie cutters and stamps, complete with real-time 3D Three.js bed preview and headless PrusaSlicer G-code generation for Prusa MK3S+ and MK4 printers.

---

## Features

- **2D-to-3D Geometry Engine:**
  - Automatic horizontal mirroring (so text and asymmetric shapes stamp right-side up into dough).
  - 3-tier stepped profile:
    - **Ergonomic Handle Rim:** 4.0 mm wide × 2.0 mm high (flat on the bed at $Z=0$).
    - **Support Collar:** 1.6 mm wide × 4.0 mm high.
    - **Cutting Blade:** 0.86 mm wide (2 perimeters for 0.4 mm nozzle) × 12.0 mm total height.
  - **Stamp Mode:** Adds a 2.0 mm solid backing plate + recessed interior imprint ribs ($Z = \text{blade\_height} - 2.5\text{ mm}$) to emboss details into dough without cutting through.
- **Headless Slicing:** Integrates directly with `prusa-slicer` CLI to produce calibrated `.gcode` ready for your SD card or OctoPrint.
- **Interactive Three.js 3D Viewport:**
  - Exact $250 \times 210\text{ mm}$ Prusa MK3S/MK4 build plate grid with 10 mm squares.
  - Live millimeter bounding box overlays ($X \times Y \times Z$).
  - 360° orbit controls and camera presets (Perspective / Top View).
- **Cutter Library:**
  - Save any generated cutter with a name: STL, G-code, original image, detected mask and every parameter are stored together.
  - Browse, search and filter the library, preview a saved cutter on the 3D bed, or download its files.
  - Reuse a saved image to make new cutters: start from it with your current settings or with the settings it was saved with. New saves are linked to the original, and each cutter's detail view lists the other cutters made from the same image.
  - Files live in a plain directory (`LIBRARY_DIR`), backed by a Google Cloud Storage bucket on Cloud Run (see [Persistent Library Storage](#5-persistent-library-storage-cloud-storage-bucket)).
- **Print Plate (batch printing):**
  - Select several cutters from the library (with a quantity for each) and print them in one go.
  - Footprints are packed onto the chosen printer's bed with MaxRects (5 mm gaps, 90° rotation when it helps) so nothing overlaps; a top-view layout preview is shown.
  - Downloads: sliced G-code, a PrusaSlicer project (`.3mf`, one named object per cutter) and a combined STL; the plate can also be viewed on the 3D bed.
- **Languages: English, French, Russian:**
  - EN / FR / RU drop-down in the header; the choice is saved in a `lang` cookie for a year. Without a cookie the browser language is used.
  - UI strings live in `app/static/i18n.js`; server error messages are translated in `app/i18n.py` using the same cookie.
- **Google OAuth 2.0 Allowlist Protection:**
  - Secure access restricted to you and your friends' Google accounts.
  - Graceful dev mode fallback when unconfigured.
- **Cloud Run Ready ($0/mo):** Scales to zero when idle, fitting entirely within Google Cloud's free tier.

---

## Local Development (Docker)

To run locally with Docker (includes PrusaSlicer and graphics libraries pre-installed):

```bash
# Build the Docker image
docker build -t prusa-cookiecutter-studio .

# Run the container (the named volume keeps the cutter library between restarts)
docker run -p 8080:8080 \
  -v cookie-library:/data/library \
  -e GOOGLE_CLIENT_ID="your-client-id.apps.googleusercontent.com" \
  -e ALLOWED_EMAILS="you@gmail.com,friend@gmail.com" \
  prusa-cookiecutter-studio
```

Open `http://localhost:8080` in your browser.

*(If you haven't set up Google OAuth yet, omit the `-e` flags to run in open Dev Mode).*

---

## Setting Up Google Cloud Project (`prusa-cookiecutter-app`)

### 1. Enable Required Cloud APIs
Ensure the following APIs are enabled in your project:
- **Cloud Run Admin API** (`run.googleapis.com`)
- **Cloud Build API** (`cloudbuild.googleapis.com`)
- **Artifact Registry API** (`artifactregistry.googleapis.com`)

You can enable them in the Google Cloud Console or via CLI:
```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com --project=prusa-cookiecutter-app
```

---

### 2. Configure Google OAuth Consent Screen (For You & Friends)

To allow only yourself and friends without public domain verification:

1. Open **[Google Cloud Console > APIs & Services > OAuth consent screen](https://console.cloud.google.com/apis/credentials/consent)**.
2. Select **User Type: External** and click **Create**.
3. Fill in:
   - **App name:** `Prusa Cookie Cutter Studio`
   - **User support email:** Your email
   - **Developer contact information:** Your email
4. Click **Save and Continue** through the Scopes step (default `.../auth/userinfo.email` and `.../auth/userinfo.profile` are sufficient).
5. **Test Users (Crucial Step):**
   - Click **+ Add Users**.
   - Enter your email address and your friends' Google account email addresses.
   - *Only users listed here will be granted login access.*
6. Save and finish. Leave the Publishing Status as **Testing** (no Google verification required).

---

### 3. Create OAuth 2.0 Client ID

1. Go to **[APIs & Services > Credentials](https://console.cloud.google.com/apis/credentials)**.
2. Click **+ Create Credentials > OAuth client ID**.
3. Select **Application type:** `Web application`.
4. Name: `Cookie Cutter Web Client`.
5. Under **Authorized JavaScript origins**, add:
   - `http://localhost:8080` (for local testing)
   - `https://<YOUR-CLOUD-RUN-SERVICE-URL>` (add this after your first deploy)
6. Click **Create** and copy your **Client ID** (looks like `XXXXXXXXXXXX.apps.googleusercontent.com`).

---

### 4. Deploy to Google Cloud Run

Deploy the container using Google Cloud Build & Cloud Run:

```bash
gcloud run deploy cookie-cutter-studio \
  --source . \
  --project prusa-cookiecutter-app \
  --region europe-west1 \
  --memory 2Gi \
  --cpu 2 \
  --max-instances 2 \
  --allow-unauthenticated \
  --set-env-vars="^;^GOOGLE_CLIENT_ID=YOUR_CLIENT_ID.apps.googleusercontent.com;ALLOWED_EMAILS=you@gmail.com,friend1@gmail.com,friend2@gmail.com"
```

> **Note:** Pass all variables in a **single** `--set-env-vars` flag — when the flag is repeated only the last one takes effect, which would drop `GOOGLE_CLIENT_ID` and leave the app open. The `^;^` prefix switches the pair separator to `;` so the commas inside `ALLOWED_EMAILS` are preserved. If `GOOGLE_CLIENT_ID` is set, `ALLOWED_EMAILS` must be non-empty or every sign-in is refused.

> **Note:** We use `--allow-unauthenticated` on Cloud Run because authentication is enforced directly by the application backend using verified Google OAuth ID tokens and the `ALLOWED_EMAILS` check. This avoids the need for an expensive Google Cloud Load Balancer (~$18/mo) required by Cloud IAP.

---

### 5. Persistent Library Storage (Cloud Storage bucket)

Cloud Run's filesystem is in-memory and wiped whenever an instance stops, so saved cutters need external storage. The app writes the library to `LIBRARY_DIR` (`/data/library` in the container); on Cloud Run, mount a **Cloud Storage bucket** at that path. No code or SDK changes are needed — the bucket appears as a normal directory.

The quickest way is the bundled script — run it from [Cloud Shell](https://shell.cloud.google.com) (or anywhere `gcloud` is logged in) after the service has been deployed once:

```bash
./scripts/setup_library_bucket.sh   # creates gs://prusa-cookiecutter-app-library and mounts it
```

Or do the same steps by hand:

```bash
# Create the bucket in a free-tier region (soft delete off: retained deleted objects are billed)
gcloud storage buckets create gs://prusa-cookiecutter-app-library \
  --project=prusa-cookiecutter-app \
  --location=us-central1 \
  --default-storage-class=STANDARD \
  --uniform-bucket-level-access \
  --public-access-prevention \
  --soft-delete-duration=0

# Let the Cloud Run service account read and write objects
# (default: PROJECT_NUMBER-compute@developer.gserviceaccount.com)
gcloud storage buckets add-iam-policy-binding gs://prusa-cookiecutter-app-library \
  --member="serviceAccount:PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
  --role="roles/storage.objectUser"

# Mount it into the service (volume mounts need the gen2 execution environment)
gcloud run services update cookie-cutter-studio \
  --project=prusa-cookiecutter-app \
  --region=europe-west1 \
  --execution-environment=gen2 \
  --add-volume=name=library,type=cloud-storage,bucket=prusa-cookiecutter-app-library \
  --add-volume-mount=volume=library,mount-path=/data/library
```

**Cost: $0.** The bucket lives in `us-central1`, which is covered by the Cloud Storage Always Free tier (5 GB Standard storage, 5,000 Class A and 50,000 Class B operations, 100 GB egress per month). A saved cutter is roughly 1–5 MB (STL + G-code + original image), so hundreds of cutters fit. The service stays in `europe-west1`; the cross-region hop adds a little latency to library calls but the traffic falls within the free egress. Soft delete is disabled because retained deleted objects would be billed.

**Layout:** each cutter is a folder `<id>/` containing `meta.json` (name, owner, parameters, dimensions, slicing stats), the STL, the G-code, `original.<ext>`, `mask.png` and `thumb.jpg`. You can browse or back it up directly with `gcloud storage ls gs://prusa-cookiecutter-app-library/`.

The library is shared by all allowed users; each entry records who saved it, and only that person can delete it.

---

## Project Structure

```
prusa-cookiecutter-app/
├── app/
│   ├── auth.py              # Google ID token verification & allowlist check
│   ├── geometry.py          # OpenCV image processing & 3-tier Shapely/Trimesh extrusion
│   ├── slicer.py            # PrusaSlicer CLI execution & G-code metadata parser
│   ├── library.py           # Persistent cutter library (files + metadata in LIBRARY_DIR)
│   ├── plate.py             # Print plate: bed packing of several cutters, STL/3MF export
│   ├── i18n.py              # Translated server error messages (EN/FR/RU)
│   ├── static/
│   │   └── i18n.js          # UI translations (EN/FR/RU) and language switching
│   ├── main.py              # FastAPI endpoints & background cleanup
│   └── templates/
│       └── index.html       # Single-page UI with Three.js 3D viewport & Prusa bed
├── scripts/
│   └── setup_library_bucket.sh  # Creates & mounts the library bucket on Cloud Run
├── Dockerfile               # Debian container with PrusaSlicer & runtime libraries
├── requirements.txt         # Python dependencies
├── slicer_config.ini        # MK3S+/MK4 calibrated slicing profile
├── .dockerignore
├── .gitignore
└── README.md
```
