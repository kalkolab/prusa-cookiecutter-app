#!/usr/bin/env bash
# Creates the Cloud Storage bucket for the cutter library and mounts it into the
# Cloud Run service at /data/library. Safe to re-run.
#
# Usage (e.g. in Cloud Shell):  ./scripts/setup_library_bucket.sh
# Override defaults with env vars: PROJECT, REGION, SERVICE, BUCKET, BUCKET_LOCATION.
# The bucket defaults to us-central1 so it stays inside the Cloud Storage Always Free
# tier (5 GB Standard storage; only us-central1, us-east1 and us-west1 qualify).
set -euo pipefail

PROJECT="${PROJECT:-prusa-cookiecutter-app}"
REGION="${REGION:-europe-west1}"
SERVICE="${SERVICE:-cookie-cutter-studio}"
BUCKET="${BUCKET:-${PROJECT}-library}"
BUCKET_LOCATION="${BUCKET_LOCATION:-us-central1}"
MOUNT_PATH="/data/library"

echo "Project: $PROJECT | Region: $REGION | Service: $SERVICE | Bucket: gs://$BUCKET ($BUCKET_LOCATION)"

gcloud services enable storage.googleapis.com --project="$PROJECT"

if gcloud storage buckets describe "gs://$BUCKET" --project="$PROJECT" >/dev/null 2>&1; then
  echo "Bucket gs://$BUCKET already exists."
else
  gcloud storage buckets create "gs://$BUCKET" \
    --project="$PROJECT" \
    --location="$BUCKET_LOCATION" \
    --default-storage-class=STANDARD \
    --uniform-bucket-level-access \
    --public-access-prevention \
    --soft-delete-duration=0
fi

# Service account the Cloud Run service runs as (falls back to the default compute account)
SA="$(gcloud run services describe "$SERVICE" --project="$PROJECT" --region="$REGION" \
  --format='value(spec.template.spec.serviceAccountName)')"
if [[ -z "$SA" ]]; then
  PROJECT_NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
  SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
fi
echo "Granting roles/storage.objectUser on gs://$BUCKET to $SA"
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member="serviceAccount:$SA" \
  --role="roles/storage.objectUser" >/dev/null

# Replace an existing mount of the same name so re-runs don't fail
gcloud run services update "$SERVICE" \
  --project="$PROJECT" \
  --region="$REGION" \
  --execution-environment=gen2 \
  --remove-volume-mount="$MOUNT_PATH" \
  --remove-volume=library \
  --add-volume="name=library,type=cloud-storage,bucket=$BUCKET" \
  --add-volume-mount="volume=library,mount-path=$MOUNT_PATH"

echo "Done. Saved cutters now persist in gs://$BUCKET"
