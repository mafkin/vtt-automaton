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


def test_workflow_takes_negative_prompt_and_seed():
    wf = generate_comfy_prompt("x", "p", negative="blurry", seed=99)
    assert wf["7"]["inputs"]["text"] == "blurry"
    assert wf["3"]["inputs"]["seed"] == 99
    # No reference: the sampler uses the checkpoint's model directly, no IP-Adapter nodes.
    assert wf["3"]["inputs"]["model"] == ["4", 0]
    assert not any(n["class_type"].startswith("IPAdapter") for n in wf.values())


def test_workflow_with_a_reference_steers_through_ip_adapter():
    wf = generate_comfy_prompt("x", "p", reference="characters/rintaro/abc.png")
    nodes = {n["class_type"]: (k, n) for k, n in wf.items()}
    load_key, load = nodes["LoadImage"]
    loader_key, loader = nodes["IPAdapterUnifiedLoader"]
    adapter_key, adapter = nodes["IPAdapterAdvanced"]
    assert load["inputs"]["image"] == "characters/rintaro/abc.png"
    assert loader["inputs"] == {"model": ["4", 0], "preset": "PLUS (high strength)"}
    assert adapter["inputs"]["model"] == [loader_key, 0]
    assert adapter["inputs"]["ipadapter"] == [loader_key, 1]
    assert adapter["inputs"]["image"] == [load_key, 0]
    # Found in the spike: 0.5 up to 80% of the steps keeps the prompt's scene.
    assert adapter["inputs"]["weight"] == 0.5 and adapter["inputs"]["end_at"] == 0.8
    assert wf["3"]["inputs"]["model"] == [adapter_key, 0]


def test_the_bubble_font_has_finnish_letters():
    # Pillow's built-in font draws ä and ö as the missing-glyph box.
    from app.layout import bubble_font

    font = bubble_font()

    def mask(ch):
        return bytes(font.getmask(ch))

    missing = mask("")  # private-use code point: never in a font
    assert all(mask(ch) != missing for ch in "äöåÄÖÅ")


def test_long_bubbles_wrap_inside_the_panel():
    from app.layout import bubble_font, wrap

    font = bubble_font()
    text = "Jessan: Tämä kala maistuu ihan paskalta, viekää minut heti keittiöön kokin luo! " * 2
    lines = wrap(text, font, 500)
    assert len(lines) > 1
    assert all(font.getlength(line) <= 500 for line in lines)
    assert " ".join(lines) == text.strip()
