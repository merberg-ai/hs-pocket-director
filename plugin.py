"""HS Pocket Director — mobile-first MiniMax H3 directing UI for Wan2GP.

The browser UI is intentionally isolated from Wan2GP internals. Generation,
project files, media uploads and model discovery will be added behind a small
request/response bridge rather than coupled directly to frontend components.
"""

from __future__ import annotations

import base64
from pathlib import Path

import gradio as gr

from shared.utils.plugins import WAN2GPPlugin

PLUGIN_ID = "hs_pocket_director"
PLUGIN_NAME = "HS Pocket Director"
PLUGIN_VERSION = "0.1.0-alpha.2"

PLUGIN_DIR = Path(__file__).resolve().parent
ASSETS_DIR = PLUGIN_DIR / "assets"
INDEX_FILE = ASSETS_DIR / "index.html"


class HSPocketDirectorPlugin(WAN2GPPlugin):
    """Wan2GP application plugin hosting the Pocket Director mobile UI."""

    def __init__(self):
        super().__init__()
        self.name = PLUGIN_NAME
        self.version = PLUGIN_VERSION
        self.description = "Mobile-first MiniMax H3 directing interface for Wan2GP."

    def setup_ui(self):
        self.add_tab(
            tab_id=PLUGIN_ID,
            label=PLUGIN_NAME,
            component_constructor=self._build_ui,
        )

    def _build_ui(self, api_session):
        """Build the plugin tab inside Wan2GP's plugin UI context.

        Keeping the positional api_session argument is intentional. Wan2GP uses
        it to create the plugin API session and wrap callbacks correctly. The
        current UI is still browser-local; backend calls are the next phase.
        """
        del api_session

        host_css = (
            "<style>"
            "#hspd-host,#hspd-host>div{padding:0!important;margin:0!important;}"
            "#hspd-host iframe{display:block;}"
            "</style>"
        )

        with gr.Column(elem_id="hspd-plugin"):
            gr.HTML(
                value=host_css + self._iframe_html(),
                elem_id="hspd-host",
                min_height=None,
            )

    def _iframe_html(self) -> str:
        if not INDEX_FILE.exists():
            return (
                "<div style='padding:20px;color:#fca5a5;background:#111827;'>"
                "HS Pocket Director could not find assets/index.html."
                "</div>"
            )

        src = self._served_asset_url(INDEX_FILE)
        if not src:
            encoded = base64.b64encode(INDEX_FILE.read_bytes()).decode("ascii")
            src = f"data:text/html;base64,{encoded}"

        return (
            "<iframe id='hspd-frame' title='HS Pocket Director' "
            "allow='clipboard-write; fullscreen' "
            "sandbox='allow-scripts allow-same-origin allow-forms allow-downloads allow-modals' "
            "style='width:100%;height:calc(100dvh - 150px);min-height:560px;"
            "border:0;border-radius:12px;background:#070a0f;' "
            f"src='{src}'></iframe>"
        )

    @staticmethod
    def _served_asset_url(path: Path) -> str:
        """Use Gradio's static file route when available."""
        try:
            set_static_paths = getattr(gr, "set_static_paths", None)
            if not callable(set_static_paths):
                return ""
            set_static_paths(paths=[str(ASSETS_DIR)])
            normalized = str(path).replace("\\", "/")
            return "/gradio_api/file=" + normalized
        except Exception:
            return ""
