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

# Run the container
docker run -p 8080:8080 \
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

## Project Structure

```
prusa-cookiecutter-app/
├── app/
│   ├── auth.py              # Google ID token verification & allowlist check
│   ├── geometry.py          # OpenCV image processing & 3-tier Shapely/Trimesh extrusion
│   ├── slicer.py            # PrusaSlicer CLI execution & G-code metadata parser
│   ├── main.py              # FastAPI endpoints & background cleanup
│   └── templates/
│       └── index.html       # Single-page UI with Three.js 3D viewport & Prusa bed
├── Dockerfile               # Debian container with PrusaSlicer & runtime libraries
├── requirements.txt         # Python dependencies
├── slicer_config.ini        # MK3S+/MK4 calibrated slicing profile
├── .dockerignore
├── .gitignore
└── README.md
```
