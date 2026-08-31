#!/usr/bin/env bash
# Sovereign infra: two-region Cloud Run Jobs, per-sub-agent service accounts,
# Cloud Trace export. Reuses the same spine pattern as Tabclose/Refill
# (specs/03-sovereign-fleet.md: "reuse the Tabclose two-region deploy
# pattern exactly").
#
# Usage:
#   GCP_PROJECT=my-project GCP_REGION_EU=europe-west1 GCP_REGION_US=us-central1 \
#     GEMINI_API_KEY=... bash infra/deploy.sh
#
# What this creates:
#   - One Artifact Registry repo, one container image, built once.
#   - Three per-sub-agent service accounts (eu-summarizer, us-summarizer,
#     us-support), each with ONLY the roles it needs: Cloud Trace write,
#     and (for GCP backend mode) Firestore + the one GCS bucket. No shared
#     key across agents -- this is the zero-trust identity requirement
#     from specs/03-sovereign-fleet.md.
#   - Two Cloud Run Jobs, one deployed into GCP_REGION_EU running as the
#     eu-summarizer service account, one into GCP_REGION_US running as the
#     us-summarizer/us-support service accounts (two jobs, one per region,
#     matching the two-region requirement without needlessly multiplying
#     Cloud Run Jobs beyond what the demo needs).
#   - A Cloud Scheduler job per region triggering its Cloud Run Job.
#   - Cloud Trace is automatic for Cloud Run + the OpenTelemetry Cloud Trace
#     exporter (see requirements.txt); no separate provisioning step needed
#     beyond enabling the API, which this script does.
#
# This script is idempotent: gcloud commands use --quiet and tolerate
# "already exists" by checking before creating where practical.

set -euo pipefail

GCP_PROJECT="${GCP_PROJECT:?set GCP_PROJECT}"
GCP_REGION_EU="${GCP_REGION_EU:-europe-west1}"
GCP_REGION_US="${GCP_REGION_US:-us-central1}"
GEMINI_API_KEY="${GEMINI_API_KEY:?set GEMINI_API_KEY}"
IMAGE_REPO="${IMAGE_REPO:-sovereign}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
GCS_BUCKET="${GCS_BUCKET:-${GCP_PROJECT}-sovereign-decisions}"

IMAGE_URI="${GCP_REGION_US}-docker.pkg.dev/${GCP_PROJECT}/${IMAGE_REPO}/sovereign:${IMAGE_TAG}"

echo "== Sovereign deploy: project=${GCP_PROJECT} eu=${GCP_REGION_EU} us=${GCP_REGION_US} =="

echo "-- Enabling required APIs"
gcloud services enable \
  run.googleapis.com \
  cloudscheduler.googleapis.com \
  cloudtrace.googleapis.com \
  firestore.googleapis.com \
  storage.googleapis.com \
  artifactregistry.googleapis.com \
  --project "${GCP_PROJECT}" --quiet

echo "-- Artifact Registry repo"
gcloud artifacts repositories describe "${IMAGE_REPO}" \
  --project "${GCP_PROJECT}" --location "${GCP_REGION_US}" >/dev/null 2>&1 || \
gcloud artifacts repositories create "${IMAGE_REPO}" \
  --project "${GCP_PROJECT}" --location "${GCP_REGION_US}" \
  --repository-format=docker --quiet

echo "-- Building and pushing image"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"  # this repo root; agentspine is vendored in-repo
gcloud builds submit "${REPO_ROOT}" \
  --project "${GCP_PROJECT}" --tag "${IMAGE_URI}" \
  --config /dev/stdin --quiet <<EOF
steps:
  - name: gcr.io/cloud-builders/docker
    args: ["build", "-f", "Dockerfile", "-t", "${IMAGE_URI}", "."]
images: ["${IMAGE_URI}"]
EOF

echo "-- GCS bucket for decision logs"
gcloud storage buckets describe "gs://${GCS_BUCKET}" --project "${GCP_PROJECT}" >/dev/null 2>&1 || \
gcloud storage buckets create "gs://${GCS_BUCKET}" \
  --project "${GCP_PROJECT}" --location "${GCP_REGION_US}" --quiet

echo "-- Per-sub-agent service accounts (least privilege, no shared key)"
for sa in eu-summarizer us-summarizer us-support; do
  gcloud iam service-accounts describe \
    "sovereign-${sa}@${GCP_PROJECT}.iam.gserviceaccount.com" \
    --project "${GCP_PROJECT}" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "sovereign-${sa}" \
    --project "${GCP_PROJECT}" \
    --display-name "Sovereign ${sa} sub-agent (least privilege)" --quiet

  # Trace write for every sub-agent's spans.
  gcloud projects add-iam-policy-binding "${GCP_PROJECT}" \
    --member "serviceAccount:sovereign-${sa}@${GCP_PROJECT}.iam.gserviceaccount.com" \
    --role "roles/cloudtrace.agent" --condition=None --quiet >/dev/null

  # Firestore access for idempotency claims.
  gcloud projects add-iam-policy-binding "${GCP_PROJECT}" \
    --member "serviceAccount:sovereign-${sa}@${GCP_PROJECT}.iam.gserviceaccount.com" \
    --role "roles/datastore.user" --condition=None --quiet >/dev/null

  # Object write on ONLY the one decision-log bucket, not project-wide storage.
  gcloud storage buckets add-iam-policy-binding "gs://${GCS_BUCKET}" \
    --member "serviceAccount:sovereign-${sa}@${GCP_PROJECT}.iam.gserviceaccount.com" \
    --role "roles/storage.objectCreator" --quiet >/dev/null
done

deploy_job() {
  local job_name="$1" region="$2" sa="$3"
  echo "-- Cloud Run Job ${job_name} in ${region} as ${sa}"
  gcloud run jobs describe "${job_name}" \
    --project "${GCP_PROJECT}" --region "${region}" >/dev/null 2>&1 && \
  gcloud run jobs update "${job_name}" \
    --project "${GCP_PROJECT}" --region "${region}" \
    --image "${IMAGE_URI}" \
    --service-account "sovereign-${sa}@${GCP_PROJECT}.iam.gserviceaccount.com" \
    --set-env-vars "SOVEREIGN_BACKEND=gcp,SOVEREIGN_GCS_BUCKET=${GCS_BUCKET},GEMINI_API_KEY=${GEMINI_API_KEY},GOOGLE_CLOUD_PROJECT=${GCP_PROJECT}" \
    --max-retries 1 --quiet || \
  gcloud run jobs create "${job_name}" \
    --project "${GCP_PROJECT}" --region "${region}" \
    --image "${IMAGE_URI}" \
    --service-account "sovereign-${sa}@${GCP_PROJECT}.iam.gserviceaccount.com" \
    --set-env-vars "SOVEREIGN_BACKEND=gcp,SOVEREIGN_GCS_BUCKET=${GCS_BUCKET},GEMINI_API_KEY=${GEMINI_API_KEY},GOOGLE_CLOUD_PROJECT=${GCP_PROJECT}" \
    --max-retries 1 --quiet
}

deploy_job "sovereign-eu-tick" "${GCP_REGION_EU}" "eu-summarizer"
deploy_job "sovereign-us-tick" "${GCP_REGION_US}" "us-summarizer"

schedule_job() {
  local scheduler_name="$1" job_name="$2" region="$3"
  echo "-- Cloud Scheduler ${scheduler_name} -> ${job_name}"
  gcloud scheduler jobs describe "${scheduler_name}" \
    --project "${GCP_PROJECT}" --location "${region}" >/dev/null 2>&1 || \
  gcloud scheduler jobs create http "${scheduler_name}" \
    --project "${GCP_PROJECT}" --location "${region}" \
    --schedule "*/2 * * * *" \
    --uri "https://${region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${GCP_PROJECT}/jobs/${job_name}:run" \
    --http-method POST \
    --oauth-service-account-email "sovereign-${job_name#sovereign-}@${GCP_PROJECT}.iam.gserviceaccount.com" \
    --quiet
}

schedule_job "sovereign-eu-schedule" "sovereign-eu-tick" "${GCP_REGION_EU}"
schedule_job "sovereign-us-schedule" "sovereign-us-tick" "${GCP_REGION_US}"

echo "== Deploy complete =="
echo "Run manually: gcloud run jobs execute sovereign-eu-tick --project ${GCP_PROJECT} --region ${GCP_REGION_EU}"
echo "Cloud Trace:  https://console.cloud.google.com/traces/list?project=${GCP_PROJECT}"
echo "Decisions:    gs://${GCS_BUCKET}/decisions/"
