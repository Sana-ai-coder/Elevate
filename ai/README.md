---
title: Elevate Topic MCQ Service
sdk: docker
app_port: 7860
---

# Elevate Topic MCQ Service

This is a FastAPI-based service for generating Multiple Choice Questions from topics.

## Runtime Notes

- The service starts quickly even when model preload is enabled because preload runs in the background.
- Set `PRELOAD_MODEL_ON_STARTUP=false` if you want startup to skip background preload entirely.
- Set `MODELS_CACHE_DIR=/data/elevate_models_cache` on Hugging Face Spaces to reuse cache across restarts.

## Optional Bucket Restore

You can restore cache from a Hugging Face bucket at container start by setting:

- `HF_BUCKET_URI=hf://buckets/<username>/<bucket-name>`
- `HF_BUCKET_CACHE_PREFIX=elevate_models_cache`
- `HF_BUCKET_RESTORE_BLOCKING=false` (default; set true only if you want restore to finish before app start)

When configured, `start.sh` runs `hf sync` from that bucket path into the configured model cache directory.
