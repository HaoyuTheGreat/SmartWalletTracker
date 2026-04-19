# ============================================================
# SmartWalletsTracker — Makefile
# 把一长串 docker/gcloud 命令封装成短 target
# 用法：make <target>，例如 `make deploy`
# ============================================================

# ---------- 配置（改这里就行） ----------
PROJECT    := smart-wallets-tracker
REGION     := us-central1
REPO       := smartwalletstracker
IMAGE_NAME := smartwalletstracker
JOB_NAME   := smartwalletstracker-job

# image 完整路径（Artifact Registry）
IMAGE := $(REGION)-docker.pkg.dev/$(PROJECT)/$(REPO)/$(IMAGE_NAME)

# tag 用 git commit hash —— 每次 build 出来的 image 都有唯一身份
# 这样云上跑的 image 能精确对应到某个 commit
TAG := $(shell git rev-parse --short HEAD)

# ---------- targets ----------

# 默认 target：敲 `make` 不带参数时显示帮助
.DEFAULT_GOAL := help

help:  ## 显示所有可用 target
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

# -------- 本地开发 --------

test-local:  ## 本地直接跑 python main.py（最快迭代，不 build image）
	python main.py

# -------- Docker build & run --------

build-local:  ## build image（本地架构，只在 Mac 本地跑）
	docker build -t $(IMAGE_NAME):local .

run-local: build-local  ## 本地容器里跑一遍（验证打包没问题）
	docker run --rm \
	  -v ~/.config/gcloud/application_default_credentials.json:/gcp/adc.json:ro \
	  -e GOOGLE_APPLICATION_CREDENTIALS=/gcp/adc.json \
	  --env-file .env \
	  $(IMAGE_NAME):local

build:  ## build image（linux/amd64，准备上云用）
	docker build --platform linux/amd64 -t $(IMAGE):$(TAG) .
	docker tag $(IMAGE):$(TAG) $(IMAGE):latest

push: build  ## build + push 到 Artifact Registry
	docker push $(IMAGE):$(TAG)
	docker push $(IMAGE):latest

# -------- 云端部署 --------

deploy: push  ## 一键部署：build + push + 更新 Cloud Run Job 指向新 image
	gcloud run jobs update $(JOB_NAME) \
	  --region=$(REGION) \
	  --image=$(IMAGE):$(TAG)
	@echo ""
	@echo "✅ Deployed $(IMAGE):$(TAG) to $(JOB_NAME)"

run:  ## 云上触发一次 Job（跑完才返回）
	gcloud run jobs execute $(JOB_NAME) --region=$(REGION) --wait

logs:  ## 看云上最近一次 Job 执行的日志
	gcloud run jobs executions list \
	  --job=$(JOB_NAME) --region=$(REGION) --limit=1

# -------- 工具 --------

clean:  ## 清掉本地 image 和 __pycache__
	docker rmi -f $(IMAGE_NAME):local 2>/dev/null || true
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

.PHONY: help test-local build-local run-local build push deploy run logs clean
