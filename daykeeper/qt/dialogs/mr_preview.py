"""The merge request preview of the Qt window: one MR as a Markdown page in the built-in
viewer, with the buttons that hand it to an AI assistant.

The page is fetched in the background (`GitLabClient.mr` and `mr_changes` with the token of
the GitLab entry), rendered by `mr_preview.render_mr` and written owner-only to
`<data>/previews/<project>-<iid>.md`; the window shows “Loading…” until then and the
error's text when the fetch fails. The viewer runs read-only and opens web links only:
the page is the server's text.

“Review”, “Fixes after review” and “Brief” build an assignment from the presets of
assistants.py and give it to `shell.send_to_ai(assignment, quiet=…, on_answer=…)` — the
send dialog, wired in by the window when it has one. Without it the assignment's text is
copied for the user to paste into an assistant. “Brief” goes by the quiet channel with
the page after the prompt; its answer is shown above the diff (`show_brief`)."""

from __future__ import annotations

import re
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import shiboken6
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from ... import palette
from ...assistants import PRESETS, Assignment, assignment_from, mr_context, project_for, read_projects
from ...bookmarks import entry_secret
from ...gitlab import Event, GitLabClient, GitLabError, project_of, web_url
from ...i18n import _
from ...mr_preview import mr_template_context, render_mr
from ...settings import private
from ..errors import guard_events
from ..jobs import run_in_thread
from .base import button, label
from .fileview import FileViewer

PREVIEWS_DIR = "previews"
_SLUG = re.compile(r"[^A-Za-z0-9._-]+")
_WEB = ("http://", "https://")


@dataclass
class MrTarget:
    """Which merge request: the project (its id, or the “group/app” path), the number, and
    what the feed already knows about it."""

    project: int | str
    iid: int
    url: str = ""
    title: str = ""
    role: str = ""  # "author" | "reviewer" | "" — my part in it
    path: str = ""  # "group/app" when known

    @property
    def ref(self) -> str:
        return f"{self.path or self.project}!{self.iid}"


def target_of(ev: Event) -> MrTarget | None:
    """The merge request of a GitLab feed event; None for a pipeline or Jira event. An older
    record has no project id: the path from its reference stands in."""
    if ev.source != "gitlab" or "!" not in ev.mr:
        return None
    path, number = project_of(ev.mr), ev.mr.rsplit("!", 1)[1]
    if not number.isdigit() or int(number) < 1 or not path:
        return None
    path = "" if path.isdigit() else path
    project: int | str = ev.project_id or path or project_of(ev.mr)
    return MrTarget(project, int(number), web_url(ev.url), ev.title, ev.role, path)


def preview_path(data_dir: Path, target: MrTarget) -> Path:
    """`<data>/previews/<project-slug>-<iid>.md`: no part of the name can leave the folder."""
    slug = _SLUG.sub("-", str(target.path or target.project)).strip("-.")[:80] or "mr"
    return Path(data_dir) / PREVIEWS_DIR / f"{slug}-{int(target.iid)}.md"


def load_mr(base_url: str, token: str, project: int | str, iid: int, fetch: Any = None) -> tuple[dict, dict]:
    """The merge request and its changes, on a worker thread."""
    client = GitLabClient(base_url, token, fetch=fetch)
    try:
        return client.mr(project, iid), client.mr_changes(project, iid)
    finally:
        client.close()


def write_preview(path: Path, text: str) -> None:
    """The page, owner-only, its folder too (it holds a diff of the user's work)."""
    from ...vault import write_private  # the crypto module: only when a page is written

    if not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        private(path.parent, folder=True)
    write_private(path, text.encode("utf-8"))


def gitlab_token(shell: Any) -> str:
    vault = shell.vault
    entry = vault.entry_by_id(vault.integrations.gitlab.entry_id) if vault is not None else None
    return entry_secret(entry) if entry is not None else ""


def context_of(target: MrTarget, mr_json: dict | None = None) -> dict[str, str]:
    """{mr_url} {mr_title} {mr_project} {mr_iid}: from the fetched MR when there is one, the
    feed's facts filling what it lacks."""
    known = mr_context(target.url, target.title, target.path or str(target.project), target.iid)
    found = mr_template_context(mr_json) if mr_json else {}
    return {k: found.get(k) or v for k, v in known.items()}


def mr_assignment(shell: Any, kind: str, ctx: dict[str, str]) -> Assignment:
    """The assignment of a preset (`mr_review`, `mr_fix`, `mr_brief`), in the project folder
    the project map names for the MR's project."""
    template = next(t for t in PRESETS if t.id == kind)
    projects = read_projects(shell.session.settings())
    project = project_for(projects, ctx.get("mr_project", ""))
    return assignment_from(template, ctx, folder=project.folder if project else "", project=project.geckit_name if project else "",
                           links=[ctx["mr_url"]] if ctx.get("mr_url") else [])


def send(shell: Any, assignment: Assignment, quiet: bool = False, on_answer: Callable[[str], None] | None = None,
         gitlab_path: str = "") -> bool:
    """Hand the assignment to the window's send dialog (`send_to_ai`); without one, copy its
    text. True — it went to the dialog."""
    hand = getattr(shell, "send_to_ai", None)
    if callable(hand):
        hand(assignment, quiet=quiet, on_answer=on_answer, gitlab_path=gitlab_path)
        return True
    shell.copy_to_clipboard(assignment.text, secret=False)
    shell.set_status(_("Copied: paste it into your assistant"))
    return False


def send_mr(shell: Any, kind: str, target: MrTarget) -> bool:
    """“Review” / “Fixes” of a feed card, without opening the preview."""
    ctx = context_of(target)
    return send(shell, mr_assignment(shell, kind, ctx), gitlab_path=ctx.get("mr_project", ""))


@guard_events
class MrPreview(FileViewer):
    """One merge request: the page, the brief above it once asked for, the buttons under it.
    `fetch` — GitLabClient's (tests put a fake one there)."""

    def __init__(self, shell: Any, target: MrTarget, fetch: Any = None):
        self.state = "loading"  # "loading" | "ready" | "error"
        self.message = _("Loading…")
        super().__init__(shell, preview_path(shell.vault_path.parent, target), web_only=True)
        self.shell = shell
        self.target = target
        self.fetch = fetch
        self.mr_json: dict | None = None
        self.markdown = ""
        self.url = target.url
        self.role = target.role
        self.setWindowTitle(f"{target.ref} — {palette.APP_TITLE}")
        self.head.set_text(f"🦊 {target.ref}")

        col = cast(QVBoxLayout, self.layout())  # FileViewer's column
        self.brief = QWidget(self)
        self.brief.setObjectName("Card")
        self.brief.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        bcol = QVBoxLayout(self.brief)
        bcol.setContentsMargins(16, 10, 16, 10)
        bcol.addWidget(label(_("BRIEF"), "muted", self.brief))
        self.brief_text = label("", "", self.brief)
        self.brief_text.setTextFormat(Qt.TextFormat.PlainText)  # the assistant's words, never markup
        self.brief_text.setWordWrap(True)
        self.brief_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        bcol.addWidget(self.brief_text)
        self.brief.hide()
        col.insertWidget(col.indexOf(self.stack), self.brief)

        bar = QHBoxLayout()
        bar.setContentsMargins(16, 8, 16, 10)
        bar.setSpacing(8)
        self.review_btn = button(_("Review"), "primary", self)
        self.review_btn.clicked.connect(lambda: self.ask("mr_review"))
        self.fix_btn = button(_("Fixes after review"), "", self)
        self.fix_btn.clicked.connect(lambda: self.ask("mr_fix"))
        self.brief_btn = button(_("Brief"), "", self)
        self.brief_btn.clicked.connect(self.ask_brief)
        self.browser_btn = button(_("Open in browser"), "link", self)
        self.browser_btn.clicked.connect(self.open_in_browser)
        for b in (self.review_btn, self.fix_btn, self.brief_btn):
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(self.browser_btn)
        col.addLayout(bar)
        self.status = label("", "muted", self)
        self.status.setWordWrap(True)
        self.status.setContentsMargins(16, 0, 16, 10)
        col.addWidget(self.status)
        self._update_buttons()
        self.load()

    # ---- the page

    def reload(self) -> None:
        if getattr(self, "state", "ready") != "ready":
            self.view.setPlainText(self.message)
            return
        super().reload()

    def load(self) -> None:
        vault = self.shell.vault
        gl = vault.integrations.gitlab if vault is not None else None
        token = gitlab_token(self.shell)
        if gl is None or not gl.url or not token:
            self._fail(_("Fill in the address and pick the entry with the token"))
            return
        self.state, self.message = "loading", _("Loading…")
        self.reload()
        url, project, iid, fetch = gl.url, self.target.project, self.target.iid, self.fetch
        run_in_thread(lambda: load_mr(url, token, project, iid, fetch), self._loaded, owner=self, name="mr-preview")

    def _fail(self, text: str) -> None:
        self.state, self.message = "error", text
        self.reload()
        self._update_buttons()

    def _loaded(self, result: tuple[dict, dict] | None, error: BaseException | None) -> None:
        if error is not None or result is None:
            text = _(str(error)) if isinstance(error, GitLabError) else (str(error) or type(error).__name__)
            self._fail(_("Could not load the merge request:\n{error}").format(error=text))
            return
        mr, changes = result
        self.markdown = render_mr(mr, changes)
        try:
            write_preview(self.path, self.markdown)
        except OSError as exc:
            self._fail(_("Could not write: {error}").format(error=exc))
            return
        self.mr_json = mr
        self.url = web_url(mr.get("web_url")) or self.url
        if not self.role:  # an MR opened without the feed's facts: mine when I wrote it
            me = self.shell.vault.integrations.gitlab.username if self.shell.vault is not None else ""
            author = (mr.get("author") or {}).get("username") if isinstance(mr.get("author"), dict) else ""
            self.role = "author" if me and author == me else ""
        self.state = "ready"
        self.reload()
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.fix_btn.setVisible(self.role == "author")
        self.brief_btn.setEnabled(self.state == "ready")
        self.browser_btn.setEnabled(self.url.startswith(_WEB))

    # ---- the buttons

    def context(self) -> dict[str, str]:
        return context_of(self.target, self.mr_json)

    def ask(self, kind: str) -> bool:
        """“Review” / “Fixes after review”: True — handed to the send dialog."""
        ctx = self.context()
        sent = send(self.shell, mr_assignment(self.shell, kind, ctx), gitlab_path=ctx.get("mr_project", ""))
        self.status.setText(_("Sent to the assistant dialog") if sent else _("Copied: paste it into your assistant"))
        return sent

    def ask_brief(self) -> bool:
        """“Brief” by the quiet channel: the prompt, then the page with the diff."""
        if self.state != "ready":
            return False
        a = mr_assignment(self.shell, "mr_brief", self.context())
        a.text = f"{a.text.rstrip()}\n\n{self.markdown}"
        self.show_brief(_("Asking for a brief…"))  # before: an answer that cannot wait replaces it at once
        sent = send(self.shell, a, quiet=True, on_answer=self._answer_hook())
        if not sent:
            self.brief.hide()
            self.status.setText(_("Copied: paste it into your assistant"))
        return sent

    def _answer_hook(self) -> Callable[[str], None]:
        """The answer may come after the window closed: then it goes nowhere."""
        def answer(text: str) -> None:
            if shiboken6.isValid(self):
                self.show_brief(text)
        return answer

    def show_brief(self, text: str) -> None:
        self.brief_text.setText(text.strip())
        self.brief.show()

    def open_in_browser(self) -> bool:
        if not self.url.startswith(_WEB):
            return False
        webbrowser.open(self.url)
        return True
