"""What an entry holds, at a glance: an icon per filled field for the list on the left.

Field names are stored as typed (or as the preset name in the language of the day), so a
name is matched against the preset in every language."""

from __future__ import annotations

from .i18n import variants

# preset field name → icon; the keys are the catalog keys used by the field presets and
# the kind templates
ICONS: dict[str, str] = {
    "login": "👤",
    "mail": "✉",
    "password": "🔑",
    "token": "🎟",
    "site": "🌐",
    "service / URL": "🌐",
    "phone": "📞",
    "valid until": "📅",
    "expires": "📅",
    "PIN": "🔢",
    "2FA key": "🛡",
    "private key": "🗝",
    "public key": "🗝",
    "passphrase": "🔑",
    "number": "💳",
    "CVV": "🔢",
    "holder": "👤",
    "value": "🔒",
}
SECRET = "🔒"  # noqa: S105 — a glyph, not a password
PLAIN = "▫"

_BY_NAME: dict[str, str] = {}


def icon_for(name: str, secret: bool) -> str:
    if not _BY_NAME:
        for key, icon in ICONS.items():
            for v in variants(key):
                _BY_NAME[v.casefold()] = icon
    return _BY_NAME.get(name.strip().casefold(), SECRET if secret else PLAIN)


def summary(fields) -> tuple[str, list[str]]:
    """(icons of the filled fields, their names). Repeated icons collapse into one."""
    icons: list[str] = []
    names: list[str] = []
    for f in fields:
        if not f.filled:
            continue
        names.append(f.key.strip() or "…")
        icon = icon_for(f.key, f.secret)
        if icon not in icons:
            icons.append(icon)
    return " ".join(icons), names
