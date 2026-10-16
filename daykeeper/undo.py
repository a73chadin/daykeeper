"""Undo and redo for the whole app: snapshots of the parts of the vault the user edits.

A step is {domain: its state before the change}. The window calls `capture` a moment after
the user stops changing things (typing in a field is one step, not one per letter); `undo`
puts the states of the last step back and keeps the current ones for `redo`. A change the
user did not make — journal items from the inbox, say — calls `rebase`: the steps of that
domain are dropped, so an undo can never wipe what came in on its own.

States are strings (the vault serialises a domain); the history only compares and keeps
them. Pure: no tkinter, no vault import."""

from __future__ import annotations

from collections.abc import Callable

LIMIT = 100  # steps kept; the oldest goes first


class History:
    def __init__(self, read: Callable[[str], str], domains: tuple[str, ...], limit: int = LIMIT):
        self.read, self.domains, self.limit = read, domains, limit
        self.last: dict[str, str] = {}
        self.undo_steps: list[dict[str, str]] = []
        self.redo_steps: list[dict[str, str]] = []

    def reset(self) -> None:
        """A fresh start (a vault opened): nothing to undo."""
        self.last = {d: self.read(d) for d in self.domains}
        self.undo_steps.clear()
        self.redo_steps.clear()

    @property
    def can_undo(self) -> bool:
        return bool(self.undo_steps)

    @property
    def can_redo(self) -> bool:
        return bool(self.redo_steps)

    def capture(self) -> bool:
        """Record what changed since the last capture as one step. True — a step was added."""
        before: dict[str, str] = {}
        for d in self.domains:
            now = self.read(d)
            if now != self.last.get(d):
                before[d] = self.last.get(d, now)
                self.last[d] = now
        if not before:
            return False
        self.undo_steps.append(before)
        del self.undo_steps[: -self.limit]
        self.redo_steps.clear()
        return True

    def rebase(self, domain: str) -> None:
        """`domain` changed by itself: its steps go, its current state is the new start."""
        for stack in (self.undo_steps, self.redo_steps):
            for step in stack:
                step.pop(domain, None)
            stack[:] = [s for s in stack if s]
        self.last[domain] = self.read(domain)

    def _swap(self, take: list, give: list, write: Callable[[str, str], None]) -> set[str]:
        self.capture()  # a change still waiting becomes its own step first
        if not take:
            return set()
        step = take.pop()
        give.append({d: self.last.get(d, "") for d in step})
        for d, state in step.items():
            write(d, state)
            self.last[d] = state
        return set(step)

    def undo(self, write: Callable[[str, str], None]) -> set[str]:
        """Put the last step back; the domains it touched (empty — nothing to undo)."""
        return self._swap(self.undo_steps, self.redo_steps, write)

    def redo(self, write: Callable[[str, str], None]) -> set[str]:
        """Bring back what the last undo put away; a new change since then ends the redo."""
        return self._swap(self.redo_steps, self.undo_steps, write)
