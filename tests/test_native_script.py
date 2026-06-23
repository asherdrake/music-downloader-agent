from app.core.native_script import (
    _MAX_VARIANTS,
    gather_native_variants,
    harvest_native_script,
)

# --- harvest_native_script ---


def test_harvest_extracts_nonlatin_runs() -> None:
    titles = [
        "Yuu - Ten no Mikaku",  # all Latin -> nothing
        "ゆう　てんのみかく",  # full-width space within a run
        "てんのみかく",
        "中島優美",
        "Hachisu",
    ]
    assert harvest_native_script(titles) == [
        "ゆう てんのみかく",
        "てんのみかく",
        "中島優美",
    ]


def test_harvest_ignores_pure_latin_titles() -> None:
    assert harvest_native_script(["Daft Punk - Discovery", "One More Time"]) == []


def test_harvest_dedupes_repeated_strings() -> None:
    assert harvest_native_script(["てんのみかく", "てんのみかく"]) == ["てんのみかく"]


def test_harvest_handles_empty_and_none_titles() -> None:
    assert harvest_native_script(["", "ゆう"]) == ["ゆう"]


# --- gather_native_variants ---


def test_gather_orders_harvest_then_transliteration_then_llm() -> None:
    variants = gather_native_variants(
        romaji_text="Ten no Mikaku",
        candidate_titles=["中島優美"],
        llm_variants=["天の味覚"],
    )
    # Harvested result comes first (most grounded).
    assert variants[0] == "中島優美"
    # Deterministic kana transliteration is included.
    assert "てんのみかく" in variants
    # The LLM guess is included too.
    assert "天の味覚" in variants


def test_gather_dedupes_across_sources() -> None:
    # Harvested string equals the kana transliteration of the romaji.
    variants = gather_native_variants(
        romaji_text="Ten no Mikaku",
        candidate_titles=["てんのみかく"],
        llm_variants=["てんのみかく"],
    )
    assert variants.count("てんのみかく") == 1


def test_gather_without_candidates_uses_transliteration() -> None:
    variants = gather_native_variants("Yuu", candidate_titles=[])
    assert "ゆう" in variants


def test_gather_caps_variant_count() -> None:
    many = [f"曲{index}" for index in range(50)]
    variants = gather_native_variants("Ten no Mikaku", candidate_titles=many)
    assert len(variants) <= _MAX_VARIANTS
