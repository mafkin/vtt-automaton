# GPU speech-to-text worker. CUDA comes from the nvidia-* pip wheels (cuBLAS, cuDNN 9), so the
# base image stays a plain Python image; the host needs the NVIDIA driver and the NVIDIA
# Container Toolkit.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra whisper --no-install-project
COPY app ./app
ENV PATH="/app/.venv/bin:$PATH" \
    LD_LIBRARY_PATH="/app/.venv/lib/python3.12/site-packages/nvidia/cublas/lib:/app/.venv/lib/python3.12/site-packages/nvidia/cudnn/lib" \
    HF_HOME=/models
EXPOSE 8770
CMD ["uvicorn", "--factory", "app.main:app_factory", "--host", "0.0.0.0", "--port", "8770"]
