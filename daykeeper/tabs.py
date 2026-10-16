"""Which tabs the top bar shows and in what order; kept in settings.json.

`tab_order` lists tab ids left to right, `tabs_hidden` the ones switched off. Unknown ids
are dropped, tabs missing from the saved order (added in a newer version) go to the end,
and at least one tab always stays on."""

from __future__ import annotations

TAB_IDS: tuple[str, ...] = ("vault", "bookmarks", "journal", "notes", "routines", "integrations")


def tab_order(settings: dict) -> list[str]:
    saved = settings.get("tab_order")
    order: list[str] = []
    for tab in saved if isinstance(saved, list) else []:
        if tab in TAB_IDS and tab not in order:
            order.append(tab)
    return order + [t for t in TAB_IDS if t not in order]


def tabs_hidden(settings: dict) -> set[str]:
    saved = settings.get("tabs_hidden")
    hidden = {t for t in (saved if isinstance(saved, list) else []) if t in TAB_IDS}
    if len(hidden) == len(TAB_IDS):
        hidden.discard(tab_order(settings)[0])
    return hidden


def visible_tabs(settings: dict) -> list[str]:
    hidden = tabs_hidden(settings)
    return [t for t in tab_order(settings) if t not in hidden]


def moved(order: list[str], tab: str, step: int) -> list[str]:
    """The order with `tab` moved by `step` places, clamped to the ends."""
    order = list(order)
    i = order.index(tab)
    j = max(0, min(len(order) - 1, i + step))
    order.insert(j, order.pop(i))
    return order
