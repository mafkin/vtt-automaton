import logging
import os

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# DejaVu Sans (bundled with its license): Pillow's built-in font has no ä, ö or å.
FONT_PATH = os.path.join(os.path.dirname(__file__), "fonts", "DejaVuSans.ttf")
FONT_SIZE = 20
PADDING = 8


def bubble_font(size: int = FONT_SIZE) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_PATH, size)


def wrap(text: str, font: ImageFont.FreeTypeFont, max_width: float) -> list[str]:
    """Split text into lines no wider than max_width (a single overlong word stays whole)."""
    lines: list[str] = []
    for word in text.split():
        if lines and font.getlength(f"{lines[-1]} {word}") <= max_width:
            lines[-1] = f"{lines[-1]} {word}"
        else:
            lines.append(word)
    return lines


def layout_bubbles(image_path: str, bubbles: list[str]):
    if not os.path.exists(image_path):
        logger.error(f"Image not found at {image_path}")
        return

    try:
        img = Image.open(image_path)
        draw = ImageDraw.Draw(img)

        # Simple boxes stacked from the top left; real balloons come later.
        font = bubble_font()
        line_height = font.size + 4
        max_width = img.width - 20 - 2 * PADDING
        y_offset = 20
        for text in bubbles:
            lines = wrap(text, font, max_width)
            width = max((font.getlength(line) for line in lines), default=0)
            height = len(lines) * line_height
            box = [10, y_offset, 10 + width + 2 * PADDING, y_offset + height + 2 * PADDING]
            draw.rectangle(box, fill="white", outline="black")
            for i, line in enumerate(lines):
                draw.text(
                    (10 + PADDING, y_offset + PADDING + i * line_height),
                    line,
                    fill="black",
                    font=font,
                )
            y_offset = box[3] + 12

        out_path = image_path.replace(".png", "_lettered.png")
        img.save(out_path)
        logger.info(f"Saved lettered panel to {out_path}")
    except Exception as e:
        logger.error(f"Failed to layout bubbles: {e}")
