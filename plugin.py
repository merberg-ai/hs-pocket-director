"""HS Pocket Director — mobile-first MiniMax H3 directing UI for Wan2GP.

The browser UI stays isolated from Wan2GP internals. This module owns the small
request/response bridge, durable media storage, and H3 capability discovery.
Generation itself remains behind the next bridge phase.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import mimetypes
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

import gradio as gr

from shared.utils.plugins import WAN2GPPlugin

PLUGIN_ID = "hs_pocket_director"
PLUGIN_NAME = "HS Pocket Director"
PLUGIN_VERSION = "0.1.0-alpha.4"
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

H3_GRID_FALLBACK = {
    "fps": 24,
    "framesMinimum": 107,
    "frameStep": 17,
    "frameOffset": 5,
    "windowMin": 124,
    "windowMax": 481,
    "windowDefault": 362,
    "overlapMin": 1,
    "overlapMax": 120,
    "overlapStep": 17,
    "overlapOffset": 1,
    "overlapDefault": 18,
    "maxRefImages": 9,
    "maxRefVideos": 3,
    "maxRefAudio": 3,
}

RESOLUTION_FALLBACK = [
    "480x832", "832x480",
    "704x1280", "720x1280", "1280x720", "1024x1024",
    "1088x1920", "1920x1088",
]

FALLBACK_MODELS = [
    {"id": "minimax_h3_hybrid", "label": "MiniMax H3 Hybrid", "pipeline": "Hybrid", "size": "Full", "verified": False},
    {"id": "minimax_h3_fl2va", "label": "MiniMax H3 FL2VA", "pipeline": "FL2VA", "size": "Full", "verified": False},
    {"id": "minimax_h3_ref2va", "label": "MiniMax H3 Ref2VA", "pipeline": "Ref2VA", "size": "Full", "verified": False},
]


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


def _json_safe(value, depth: int = 0):
    if depth > 5:
        return str(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v, depth + 1) for v in value]
    return str(value)


def _deep_value(obj, keys):
    wanted = {str(k).lower() for k in keys}
    if isinstance(obj, dict):
        for key, value in obj.items():
            if str(key).lower() in wanted and value is not None:
                return value
        for value in obj.values():
            found = _deep_value(value, wanted)
            if found is not None:
                return found
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            found = _deep_value(value, wanted)
            if found is not None:
                return found
    return None


def _as_int(value, default=None):
    try:
        if value is None:
            return default
        return int(round(float(value)))
    except Exception:
        return default


def _as_float(value, default=None):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _collect_resolutions(value):
    found = set()
    pattern = re.compile(r"(?<!\d)(\d{3,4})\s*[x×]\s*(\d{3,4})(?!\d)", re.I)

    def walk(node):
        if isinstance(node, dict):
            for key, item in node.items():
                key_l = str(key).lower()
                if "resolution" in key_l or "size" in key_l or isinstance(item, (dict, list, tuple)):
                    walk(item)
        elif isinstance(node, (list, tuple, set)):
            for item in node:
                walk(item)
        elif isinstance(node, str):
            for width, height in pattern.findall(node):
                w, h = int(width), int(height)
                if 256 <= w <= 4096 and 256 <= h <= 4096:
                    found.add(f"{w}x{h}")

    walk(value)
    return sorted(found, key=lambda item: (int(item.split("x")[0]) * int(item.split("x")[1]), item))


def _snap_grid(value, offset, step, minimum, maximum):
    value = int(round(value))
    step = max(1, int(step))
    offset = int(offset)
    minimum = int(minimum)
    maximum = int(maximum)
    snapped = round((value - offset) / step) * step + offset
    while snapped < minimum:
        snapped += step
    while snapped > maximum:
        snapped -= step
    return max(minimum, min(maximum, snapped))


# Runs in the Wan2GP parent page. The mobile UI lives inside an iframe. One
# hidden request/response pair is deliberately serialized: it is boring, but
# very reliable across Gradio versions and plenty fast for UI/config traffic.
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
      try { const found = root.querySelector(selector); if (found) return found; } catch (_) {}
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
    } catch (err) { console.error("[HS-Pocket] could not set", id, err); return false; }
  }
  function click(id) {
    const h = host(id);
    if (!h) return false;
    const button = h.matches && h.matches("button") ? h : h.querySelector("button");
    if (!button) return false;
    button.click(); return true;
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
    state.timer = null; state.busy = false; setTimeout(pump, 20);
  }
  function pump() {
    if (state.busy || !state.queue.length) return;
    const message = state.queue.shift(); state.busy = true;
    if (!setValue("hspd-req", JSON.stringify(message))) {
      toFrame({ id: message.id, error: "Pocket Director bridge request control is missing." }); release(); return;
    }
    const ttl = Math.max(5000, Math.min(Number(message.ttl || 120000), 900000));
    state.timer = setTimeout(function () { toFrame({ id: message.id, error: "Pocket Director bridge timed out." }); release(); }, ttl + 1500);
    setTimeout(function () {
      if (!click("hspd-go")) { toFrame({ id: message.id, error: "Pocket Director bridge trigger is missing." }); release(); }
    }, 120);
  }
  window.addEventListener("message", function (event) {
    const message = event.data;
    if (!message || typeof message !== "object" || message.source !== FRAME_TAG) return;
    state.queue.push(message); pump();
  });
  setInterval(function () {
    const el = input("hspd-resp");
    const value = el ? String(el.value || "") : "";
    if (!value || value === state.lastResp) return;
    state.lastResp = value;
    try { toFrame(JSON.parse(value)); } catch (err) { toFrame({ error: "Bad bridge response: " + String(err) }); }
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
        # These are all used by current H3 Director builds. request_global is
        # intentionally best-effort so Pocket Director can still boot on an
        # older/newer Wan2GP build and fall back gracefully.
        for name in (
            "get_model_def",
            "get_model_defs",
            "get_model_name",
            "get_base_model_type",
            "get_current_model_settings",
        ):
            try:
                self.request_global(name)
            except Exception as exc:
                trace(f"optional global {name} unavailable: {exc}")

        self.add_custom_js(_BRIDGE_JS)
        self.add_tab(tab_id=PLUGIN_ID, label=PLUGIN_NAME, component_constructor=self._build_ui)
        trace(f"registered {PLUGIN_NAME} {PLUGIN_VERSION}")

    def _build_ui(self, api_session):
        # Keeping this positional argument is important. Wan2GP builds the
        # per-plugin API session when the tab constructor accepts it.
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
                go.click(fn=self._on_bridge, inputs=[req], outputs=[resp], show_progress="hidden", trigger_mode="multiple")
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
            return json.dumps({"cmd": cmd + ":ok", "id": message_id, "data": _json_safe(result)})
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
            return {"ok": True, "version": PLUGIN_VERSION, "workspace": str(WORKSPACE), "mediaCount": len(self._library)}
        if cmd == "list_media":
            return self._list_media()
        if cmd == "adopt_media":
            return self._adopt_media(data)
        if cmd == "upload_media":
            return self._upload_media(data)
        if cmd == "delete_media":
            return self._delete_media(data)
        if cmd == "capabilities":
            return self._capabilities()
        if cmd == "validate_plan":
            return self._validate_plan(data)
        raise ValueError(f"unknown bridge command: {cmd}")

    # ------------------------------------------------------------------
    # H3 capability discovery
    # ------------------------------------------------------------------
    def _session(self):
        # Literal dotted access is intentional. Wan2GP's callback wrapper
        # recognises this name and provides/pumps the plugin API session.
        return self._wangp_session if hasattr(self, "_wangp_session") else None

    def _call_optional(self, name, *args):
        fn = getattr(self, name, None)
        if callable(fn):
            try:
                return fn(*args)
            except Exception as exc:
                trace(f"{name}{args}: {exc}")
        return None

    @staticmethod
    def _normalize_model_defs(defs):
        out = []
        if isinstance(defs, dict):
            for container_key in ("models", "model_defs", "definitions"):
                if container_key in defs and isinstance(defs[container_key], (dict, list, tuple)):
                    nested = HSPocketDirectorPlugin._normalize_model_defs(defs[container_key])
                    if nested:
                        return nested
            for key, value in defs.items():
                if isinstance(value, dict):
                    out.append((str(key), value))
        elif isinstance(defs, (list, tuple)):
            for item in defs:
                if isinstance(item, (list, tuple)) and len(item) == 2 and isinstance(item[1], dict):
                    out.append((str(item[0]), item[1]))
                elif isinstance(item, dict):
                    model_id = item.get("model_type") or item.get("id") or item.get("key") or item.get("name") or ""
                    out.append((str(model_id), item))
                elif isinstance(item, str):
                    out.append((item, {}))
        return out

    def _model_def_for(self, model_type):
        model_type = str(model_type or "")
        if not model_type:
            return {}
        session = self._session()
        if session is not None and hasattr(session, "get_model_def"):
            try:
                value = session.get_model_def(model_type)
                if isinstance(value, dict):
                    return value
            except Exception as exc:
                trace(f"session.get_model_def({model_type}): {exc}")
        value = self._call_optional("get_model_def", model_type)
        return value if isinstance(value, dict) else {}

    def _raw_model_defs(self):
        session = self._session()
        attempts = []
        if session is not None:
            if hasattr(session, "list_model_defs"):
                attempts.append(("session.list_model_defs", lambda: session.list_model_defs()))
            if hasattr(session, "get_model_defs"):
                attempts.append(("session.get_model_defs", lambda: session.get_model_defs()))
        injected = getattr(self, "get_model_defs", None)
        if callable(injected):
            attempts.append(("global get_model_defs", lambda: injected()))

        for label, fn in attempts:
            try:
                normalized = self._normalize_model_defs(fn())
            except Exception as exc:
                trace(f"{label} failed: {exc}")
                continue
            expanded = []
            for model_id, model_def in normalized:
                if not model_def and model_id:
                    model_def = self._model_def_for(model_id)
                expanded.append((model_id, model_def or {}))
            if expanded:
                trace(f"{label} -> {len(expanded)} model definition(s)")
                return expanded
        return []

    @staticmethod
    def _is_h3(model_id, model_def):
        try:
            haystack = (str(model_id) + " " + json.dumps(model_def, default=str)).lower()
        except Exception:
            haystack = str(model_id).lower()
        return ("minimax" in haystack and "h3" in haystack) or "minimax_h3" in haystack or "minimax-h3" in haystack

    @staticmethod
    def _infer_pipeline(text):
        text = str(text or "").lower()
        if "hybrid" in text:
            return "Hybrid"
        if "ref2va" in text:
            return "Ref2VA"
        if "fl2va" in text:
            return "FL2VA"
        return "H3"

    @staticmethod
    def _infer_size(text):
        text = str(text or "").lower()
        if "pruned" in text or "20b" in text:
            return "Pruned"
        if "33b" in text or "full" in text:
            return "Full"
        return ""

    @staticmethod
    def _model_label(model_id, model_def):
        if isinstance(model_def, dict):
            for key in ("display_name", "label", "name", "title"):
                value = model_def.get(key)
                if isinstance(value, str) and value.strip() and value.strip() != model_id:
                    return value.strip()
            nested = model_def.get("model")
            if isinstance(nested, dict):
                for key in ("display_name", "label", "name", "title"):
                    value = nested.get(key)
                    if isinstance(value, str) and value.strip() and value.strip() != model_id:
                        return value.strip()
        return model_id.replace("_", " ").replace("-", " ").strip().title()

    @staticmethod
    def _derive_grid(model_def):
        grid = dict(H3_GRID_FALLBACK)
        aliases = {
            "fps": ("fps", "video_fps"),
            "framesMinimum": ("frames_minimum", "minimum_frames", "frames_min"),
            "frameStep": ("frames_steps", "frame_step", "frames_step"),
            "frameOffset": ("frames_offset", "frame_offset"),
            "windowMin": ("sliding_window_minimum", "window_min", "window_minimum"),
            "windowMax": ("sliding_window_maximum", "window_max", "window_maximum"),
            "windowDefault": ("sliding_window_default", "window_default", "sliding_window_size"),
            "overlapMin": ("overlap_min", "sliding_window_overlap_min"),
            "overlapMax": ("overlap_max", "sliding_window_overlap_max"),
            "overlapStep": ("overlap_step", "sliding_window_overlap_step"),
            "overlapOffset": ("overlap_offset", "sliding_window_overlap_offset"),
            "overlapDefault": ("overlap_default", "sliding_window_overlap", "sliding_window_overlap_default"),
            "maxRefImages": ("max_ref_images", "max_reference_images", "reference_images_max"),
            "maxRefVideos": ("max_ref_videos", "max_reference_videos", "reference_videos_max"),
            "maxRefAudio": ("max_ref_audio", "max_reference_audio", "reference_audio_max"),
        }
        for target, keys in aliases.items():
            value = _as_int(_deep_value(model_def, keys))
            if value is not None:
                grid[target] = value

        # Wan2GP H3 definitions commonly expose frames_minimum without a
        # separate window minimum. Match Director's behaviour in that case.
        if _deep_value(model_def, aliases["windowMin"]) is None:
            frames_min = _as_int(_deep_value(model_def, aliases["framesMinimum"]))
            if frames_min is not None:
                grid["windowMin"] = frames_min

        grid["frameStep"] = max(1, grid["frameStep"])
        grid["overlapStep"] = max(1, grid["overlapStep"])
        grid["windowMax"] = max(grid["windowMin"], grid["windowMax"])
        grid["overlapMax"] = max(grid["overlapMin"], grid["overlapMax"])
        grid["windowDefault"] = max(grid["windowMin"], min(grid["windowMax"], grid["windowDefault"]))
        grid["overlapDefault"] = max(grid["overlapMin"], min(grid["overlapMax"], grid["overlapDefault"]))
        return grid

    def _current_model_name(self):
        value = self._call_optional("get_model_name")
        if value:
            return str(value)
        session = self._session()
        if session is not None:
            for name in ("get_model_name", "current_model_name"):
                fn = getattr(session, name, None)
                if callable(fn):
                    try:
                        value = fn()
                        if value:
                            return str(value)
                    except Exception:
                        pass
        return ""

    def _current_base_model(self):
        value = self._call_optional("get_base_model_type")
        return str(value) if value else ""

    def _current_settings(self):
        value = self._call_optional("get_current_model_settings")
        if not isinstance(value, dict):
            return {}
        allow = {
            "model_type", "resolution", "video_resolution", "seed", "repeat_generation",
            "num_inference_steps", "steps", "flow_shift", "flowshift", "guidance_scale", "guidance",
            "sliding_window_size", "sliding_window_overlap", "sample_solver", "solver",
            "activated_loras", "lora_weights", "guidance_phases", "attention_sparsity",
            "skip_steps_cache_type", "skip_steps_multiplier", "skip_steps_start_step_perc",
        }
        return {str(k): _json_safe(v) for k, v in value.items() if str(k) in allow}

    def _list_h3_models(self):
        current = self._current_model_name()
        rows = []
        seen = set()
        for model_id, model_def in self._raw_model_defs():
            if not model_id:
                continue
            if not self._is_h3(model_id, model_def):
                continue
            haystack = f"{model_id} {json.dumps(model_def, default=str)}"
            item = {
                "id": model_id,
                "label": self._model_label(model_id, model_def),
                "pipeline": self._infer_pipeline(haystack),
                "size": self._infer_size(haystack),
                "verified": True,
                "resolutions": _collect_resolutions(model_def),
                "grid": self._derive_grid(model_def),
            }
            rows.append(item)
            seen.add(model_id)

        if current and current not in seen and "h3" in current.lower():
            model_def = self._model_def_for(current)
            haystack = f"{current} {json.dumps(model_def, default=str)}"
            rows.append({
                "id": current,
                "label": self._model_label(current, model_def),
                "pipeline": self._infer_pipeline(haystack),
                "size": self._infer_size(haystack),
                "verified": bool(model_def),
                "resolutions": _collect_resolutions(model_def),
                "grid": self._derive_grid(model_def),
            })

        if not rows:
            rows = [{**item, "resolutions": list(RESOLUTION_FALLBACK), "grid": dict(H3_GRID_FALLBACK)} for item in FALLBACK_MODELS]

        order = {"Hybrid": 0, "FL2VA": 1, "Ref2VA": 2, "H3": 3}
        rows.sort(key=lambda item: (order.get(item["pipeline"], 9), item["label"].lower()))
        return rows

    @staticmethod
    def _pick_setting(settings, *names, default=None):
        for name in names:
            if name in settings and settings[name] not in (None, ""):
                return settings[name]
        return default

    def _capabilities(self):
        models = self._list_h3_models()
        live_models = [item for item in models if item.get("verified")]
        source = "live" if live_models else "fallback"
        current_model = self._current_model_name()
        base_model = self._current_base_model()
        settings = self._current_settings()

        selected = next((item for item in models if item["id"] == current_model), None) or models[0]
        grid = dict(selected.get("grid") or H3_GRID_FALLBACK)
        resolutions = []
        for item in models:
            for resolution in item.get("resolutions") or []:
                if resolution not in resolutions:
                    resolutions.append(resolution)
        if not resolutions:
            resolutions = list(RESOLUTION_FALLBACK)
        resolutions.sort(key=lambda item: (int(item.split("x")[0]) * int(item.split("x")[1]), item))

        current_resolution = str(self._pick_setting(settings, "resolution", "video_resolution", default="") or "").replace("×", "x")
        defaults = {
            "model": current_model if any(item["id"] == current_model for item in models) else selected["id"],
            "resolution": current_resolution if current_resolution in resolutions else ("704x1280" if "704x1280" in resolutions else resolutions[0]),
            "steps": _as_int(self._pick_setting(settings, "num_inference_steps", "steps"), 20),
            "seed": _as_int(self._pick_setting(settings, "seed"), -1),
            "flowShift": _as_float(self._pick_setting(settings, "flow_shift", "flowshift"), 12.0),
            "guidance": _as_float(self._pick_setting(settings, "guidance_scale", "guidance"), 1.0),
            "windowSize": _as_int(self._pick_setting(settings, "sliding_window_size"), grid["windowDefault"]),
            "overlap": _as_int(self._pick_setting(settings, "sliding_window_overlap"), grid["overlapDefault"]),
        }

        session = self._session()
        warnings = []
        if source == "fallback":
            warnings.append("Wan2GP did not return a live H3 model roster. Pocket Director is showing conservative H3 fallback values until discovery succeeds.")
        if not current_model:
            warnings.append("Wan2GP did not report a current model name.")

        result = {
            "version": PLUGIN_VERSION,
            "source": source,
            "models": models,
            "resolutions": resolutions,
            "grid": grid,
            "defaults": defaults,
            "current": {"model": current_model, "baseModel": base_model, "settings": settings},
            "referenceLimits": {
                "images": grid["maxRefImages"],
                "videos": grid["maxRefVideos"],
                "audio": grid["maxRefAudio"],
            },
            "features": {
                "sessionApi": session is not None,
                "liveModelDefs": bool(live_models),
                "currentSettings": bool(settings),
            },
            "warnings": warnings,
        }
        trace(f"capabilities: source={source} H3_models={len(models)} current={current_model or '-'}")
        return result

    def _validate_plan(self, data):
        caps = self._capabilities()
        models = caps["models"]
        model_id = str(data.get("model") or caps["defaults"]["model"])
        model = next((item for item in models if item["id"] == model_id), None)
        if model is None:
            return {"ok": False, "errors": [f"Unknown H3 model: {model_id}"], "warnings": [], "summary": {}}

        grid = dict(model.get("grid") or caps["grid"] or H3_GRID_FALLBACK)
        fps = max(1, _as_int(data.get("fps"), grid.get("fps", 24)))
        duration = max(0.5, _as_float(data.get("durationSec"), 0.5))
        frames = max(1, int(round(duration * fps)))
        scene_count = max(0, _as_int(data.get("sceneCount"), 0))
        resolution = str(data.get("resolution") or caps["defaults"]["resolution"]).replace("×", "x")
        allowed_resolutions = model.get("resolutions") or caps["resolutions"]

        requested_window = _as_int(data.get("windowSize"), grid["windowDefault"])
        requested_overlap = _as_int(data.get("overlap"), grid["overlapDefault"])
        window = _snap_grid(requested_window, grid["frameOffset"], grid["frameStep"], grid["windowMin"], grid["windowMax"])
        overlap = _snap_grid(requested_overlap, grid["overlapOffset"], grid["overlapStep"], grid["overlapMin"], grid["overlapMax"])
        if overlap >= window:
            overlap = max(grid["overlapMin"], window - grid["overlapStep"])
            overlap = _snap_grid(overlap, grid["overlapOffset"], grid["overlapStep"], grid["overlapMin"], min(grid["overlapMax"], window - 1))

        stride = max(1, window - overlap)
        windows = 1 if frames <= window else 1 + math.ceil((frames - window) / stride)
        errors = []
        warnings = []
        if scene_count <= 0:
            errors.append("The project has no scenes.")
        if resolution not in allowed_resolutions:
            warnings.append(f"{resolution} was not advertised for {model['label']}; verify it before generation.")
        if requested_window != window:
            warnings.append(f"Window size {requested_window} is off the H3 frame grid; Pocket Director would use {window}.")
        if requested_overlap != overlap:
            warnings.append(f"Overlap {requested_overlap} is off the H3 frame grid; Pocket Director would use {overlap}.")
        if frames < grid["framesMinimum"]:
            warnings.append(f"The timeline is {frames} frames, below this model's advertised minimum generation size of {grid['framesMinimum']} frames.")
        if not model.get("verified"):
            warnings.append("This model entry came from Pocket Director's fallback roster rather than live Wan2GP discovery.")

        return {
            "ok": not errors,
            "errors": errors,
            "warnings": warnings,
            "summary": {
                "model": model["id"],
                "label": model["label"],
                "pipeline": model["pipeline"],
                "resolution": resolution,
                "fps": fps,
                "durationSec": round(duration, 3),
                "targetFrames": frames,
                "windowSize": window,
                "overlap": overlap,
                "windows": windows,
                "steps": max(1, _as_int(data.get("steps"), 20)),
                "seed": _as_int(data.get("seed"), -1),
                "referenceCount": max(0, _as_int(data.get("referenceCount"), 0)),
            },
        }

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

    def _store_media_from_path(self, src: Path, name: str, mime: str, size: int, digest: str, kind: str, ext: str):
        media_id = "m_" + digest[:20]
        dest = MEDIA_DIR / (media_id + ext)
        duplicate = dest.exists()
        if not duplicate:
            shutil.copy2(src, dest)
        return self._record_media(media_id, dest, name, mime, size, kind, duplicate)

    def _record_media(self, media_id: str, dest: Path, name: str, mime: str, size: int, kind: str, duplicate: bool):
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
        return {"item": self._public_media(item), "duplicate": duplicate, "fileBase": self._file_base()}

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
            return "<div style='padding:20px;color:#fca5a5;background:#111827;'>HS Pocket Director could not find assets/index.html.</div>"
        src = self._served_asset_url(INDEX_FILE)
        if not src:
            encoded = base64.b64encode(INDEX_FILE.read_bytes()).decode("ascii")
            src = f"data:text/html;base64,{encoded}"
        return (
            "<iframe id='hspd-frame' title='HS Pocket Director' "
            "allow='autoplay; clipboard-write; fullscreen' "
            "sandbox='allow-scripts allow-same-origin allow-forms allow-downloads allow-modals' "
            "style='width:100%;height:calc(100dvh - 150px);min-height:560px;border:0;border-radius:12px;background:#070a0f;' "
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
