import re
from typing import Callable, Iterable

from app.core.romanize import derive_kana_variants

# A maximal run of non-ASCII characters (optionally spanning internal spaces).
_NON_LATIN_RUN: re.Pattern[str] = re.compile(r"[^\x00-\x7f]+(?:\s+[^\x00-\x7f]+)*")

# Cap the variants per field so the downstream Discogs search stays bounded.
_MAX_VARIANTS: int = 8

# A transliterator turns romanized text into native-script variants.
Transliterator = Callable[[str], list[str]]

# Deterministic per-language transliterators (romanized -> native script). This
# is the extension point: add a new language by appending its transliterator
# (e.g. a Hangul romaja converter) — the rest of the pipeline is unchanged.
_TRANSLITERATORS: tuple[Transliterator, ...] = (derive_kana_variants,)


def harvest_native_script(titles: Iterable[str]) -> list[str]:
    """Extract runs of non-Latin text from search-result titles.

    Language-agnostic: any non-ASCII script (CJK, kana, Hangul, Cyrillic,
    Greek, Arabic, ...) present in a YouTube/search title is captured. This is
    the most reliable native-script source because it is grounded in real
    search results rather than a model's memory — a romanized query on YouTube
    still surfaces the original-script title.
    """
    found: list[str] = []
    for title in titles:
        for match in _NON_LATIN_RUN.findall(title or ""):
            cleaned = " ".join(match.split())
            if cleaned and cleaned not in found:
                found.append(cleaned)
    return found


def _dedupe(values: Iterable[str]) -> list[str]:
    unique: list[str] = []
    for value in values:
        if value and value not in unique:
            unique.append(value)
    return unique


def gather_native_variants(
    romaji_text: str,
    candidate_titles: Iterable[str],
    llm_variants: Iterable[str] | None = None,
) -> list[str]:
    """Aggregate native-script variants for one field (artist or title).

    Sources, most-grounded first:
      1. native script harvested from real search-result titles,
      2. deterministic transliteration of the romanized text,
      3. the LLM's guesses.

    De-duplicated and capped to keep the downstream search bounded.
    """
    variants: list[str] = list(harvest_native_script(candidate_titles))
    for transliterate in _TRANSLITERATORS:
        variants.extend(transliterate(romaji_text))
    if llm_variants:
        variants.extend(llm_variants)
    return _dedupe(variants)[:_MAX_VARIANTS]
