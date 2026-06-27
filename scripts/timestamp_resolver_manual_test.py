from app.core.config import get_settings
from app.core.llm import get_llm
from app.models.chapter_map import TimestampResolution
from app.pipeline.timestamp_resolver import _format_timestamp, resolve_timestamps

_METHOD_LABELS = {
    "native": "Level 1 (yt-dlp native chapters)",
    "llm": "Level 2 (LLM page-text extraction)",
    "discogs": "Level 3 (Discogs durations)",
    "manual_required": "All levels failed -> chapter_map_prompt interrupt",
}


def print_resolution(resolution: TimestampResolution) -> None:
    print(f"Method:      {_METHOD_LABELS.get(resolution.method, resolution.method)}")
    print(f"Chapter map: {resolution.chapter_map_path or '(none)'}")
    print(f"\nChapters ({len(resolution.chapters)}):")
    if not resolution.chapters:
        print("  (none)")
        return
    for chapter in resolution.chapters:
        start = _format_timestamp(chapter.start_seconds)
        print(f"  {start}  {chapter.track_name}")


url = "https://www.youtube.com/watch?v=3OETJzH7TKg"
settings = get_settings()
llm = get_llm()
output_dir = settings.local_files_directory

resolution: TimestampResolution = resolve_timestamps(
    url=url, release=None, llm=llm, output_dir=output_dir
)

print("1. NATIVE CHAPTERS")
print_resolution(resolution)


url = "https://www.youtube.com/watch?v=5U-PC_OUuYg"


print()
print("2. DESCRIPTION")
desc_res: TimestampResolution = resolve_timestamps(
    url=url, release=None, llm=llm, output_dir=output_dir
)
print_resolution(desc_res)


url = "https://www.youtube.com/watch?v=LyZgTaQG7bU"

print()
print("3. LLM - COMMENTS")
comments_res: TimestampResolution = resolve_timestamps(
    url=url, release=None, llm=llm, output_dir=output_dir
)
print_resolution(comments_res)
