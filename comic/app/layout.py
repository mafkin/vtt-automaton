import logging
from PIL import Image, ImageDraw, ImageFont
import os

logger = logging.getLogger(__name__)

def layout_bubbles(image_path: str, bubbles: list[str]):
    if not os.path.exists(image_path):
        logger.error(f"Image not found at {image_path}")
        return
        
    try:
        img = Image.open(image_path)
        draw = ImageDraw.Draw(img)
        
        # Simple stub for placing text on the image
        y_offset = 20
        for text in bubbles:
            # We would normally use a real font. For stub, default PIL font or simple rects.
            draw.rectangle([10, y_offset, 400, y_offset + 40], fill="white", outline="black")
            draw.text((15, y_offset + 10), text, fill="black")
            y_offset += 60
            
        out_path = image_path.replace(".png", "_lettered.png")
        img.save(out_path)
        logger.info(f"Saved lettered panel to {out_path}")
    except Exception as e:
        logger.error(f"Failed to layout bubbles: {e}")
