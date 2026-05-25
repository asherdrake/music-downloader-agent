# Discogs API as the metadata source

The Spotify Web API is not used for Track Metadata despite Spotify being the sync target. This agent exists specifically to acquire music that is *absent from Spotify* — so Spotify cannot reliably return metadata for it. The Discogs API is used instead, as it has broad coverage of physical releases, obscure recordings, and non-streaming catalogue. YouTube tags are not trusted as a metadata source because they are frequently incomplete or wrong.
