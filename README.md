# HS Pocket Director

A mobile-first MiniMax H3 directing interface for Wan2GP.

HS Pocket Director is intentionally designed for phone and tablet browsers first: large touch targets, scene-card editing, compact navigation, browser-native file handling, and a configurable dark/glass interface.

## Status

**0.1.0-alpha.2 — mobile interaction framework**

The current build provides the Wan2GP plugin shell, responsive frontend, full-screen mobile scene editor, reusable toast notifications, custom modal dialogs, and consistent loading/spinner states. H3 generation, project-file persistence, media upload, and the Wan2GP request/response bridge are intentionally not wired yet.

## Current UI

- Mobile-first responsive layout
- Dark glass/translucent card design
- Bottom navigation for Scenes, Library, Generate, and Settings
- Scene-card timeline with add, edit, save, and delete flows
- Full-screen touch-friendly scene editor
- Scene title, duration, prompt, references, camera, and transition controls
- Start/end image placeholders for the upcoming Library integration
- Reusable toast service for status, success, warnings, and errors
- Toast settings for enable/disable, position, duration, routine status messages, and errors
- Custom application modal for notices and destructive confirmations; no browser `alert()`/`confirm()` UI
- Shared spinner/busy-state pattern for asynchronous actions
- Persistent browser-side project placeholders and UI preferences
- Theme presets, accent color, glass blur, UI density, and motion controls
- No Node/npm build step for the initial framework

## Install for development

Clone this repository into Wan2GP's `plugins` directory:

```text
Wan2GP/
└── plugins/
    └── hs-pocket-director/
        ├── __init__.py
        ├── plugin.py
        ├── plugin_info.json
        └── assets/
            └── index.html
```

Enable **HS Pocket Director** in Wan2GP's Plugins tab and restart Wan2GP.

## Architecture

The frontend is currently a self-contained HTML/CSS/JavaScript application hosted in an iframe by the Wan2GP plugin. This keeps installation trivial and gives the mobile UI isolated styling. A backend bridge will be added behind a small API boundary so the frontend can later move to React or another framework without rewriting Wan2GP integration.

UI services such as toasts, modals, and busy states are centralized so later backend operations can report progress and errors without coupling generation logic to specific controls.

## Roadmap

1. Mobile UI shell and settings — complete
2. Mobile scene editor and notification framework — complete
3. Browser-native media Library and upload
4. Wan2GP request/response bridge
5. H3 model/capability discovery
6. Generation and live progress
7. Results, regeneration, and stitching
8. Project save/load and autosave

## License

No license has been selected yet.
