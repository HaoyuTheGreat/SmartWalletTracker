# ============================================================
# SmartWalletsTracker — Makefile
# Wraps long docker/gcloud commands into short targets.
# Usage: make <target>, e.g. `make deploy`
# ============================================================

# ---------- Config (edit here) ----------
PROJECT    := smart-wallets-tracker
REGION     := us-central1
REPO       := smartwalletstracker

# Pipeline (Cloud Run Job — daily ingestion / parsing / classification)
IMAGE_NAME := smartwalletstracker
JOB_NAME   := smartwalletstracker-job

# API service (Cloud Run Service — REST API serving the frontend)
API_IMAGE_NAME   := swt-api
API_SERVICE_NAME := smartwalletstracker-api
QUERYSMITH_SA    := smartwallets-querysmith-reader@$(PROJECT).iam.gserviceaccount.com

# Full image paths (Artifact Registry)
IMAGE     := $(REGION)-docker.pkg.dev/$(PROJECT)/$(REPO)/$(IMAGE_NAME)
API_IMAGE := $(REGION)-docker.pkg.dev/$(PROJECT)/$(REPO)/$(API_IMAGE_NAME)

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

# -------- API service (Cloud Run Service) --------
# Separate from the pipeline above: this is the long-running FastAPI server
# that exposes the BigQuery warehouse + QuerySmith chat agent.

test-api-local:  ## Run uvicorn locally with hot reload (fastest API iteration)
	uvicorn api.main:app --reload

build-api:  ## Build API image for linux/amd64 (uses Dockerfile.api)
	docker build --platform linux/amd64 -f Dockerfile.api -t $(API_IMAGE):$(TAG) .
	docker tag $(API_IMAGE):$(TAG) $(API_IMAGE):latest

push-api: build-api  ## Build + push API image to Artifact Registry
	docker push $(API_IMAGE):$(TAG)
	docker push $(API_IMAGE):latest

deploy-api: push-api  ## One-shot: build + push + update Cloud Run Service
	gcloud run services update $(API_SERVICE_NAME) \
	  --region=$(REGION) \
	  --image=$(API_IMAGE):$(TAG)
	@echo ""
	@echo "Deployed $(API_IMAGE):$(TAG) to $(API_SERVICE_NAME)"

logs-api:  ## Stream recent logs from the API service
	gcloud run services logs read $(API_SERVICE_NAME) \
	  --region=$(REGION) --limit=50

url-api:  ## Print the API service public URL
	@gcloud run services describe $(API_SERVICE_NAME) \
	  --region=$(REGION) --format='value(status.url)'

# -------- Utilities --------

clean:  ## Remove local image and __pycache__
	docker rmi -f $(IMAGE_NAME):local 2>/dev/null || true
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

.PHONY: help test-local build-local run-local build push deploy run logs clean \
        test-api-local build-api push-api deploy-api logs-api url-api
