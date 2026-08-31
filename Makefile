# Sovereign — everything runs from THIS directory. This repo is standalone:
# `pip install -e .` puts agentspine and every dependency in your venv, so no
# PYTHONPATH juggling and no sibling directories are required.
#
#   PY=/path/to/venv/bin/python make test
# or just `make test` after activating the venv.
PY ?= python3

GCP_REGION ?= us-central1
GCP_REGION_EU ?= europe-west1

.PHONY: install test demo demo-short job deploy teardown red-green

install:
	$(PY) -m pip install -e .

test:
	$(PY) -m pytest tests/ -v

# `make demo` is the full offline end-to-end runner (allow + deny + artifact
# + idempotency + tamper). `make demo-short` is the older policy-only script.
demo:
	$(PY) demo_local.py

demo-short:
	$(PY) demo.py

job:
	$(PY) -m job.main

red-green:
	@echo "See LIMITATIONS.md 'Red/green verification' for the manual break/restore steps."
	@echo "Run: make test  (should be GREEN, 58 passed)"

deploy:
	@test -n "$(PROJECT_ID)" || { echo "usage: make deploy PROJECT_ID=<gcp-project>"; exit 1; }
	PROJECT_ID=$(PROJECT_ID) REGION=$(GCP_REGION) REGION_EU=$(GCP_REGION_EU) bash ../../infra/deploy_sovereign.sh

teardown:
	@test -n "$(PROJECT_ID)" || { echo "usage: make teardown PROJECT_ID=<gcp-project>"; exit 1; }
	PROJECT_ID=$(PROJECT_ID) REGION=$(GCP_REGION) REGION_B=$(GCP_REGION_EU) PROJECTS=sovereign bash ../../infra/teardown.sh
