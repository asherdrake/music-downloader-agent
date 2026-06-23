from app.core.romanize import romanize


def test_romanize_kanji_to_spaced_hepburn() -> None:
    assert romanize("天の味覚") == "ten no mikaku"


def test_romanize_hiragana_artist() -> None:
    assert romanize("ゆう") == "yuu"


def test_romanize_collapses_whitespace() -> None:
    # Full-width space between segments must not produce a multi-space run.
    assert romanize("ゆう　てんのみかく") == "yuu tennomikaku"


def test_romanize_leaves_latin_unchanged() -> None:
    assert romanize("Pink Floyd - The Wall") == "Pink Floyd - The Wall"


def test_romanize_empty_string() -> None:
    assert romanize("") == ""
