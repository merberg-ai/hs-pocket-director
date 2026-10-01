# HS Pocket Director

A mobile-first MiniMax H3 directing interface for Wan2GP.

HS Pocket Director is intentionally designed for phone and tablet browsers first: large touch targets, scene-card editing, compact navigation, browser-native file handling, and a configurable dark/glass interface.

## Status

**0.1.0-alpha.3 — persistent media Library + Wan2GP bridge**

The current build now has a real request/response bridge between the isolated mobile iframe UI and the Wan2GP Python plugin, plus a persistent host-side media Library. Images, video, and audio are selected with the browser file picker on the phone/tablet, uploaded to Wan2GP, copied into the plugin workspace, indexed, and restored when the UI reconnects.

H3 model discovery and generation are intentionally the next phase.

## Current UI

- Mobile-first responsive layout
- Dark glass/translucent card design
- Bottom navigation for Scenes, Library, Generate, and Settings
- Scene-card timeline with add, edit, save, and delete flows
- Full-screen touch-friendly scene editor
- Scene title, duration, prompt, references, camera, and continuity controls
- Start/end image selection from the persistent media Library
- Reusable toast service for status, success, warnings, and errors
- Toast settings for enable/disable, position, duration, startup notifications, routine status messages, and errors
- Custom application modal for notices, media previews, and destructive confirmations; no browser `alert()`/`confirm()` UI
- Shared spinner/busy-state pattern for asynchronous actions
- Real browser ↔ Wan2GP request/response bridge
- Bridge connection status in the top bar
- Browser-native image/video/audio selection on the phone or tablet
- Direct upload through Gradio's upload endpoint with a 12 MB inline fallback
- Persistent host-side `workspace/media` storage and `workspace/library.json` index
- Content-addressed SHA-256 media IDs so uploading the same file again does not create duplicate copies
- Library filters for images, video, and audio
- Large/compact media-grid preference
- Media detail/preview modal and host-side deletion
- Scene links are cleared if referenced media is deleted
- Persistent browser-side scene placeholders and UI preferences
- Theme presets, accent color, glass blur, UI density, and motion controls
- No Node/npm build step for the current framework

## Media storage

Media selected in the browser is not kept only in browser storage. The normal path is:

1. The phone/tablet browser posts the selected file to Wan2GP's Gradio upload endpoint.
2. Gradio provides a temporary upload path on the host.
3. HS Pocket Director immediately adopts that temporary file into `workspace/media`.
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

Because the Python plugin is part of the bridge, updates to `plugin.py` require a Wan2GP restart.

## Architecture

The frontend is currently a self-contained HTML/CSS/JavaScript application hosted in an iframe by the Wan2GP plugin. This keeps installation trivial and gives the mobile UI isolated styling.

The iframe sends small command objects to a parent-page bridge. The parent bridge serializes those requests through hidden Gradio controls into Python and sends structured responses back to the iframe. Media bytes use Gradio's normal upload route whenever possible rather than being forced through the JSON bridge.

UI services such as toasts, modals, and busy states are centralized so later model discovery, generation, progress reporting, and project operations can use the same interaction patterns without coupling backend logic to individual controls.

## Roadmap

1. Mobile UI shell and settings — complete
2. Mobile scene editor and notification framework — complete
3. Browser-native media Library and upload — complete
4. Wan2GP request/response bridge — complete
5. H3 model/capability discovery — next
6. Generation and live progress
7. Results, regeneration, and stitching
8. Project save/load and autosave

## License

No license has been selected yet.
