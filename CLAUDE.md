# CLAUDE.md — AI Music Downloader Configuration

## Purpose (WHY)
An automated, single-prompt full-stack utility designed to parse user music requests, locate and download verified audio sources, inject correct metadata from Discogs, and synchronise the resulting files into a desktop Spotify instance via the Spotify Desktop Driver. Built specifically for music absent from Spotify's streaming catalogue.

## Tech Stack & Environment (WHAT)
- **Language:** Python 3.11+
- **Agent Orchestration Engine:** LangGraph (Stateful graph pipeline with SQLite-backed state persistence)
- **LLM Abstraction Layer:** `langchain-core` (Unified Chat Model Interface; default backend: Claude Haiku via Anthropic)
- **API Framework:** FastAPI (Uvicorn server)
- **Downloader Engine:** `yt-dlp` (Python wrapper) + `ffmpeg` (Post-processing handler)
- **Metadata Tagging:** `mutagen` (ID3v2.3/v2.4 for MP3/M4A)
- **Metadata Source:** Discogs API (Track Metadata and Album Artwork; original release preferred)
- **Candidate Scoring:** `rapidfuzz` (Fuzzy string similarity for title match signal)
- **UI Automation:** `pyautogui` & `keyboard` (Spotify Desktop Driver — adds local file Tracks to playlists)
- **Playlist Management:** Spotify Web API (creates/names playlists, sets cover art; cannot add local files)
- **Candidate Search:** `yt-dlp` native `ytsearch` extractor (no separate search API required)

## Operational Guardrails (HOW)

### 1. Architectural Strategy Constraints
- **Stateful Graph Pipeline (LangGraph):** Implement the core engine as a LangGraph state network with SQLite persistence. Define explicit graph nodes: `parse_request` → `search_candidates` → `evaluate_candidates` → `[interrupt: candidate_review]` → `resolve_timestamps` (Compilation Source only) → `[interrupt: chapter_map_prompt]` (all-levels-fail only) → `download` → `inject_metadata` → `sync_spotify`. Use conditional edges to loop back to `search_candidates` when no Candidate clears the **0.7** Confidence Threshold.
- **LLM Usage is Narrowly Scoped:** The LLM is used in exactly two places — (1) `parse_request`: converting a free-form Music Request into a structured Download Intent; (2) Level 2 timestamp extraction: parsing video descriptions and comments into a Chapter Map. Candidate evaluation is fully deterministic (fuzzy string matching + numeric comparisons). Do not introduce LLM calls outside these two nodes.
- **Unified Interface Modularity:** Utilise `langchain-core` models strictly via the unified `BaseChatModel` abstraction. Ensure all LLM blocks hook into the model factory wrapper to support effortless hot-swapping between Anthropic, OpenAI, and Google GenAI backends.
- **Human-in-the-Loop via API Interrupts:** Both `candidate_review` and `chapter_map_prompt` are LangGraph interrupt nodes. The pipeline suspends, serialises Pipeline State to SQLite, and returns a response to the API caller. Execution resumes only when the caller POSTs back a selection or a Chapter Map file.

### 2. Multi-Tiered Album Timestamp Resolution Hierarchy
Applies only to Compilation Sources (single long video containing all Tracks back-to-back). The agent resolves track boundaries using a cascading fallback hierarchy:
- **Level 1 (Deterministic Native):** Inspect the video's `info_json` payload via `yt-dlp --dump-json`. If the native `chapters` array is populated, execute splitting immediately via `--split-chapters`.
- **Level 2 (LLM Page Text Extraction):** If native chapters are missing, pass the raw video description and top 50 comments to the LLM. The LLM converts discovered timestamps (`MM:SS` or `HH:MM:SS`) into a structured Chapter Map.
- **Level 3 (Discogs API):** If the page contains no timestamps, query the Discogs API for the same release used for Track Metadata. Fetch track durations from the release payload and calculate cumulative chapter start times mathematically.
- **All-Levels-Fail (Hard Stop):** If all three levels fail, the pipeline suspends at the `chapter_map_prompt` interrupt and prompts the user to supply a Chapter Map manually. Do not download an unsplit Compilation as a single Track.
- **Chapter Map Injection:** For Level 2 and Level 3 fallbacks, write the resolved Chapter Map as a plaintext file (one `START_TIME Track Name` per line) and feed it to yt-dlp via:
  `--split-chapters --extractor-args "youtube:chapters_file=path/to/chapters.txt"`

### 3. Desktop Sync & File Operations
- **Spotify Desktop Driver:** The Spotify Web API cannot add local files to playlists (it can only reorder or remove tracks already present). Use `pyautogui` to drive the Spotify desktop client directly: locate newly-downloaded Track files by name in the "Local Files" view and add them to the Local Bridge and any Album Playlists specified by Playlist Actions. Never bulk-select all local files — target only the newly-downloaded Tracks.
- **Local Bridge:** Every downloaded Track must be added to the user-configured Local Bridge playlist (path set via environment variable), regardless of any other Playlist Actions. This is a permanent destination, not a staging area. It enables cross-device sync for other devices on the same Spotify account.
- **Spotify Web API Role:** Use the Spotify Web API only for operations it supports: creating new Album Playlists, setting playlist names, and setting playlist cover art. Never attempt to add local file Tracks via the API.
- **Isolation of Concerns:** Keep `pyautogui` mechanics completely isolated from core API routing logic. Wrap the Spotify Desktop Driver inside a background worker task.
- **Audio Format:** Default output is `.mp3`. `.m4a` is supported as an opt-in. Never produce `.wav` or uncompressed containers — Spotify's local file scanner will not index them. All Tracks in an Album must use the same format.
- **Duplicate Handling:** Before downloading a Track, check whether it already exists at the expected Album Subfolder path. If it does, skip the download and report it to the user — do not overwrite.
- **File Layout:** Write all Track files to the Local Files Directory (configured via environment variable), organised as `<Artist>/<Album>/<track_number> - <title>.mp3`.

### 4. Metadata Injection
- **Source:** All Track Metadata (title, artist, album, track number, disc number, release year, genre, album artwork) is fetched from the Discogs API. Do not trust YouTube video tags as a metadata source.
- **Release Selection:** Prefer the original release (earliest year) for the matching artist and title. If the Download Intent specifies a particular edition (e.g. "2011 remaster"), filter the Discogs query accordingly.
- **Tagging:** Use `mutagen` to write ID3v2.3 tags for `.mp3` and MP4 tags for `.m4a`. Embed Album Artwork as cover art in every Track file.

### 5. Development & Tooling Commands
- **Run Unit/Integration Tests:** `pytest`
- **Format Codebase:** `black . && isort .`
- **Lint Codebase:** `flake8 .`
- **Spin up API Environment:** `uvicorn main:app --reload`

### 6. Matt Pocock Skill Interactivity Rules
- **Workflow Pipeline:** Prior to executing any large feature change, enforce sequential execution: `/grill-with-docs` -> `/to-prd` -> `/to-issues` -> `/tdd`.
- **Plan Mode Coordination:** Never skip task boundaries. When using Claude's native Plan Mode (`Shift+Tab`), verify the read-only markdown file against current `.issue` tracking states before attempting code modification.
- **TDD Mode:** Implement functions using strict Red-Green-Refactor logic via `/tdd`. Unit tests must be written and verify failures prior to implementation changes.

### 7. Code Formatting Style Preference
- Use strict Python type hinting across all modules (`from typing import ...`).
- Prefer descriptive variable names over short variables (e.g., use `duration_in_seconds` instead of `dur`).
- Handle file routing exceptions gracefully, always verifying local absolute paths using `pathlib.Path`.

## Agent skills

### Issue tracker

Issues live in GitHub Issues for `asherdrake/music-downloader-agent`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-label vocabulary (needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context repo — one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
