import io

import pytest
from PIL import Image

from app import bible
from app.bible import BibleError


def png(size=(64, 64), fmt="PNG", colour="red") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, fmt)
    return buf.getvalue()


def test_a_missing_bible_is_an_empty_default():
    b = bible.load()
    assert b.characters == [] and b.bubble_language == "Finnish"
    assert b.style.negative  # a sensible default negative prompt


def test_save_and_load_round_trip(bible_dir):
    b = bible.load()
    b.setting = "Restov, Brevoy"
    bible.save(b)
    assert bible.load().setting == "Restov, Brevoy"
    assert not list(bible_dir.glob("*.tmp"))  # written atomically


def test_characters_get_unique_slug_ids():
    a = bible.add_character("Rintaro")
    b = bible.add_character("Rintaro")
    c = bible.add_character("Käl the Goblin")
    assert (a.id, b.id, c.id) == ("rintaro", "rintaro-2", "kal-the-goblin")
    assert [ch.name for ch in bible.load().characters] == ["Rintaro", "Rintaro", "Käl the Goblin"]


def test_update_and_match_by_name_or_alias():
    ch = bible.add_character("Rintaro")
    bible.update_character(ch.id, name="Rintaro", aliases=["Rin", " "], appearance="red kimono")
    b = bible.load()
    assert b.characters[0].aliases == ["Rin"]
    assert [c.id for c in b.match(["rin", "Nobody", "RINTARO"])] == ["rintaro"]


def test_unknown_character_raises_key_error():
    with pytest.raises(KeyError):
        bible.update_character("nope", name="x", aliases=[], appearance="")


def test_images_are_reencoded_shrunk_and_stored(bible_dir):
    ch = bible.add_character("Rintaro")
    name = bible.add_image(ch.id, png((3000, 1500), "JPEG"))
    path = bible.image_path(ch.id, name)
    assert path.parent == bible_dir / "characters" / "rintaro" and path.suffix == ".png"
    assert max(Image.open(path).size) == 1024
    assert bible.load().characters[0].images == [name]


@pytest.mark.parametrize(
    "data, error",
    [(b"not an image", "not an image"), (b"x" * (10 * 1024 * 1024 + 1), "too large")],
)
def test_bad_uploads_are_refused(data, error):
    ch = bible.add_character("Rintaro")
    with pytest.raises(BibleError, match=error):
        bible.add_image(ch.id, data)
    assert bible.load().characters[0].images == []


def test_image_paths_cannot_escape(bible_dir):
    ch = bible.add_character("Rintaro")
    name = bible.add_image(ch.id, png())
    for bad_id, bad_name in [("rintaro", "../../bible.json"), ("../x", name), ("nope", name)]:
        with pytest.raises(KeyError):
            bible.image_path(bad_id, bad_name)


def test_deleting_removes_files(bible_dir):
    ch = bible.add_character("Rintaro")
    first = bible.add_image(ch.id, png())
    bible.add_image(ch.id, png(colour="blue"))
    bible.delete_image(ch.id, first)
    assert len(bible.load().characters[0].images) == 1
    bible.delete_character(ch.id)
    assert bible.load().characters == []
    assert not (bible_dir / "characters" / "rintaro").exists()


def test_the_saved_bible_is_readable_on_the_host(bible_dir):
    # The containers write it as root; the server's user should still be able to read it.
    bible.save(bible.load())
    assert (bible_dir / "bible.json").stat().st_mode & 0o777 == 0o644


# --- character sheets and the style anchor ---------------------------------------------------


def test_sheets_are_stored_approved_and_deleted(bible_dir):
    ch = bible.add_character("Rintaro")
    first = bible.add_sheet(ch.id, png(colour="blue"))
    second = bible.add_sheet(ch.id, png(colour="green"))
    assert (first, second) == ("sheet_1.png", "sheet_2.png")
    assert bible.sheet_path(ch.id, first).parent == bible_dir / "characters" / "rintaro"

    bible.approve_sheet(ch.id, second)
    assert bible.load().character(ch.id).sheet == second
    bible.delete_sheet(ch.id, second)  # deleting the approved sheet un-approves it
    c = bible.load().character(ch.id)
    assert c.sheets == [first] and c.sheet is None
    assert bible.add_sheet(ch.id, png()) == "sheet_3.png"  # numbers are never reused


def test_sheet_paths_cannot_escape():
    ch = bible.add_character("Rintaro")
    bible.add_sheet(ch.id, png())
    for bad in ["../bible.json", "sheet_9.png", "x.png"]:
        with pytest.raises(KeyError):
            bible.sheet_path(ch.id, bad)
    with pytest.raises(KeyError):
        bible.approve_sheet(ch.id, "sheet_9.png")


def test_the_reference_is_the_approved_sheet_else_the_first_image():
    ch = bible.add_character("Rintaro")
    assert bible.reference_image(bible.load().character(ch.id)) is None
    bible.add_image(ch.id, png(colour="red"))
    ref = bible.reference_image(bible.load().character(ch.id))
    assert Image.open(io.BytesIO(ref)).getpixel((0, 0)) == (255, 0, 0)
    bible.approve_sheet(ch.id, bible.add_sheet(ch.id, png(colour="blue")))
    ref = bible.reference_image(bible.load().character(ch.id))
    assert Image.open(io.BytesIO(ref)).getpixel((0, 0)) == (0, 0, 255)


def test_style_anchor_set_read_and_clear(bible_dir):
    assert bible.anchor_image() is None
    bible.set_anchor(png(colour="green"))
    assert bible.load().style.anchor == "anchor.png"
    assert (bible_dir / "style" / "anchor.png").exists()
    assert bible.anchor_image() is not None
    bible.clear_anchor()
    assert bible.load().style.anchor is None and bible.anchor_image() is None


def test_bible_level_calls_are_counted():
    bible.charge(5500)
    bible.charge(None)
    assert bible.load().tokens_used == 5500


def test_new_fields_have_defaults_for_old_bibles(bible_dir):
    bible_dir.mkdir(parents=True, exist_ok=True)
    (bible_dir / "bible.json").write_text(
        '{"characters": [{"id": "kal", "name": "Käl", "images": []}], "style": {"positive": "x"}}'
    )
    b = bible.load()
    assert b.characters[0].traits == [] and b.characters[0].sheet is None
    assert b.style.page_look and b.style.anchor is None and b.tokens_used == 0


# --- round 2: never list, detail sheets, references -------------------------------------------


def test_never_list_is_saved_and_kept_when_not_sent():
    ch = bible.add_character("Pentik")
    bible.update_character(ch.id, "Pentik", [], "", traits=["great helm"], never=["tabard", " "])
    assert bible.load().character(ch.id).never == ["tabard"]
    bible.update_character(ch.id, "Pentik", [], "")  # a form without the field keeps it
    assert bible.load().character(ch.id).never == ["tabard"]


def test_detail_sheets_share_the_counter_and_are_approved_separately():
    ch = bible.add_character("Pentik")
    full = bible.add_sheet(ch.id, png(colour="blue"))
    detail = bible.add_sheet(ch.id, png(colour="green"), kind="detail")
    assert (full, detail) == ("sheet_1.png", "detail_2.png")
    bible.approve_sheet(ch.id, full)
    bible.approve_sheet(ch.id, detail)
    c = bible.load().character(ch.id)
    assert (c.sheet, c.detail) == (full, detail)
    bible.delete_sheet(ch.id, detail)
    c = bible.load().character(ch.id)
    assert (c.sheet, c.detail) == (full, None)


def test_references_are_the_sheet_then_the_details():
    ch = bible.add_character("Pentik")
    assert bible.references(bible.load().character(ch.id)) == []
    bible.add_image(ch.id, png(colour="red"))
    [(label, _)] = bible.references(bible.load().character(ch.id))
    assert label == "reference image"
    bible.approve_sheet(ch.id, bible.add_sheet(ch.id, png(colour="blue")))
    bible.approve_sheet(ch.id, bible.add_sheet(ch.id, png(colour="green"), kind="detail"))
    refs = bible.references(bible.load().character(ch.id))
    assert [label for label, _ in refs] == ["full-body sheet", "close-ups of details"]
    colours = [Image.open(io.BytesIO(data)).getpixel((0, 0)) for _, data in refs]
    assert colours == [(0, 0, 255), (0, 128, 0)]


def test_height_is_kept_unless_given():
    ch = bible.add_character("Rintaro")
    bible.update_character(ch.id, "Rintaro", [], "", height_cm=60)
    bible.update_character(ch.id, "Rintaro", [], "")  # no height in the call: unchanged
    assert bible.load().character(ch.id).height_cm == 60
    bible.update_character(ch.id, "Rintaro", [], "", height_cm=None)  # cleared
    assert bible.load().character(ch.id).height_cm is None
