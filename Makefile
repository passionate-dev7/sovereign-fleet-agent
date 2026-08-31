PY ?= $(if $(wildcard ../../.venv/bin/python),../../.venv/bin/python,python3)

.PHONY: test demo demo-local deploy teardown red-green

test:
	$(PY) -m pytest tests/ -v

# `make demo` is the full offline end-to-end runner (allow + deny + artifact
# + idempotency + tamper). `make demo-short` is the older policy-only script.
demo:
	$(PY) demo_local.py

demo-local: demo

demo-short:
	$(PY) demo.py

job:
	$(PY) -m job.main

red-green:
	@echo "See LIMITATIONS.md 'Red/green verification' for the manual break/restore steps."
	@echo "Run: make test  (should be GREEN, 36 passed)"

GCP_REGION ?= us-central1
GCP_REGION_EU ?= europe-west1

deploy:
	@test -n "$(PROJECT_ID)" || { echo "usage: make deploy PROJECT_ID=<gcp-project>"; exit 1; }
	PROJECT_ID=$(PROJECT_ID) REGION=$(GCP_REGION) REGION_EU=$(GCP_REGION_EU) bash ../../infra/deploy_sovereign.sh

teardown:
	@test -n "$(PROJECT_ID)" || { echo "usage: make teardown PROJECT_ID=<gcp-project>"; exit 1; }
	PROJECT_ID=$(PROJECT_ID) REGION=$(GCP_REGION) REGION_B=$(GCP_REGION_EU) PROJECTS=sovereign bash ../../infra/teardown.sh
