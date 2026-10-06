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
