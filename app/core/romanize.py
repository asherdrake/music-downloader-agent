from functools import lru_cache

import jaconv
import pykakasi


@lru_cache(maxsize=1)
def _converter() -> "pykakasi.kakasi":
    return pykakasi.kakasi()


def romanize(text: str) -> str:
    """Transliterate Japanese (kanji/kana) text to Hepburn romaji.

    Latin-script input is returned essentially unchanged, so this is safe to
    apply to any candidate string before fuzzy matching. Collapses the runs of
    whitespace pykakasi emits between segments.
    """
    if not text:
        return ""
    segments = _converter().convert(text)
    romaji = " ".join(segment["hepburn"] for segment in segments)
    return " ".join(romaji.split())


def derive_kana_variants(romaji_text: str) -> list[str]:
    """Deterministically transliterate spaced Hepburn romaji to kana.

    Returns the hiragana and katakana forms with whitespace stripped (the form
    catalogues store titles in) — e.g. "Ten no Mikaku" -> ["てんのみかく",
    "テンノミカク"]. Word-spaced input matters: jaconv reads the particle "no"
    as の only when it is its own token, so pass the romaji as the LLM wrote it.
    Returns [] for input that does not transliterate.
    """
    if not romaji_text.strip():
        return []
    hiragana = "".join(jaconv.alphabet2kana(romaji_text.lower()).split())
    if not hiragana:
        return []
    katakana = jaconv.hira2kata(hiragana)
    variants: list[str] = []
    for variant in (hiragana, katakana):
        if variant and variant not in variants:
            variants.append(variant)
    return variants
