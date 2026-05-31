"""
In Powershell:
Terminal 1: `uvicorn main:app --reload
Terminal 2:
`
Invoke-RestMethod -Method POST `
  -Uri "http://localhost:8000/download" `
  -ContentType "application/json" `
  -Body '{"request": "Download the album 3 by tricot, create a playlist for it, set the image to the album art, and add the correct tracks in the album order."}'
`

Most recent output:
target_type      : Album
artist           : tricot
title            : 3
resource_hint    :
edition          :
playlist_actions : {@{playlist_name=3; create_if_missing=True; set_cover_image=album_art; track_order=album_order}}
"""
