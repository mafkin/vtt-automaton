FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN git clone https://github.com/comfyanonymous/ComfyUI.git . && \
    git checkout v0.39.0

RUN uv venv && \
    uv pip install -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cu128

# IP-Adapter nodes: steer a panel toward a character's reference image (comic bible).
# Models go in data/comfyui/models/{clip_vision,ipadapter}; see the README.
RUN git clone https://github.com/cubiq/ComfyUI_IPAdapter_plus.git custom_nodes/ComfyUI_IPAdapter_plus && \
    git -C custom_nodes/ComfyUI_IPAdapter_plus checkout a0f451a5113cf9becb0847b92884cb10cbdec0ef

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8188

CMD ["python", "main.py", "--listen", "0.0.0.0", "--port", "8188"]
