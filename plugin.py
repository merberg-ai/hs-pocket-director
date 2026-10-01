"""HS Pocket Director — mobile-first MiniMax H3 directing UI for Wan2GP.

The browser UI stays isolated from Wan2GP internals. This module owns the small
request/response bridge plus durable media storage; later model discovery and
generation will be added behind the same bridge boundary.
"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import shutil
import tempfile
import time
from pathlib import Path

import gradio as gr

from shared.utils.plugins import WAN2GPPlugin

PLUGIN_ID = "hs_pocket_director"
PLUGIN_NAME = "HS Pocket Director"
PLUGIN_VERSION = "0.1.0-alpha.3"
LOG_PREFIX = "[HS-Pocket]"

PLUGIN_DIR = Path(__file__).resolve().parent
ASSETS_DIR = PLUGIN_DIR / "assets"
INDEX_FILE = ASSETS_DIR / "index.html"
WORKSPACE = PLUGIN_DIR / "workspace"
MEDIA_DIR = WORKSPACE / "media"
LIBRARY_JSON = WORKSPACE / "library.json"

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".avif", ".bmp", ".gif", ".tiff"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
AUDIO_EXTS = {".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aac"}
SUPPORTED_EXTS = IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS
MAX_MEDIA_BYTES = 2 * 1024 * 1024 * 1024
INLINE_UPLOAD_LIMIT = 12 * 1024 * 1024


def trace(message: str) -> None:
    try:
        print(f"{LOG_PREFIX} {message}", flush=True)
    except Exception:
        pass


def _atomic_write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _safe_ext(name: str) -> str:
    ext = Path(name or "").suffix.lower()
    return ext if ext in SUPPORTED_EXTS else ""


def _media_kind(name: str, mime: str = "") -> str:
    mime = str(mime or "").lower()
    ext = Path(name or "").suffix.lower()
    if mime.startswith("image/") or ext in IMAGE_EXTS:
        return "image"
    if mime.startswith("video/") or ext in VIDEO_EXTS:
        return "video"
    if mime.startswith("audio/") or ext in AUDIO_EXTS:
        return "audio"
    return "other"


# Runs in the Wan2GP PARENT page. The mobile UI itself lives in an iframe.
# Requests are serialized deliberately: a single hidden Gradio textbox/button
# pair is simple and reliable, and library operations are not latency-critical.
_BRIDGE_JS = r"""
(function () {
  if (window.__HSPD_BRIDGE__) return;
  window.__HSPD_BRIDGE__ = true;

  const FRAME_TAG = "hspd_frame";
  const PARENT_TAG = "hspd_parent";
  const state = { queue: [], busy: false, timer: null, lastResp: "" };

  function roots() {
    const out = [document];
    try {
      const app = document.querySelector("gradio-app");
      if (app && app.shadowRoot) out.push(app.shadowRoot);
    } catch (_) {}
    return out;
  }

  function query(selector) {
    for (const root of roots()) {
      try {
        const found = root.querySelector(selector);
        if (found) return found;
      } catch (_) {}
    }
    return null;
  }

  function host(id) { return query("#" + id); }

  function input(id) {
    const h = host(id);
    if (!h) return null;
    if (h.matches && h.matches("textarea,input")) return h;
    return h.querySelector("textarea,input[type='text'],input:not([type='hidden'])");
  }

  function setValue(id, value) {
    const el = input(id);
    if (!el) return false;
    try {
      const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
      setter.call(el, value);
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    } catch (err) {
      console.error("[HS-Pocket] could not set", id, err);
      return false;
    }
  }

  function click(id) {
    const h = host(id);
    if (!h) return false;
    const button = h.matches && h.matches("button") ? h : h.querySelector("button");
    if (!button) return false;
    button.click();
    return true;
  }

  function frameWindow() {
    const frame = query("#hspd-frame");
    return frame && frame.contentWindow ? frame.contentWindow : null;
  }

  function toFrame(msg) {
    const target = frameWindow();
    if (!target) return;
    try { target.postMessage(Object.assign({ source: PARENT_TAG }, msg), "*"); } catch (_) {}
  }

  function release() {
    if (state.timer) clearTimeout(state.timer);
    state.timer = null;
    state.busy = false;
    setTimeout(pump, 20);
  }

  function pump() {
    if (state.busy || !state.queue.length) return;
    const message = state.queue.shift();
    state.busy = true;
    if (!setValue("hspd-req", JSON.stringify(message))) {
      toFrame({ id: message.id, error: "Pocket Director bridge request control is missing." });
      release();
      return;
    }
    const ttl = Math.max(5000, Math.min(Number(message.ttl || 120000), 900000));
    state.timer = setTimeout(function () {
      toFrame({ id: message.id, error: "Pocket Director bridge timed out." });
      release();
    }, ttl + 1500);
    setTimeout(function () {
      if (!click("hspd-go")) {
        toFrame({ id: message.id, error: "Pocket Director bridge trigger is missing." });
        release();
      }
    }, 120);
  }

  window.addEventListener("message", function (event) {
    const message = event.data;
    if (!message || typeof message !== "object" || message.source !== FRAME_TAG) return;
    state.queue.push(message);
    pump();
  });

  setInterval(function () {
    const el = input("hspd-resp");
    const value = el ? String(el.value || "") : "";
    if (!value || value === state.lastResp) return;
    state.lastResp = value;
    try { toFrame(JSON.parse(value)); }
    catch (err) { toFrame({ error: "Bad bridge response: " + String(err) }); }
    release();
  }, 100);

  console.log("[HS-Pocket] parent bridge installed");
})();
"""


class HSPocketDirectorPlugin(WAN2GPPlugin):
    """Wan2GP application plugin hosting the Pocket Director mobile UI."""

    def __init__(self):
        super().__init__()
        self.name = PLUGIN_NAME
        self.version = PLUGIN_VERSION
        self.description = "Mobile-first MiniMax H3 directing interface for Wan2GP."
        WORKSPACE.mkdir(parents=True, exist_ok=True)
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        self._library = self._load_library()

    def setup_ui(self):
        self.add_custom_js(_BRIDGE_JS)
        self.add_tab(
            tab_id=PLUGIN_ID,
            label=PLUGIN_NAME,
            component_constructor=self._build_ui,
        )
        trace(f"registered {PLUGIN_NAME} {PLUGIN_VERSION}")

    def _build_ui(self, api_session):
        """Build the plugin tab inside Wan2GP's plugin UI context."""
        del api_session
        host_css = (
            "<style>"
            "#hspd-host,#hspd-host>div{padding:0!important;margin:0!important;}"
            "#hspd-host iframe{display:block;}"
            "</style>"
        )

        with gr.Column(elem_id="hspd-plugin"):
            gr.HTML(value=host_css + self._iframe_html(), elem_id="hspd-host", min_height=None)
            req = gr.Textbox(label="req", visible=False, elem_id="hspd-req")
            resp = gr.Textbox(label="resp", visible=False, elem_id="hspd-resp")
            go = gr.Button("go", visible=False, elem_id="hspd-go")
            try:
                go.click(
                    fn=self._on_bridge,
                    inputs=[req],
                    outputs=[resp],
                    show_progress="hidden",
                    trigger_mode="multiple",
                )
            except TypeError:
                go.click(fn=self._on_bridge, inputs=[req], outputs=[resp], show_progress="hidden")

    # ------------------------------------------------------------------
    # Request/response bridge
    # ------------------------------------------------------------------
    def _on_bridge(self, raw):
        try:
            message = json.loads(raw or "{}")
            cmd = str(message.get("cmd") or "")
            data = message.get("data") or {}
            message_id = message.get("id")
            result = self._dispatch(cmd, data)
            return json.dumps({"cmd": cmd + ":ok", "id": message_id, "data": result})
        except Exception as exc:
            trace(f"bridge error: {exc}")
            try:
                message_id = message.get("id")
                cmd = str(message.get("cmd") or "error")
            except Exception:
                message_id = None
                cmd = "error"
            return json.dumps({"cmd": cmd + ":error", "id": message_id, "error": str(exc)})

    def _dispatch(self, cmd: str, data: dict):
        if cmd == "ping":
            return {
                "ok": True,
                "version": PLUGIN_VERSION,
                "workspace": str(WORKSPACE),
                "mediaCount": len(self._library),
            }
        if cmd == "list_media":
            return self._list_media()
        if cmd == "adopt_media":
            return self._adopt_media(data)
        if cmd == "upload_media":
            return self._upload_media(data)
        if cmd == "delete_media":
            return self._delete_media(data)
        raise ValueError(f"unknown bridge command: {cmd}")

    # ------------------------------------------------------------------
    # Media library
    # ------------------------------------------------------------------
    def _load_library(self) -> dict:
        if LIBRARY_JSON.exists():
            try:
                raw = json.loads(LIBRARY_JSON.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    return raw
            except Exception as exc:
                trace(f"library.json unreadable: {exc}")
        rebuilt = {}
        try:
            for path in MEDIA_DIR.iterdir():
                if not path.is_file() or path.name.startswith("."):
                    continue
                kind = _media_kind(path.name)
                if kind == "other":
                    continue
                media_id = path.stem
                rebuilt[media_id] = {
                    "mediaId": media_id,
                    "name": path.name,
                    "file": path.name,
                    "kind": kind,
                    "mime": mimetypes.guess_type(path.name)[0] or "",
                    "size": path.stat().st_size,
                    "createdAt": path.stat().st_mtime,
                }
        except Exception as exc:
            trace(f"library rebuild failed: {exc}")
        if rebuilt:
            try:
                _atomic_write_json(LIBRARY_JSON, rebuilt)
            except Exception:
                pass
        return rebuilt

    def _save_library(self) -> None:
        _atomic_write_json(LIBRARY_JSON, self._library)

    def _file_base(self) -> str:
        try:
            set_static_paths = getattr(gr, "set_static_paths", None)
            if callable(set_static_paths):
                set_static_paths(paths=[str(ASSETS_DIR), str(MEDIA_DIR)])
                return "/gradio_api/file="
        except Exception as exc:
            trace(f"static media registration failed: {exc}")
        return ""

    def _public_media(self, item: dict) -> dict:
        out = dict(item)
        file_name = str(item.get("file") or "")
        path = MEDIA_DIR / file_name if file_name else None
        out["path"] = str(path) if path else ""
        out["missing"] = not bool(path and path.is_file())
        return out

    def _list_media(self):
        items = [self._public_media(item) for item in self._library.values()]
        items.sort(key=lambda item: float(item.get("createdAt") or 0), reverse=True)
        return {"items": items, "fileBase": self._file_base()}

    @staticmethod
    def _allowed_temp_source(path: Path) -> bool:
        try:
            resolved = path.resolve()
            roots = [Path(tempfile.gettempdir()).resolve()]
            extra = os.environ.get("GRADIO_TEMP_DIR")
            if extra:
                roots.append(Path(extra).resolve())
            for root in roots:
                try:
                    resolved.relative_to(root)
                    return True
                except ValueError:
                    continue
        except Exception:
            return False
        return False

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _adopt_media(self, data: dict):
        name = Path(str(data.get("name") or "upload.bin")).name
        mime = str(data.get("mime") or "")
        ext = _safe_ext(name)
        kind = _media_kind(name, mime)
        if not ext or kind == "other":
            raise ValueError("unsupported media type")

        src = Path(str(data.get("path") or ""))
        if not src.is_file():
            raise ValueError("Gradio upload file was not found")
        if not self._allowed_temp_source(src):
            raise ValueError("upload source is outside the allowed temporary directory")

        size = src.stat().st_size
        if size <= 0:
            raise ValueError("the uploaded file is empty")
        if size > MAX_MEDIA_BYTES:
            raise ValueError("media file is larger than the 2 GB library limit")

        digest = self._hash_file(src)
        return self._store_media_from_path(src, name, mime, size, digest, kind, ext)

    def _upload_media(self, data: dict):
        """Small-file fallback when the browser cannot reach Gradio's upload route."""
        name = Path(str(data.get("name") or "upload.bin")).name
        mime = str(data.get("mime") or "")
        ext = _safe_ext(name)
        kind = _media_kind(name, mime)
        if not ext or kind == "other":
            raise ValueError("unsupported media type")
        encoded = str(data.get("b64") or "")
        if not encoded:
            raise ValueError("inline upload payload is empty")
        blob = base64.b64decode(encoded, validate=True)
        if len(blob) > INLINE_UPLOAD_LIMIT:
            raise ValueError("inline upload exceeds the 12 MB fallback limit")
        digest = hashlib.sha256(blob).hexdigest()
        media_id = "m_" + digest[:20]
        dest = MEDIA_DIR / (media_id + ext)
        duplicate = dest.exists()
        if not duplicate:
            dest.write_bytes(blob)
        return self._record_media(media_id, dest, name, mime, len(blob), kind, duplicate)

    def _store_media_from_path(
        self,
        src: Path,
        name: str,
        mime: str,
        size: int,
        digest: str,
        kind: str,
        ext: str,
    ):
        media_id = "m_" + digest[:20]
        dest = MEDIA_DIR / (media_id + ext)
        duplicate = dest.exists()
        if not duplicate:
            shutil.copy2(src, dest)
        return self._record_media(media_id, dest, name, mime, size, kind, duplicate)

    def _record_media(
        self,
        media_id: str,
        dest: Path,
        name: str,
        mime: str,
        size: int,
        kind: str,
        duplicate: bool,
    ):
        existing = self._library.get(media_id) or {}
        item = {
            "mediaId": media_id,
            "name": existing.get("name") or name,
            "file": dest.name,
            "kind": kind,
            "mime": mime or mimetypes.guess_type(name)[0] or "",
            "size": int(size),
            "createdAt": existing.get("createdAt") or time.time(),
        }
        self._library[media_id] = item
        self._save_library()
        trace(f"media {'=' if duplicate else '+'} {name} -> {dest.name} ({size} bytes)")
        return {
            "item": self._public_media(item),
            "duplicate": duplicate,
            "fileBase": self._file_base(),
        }

    def _delete_media(self, data: dict):
        media_id = str(data.get("mediaId") or "")
        item = self._library.get(media_id)
        if not item:
            raise ValueError("media item was not found")
        file_name = str(item.get("file") or "")
        path = MEDIA_DIR / file_name
        try:
            path.resolve().relative_to(MEDIA_DIR.resolve())
        except ValueError as exc:
            raise ValueError("refusing to delete a file outside the media library") from exc
        if path.exists():
            path.unlink()
        self._library.pop(media_id, None)
        self._save_library()
        trace(f"media - {file_name}")
        return {"ok": True, "mediaId": media_id}

    # ------------------------------------------------------------------
    # iframe host
    # ------------------------------------------------------------------
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
            "allow='autoplay; clipboard-write; fullscreen' "
            "sandbox='allow-scripts allow-same-origin allow-forms allow-downloads allow-modals' "
            "style='width:100%;height:calc(100dvh - 150px);min-height:560px;"
            "border:0;border-radius:12px;background:#070a0f;' "
            f"src='{src}'></iframe>"
        )

    @staticmethod
    def _served_asset_url(path: Path) -> str:
        try:
            set_static_paths = getattr(gr, "set_static_paths", None)
            if not callable(set_static_paths):
                return ""
            set_static_paths(paths=[str(ASSETS_DIR), str(MEDIA_DIR)])
            normalized = str(path).replace("\\", "/")
            return "/gradio_api/file=" + normalized
        except Exception as exc:
            trace(f"static asset registration failed: {exc}")
            return ""
