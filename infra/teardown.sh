#!/usr/bin/env bash
# Sovereign teardown: delete everything infra/deploy.sh created, so nothing
# billable is left running after filming. Per AIM.md: "Services torn down
# after filming; proof lives in the recording and the repo."
#
# Usage:
#   GCP_PROJECT=my-project GCP_REGION_EU=europe-west1 GCP_REGION_US=us-central1 \
#     bash infra/teardown.sh

set -euo pipefail

GCP_PROJECT="${GCP_PROJECT:?set GCP_PROJECT}"
GCP_REGION_EU="${GCP_REGION_EU:-europe-west1}"
GCP_REGION_US="${GCP_REGION_US:-us-central1}"
GCS_BUCKET="${GCS_BUCKET:-${GCP_PROJECT}-sovereign-decisions}"

echo "== Sovereign teardown: project=${GCP_PROJECT} =="

gcloud scheduler jobs delete "sovereign-eu-schedule" \
  --project "${GCP_PROJECT}" --location "${GCP_REGION_EU}" --quiet 2>/dev/null || true
gcloud scheduler jobs delete "sovereign-us-schedule" \
  --project "${GCP_PROJECT}" --location "${GCP_REGION_US}" --quiet 2>/dev/null || true

gcloud run jobs delete "sovereign-eu-tick" \
  --project "${GCP_PROJECT}" --region "${GCP_REGION_EU}" --quiet 2>/dev/null || true
gcloud run jobs delete "sovereign-us-tick" \
  --project "${GCP_PROJECT}" --region "${GCP_REGION_US}" --quiet 2>/dev/null || true

for sa in eu-summarizer us-summarizer us-support; do
  gcloud iam service-accounts delete \
    "sovereign-${sa}@${GCP_PROJECT}.iam.gserviceaccount.com" \
    --project "${GCP_PROJECT}" --quiet 2>/dev/null || true
done

echo "-- Decision log bucket left intact for audit purposes: gs://${GCS_BUCKET}"
echo "   Delete manually with: gcloud storage rm -r gs://${GCS_BUCKET} --project ${GCP_PROJECT}"
echo "== Teardown complete (Cloud Run Jobs, Scheduler, service accounts removed) =="
