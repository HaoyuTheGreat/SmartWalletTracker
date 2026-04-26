# syntax=docker/dockerfile:1

# ============================================================
# Base image: official Python 3.11 slim
# slim is half the size of full and ships only the minimal
# Debian system needed to run Python.
# ============================================================
FROM python:3.11-slim

# ============================================================
# Python runtime env vars
# ============================================================
# Don't write .pyc bytecode files (no point inside a container)
ENV PYTHONDONTWRITEBYTECODE=1
# Disable stdout/stderr buffering so print() flushes immediately
# → Cloud Logging shows output in real time
ENV PYTHONUNBUFFERED=1

# ============================================================
# Working directory (equivalent to cd /app)
# ============================================================
WORKDIR /app

# ============================================================
# Step 1: copy requirements.txt and install deps in its own layer
# → editing code without touching deps doesn't trigger a reinstall
# ============================================================
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ============================================================
# Step 2: copy project code (most-changed layer goes last)
# (.dockerignore already excludes .env / __pycache__ / llm.py / etc.)
# ============================================================
COPY . .

# ============================================================
# Container entrypoint
# ============================================================
CMD ["python", "main.py"]
