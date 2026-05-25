# Drop Serper.dev and Tavily from the architecture

Serper.dev and Tavily were originally included as the Level 3 timestamp fallback search layer (querying Discogs/Wikipedia/Genius for tracklist data). Since the Discogs API is already used for Track Metadata and returns track durations directly in the release payload, Level 3 can call the Discogs API directly — making Serper.dev and Tavily redundant. Both are removed to reduce external API dependencies and simplify the stack.
