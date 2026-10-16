"""Timers over Tk's `after`: one idea, three shapes, instead of a job id and an
`after_cancel` kept by hand in every place that waits.

`Debounce` runs its function once, a delay after the last touch (autosave, the clipboard
wipe, a tooltip, a resize that pauses). `IdleOnce` runs it once at idle however many
times it is asked before then (a relayout). `Ticker` runs it every so often while
started; the function may answer False to stop the ticks. All three take anything with
`after`, `after_cancel` and `after_idle`, so they are tested on a fake clock."""

from __future__ import annotations

from typing import Any
from collections.abc import Callable


class Debounce:
    """Calls `fn(*args)` `delay_ms` after the last `touch(*args)`; a touch before that
    restarts the clock and replaces the arguments. `flush` runs it now when it is due."""

    def __init__(self, widget: Any, delay_ms: int, fn: Callable[..., Any]):
        self.widget, self.delay_ms, self.fn = widget, delay_ms, fn
        self._job: str | None = None
        self._args: tuple = ()

    @property
    def pending(self) -> bool:
        return self._job is not None

    def touch(self, *args: Any) -> None:
        self.cancel()
        self._args = args
        self._job = self.widget.after(self.delay_ms, self._fire)

    def cancel(self) -> None:
        if self._job is not None:
            self.widget.after_cancel(self._job)
            self._job = None

    def flush(self) -> None:
        """Run the waiting call at once (nothing when none is waiting)."""
        if self._job is not None:
            self.cancel()
            self.fn(*self._args)

    def _fire(self) -> None:
        self._job = None
        self.fn(*self._args)


class IdleOnce:
    """Calls `fn()` once at idle, however many times `touch` is called before that."""

    def __init__(self, widget: Any, fn: Callable[[], Any]):
        self.widget, self.fn = widget, fn
        self._job: str | None = None

    @property
    def pending(self) -> bool:
        return self._job is not None

    def touch(self) -> None:
        if self._job is None:
            self._job = self.widget.after_idle(self._fire)

    def cancel(self) -> None:
        if self._job is not None:
            self.widget.after_cancel(self._job)
            self._job = None

    def _fire(self) -> None:
        self._job = None
        self.fn()


class Ticker:
    """Calls `fn()` every `ms` from `start` until `stop`; the next tick is booked before
    `fn` runs, so an error inside it does not end the ticks, and `fn` answering False
    does. `start` while running is nothing."""

    def __init__(self, widget: Any, ms: int, fn: Callable[[], Any]):
        self.widget, self.ms, self.fn = widget, ms, fn
        self._job: str | None = None

    @property
    def running(self) -> bool:
        return self._job is not None

    def start(self) -> None:
        if self._job is None:
            self._job = self.widget.after(self.ms, self._tick)

    def stop(self) -> None:
        if self._job is not None:
            self.widget.after_cancel(self._job)
            self._job = None

    def _tick(self) -> None:
        self._job = self.widget.after(self.ms, self._tick)
        if self.fn() is False:
            self.stop()
