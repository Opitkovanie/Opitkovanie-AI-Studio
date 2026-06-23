"""
Minimal Streamlit shim for DubCut Studio.

The DubMaster / ShortsGenerator engine modules were written for Streamlit and do
`import streamlit as st`, then call progress/status helpers (st.spinner, st.success,
st.progress, ...) and read/write st.session_state.

We do NOT run Streamlit. Instead this shim provides API-compatible no-op / log-forwarding
implementations so the heavy engine logic (download, whisper, gemini, ffmpeg, demucs, qwen-tts)
can be imported and executed unchanged inside the native FastAPI backend.

Progress/log calls are forwarded to a per-thread "sink" (see backend.jobs) so the desktop UI
can stream them over Server-Sent Events.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Per-thread sink: backend.jobs binds a callable that receives (level, message)
# ---------------------------------------------------------------------------
_local = threading.local()


def _emit(level: str, message: Any) -> None:
    sink: Optional[Callable[[str, str], None]] = getattr(_local, "sink", None)
    if sink is not None:
        try:
            sink(level, str(message))
        except Exception:
            pass


def bind_sink(sink: Optional[Callable[[str, str], None]]) -> None:
    _local.sink = sink


# ---------------------------------------------------------------------------
# Stop signal: st.stop() in vendor code aborts the run cleanly.
# ---------------------------------------------------------------------------
class StopException(Exception):
    pass


def stop() -> None:  # noqa: A001 - mirror streamlit API name
    raise StopException()


def rerun(*_a, **_k) -> None:
    # No reactive model in the backend; rerun is a no-op (the pipeline returns instead).
    return None


# ---------------------------------------------------------------------------
# session_state: attribute + item access dict, like Streamlit's SessionState.
# ---------------------------------------------------------------------------
class _SessionState(dict):
    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            return None

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value

    def __delattr__(self, name: str) -> None:
        self.pop(name, None)


session_state = _SessionState()


# ---------------------------------------------------------------------------
# Status / log helpers
# ---------------------------------------------------------------------------
def success(msg: Any = "", *a, **k) -> None:
    _emit("success", msg)


def info(msg: Any = "", *a, **k) -> None:
    _emit("info", msg)


def warning(msg: Any = "", *a, **k) -> None:
    _emit("warning", msg)


def error(msg: Any = "", *a, **k) -> None:
    _emit("error", msg)


def write(*args, **k) -> None:
    _emit("info", " ".join(str(a) for a in args))


def text(msg: Any = "", *a, **k) -> None:
    _emit("info", msg)


def markdown(msg: Any = "", *a, **k) -> None:
    _emit("info", msg)


def caption(msg: Any = "", *a, **k) -> None:
    _emit("info", msg)


def toast(msg: Any = "", *a, **k) -> None:
    _emit("info", msg)


def code(msg: Any = "", *a, **k) -> None:
    _emit("info", msg)


def stop_if_cancelled() -> None:
    if session_state.get("cancel_renders") or session_state.get("is_running") is False:
        stop()


@contextmanager
def spinner(label: str = "", *a, **k):
    _emit("step", label)
    yield


@contextmanager
def status(label: str = "", *a, **k):
    _emit("step", label)
    yield _Empty()


# ---------------------------------------------------------------------------
# Placeholder widgets (st.empty / st.progress) — return chainable no-op objects.
# ---------------------------------------------------------------------------
class _Progress:
    def __init__(self, *_a, **_k):
        pass

    def progress(self, value: float, text: Any = None):
        if text is not None:
            _emit("progress", f"{int(float(value) * 100) if value <= 1 else int(value)}% · {text}")
        return self

    def empty(self):
        return self

    def update(self, *a, **k):
        return self


class _Empty:
    """Mimics the object returned by st.empty(); supports .text/.markdown/.progress/.empty()."""

    def __getattr__(self, _name):
        def _method(*a, **k):
            if a:
                _emit("info", a[0])
            return self

        return _method

    def __call__(self, *a, **k):
        return self


def empty(*_a, **_k):
    return _Empty()


def progress(value: float = 0.0, text: Any = None, *a, **k):
    p = _Progress()
    if text is not None:
        p.progress(value, text)
    return p


def container(*_a, **_k):
    return _Empty()


def expander(*_a, **_k):
    return _Empty()


# ---------------------------------------------------------------------------
# Misc no-ops so imports / module-level calls don't explode.
# ---------------------------------------------------------------------------
def set_page_config(*_a, **_k):
    return None


def cache_data(*a, **k):
    # Support both @st.cache_data and @st.cache_data(...)
    if a and callable(a[0]):
        return a[0]

    def _wrap(fn):
        return fn

    return _wrap


cache_resource = cache_data


def _noop(*_a, **_k):
    return _Empty()


# Catch-all for any other attribute referenced by vendor code.
def __getattr__(name: str):  # PEP 562 module-level __getattr__
    return _noop


# components.v1.html shim
class _ComponentsV1:
    @staticmethod
    def html(*_a, **_k):
        return None

    @staticmethod
    def iframe(*_a, **_k):
        return None


class _Components:
    v1 = _ComponentsV1()


components = _Components()
