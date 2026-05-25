## Problem Statement

I have music I want to listen to on Spotify that is not available in Spotify's streaming catalogue. Currently there is no automated way to: find the correct audio source for a track or album, download it with accurate metadata, and get it into Spotify as an organised local file — all from a single natural language request. Doing this manually involves multiple tools, repeated copy-pasting of metadata, and tedious UI interaction with the Spotify desktop client every time a new album is added.

## Solution

A single-prompt FastAPI-backed agent that accepts a free-form Music Request, resolves it into a structured Download Intent, finds and scores YouTube Candidates, downloads the selected audio, injects Discogs metadata, and synchronises the resulting Track files into Spotify via the Spotify Desktop Driver — adding every Track to the user-configured Local Bridge playlist and executing any additional Playlist Actions extracted from the request.

## User Stories

1. As a user, I want to submit a free-form natural language Music Request (e.g. "Download Abbey Road by The Beatles"), so that I do not have to structure my input manually.
2. As a user, I want to include a YouTube URL in my Music Request as a Resource Hint, so that the agent uses that exact source instead of searching.
3. As a user, I want the agent to distinguish between a Track request and an Album request, so that single songs and full albums are handled appropriately.
4. As a user, I want the agent to extract Playlist Actions from my request (e.g. "add to my study music playlist"), so that downloaded Tracks are organised without a separate step.
5. As a user, I want to see a ranked list of Candidates returned before the download begins, so that I can confirm the correct source was found.
6. As a user, I want the pipeline to pause and wait for my Candidate selection via an API call, so that any frontend (CLI, web UI) can drive the confirmation step.
7. As a user, I want the pipeline to automatically retry the search with an adjusted query when no Candidate clears the Confidence Threshold, so that I do not have to re-submit the request manually.
8. As a user, I want downloaded Track files to have complete, accurate metadata (title, artist, album, track number, disc number, release year, genre, album artwork) sourced from Discogs, so that my Spotify library looks correct.
9. As a user, I want the agent to prefer the original release when fetching Discogs metadata, so that I get the canonical track listing by default.
10. As a user, I want to specify a particular edition in my Music Request (e.g. "2011 remaster"), so that the correct Discogs release is used for metadata.
11. As a user, I want Album Artwork embedded in every Track file, so that cover art appears in Spotify and on other devices.
12. As a user, I want downloaded files saved as `.mp3` by default, so that Spotify's local file scanner reliably indexes them.
13. As a user, I want Track files organised into `<Artist>/<Album>/` subfolders inside my configured Local Files Directory, so that my music folder stays clean.
14. As a user, I want the agent to detect Duplicates before downloading and skip-and-report them, so that existing files are never overwritten accidentally.
15. As a user, I want every downloaded Track automatically added to my Local Bridge playlist, so that cross-device sync works without any manual action on other devices.
16. As a user, I want Album Playlists created with the correct album title, cover art, and track order when my request asks for it, so that albums are browsable as a unit in Spotify.
17. As a user, I want Tracks added to an existing named playlist (e.g. "fav songs") when my request specifies it, so that I can build curated playlists from a single prompt.
18. As a user, I want a Compilation Source (single long video) automatically split into individual Track files using native chapter data, so that albums are not delivered as one giant file.
19. As a user, I want the agent to fall back to LLM-based description and comment parsing when a Compilation Source has no native chapters, so that timestamp resolution works even on videos without chapter metadata.
20. As a user, I want the agent to fall back to Discogs track durations to calculate chapter start times when the video has no timestamps at all, so that splitting still works for well-catalogued releases.
21. As a user, I want the pipeline to pause and prompt me to supply a Chapter Map manually when all three timestamp resolution levels fail, so that I can still complete the download rather than having the agent silently produce a broken result.
22. As a user, I want the pipeline state persisted to SQLite at every interrupt point, so that I can resume a paused download after supplying a Candidate selection or Chapter Map without restarting from scratch.
23. As a user, I want a Playlist Source Album (YouTube playlist of individual Track videos) handled the same way as a Compilation Source from the output perspective, so that the end result is always a correctly-tagged list of Track files.
24. As a user, I want the agent to configure the Local Files Directory and Local Bridge playlist ID via environment variables, so that I can point it at my Spotify-watched folder without changing code.
25. As a user, I want the LLM backend to be swappable between Anthropic, OpenAI, and Google GenAI without touching the pipeline, so that I can switch providers if needed.

## Implementation Decisions

### Modules

**Request Parser**
Wraps the LLM call (Claude Haiku default via `BaseChatModel`) that converts a Music Request string into a typed Download Intent. The Download Intent carries: `target_type` (Track | Album), `artist`, `title`, `resource_hint` (optional URL), and `playlist_actions` (list, may be empty). This is one of exactly two places in the system where an LLM is called.

**Candidate Search**
Uses yt-dlp's native `ytsearch:N` extractor to query YouTube and return a list of Candidates. Each Candidate carries: `url`, `title`, `duration_seconds`, `channel_name`, `view_count`. When a Resource Hint is present in the Download Intent, search is bypassed entirely and the hint URL is returned as the sole Candidate.

**Candidate Evaluator**
Purely deterministic — no LLM. Scores each Candidate against the Download Intent using three weighted signals:
- **Title match** (0-1): fuzzy string similarity via `rapidfuzz` between the video title and `{artist} {title}`.
- **Duration match** (0-1): proximity of video duration to the expected runtime (sourced from Discogs for Albums; estimated for Tracks).
- **Source quality** (0-1): heuristic channel scoring (official artist/label channels score highest; generic re-upload channels score lowest).

Candidates scoring below **0.7** are rejected. The evaluator also filters out Candidates whose target type does not match the Download Intent. Returns a ranked list of `ScoredCandidate` objects.

**Discogs Client**
Single shared module used by both the Metadata Injector and the Timestamp Resolver (Level 3). Queries the Discogs API for a release matching artist + title, preferring the earliest release year. Returns a typed `Release` object containing: track list (with durations), artist, album title, year, genre, and artwork URL. Accepts an optional `edition` hint from the Download Intent to narrow the query.

**Timestamp Resolver**
Applies only to Compilation Sources. Implements the three-level fallback hierarchy:
- Level 1: inspects yt-dlp `info_json` for a native `chapters` array.
- Level 2: passes video description + top 50 comments to the LLM (second of two LLM use cases). The LLM returns structured `{start_time, track_name}` entries.
- Level 3: calls the Discogs Client for the matched release, reads `duration` per track, accumulates cumulative start times.
- Hard stop: if all levels fail, signals a `chapter_map_prompt` interrupt. Accepts a manually-supplied Chapter Map on resume.

Writes the resolved Chapter Map as a plaintext file and returns its path for yt-dlp injection.

**Downloader**
Calls yt-dlp + ffmpeg to download the selected Candidate. For a Track intent or a Playlist Source Album, downloads individual files. For a Compilation Source Album, injects the Chapter Map and uses `--split-chapters` to produce individual Track files. Outputs all files to the correct Album Subfolder inside the Local Files Directory. Default format: `.mp3`.

**Metadata Injector**
Calls the Discogs Client to fetch the Release for the Download Intent, then uses `mutagen` to write ID3v2.3 tags (`.mp3`) or MP4 tags (`.m4a`) to every Track file. Embeds Album Artwork as cover art. Uses track order from the Discogs release to set track numbers.

**Playlist Manager**
Wraps the Spotify Web API. Responsible for: creating new Album Playlists (name, cover art) and adding Tracks to existing named playlists when Playlist Actions request it. Does not attempt to add local file Tracks via the API — that is the Spotify Desktop Driver's responsibility.

**Spotify Desktop Driver**
Uses `pyautogui` to drive the Spotify desktop client: navigates to the Local Files view, locates newly-downloaded Track files by name, and adds them to the Local Bridge and any Album Playlists specified by Playlist Actions. Targets only the newly-downloaded files — never bulk-selects all local files. Runs in a background worker task, isolated from API routing logic.

**Pipeline Orchestrator**
LangGraph stateful graph wiring all nodes in sequence: `parse_request` → `search_candidates` → `evaluate_candidates` → `[interrupt: candidate_review]` → `resolve_timestamps` → `[interrupt: chapter_map_prompt]` → `download` → `inject_metadata` → `sync_spotify`. Conditional edge loops `search_candidates` when no Candidate clears 0.7. Pipeline State is persisted to SQLite at every interrupt.

**FastAPI Application**
Two endpoints:
- `POST /download` — accepts a Music Request string, starts a Pipeline run, returns either a `candidate_review` payload (ranked Candidates) or a completed result.
- `POST /resume` — accepts a thread ID + user selection (Candidate URL or Chapter Map), resumes the suspended Pipeline run.

### Architectural decisions
- Serper.dev and Tavily are not used. See ADR-0003.
- Spotify Web API cannot add local files to playlists. See ADR-0002.
- Discogs is the metadata source, not Spotify. See ADR-0001.
- Local Bridge is a permanent universal playlist, not a staging area. See ADR-0004.
- Candidate evaluation is fully deterministic — no LLM scoring.
- LLM calls are limited to exactly two nodes: `parse_request` and Level 2 of `resolve_timestamps`.

## Testing Decisions

**What makes a good test**
Tests should verify the external behaviour of a module through its public interface, not its internal implementation. A good test asks "does this module produce the right output for a given input?" — not "does it call this internal method?" Mocks are used only at system boundaries (LLM APIs, HTTP APIs, yt-dlp subprocess) to keep tests fast and deterministic.

**Modules with tests**

- **Request Parser** — mock the `BaseChatModel` call. Assert that a variety of Music Request strings produce correctly typed Download Intents: plain track requests, album requests, requests with embedded Resource Hints, requests with Playlist Actions, and ambiguous/abbreviated titles.

- **Candidate Evaluator** — no mocks needed (fully deterministic). Test scoring edge cases: exact title match, partial match, mismatched duration, official vs. re-upload channel, target type mismatch rejection, threshold boundary behaviour (exactly 0.7, just below 0.7).

- **Discogs Client** — mock HTTP responses. Test: correct release selection (earliest year preferred), edition filtering when hint is present, graceful handling of no-results and API errors, correct mapping of Discogs payload fields to the `Release` type.

- **Timestamp Resolver** — mock yt-dlp `info_json` responses, mock the `BaseChatModel` call for Level 2, mock the Discogs Client for Level 3. Test each level independently and the fallback cascade: Level 1 success short-circuits; Level 2 is called only when Level 1 fails; Level 3 is called only when Level 2 finds nothing; all-levels-fail triggers the interrupt signal.

- **Metadata Injector** — mock the Discogs Client; use real `mutagen` on temporary `.mp3` files. Assert that each ID3 field is written correctly, that Album Artwork bytes are embedded, and that track numbers match the Discogs release order.

## Out of Scope

- A graphical or web frontend — the API surface is the integration point; UI is a future concern.
- Multi-user or authenticated API access — this is a single-user local tool.
- Support for audio sources other than YouTube (e.g. SoundCloud, Bandcamp).
- Editing or re-tagging files that have already been downloaded and tagged.
- Automatic removal of Tracks from Spotify playlists.
- Scheduling or batch queue management.
- Streaming or partial download progress reporting.

## Further Notes

- `CONTEXT.md` at the repo root is the authoritative domain glossary. All code, tests, and issue titles should use the vocabulary defined there.
- Four ADRs are already recorded in `docs/adr/` covering: Discogs as metadata source, Spotify Desktop Driver for playlist population, removal of Serper/Tavily, and the Local Bridge as permanent universal playlist.
- The Spotify Desktop Driver is inherently environment-dependent (requires the Spotify desktop client to be running and focused). It should be integration/manually tested only — not unit tested.
- The Confidence Threshold of 0.7 is set in configuration, not hardcoded, so it can be tuned without a code change.
