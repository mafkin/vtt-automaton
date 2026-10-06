import asyncio
import json
import logging

import httpx
import websockets

from app.config import settings

logger = logging.getLogger(__name__)


def generate_comfy_prompt(
    prompt_text: str,
    prompt_prefix: str,
    negative: str = "bad hands, text, error",
    seed: int = 1337,
    reference: str | None = None,
):
    """SDXL workflow for one panel.

    reference: a character image relative to ComfyUI's input folder (characters/<id>/<file>).
    With it, IP-Adapter steers the panel toward that character's look.
    """
    workflow = {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": 20,
                "cfg": 8.0,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
        },
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sdxl.safetensors"}},
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {"batch_size": 1, "width": 1024, "height": 1024},
        },
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt_text, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["4", 1]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": prompt_prefix, "images": ["8", 0]},
        },
    }
    if reference:
        workflow |= {
            "10": {"class_type": "LoadImage", "inputs": {"image": reference}},
            "11": {
                "class_type": "IPAdapterUnifiedLoader",
                "inputs": {"model": ["4", 0], "preset": "PLUS (high strength)"},
            },
            "12": {
                "class_type": "IPAdapterAdvanced",
                "inputs": {
                    "model": ["11", 0],
                    "ipadapter": ["11", 1],
                    "image": ["10", 0],
                    # Higher weights copy the whole reference picture, not just the character.
                    "weight": settings.ipadapter_weight,
                    "weight_type": "linear",
                    "combine_embeds": "concat",
                    "start_at": 0.0,
                    "end_at": settings.ipadapter_end_at,
                    "embeds_scaling": "V only",
                },
            },
        }
        workflow["3"]["inputs"]["model"] = ["12", 0]
    return workflow


async def queue_prompt(prompt: dict):
    client_id = "comic_worker_client_id"
    payload = {"prompt": prompt, "client_id": client_id}

    async with httpx.AsyncClient() as client:
        r = await client.post(f"{settings.comfyui_url}/prompt", json=payload, timeout=10.0)
        r.raise_for_status()
        return r.json(), client_id


async def interrupt():
    async with httpx.AsyncClient() as client:
        await client.post(f"{settings.comfyui_url}/interrupt", timeout=5.0)


async def wait_for_completion(prompt_id: str, client_id: str, timeout: float = 90.0):
    ws_url = (
        settings.comfyui_url.replace("http://", "ws://").replace("https://", "wss://")
        + f"/ws?clientId={client_id}"
    )

    try:
        async with websockets.connect(ws_url) as websocket:
            while True:
                # Watchdog logic: timeout on waiting for message
                msg = await asyncio.wait_for(websocket.recv(), timeout=timeout)
                if isinstance(msg, str):
                    data = json.loads(msg)
                    msg_type = data.get("type")

                    if msg_type == "executing":
                        node = data["data"].get("node")
                        # If node is None, execution has finished
                        if node is None and data["data"].get("prompt_id") == prompt_id:
                            return True

                    elif msg_type == "execution_error":
                        if data["data"].get("prompt_id") == prompt_id:
                            logger.error(
                                f"ComfyUI execution error: {data['data'].get('exception_type')}"
                            )
                            return False

    except TimeoutError:
        logger.error(f"ComfyUI execution timed out after {timeout} seconds.")
        await interrupt()
        return False
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        return False
