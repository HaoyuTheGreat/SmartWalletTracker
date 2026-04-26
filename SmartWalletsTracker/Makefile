# ============================================================
# SmartWalletsTracker — Makefile
# Wraps long docker/gcloud commands into short targets.
# Usage: make <target>, e.g. `make deploy`
# ============================================================

# ---------- Config (edit here) ----------
PROJECT    := smart-wallets-tracker
REGION     := us-central1
REPO       := smartwalletstracker
IMAGE_NAME := smartwalletstracker
JOB_NAME   := smartwalletstracker-job

# Full image path (Artifact Registry)
IMAGE := $(REGION)-docker.pkg.dev/$(PROJECT)/$(REPO)/$(IMAGE_NAME)

# Tag with the git commit hash so every built image has a unique identity
# and the cloud-running image traces back to a specific commit.
TAG := $(shell git rev-parse --short HEAD)

# ---------- targets ----------

# Default target: bare `make` shows help
.DEFAULT_GOAL := help

help:  ## Show all available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

# -------- Local development --------

test-local:  ## Run python main.py directly (fastest iteration, no image build)
	python main.py

# -------- Docker build & run --------

build-local:  ## Build image for local arch (Mac only)
	docker build -t $(IMAGE_NAME):local .

run-local: build-local  ## Run the local container once (verifies the image works)
	docker run --rm \
	  -v ~/.config/gcloud/application_default_credentials.json:/gcp/adc.json:ro \
	  -e GOOGLE_APPLICATION_CREDENTIALS=/gcp/adc.json \
	  --env-file .env \
	  $(IMAGE_NAME):local

build:  ## Build image for linux/amd64 (cloud-bound)
	docker build --platform linux/amd64 -t $(IMAGE):$(TAG) .
	docker tag $(IMAGE):$(TAG) $(IMAGE):latest

push: build  ## Build + push to Artifact Registry
	docker push $(IMAGE):$(TAG)
	docker push $(IMAGE):latest

# -------- Cloud deploy --------

deploy: push  ## One-shot: build + push + point Cloud Run Job at the new image
	gcloud run jobs update $(JOB_NAME) \
	  --region=$(REGION) \
	  --image=$(IMAGE):$(TAG)
	@echo ""
	@echo "Deployed $(IMAGE):$(TAG) to $(JOB_NAME)"

run:  ## Trigger one Job execution in the cloud (waits for completion)
	gcloud run jobs execute $(JOB_NAME) --region=$(REGION) --wait

logs:  ## Show the most recent Job execution
	gcloud run jobs executions list \
	  --job=$(JOB_NAME) --region=$(REGION) --limit=1

# -------- Utilities --------

clean:  ## Remove local image and __pycache__
	docker rmi -f $(IMAGE_NAME):local 2>/dev/null || true
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

.PHONY: help test-local build-local run-local build push deploy run logs clean
