# HS Pocket Director

A mobile-first MiniMax H3 directing interface for Wan2GP.

HS Pocket Director is designed for phone and tablet browsers first: large touch targets, scene-card editing, compact navigation, browser-native file handling, persistent host media, and a configurable dark/glass interface.

## Status

**0.1.0-alpha.4 — live H3 capability discovery + Generate configuration**

Pocket Director now queries the running Wan2GP installation for MiniMax H3 model definitions and current settings, then builds the mobile Generate screen from what that installation actually exposes. If live discovery is unavailable, the UI remains usable with conservative H3 fallback values and clearly labels them as **Fallback** rather than pretending they came from Wan2GP.

Actual rendering is intentionally the next phase. Alpha.4 ends at configuration and end-to-end plan validation.

## Current features

- Mobile-first responsive layout
- Dark glass/translucent card design
- Bottom navigation for Scenes, Library, Generate, and Settings
- Scene-card timeline with add, edit, save, and delete flows
- Full-screen touch-friendly scene editor
- Scene title, duration, prompt, references, camera, and continuity controls
- Start/end image selection from the persistent media Library
- Reusable toast service for status, success, warnings, and errors
- Configurable toast position, duration, startup notification, routine status, and error messages
- Custom application modal for notices, previews, validation results, and destructive confirmations
- Shared spinner/busy-state pattern for asynchronous actions
- Real browser ↔ Wan2GP request/response bridge
- Browser-native image/video/audio selection on the phone or tablet
- Gradio upload route with a 12 MB inline fallback
- Persistent host-side `workspace/media` storage and `workspace/library.json` index
- SHA-256 content-addressed media IDs and duplicate suppression
- Library filtering, preview, deletion, and large/compact grid modes
- Live H3 model/capability discovery from Wan2GP where available
- Fallback H3 model/grid data when upstream discovery is unavailable
- Live/fallback source badge in Generate
- Dynamic H3 model and resolution selectors
- Timeline duration, target-frame, scene-count, and media-reference stats
- Mobile quality controls for inference steps and seed
- Advanced sliding-window size, overlap, flow-shift, and guidance controls
- Window/overlap selectors generated from the discovered H3 frame grid
- Wan2GP diagnostics for current model, discovery source, reference limits, and frame-grid geometry
- **Validate setup** bridge action that checks/snaps the plan in Python and reports errors/warnings in the custom modal
- Persistent browser-side Generate preferences
- No Node/npm build step for the current framework

## H3 discovery

Pocket Director asks Wan2GP for the model APIs currently exposed by the plugin session and optional globals, including model definitions, current model name/base type, and current model settings when available.

The backend is intentionally defensive because Wan2GP evolves quickly. Model-definition collections can arrive in different shapes, optional APIs are treated as optional, and useful H3 defaults remain available if discovery fails.

The Generate screen always identifies its source:

- **WAN2GP LIVE** — H3 model definitions were discovered from the running installation.
- **FALLBACK** — Pocket Director is using its conservative built-in H3 roster/grid until live discovery succeeds.

The fallback frame geometry currently tracks the established MiniMax H3 defaults used by the existing Director ecosystem: 24 fps, 17-frame grid steps, H3 sliding-window/overlap geometry, and the common portrait/landscape output sizes. These values are a safety net, not a claim about what a particular Wan2GP install has loaded.

## Plan validation

**Validate setup** does not render video. It sends the current mobile configuration back to the Python plugin, where Pocket Director:

1. Resolves the chosen H3 model.
2. Converts the scene-card timeline duration to target frames.
3. Checks the selected output resolution against discovered/known values.
4. Snaps window size and overlap to the model's H3 frame grid when necessary.
5. Calculates the expected sliding-window count.
6. Reports blocking errors and non-blocking warnings through the custom modal/toast system.

This gives the next generation phase a clean, already-validated configuration instead of discovering illegal scheduler values when the GPU job has already started.

## Media storage

Media selected in the browser is not kept only in browser storage. The normal path is:

1. The phone/tablet browser posts the selected file to Wan2GP's Gradio upload endpoint.
2. Gradio provides a temporary upload path on the host.
3. HS Pocket Director adopts that temporary file into `workspace/media`.
4. The file is hashed with SHA-256 and stored under a content-addressed ID.
5. Metadata is recorded in `workspace/library.json`.

The adopt-media bridge accepts only temporary upload locations, and deletion is restricted to files already managed by the plugin's media directory.

If the Gradio upload endpoint is unavailable, files up to 12 MB can fall back to an inline bridge upload. Large video/audio files therefore do not normally travel through JSON/base64.

## Install for development

Clone this repository into Wan2GP's `plugins` directory:

```text
Wan2GP/
└── plugins/
    └── hs-pocket-director/
        ├── __init__.py
        ├── plugin.py
        ├── plugin_info.json
        ├── assets/
        │   └── index.html
        └── workspace/       # created automatically at runtime
```

Enable **HS Pocket Director** in Wan2GP's Plugins tab and restart Wan2GP.

Updates to `plugin.py` require a Wan2GP restart. Frontend-only changes may still be affected by browser/iframe caching, so a hard refresh is useful while developing.

## Architecture

The frontend is currently a self-contained HTML/CSS/JavaScript application hosted in an iframe by the Wan2GP plugin. This keeps installation trivial and gives the mobile UI isolated styling.

The iframe sends small command objects to a parent-page bridge. The parent bridge serializes those requests through hidden Gradio controls into Python and returns structured responses. Media bytes use Gradio's normal upload route whenever possible rather than being forced through the JSON bridge.

UI services such as toasts, modals, and busy states are centralized so model discovery, validation, generation, progress reporting, and future project operations all use the same interaction patterns.

## Roadmap

1. Mobile UI shell and settings — complete
2. Mobile scene editor and notification framework — complete
3. Browser-native media Library and upload — complete
4. Wan2GP request/response bridge — complete
5. H3 model/capability discovery + Generate configuration — complete
6. Real H3 generation + live progress — next
7. Results, regeneration, and stitching
8. Project save/load and autosave

## License

No license has been selected yet.
