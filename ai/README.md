---
title: Elevate Topic MCQ Service
sdk: docker
app_port: 7860
---

# Elevate Topic MCQ Service

This is a FastAPI-based service for generating Multiple Choice Questions from topics.

## Runtime Notes

- The service preloads the model during startup and only reports ready after model load completes.
- Set `MODELS_CACHE_DIR=/data/elevate_models_cache` on Hugging Face Spaces to reuse cache across restarts.

## Optional Bucket Restore

You can restore cache from a Hugging Face bucket at container start by setting:

- `HF_BUCKET_URI=hf://buckets/<username>/<bucket-name>`
- `HF_BUCKET_CACHE_PREFIX=elevate_models_cache`

When configured, `start.sh` runs `hf sync` from that bucket path into the configured model cache directory.
