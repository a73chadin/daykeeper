"""Change counters per data domain: a tab is rebuilt when it is shown only if a domain it
draws from has moved since the last time. One counter for the whole vault made a keystroke
in a password rebuild every tile of Bookmarks and every card of Notes on the next visit.
Pure (no tkinter)."""

from __future__ import annotations

DOMAINS = ("vault", "journal", "notes", "bookmarks", "routines", "integrations", "calendar")

# what each tab draws from; the Passwords tab keeps itself up to date and has no entry
VIEW_DOMAINS: dict[str, tuple[str, ...]] = {
    "journal": ("journal",),
    "notes": ("notes",),
    "bookmarks": ("bookmarks",),
    "routines": ("routines",),
    "integrations": ("integrations",),
    "calendar": ("calendar",),
}


class Revisions:
    """Counts changes per domain. `touch` on an unknown domain is a KeyError: a typo must
    not silently become a domain nobody draws from."""

    def __init__(self, domains: tuple[str, ...] = DOMAINS):
        self._count: dict[str, int] = dict.fromkeys(domains, 0)

    def touch(self, domain: str) -> None:
        self._count[domain] += 1

    def of(self, *domains: str) -> tuple[int, ...]:
        """The counters of these domains, in this order — a part of a tab's view key."""
        return tuple(self._count[d] for d in domains)

    def snapshot(self) -> dict[str, int]:
        return dict(self._count)
