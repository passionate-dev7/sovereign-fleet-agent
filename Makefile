PY ?= python3

.PHONY: test demo deploy teardown red-green

test:
	$(PY) -m pytest tests/ -v

demo:
	$(PY) demo.py

job:
	$(PY) -m job.main

red-green:
	@echo "See LIMITATIONS.md 'Red/green verification' for the manual break/restore steps."
	@echo "Run: make test  (should be GREEN, 36 passed)"

deploy:
	GCP_PROJECT=$(GCP_PROJECT) GCP_REGION_EU=$(GCP_REGION_EU) GCP_REGION_US=$(GCP_REGION_US) \
	GEMINI_API_KEY=$(GEMINI_API_KEY) bash infra/deploy.sh

teardown:
	GCP_PROJECT=$(GCP_PROJECT) GCP_REGION_EU=$(GCP_REGION_EU) GCP_REGION_US=$(GCP_REGION_US) \
	bash infra/teardown.sh
