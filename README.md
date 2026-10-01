# HS Pocket Director

A mobile-first MiniMax H3 directing interface for Wan2GP.

HS Pocket Director is intentionally designed for phone and tablet browsers first: large touch targets, scene-card editing, compact navigation, browser-native file handling, and a configurable dark/glass interface.

## Status

**0.1.0-alpha.1 — UI framework only**

The current scaffold provides the Wan2GP plugin shell and a responsive frontend. H3 generation, project persistence, media upload, and the Wan2GP bridge are intentionally not wired yet.

## Current UI

- Mobile-first responsive layout
- Dark glass/translucent card design
- Bottom navigation for Scenes, Library, Generate, and Settings
- Scene-card placeholders for the future timeline workflow
- Persistent browser-side appearance settings
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

For the initial framework the frontend is a self-contained HTML/CSS/JavaScript application hosted in an iframe by the Wan2GP plugin. This keeps installation trivial and gives the mobile UI its own isolated styling. A backend bridge will be added behind a small API boundary so the frontend can later move to React or another framework without rewriting Wan2GP integration.

## Roadmap

1. Mobile UI shell and settings
2. Scene/project data model
3. Browser-native media upload
4. Wan2GP request/response bridge
5. H3 model/capability discovery
6. Generation and live progress
7. Results, regeneration, and stitching
8. Project save/load and autosave

## License

No license has been selected yet.
