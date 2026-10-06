FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN git clone https://github.com/comfyanonymous/ComfyUI.git . && \
    git checkout v0.39.0

RUN uv venv && \
    uv pip install -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cu124

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8188

CMD ["python", "main.py", "--listen", "0.0.0.0", "--port", "8188"]
