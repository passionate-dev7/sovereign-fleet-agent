FROM python:3.12-slim

WORKDIR /app

# Build context is the devpost/ repo root (see infra/deploy.sh:
# `gcloud builds submit` from repo root with -f projects/sovereign/Dockerfile),
# so both projects/sovereign and the shared spine at projects/shared are
# available to COPY without vendoring or a separate publish step.

COPY projects/sovereign/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY projects/shared /app/shared
RUN pip install --no-cache-dir -e /app/shared

COPY projects/sovereign /app

CMD ["python", "-m", "job.main"]
