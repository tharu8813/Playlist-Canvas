"""Hold a live render's outcome until Canvas capture has handed it over."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Slot


class RenderOutcomeGate(QObject):
    """Buffer ``RenderWorker`` outcome signals while capture is still running.

    With live (piped) capture the final FFmpeg starts before the Canvas is
    fully captured. Its outcome must not tear down the export UI from inside
    the capture loop's event pump, so the gate records it instead. Capture
    reads :meth:`failure_message` to stop early; afterwards :meth:`open`
    replays the buffered outcome to the normal completion handlers, or
    :meth:`discard` swallows it when the export falls back to intermediate
    files. The gate lives on the GUI thread, so its slots run there.
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._events: list[tuple[str, object]] = []
        self._handlers: dict[str, Callable[..., None]] | None = None
        self._outcome: tuple[str, object] | None = None

    def watch(self, worker: QObject) -> None:
        worker.succeeded.connect(self._on_succeeded)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(self._on_cancelled)
        worker.finished.connect(self._on_finished)

    @property
    def outcome(self) -> tuple[str, object] | None:
        return self._outcome

    def failure_message(self) -> str | None:
        """Why the final render stopped, or ``None`` while it can still succeed."""
        if self._outcome is None:
            return None
        kind, payload = self._outcome
        if kind == "failed":
            return str(payload)
        if kind == "cancelled":
            return "The final render was cancelled."
        return None

    def open(self, **handlers: Callable[..., None]) -> None:
        """Deliver buffered and future outcomes to ``handlers`` by event name."""
        self._handlers = handlers
        events, self._events = self._events, []
        for kind, payload in events:
            self._dispatch(kind, payload)

    def discard(self) -> None:
        """Swallow every buffered and future outcome."""
        self._handlers = {}
        self._events.clear()

    @Slot(object)
    def _on_succeeded(self, result: object) -> None:
        self._record("succeeded", result)

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self._record("failed", message)

    @Slot()
    def _on_cancelled(self) -> None:
        self._record("cancelled", None)

    @Slot()
    def _on_finished(self) -> None:
        self._record("finished", None)

    def _record(self, kind: str, payload: object) -> None:
        if kind != "finished" and self._outcome is None:
            self._outcome = (kind, payload)
        if self._handlers is None:
            self._events.append((kind, payload))
        else:
            self._dispatch(kind, payload)

    def _dispatch(self, kind: str, payload: object) -> None:
        handler = (self._handlers or {}).get(kind)
        if handler is None:
            return
        if kind in {"succeeded", "failed"}:
            handler(payload)
        else:
            handler()
