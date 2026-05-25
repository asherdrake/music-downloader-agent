# Spotify Desktop Driver for adding local files to playlists

The Spotify Web API cannot add local files to playlists — it can only reorder or remove local file tracks that are already present. This makes any API-only workflow impossible for initial playlist population. The Spotify Desktop Driver (pyautogui) is used instead to add local file tracks from the Spotify desktop client's Local Files view into the Local Bridge and any Album Playlists. The Spotify Web API is still used for operations it does support: creating playlists, setting playlist names, and setting cover art.
