# Stream pipeline progress over SSE; interrupts pause the stream

The chat UI needs live feedback during the pipeline's long-running steps (Candidate Search, download, Spotify sync), which can run for minutes. We expose the LangGraph run as a **Server-Sent Events** stream: node-level progress (and yt-dlp per-track download progress) is pushed to the browser's `EventSource` and appended to the chat as the agent "works."

The interaction protocol: **the stream runs until the pipeline hits a human-in-the-loop interrupt (e.g. Candidate Review) or END.** The interrupt terminates the stream; the UI renders the interrupt prompt; the user submits their answer via a POST; a new stream then resumes the run from the persisted Pipeline State. Answers are always discrete POSTs — only progress is streamed.

Rejected: synchronous request/response (a single `/resume` call would block for minutes with no feedback and risk client/proxy timeouts) and status polling (functional, but lacks the live "agent is responding" feel that justifies a chat UI).
