from app.pipeline.candidate_search import search_candidates
from app.models.download_intent import DownloadIntent
from app.models.candidate import Candidate

hint_intent = DownloadIntent(
    target_type="Track",
    artist="tricot",
    title="The Danger of Not Mixing",
    resource_hint="https://www.youtube.com/watch?v=WC3VuXS_4xA&list=RDWC3VuXS_4xA",
)

# print(search_candidates(hint_intent))

intent = DownloadIntent(
    target_type="Album",
    artist="tricot",
    title="3",
)

candidates: list[Candidate] = search_candidates(intent)
for c in candidates:
    print(c)
