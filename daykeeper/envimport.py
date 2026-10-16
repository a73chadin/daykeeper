"""Import variables from a KEY=VALUE file (.env) into the vault: keys are filed into the lists
"Stands", "Brokers", "Integrations" and "Miscellaneous". The main list is left untouched."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .i18n import _
from .vault import Entry, Field, Vault

# List name keys; translated when the lists are created.
GROUP_STANDS = "Stands"
GROUP_BROKERS = "Brokers"
GROUP_INTEGRATIONS = "Integrations"
GROUP_OTHER = "Miscellaneous"
GROUP_ORDER = (GROUP_STANDS, GROUP_BROKERS, GROUP_INTEGRATIONS, GROUP_OTHER)

# A stand prefix: a stage, a second stage when the two share the key (`DEV_QA_`), and a
# region (`QA_EU_`) → "dev / qa (shared)", "qa-eu".
STAGES = ("DEV", "QA", "UAT", "STAGE", "STAGING", "PROD")
REGIONS = ("US", "EU", "UK", "ASIA")
STAND_RE = re.compile(rf"^({'|'.join(STAGES)})_(?:({'|'.join(STAGES)})_)?(?:({'|'.join(REGIONS)})_)?")


def stand_of(key: str) -> str:
    """The stand a variable belongs to, "" when it names none."""
    m = STAND_RE.match(key)
    if not m:
        return ""
    stage, shared, region = (g.lower() if g else "" for g in m.groups())
    name = f"{stage}-{region}" if region else stage
    return f"{stage} / {shared} (shared)" if shared else name


BROKER_OWNER_RE = re.compile(r"^(TINVEST|ALOR|BCS|SBER|VTB)_(?:TOKEN|KEY|SECRET)_(.+)$")
BROKER_PREFIXES: dict[str, str] = {
    "TINVEST": "T-Invest",
    "BYBIT": "Bybit",
    "BINANCE": "Binance",
    "ALOR": "Alor",
    "BCS": "BCS",
}

INTEGRATION_PREFIXES: list[tuple[str, str]] = [
    ("TELEGRAM", "Telegram"),
    ("ALLURE", "Allure TestOps"),
    ("TESTOPS", "Allure TestOps"),
    ("JIRA", "Jira"),
    ("CONFLUENCE", "Confluence"),
    ("ATLASSIAN", "Jira"),
    ("LANGFUSE", "Langfuse"),
    ("LITE_LLM", "LiteLLM"),
    ("LITELLM", "LiteLLM"),
    ("POSTHOG", "PostHog"),
    ("GITLAB", "GitLab"),
    ("GLAB", "GitLab"),
    ("GITHUB", "GitHub"),
    ("ANTHROPIC", "Claude"),
    ("CLAUDE", "Claude"),
    ("OPENAI", "OpenAI"),
    ("GLITCHTIP", "GlitchTip"),
    ("SENTRY", "GlitchTip"),
    ("COOLIFY", "Coolify"),
    ("GRAFANA", "Grafana"),
    ("SMTP", "Mail"),
    ("MAIL", "Mail"),
]

SECRET_WORDS = ("TOKEN", "KEY", "SECRET", "PASSWORD", "PASS", "PAT", "PEPPER", "PRIVATE", "COOKIE", "AUTH")
PUBLIC_WORDS = ("URL", "EMAIL", "HOST", "ENDPOINT", "ID", "NAME", "PATH", "DIR", "PORT")


def parse_env(text: str) -> list[tuple[str, str]]:
    """KEY=VALUE lines; comments, blank lines and `export ` are ignored; quotes are stripped.
    On a repeated key the last one wins — like ordinary loaders do."""
    pairs: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip().lstrip("﻿")
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        pairs[key] = value
    return list(pairs.items())


def is_secret(key: str) -> bool:
    parts = key.upper().split("_")
    if any(p in PUBLIC_WORDS for p in parts) and not any(p in ("TOKEN", "SECRET", "PASSWORD", "PASS") for p in parts):
        return False
    return any(p in SECRET_WORDS for p in parts)


@dataclass
class OwnRule:
    """A rule of the user's own from settings.json ("env_import": [{"prefix": "STRIPE_",
    "list": "Payments", "entry": "Stripe"}]): it goes before the built-in ones."""

    prefix: str
    group: str
    name: str


def own_rules(settings: dict) -> list[OwnRule]:
    """The user's rules; a broken one is skipped. Longer prefixes first."""
    rules = []
    raw = settings.get("env_import")
    for row in raw if isinstance(raw, list) else []:
        if isinstance(row, dict) and str(row.get("prefix") or "").strip():
            prefix = str(row["prefix"]).strip().upper()
            rules.append(OwnRule(prefix, str(row.get("list") or GROUP_OTHER).strip() or GROUP_OTHER, str(row.get("entry") or prefix.strip("_").title()).strip()))
    return sorted(rules, key=lambda r: -len(r.prefix))


def classify(key: str, rules: list[OwnRule] | None = None) -> tuple[str, str]:
    """(list name, entry name) for a variable; the list name is already translated."""
    up = key.upper()
    for rule in rules or []:
        if up.startswith(rule.prefix):
            return (_(rule.group) if rule.group in GROUP_ORDER else rule.group), rule.name
    stand = stand_of(up)
    if stand:
        return _(GROUP_STANDS), stand
    m = BROKER_OWNER_RE.match(up)
    if m:
        return _(GROUP_BROKERS), f"{BROKER_PREFIXES.get(m.group(1), m.group(1).title())} — {m.group(2)}"
    head = up.split("_", 1)[0]
    if head in BROKER_PREFIXES:
        return _(GROUP_BROKERS), BROKER_PREFIXES[head]
    for prefix, name in INTEGRATION_PREFIXES:
        if up == prefix or up.startswith(prefix + "_"):
            return _(GROUP_INTEGRATIONS), name
    return _(GROUP_OTHER), head.title() if head else key


@dataclass
class PlannedEntry:
    group: str
    name: str
    fields: list[Field] = field(default_factory=list)


def plan_import(pairs: list[tuple[str, str]], rules: list[OwnRule] | None = None) -> list[PlannedEntry]:
    """Group pairs into entries. Order — as in the file (first occurrence of the entry)."""
    planned: dict[tuple[str, str], PlannedEntry] = {}
    for key, value in pairs:
        if not value:
            continue  # empty values are not imported — useless in the vault
        group, name = classify(key, rules)
        entry = planned.setdefault((group, name), PlannedEntry(group, name))
        entry.fields.append(Field(key, value, secret=is_secret(key)))
    return list(planned.values())


@dataclass
class ImportResult:
    groups_added: list[str] = field(default_factory=list)
    entries_added: int = 0
    entries_updated: int = 0
    fields_added: int = 0
    fields_updated: int = 0
    fields_same: int = 0

    def summary(self) -> str:
        parts = [_("entries: +{n}").format(n=self.entries_added)]
        if self.entries_updated:
            parts.append(_("extended {n}").format(n=self.entries_updated))
        parts.append(_("fields: +{n}").format(n=self.fields_added))
        if self.fields_updated:
            parts.append(_("updated {n}").format(n=self.fields_updated))
        if self.fields_same:
            parts.append(_("unchanged {n}").format(n=self.fields_same))
        if self.groups_added:
            parts.append(_("lists: {names}").format(names=", ".join(self.groups_added)))
        return " · ".join(parts)


def apply_plan(vault: Vault, plan: list[PlannedEntry]) -> ImportResult:
    """Write the plan into the vault. An entry with the same name in the same list is extended:
    a new field is added, a field with the same key and another value is updated."""
    result = ImportResult()
    for item in plan:
        if item.group not in vault.groups:
            vault.add_group(item.group)
            result.groups_added.append(item.group)
        existing: Entry | None = next(
            (e for e in vault.entries if e.group == item.group and e.name == item.name), None
        )
        if existing is None:
            entry = vault.add_entry(item.name, kind="other", group=item.group)
            entry.fields = []
            result.entries_added += 1
        else:
            entry = existing
        touched = False
        for f in item.fields:
            match = next((x for x in entry.fields if x.key == f.key), None)
            if match is None:
                entry.fields.append(Field(f.key, f.value, f.secret))
                result.fields_added += 1
                touched = True
            elif match.value != f.value:
                match.value = f.value
                result.fields_updated += 1
                touched = True
            else:
                result.fields_same += 1
        if existing is not None and touched:
            result.entries_updated += 1
    return result


def describe_plan(plan: list[PlannedEntry]) -> str:
    """Preview text: list → entries → field names (no values)."""
    by_group: dict[str, list[PlannedEntry]] = {}
    for item in plan:
        by_group.setdefault(item.group, []).append(item)
    lines: list[str] = []
    order = [_(g) for g in GROUP_ORDER]
    for group in order + [g for g in by_group if g not in order]:  # the user's own lists after the built-in ones
        items = by_group.get(group)
        if not items:
            continue
        lines.append(f"▌ {group}")
        for item in items:
            keys = ", ".join(("🔒 " if f.secret else "") + f.key for f in item.fields)
            lines.append(f"    {item.name}  —  {keys}")
        lines.append("")
    return "\n".join(lines).rstrip()
