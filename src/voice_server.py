"""Localhost-only HTTP server for the Claude-Code Stop-hook -> TTS bridge.

Bound strictly to ``127.0.0.1``: nothing on the network ever reaches it.
The Stop-hook script (``tools/claude-code-stop-hook.py``) is a small
stdlib-only Python script the user registers in
``~/.claude/settings.json``; on every Claude Code turn it POSTs the raw
assistant text here, the server hands it to :class:`Summarizer` for a
spoken-style rewrite, then to :class:`TTSService.speak` for playback.

Endpoints:

* ``GET  /health``   — liveness check; returns version + voice_enabled.
* ``POST /speak``    — JSON body ``{"text": "...", "summarize": bool?}``.
                       Pipes through summariser (per the ``summarize``
                       flag, defaulting to the user's setting) and then
                       enqueues onto :class:`TTSService`.
* ``POST /interrupt``— calls :meth:`TTSService.interrupt`. The 'shut up'
                       global hotkey (Phase 5) routes through here too.

The server intentionally never returns the API key, the resolved
summary text, or anything else that could leak into a tail of the hook
script's output. 200/204/4xx + a tiny JSON ack only.
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__

log = logging.getLogger(__name__)

_BIND_HOST = "127.0.0.1"  # NEVER 0.0.0.0 — local-only by design.


class _Handler(BaseHTTPRequestHandler):
    """Per-request handler; service refs are injected via class attrs."""

    server_version = f"TextWhisper/{__version__}"

    # Populated by VoiceIPCServer.start() before the server starts.
    settings = None  # type: ignore[assignment]
    tts = None       # type: ignore[assignment]
    summarizer = None  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # Request handlers
    # ------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        if self.path == "/health":
            self._respond_json(
                200,
                {
                    "ok": True,
                    "version": __version__,
                    "voice_enabled": bool(
                        self.settings.get("voice_enabled", False)
                    ),
                },
            )
            return
        self._respond_json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/speak":
            self._handle_speak()
        elif self.path == "/interrupt":
            self._handle_interrupt()
        else:
            self._respond_json(404, {"error": "not_found"})

    # ------------------------------------------------------------------
    # /speak
    # ------------------------------------------------------------------

    def _handle_speak(self) -> None:
        if not bool(self.settings.get("voice_enabled", False)):
            log.info("Stop hook /speak ignored — voice_enabled is False.")
            self._respond_json(204, {"ok": True, "skipped": "voice_disabled"})
            return
        body = self._read_json_body()
        if body is None:
            return
        text = str(body.get("text", "") or "").strip()
        if not text:
            self._respond_json(400, {"error": "empty_text"})
            return
        summarize = bool(
            body.get("summarize", self.settings.get("voice_summarize", True))
        )
        spoken = text
        if summarize and self.summarizer is not None:
            try:
                spoken = self.summarizer.summarize(text)
            except Exception as e:
                # Don't block read-back on summarisation failure — fall
                # back to the raw text so the user hears SOMETHING and
                # can fix their key. Never log the key in the error.
                log.warning(
                    "Summariser failed (%s); reading raw response.", type(e).__name__
                )
                spoken = text
        if not spoken:
            self._respond_json(204, {"ok": True, "skipped": "empty_summary"})
            return
        self.tts.speak(spoken)
        self._respond_json(202, {"ok": True, "spoken_chars": len(spoken)})

    # ------------------------------------------------------------------
    # /interrupt
    # ------------------------------------------------------------------

    def _handle_interrupt(self) -> None:
        self.tts.interrupt()
        self._respond_json(200, {"ok": True})

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _read_json_body(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            self._respond_json(400, {"error": "bad_content_length"})
            return None
        if length <= 0 or length > 5 * 1024 * 1024:
            self._respond_json(400, {"error": "bad_content_length"})
            return None
        try:
            raw = self.rfile.read(length)
            return json.loads(raw.decode("utf-8"))
        except Exception:
            self._respond_json(400, {"error": "bad_json"})
            return None

    def _respond_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "null")
        self.end_headers()
        self.wfile.write(body)

    # Quiet down the default access log — we already log relevant events.
    def log_message(self, format: str, *args) -> None:  # noqa: A002
        log.debug("voice_ipc: %s", format % args)


class VoiceIPCServer:
    """Owns the lifecycle of the loopback HTTP listener."""

    def __init__(self, settings, tts, summarizer) -> None:
        self.settings = settings
        self.tts = tts
        self.summarizer = summarizer
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._httpd is not None

    @property
    def port(self) -> int | None:
        if self._httpd is None:
            return None
        return self._httpd.server_address[1]

    def start(self) -> None:
        if self._httpd is not None:
            return
        port = int(self.settings.get("voice_ipc_port", 47821))
        # Inject service refs onto the handler class so each request
        # finds them. We make a fresh subclass per server instance to
        # avoid bleeding state across multiple servers (e.g. in tests).
        handler_cls = type(
            "_VoiceHandler",
            (_Handler,),
            {
                "settings": self.settings,
                "tts": self.tts,
                "summarizer": self.summarizer,
            },
        )
        try:
            self._httpd = ThreadingHTTPServer((_BIND_HOST, port), handler_cls)
        except OSError as e:
            log.error(
                "Voice IPC bind failed on %s:%d (%s). "
                "Pick a different port in Settings → Voice → IPC port.",
                _BIND_HOST, port, e,
            )
            self._httpd = None
            return
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="TextWhisper-VoiceIPC",
            daemon=True,
        )
        self._thread.start()
        log.info("Voice IPC listening on http://%s:%d/", _BIND_HOST, self.port)

    def stop(self) -> None:
        if self._httpd is None:
            return
        try:
            self._httpd.shutdown()
            self._httpd.server_close()
        except Exception:
            log.exception("Voice IPC shutdown raised")
        self._httpd = None
        if self._thread:
            self._thread.join(timeout=2)
        self._thread = None
