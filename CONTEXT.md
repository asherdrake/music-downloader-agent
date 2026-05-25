# Music Downloader Agent

An automated utility that accepts a user's music request, locates the best matching audio source, downloads it with correct metadata, and organises the resulting files into a desktop Spotify instance as Album Playlists. Designed specifically for music absent from Spotify's streaming catalogue.

## Language

### Input & Parsing

**Music Request**:
The free-form natural language string a user submits to initiate a download (e.g. "Download Abbey Road by The Beatles"). May optionally contain one or more Resource Hints.
_Avoid_: Query, prompt, command, instruction

**Resource Hint**:
A concrete URL embedded in a Music Request that the agent must use directly as the audio source, bypassing the search phase entirely (e.g. a YouTube watch link).
_Avoid_: Link, URL, override

**Download Intent**:
The structured output produced by the LLM parser from a Music Request. Contains: target type (Track or Album), artist, title, any extracted Resource Hint, and zero or more Playlist Actions. This is what the rest of the pipeline operates on. Parsing is one of two places in the system where an LLM is genuinely required (the other being Level 2 timestamp extraction).
_Avoid_: Parsed query, structured request, intent object

**Playlist Action**:
An optional instruction extracted from the Music Request that specifies additional playlist organisation beyond the Local Bridge. Examples: "create an Album Playlist with correct metadata and track order", "add to existing playlist 'fav songs'". A Download Intent may contain zero or more Playlist Actions. When none are present, Tracks are added only to the Local Bridge.
_Avoid_: Playlist instruction, organisation step, playlist task

### Core Entities

**Track**:
A single audio file representing one song. The atomic unit of the system. Every download ultimately produces one or more Track files.
_Avoid_: Song, file, audio file

**Album**:
A collection of Tracks sharing common metadata (artist, album title, year, artwork). An Album always resolves to a list of Track files regardless of its source format. The source may be a Playlist Source or a Compilation Source.
_Avoid_: Collection, folder, release

**Playlist Source**:
An Album sourced from a set of individual Track videos (e.g. a YouTube playlist). No splitting is required — each video maps directly to one Track.
_Avoid_: Playlist, video playlist

**Compilation Source**:
An Album sourced from a single long video or audio file containing all Tracks back-to-back. Requires timestamp-based splitting to produce individual Track files. The timestamp resolution hierarchy in the pipeline only applies to this source format.
_Avoid_: Compilation, full album video, single-video album

**Chapter Map**:
A plaintext file with one `START_TIME Track Name` entry per line that defines the track boundaries of a Compilation Source. Used by yt-dlp's `--split-chapters` flag to split the file into individual Tracks. Generated automatically by Level 2 (LLM extraction — the second of two genuine LLM use cases in the system) or Level 3 (Discogs API), or supplied manually by the user when all three levels fail.
_Avoid_: Timestamps file, chapter file, cue sheet

### Spotify Sync

**Local Bridge**:
The single permanent Spotify playlist configured by the user (via environment variable) that receives every downloaded Track — singles and album tracks alike. Always populated regardless of whether any Playlist Actions are present in the Download Intent. Serves two purposes: makes local file Tracks visible to Spotify, and enables cross-device sync (other devices on the same account can download the Track for offline listening). The user creates this playlist manually; the agent only adds Tracks to it.
_Avoid_: Staging playlist, bridge playlist, sync playlist

**Album Playlist**:
An optional Spotify user playlist created when a Playlist Action in the Download Intent requests album-level organisation. Has the album title as its name, Album Artwork as its cover image, and Tracks in the correct order. Not an official Spotify album — purely a user-managed playlist that mirrors album structure. Created and named via the Spotify Web API; populated with local file Tracks via the Spotify Desktop Driver (the API cannot add local files to playlists).
_Avoid_: Playlist, Spotify album, local album

**Spotify Desktop Driver**:
The UI automation sequence used to add local file Tracks from Spotify's "Local Files" view into the Local Bridge and any Album Playlists specified by Playlist Actions. Targets only newly-downloaded Track files by name — does not bulk-select all local files. Executed via pyautogui on the Spotify desktop client. Required because the Spotify Web API cannot add local files to playlists — only reorder or remove tracks already present. See ADR-0002.
_Avoid_: Clipboard Bridge, macro, automation script, sync bridge

### Audio Format

**Audio Format**:
The container and codec used for all downloaded Track files. Default is `.mp3`. `.m4a` (AAC) is supported but not the default. Mixed-format Albums are not permitted — all Tracks in an Album use the same format.
_Avoid_: File format, codec, container

**Duplicate**:
A Track file that already exists in the Local Files Directory at the expected Album Subfolder path. When detected before download, the agent skips the download for that Track and reports it to the user — it does not overwrite.
_Avoid_: Existing file, conflict

### File Output

**Local Files Directory**:
The filesystem directory Spotify is configured to scan for local audio files. All downloaded Track files are written here, organised into per-Album subfolders structured as `<Artist>/<Album>/<track_number> - <title>.mp3`. Path is configurable via an environment variable.
_Avoid_: Output folder, download directory, music folder

**Album Subfolder**:
A directory inside the Local Files Directory named `<Artist>/<Album>/` that contains all Track files for a single Album. Created by the agent on download.
_Avoid_: Album folder, output directory

### Metadata

**Track Metadata**:
The set of structured tags injected into a Track file after download: track title, artist, album title, track number, disc number, release year, genre, and album artwork. Sourced exclusively from the Discogs API — not from YouTube tags or the Spotify API. The Discogs release used is the original release (earliest year) unless the Download Intent specifies a particular edition.
_Avoid_: ID3 tags, tags, file metadata

**Album Artwork**:
The high-resolution cover image fetched from Discogs and embedded into every Track file in an Album as part of metadata injection.
_Avoid_: Cover art, thumbnail, artwork

## Example dialogue

> **Dev:** The user typed "download that Boards of Canada album with the creepy samples and put it in my study music playlist." What does the parser produce?
>
> **Domain expert:** A Download Intent with target type Album, artist "Boards of Canada", title resolved by the LLM to something like "Geogaddi" (the most likely match), and one Playlist Action: "add to existing playlist 'study music'." No Resource Hint since no URL was provided.
>
> **Dev:** What happens at Candidate Review if all returned Candidates score below 0.7?
>
> **Domain expert:** The pipeline loops back to search with an adjusted query — not a hard stop. Hard stop only happens when timestamps can't be resolved for a Compilation Source.
>
> **Dev:** After download, where do the Track files go?
>
> **Domain expert:** Into the Album Subfolder inside the Local Files Directory. Then the Spotify Desktop Driver adds them to the Local Bridge first, and also to the "study music" playlist per the Playlist Action. The Spotify Web API creates no playlists in this case — "study music" already exists.

### Pipeline

**Pipeline**:
The LangGraph stateful graph that orchestrates the full download workflow. Nodes: `parse_request` → `search_candidates` → `evaluate_candidates` → `[interrupt: candidate_review]` → `resolve_timestamps` (Compilation Source only) → `[interrupt: chapter_map_prompt]` (failure only) → `download` → `inject_metadata` → `sync_spotify`. Conditional edges loop back to `search_candidates` when no Candidate clears the Confidence Threshold.
_Avoid_: Workflow, agent, graph

**Pipeline State**:
The serialised LangGraph state persisted to SQLite between interrupt points. Allows the Pipeline to suspend at a Candidate Review or Chapter Map prompt and resume cleanly without re-running prior nodes.
_Avoid_: Graph state, session, context

### Search & Evaluation

**Candidate Search**:
The phase that produces a ranked list of Candidates for a given Download Intent. Uses yt-dlp's native `ytsearch` extractor to query YouTube directly — returns structured video metadata (title, duration, channel) without a separate search API.
_Avoid_: Search phase, YouTube search, video search

**Timestamp Search**:
A direct Discogs API call executed during the Level 3 timestamp fallback for a Compilation Source. Fetches track durations from the same Discogs release used for Track Metadata and calculates cumulative chapter start times mathematically. Distinct from Candidate Search — it resolves chapter boundaries, not Candidates.
_Avoid_: External search, tracklist search, web search

**Candidate**:
A search result (typically a YouTube video) returned by the search phase as a potential match for a Download Intent. Candidates are not pre-filtered — the Evaluator is responsible for rejecting those that don't match the target type or fall below the Confidence Threshold.
_Avoid_: Result, match, hit, video

**Confidence Score**:
A composite 0–1 score assigned to each Candidate by deterministic computation — no LLM involved. Derived from three weighted signals: title match (fuzzy string similarity between video title and artist + track/album name), duration match (video length vs. expected runtime), and source quality (official channel vs. re-upload heuristics). Candidates scoring below the Confidence Threshold are rejected.
_Avoid_: Score, rating, quality score

**Confidence Threshold**:
The minimum Confidence Score a Candidate must reach to be considered viable. Set at **0.7**. Candidates below this threshold cause the pipeline to loop back and adjust the search. Note: supersedes the 0.85 value stated in CLAUDE.md.
_Avoid_: Score cutoff, minimum score

**Candidate Review**:
The step after evaluation where all viable Candidates (those at or above the Confidence Threshold) are surfaced to the user via an API response. The pipeline pauses until the caller POSTs back a selection. This decouples the confirmation interaction from the core pipeline, allowing any frontend (CLI, web UI, etc.) to drive it.
_Avoid_: Source confirmation, result preview, user approval
