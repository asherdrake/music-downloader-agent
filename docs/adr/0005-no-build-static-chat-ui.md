# Chat UI as a no-build static page served by FastAPI

We need a chat-style UI to drive the download pipeline's human-in-the-loop interrupts (Candidate Review and others). We chose a single static page served by the existing FastAPI app via `StaticFiles`, built with **Preact + htm loaded from a CDN** — components and reactivity with **no Node toolchain and no build step**. The page talks to the existing JSON API; drag-and-drop, thumbnails, and multipart upload use standard browser APIs.

We rejected a React/Svelte + Vite SPA: it adds npm/Node and a bundling step to a single-user, local-only, pure-Python project for no proportional benefit at this scope. The JSON API is the stable contract, so we can graduate to a bundled SPA later without backend rework. This deliberately does not foreclose the future multi-Workflow generalization — that lives behind the API, not the frontend.
