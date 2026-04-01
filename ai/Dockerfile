FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=7860

# Force the app directory into the Python path.
ENV PYTHONPATH=/app

# Set the Hugging Face home directory to a standard location
ENV HF_HOME=/root/.cache/huggingface

WORKDIR /app

COPY requirements.txt /app/requirements.txt
# Install requirements AND the huggingface CLI tool
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt && \
    pip install --no-cache-dir huggingface_hub

# ---------------------------------------------------------
# BAKE THE MODEL INTO THE DOCKER IMAGE
# This guarantees it is cached and ready instantly on startup.
# If you are using Qwen or another model, change this argument!
# ---------------------------------------------------------
ARG MODEL_NAME="TinyLlama/TinyLlama-1.1B-Chat-v1.0"
RUN huggingface-cli download ${MODEL_NAME}

COPY . /app
RUN chmod +x /app/start.sh

EXPOSE 7860

CMD ["/app/start.sh"]