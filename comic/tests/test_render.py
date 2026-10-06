from PIL import Image

from app.comfy import generate_comfy_prompt
from app.layout import layout_bubbles


def test_comfy_workflow_wires_prompt_and_prefix():
    workflow = generate_comfy_prompt("a dwarf fighter in a tavern", "comic_s1_p1_pan1")
    assert workflow["6"]["inputs"]["text"] == "a dwarf fighter in a tavern"
    assert workflow["9"]["inputs"]["filename_prefix"] == "comic_s1_p1_pan1"
    assert workflow["9"]["inputs"]["images"] == ["8", 0]


def test_layout_writes_a_lettered_copy(tmp_path):
    panel = tmp_path / "comic_s1_p1_pan1_00001_.png"
    Image.new("RGB", (512, 512), "gray").save(panel)
    layout_bubbles(str(panel), ["Hyökkään!", "Nethys, mitä Trip tekee?"])
    lettered = tmp_path / "comic_s1_p1_pan1_00001__lettered.png"
    assert lettered.exists()
    assert Image.open(lettered).getpixel((20, 30)) == (255, 255, 255)  # a bubble was drawn
