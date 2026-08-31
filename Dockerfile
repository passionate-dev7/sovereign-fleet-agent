FROM python:3.12-slim

WORKDIR /app

# Build context is THIS repo root: `docker build -t sovereign .`
# The repo vendors agentspine, so the image builds from a clean clone of just
# this repo, with no sibling directory and no separate publish step.

COPY requirements-cloudrun.txt ./requirements-cloudrun.txt
RUN pip install --no-cache-dir -r requirements-cloudrun.txt

COPY . /app

CMD ["python", "-m", "job.main"]
