"""The “From a call…” wizard of the Notes tab: a transcript (or a recording through a
stand-in whisper) and a stand-in assistant become a note card with items and the
transcript attached. Nothing real is started: whisper, ffmpeg and the assistant are Python
children, and the lookups (PATH, GeckIt's folder) are fakes."""
from __future__ import annotations

import json
import os
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests.helpers import reset_globals
from tests.qt_helpers import close_window, open_window, pump, qt_app, require_qt, wait_until

require_qt()

from PySide6.QtCore import Qt  # noqa: E402

from daykeeper.calls import CALLS_DIR, SETTING_BINARY, SETTING_LANGUAGE, SETTING_MODEL, SETTING_THREADS, SETTING_WATCH, SETTING_WATCH_FOLDER, SETTING_WATCH_SINCE  # noqa: E402
from daykeeper.journal import today  # noqa: E402
from daykeeper.qt.dialogs import calls as wizard_mod  # noqa: E402
from daykeeper.qt.dialogs.calls import FILE, REVIEW, CallTools  # noqa: E402

TRANSCRIPT = (
    "Mia: Let's go over the garden club newsletter for May.\n"
    "Leo: The photos from the spring fair are ready, I will pick ten.\n"
    "Mia: Good. Ana writes the piece on tomatoes by Friday.\n"
)
DIGEST = {
    "summary": "The garden club planned the May newsletter.",
    "notes": ["Photos from the spring fair are ready", "The newsletter goes out in May"],
    "tasks": [{"text": "Pick ten photos", "owner": "Leo", "due": ""}, {"text": "Write the tomato piece", "owner": "Ana", "due": "Friday"}],
}


def setUpModule():
    qt_app()


def assistant_script(folder: Path, result: str) -> list[str]:
    """A Python child standing in for `claude -p --output-format json`: reads the prompt,
    prints one result object."""
    script = folder / "fake_assistant.py"
    script.write_text(
        "import json, sys\n"
        "prompt = sys.stdin.read()\n"
        f"print(json.dumps({{'result': {result!r} if 'Transcript:' in prompt else 'no prompt', 'total_cost_usd': 0.01}}))\n",
        encoding="utf-8",
    )
    return [sys.executable, str(script)]


def wav_header() -> bytes:
    """44 bytes of a WAV of 16 kHz mono 16-bit PCM with no samples: no conversion needed."""
    fmt = struct.pack("<HHIIHH", 1, 1, 16000, 32000, 2, 16)
    return b"RIFF" + struct.pack("<I", 36) + b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", 0)


class CallWizardTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.win = open_window(self.tmp.name)
        self.win.show_page("notes")
        pump()
        self.page = self.win.page("notes")
        self.transcript = self.dir / "garden-club.txt"
        self.transcript.write_text(TRANSCRIPT, encoding="utf-8")
        self.wizard = None

    def tearDown(self):
        if self.wizard is not None:
            try:
                self.wizard.close()
            except RuntimeError:  # already deleted
                pass
        close_window(self.win)
        self.tmp.cleanup()
        reset_globals()

    def tools(self, **kw) -> CallTools:
        base = dict(which=lambda _n: None, exists=os.path.isfile, environ={}, assistant=lambda _a, _s: assistant_script(self.dir, json.dumps(DIGEST)))
        base.update(kw)
        return CallTools(**base)

    def open(self, tools: CallTools):
        self.wizard = self.page.from_call(tools)
        pump()
        return self.wizard

    def reach_review(self, w) -> None:
        self.assertTrue(wait_until(lambda: w.step == REVIEW, 15000), w.file_error.text())

    def test_the_button_and_the_tab_menu_open_the_wizard(self):
        self.assertTrue(self.page.call_btn.toolTip())
        menu = self.page.tab_menu()
        texts = [a.text() for a in menu.actions()]
        self.assertIn("From a call…", texts)
        menu.deleteLater()
        self.page.call_btn.click()
        pump()
        opened = [c for c in self.win.findChildren(wizard_mod.CallWizard) if c.isVisible()]
        self.assertEqual(len(opened), 1)
        self.wizard = opened[0]
        self.assertEqual(self.wizard.step, FILE)
        self.assertFalse(self.wizard.isModal())  # the window stays usable while a call is transcribed

    def test_a_transcript_and_a_digest_make_a_card_with_items_and_the_transcript(self):
        w = self.open(self.tools())
        w.choose(str(self.transcript))
        self.reach_review(w)
        self.assertEqual(w.summary.toPlainText(), DIGEST["summary"])
        self.assertEqual(w.notes_list.count(), 2)
        self.assertEqual(w.tasks_list.count(), 2)
        self.assertEqual(w.tasks_list.item(1).text(), "Write the tomato piece (Ana, Friday)")
        self.assertFalse(w.review_error.isVisibleTo(w))
        self.assertEqual(w.title_edit.text(), f"Call {today()}")
        # the second note is unchecked: left out of the card
        w.notes_list.item(1).setCheckState(Qt.CheckState.Unchecked)
        w.create_btn.click()
        pump()
        notes = [n for n in self.win.vault.notes.for_day(today()) if n.title == f"Call {today()}"]
        self.assertEqual(len(notes), 1)
        note = notes[0]
        self.assertEqual(note.text, DIGEST["summary"])
        self.assertEqual([i.text for i in note.items], ["Photos from the spring fair are ready", "→ Pick ten photos (Leo)", "→ Write the tomato piece (Ana, Friday)"])
        self.assertEqual(len(note.attachments), 1)
        path = Path(note.attachments[0].path)
        self.assertEqual(path.parent, self.dir / CALLS_DIR)
        self.assertEqual(path.read_text(encoding="utf-8"), TRANSCRIPT.strip() + "\n")
        if os.name == "posix":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertIn(note.id, self.page.cards)  # the day is shown with the new card
        self.assertEqual(self.win.main_screen.status.text(), f"Note made from the call: Call {today()}")

    def test_subtitles_lose_their_numbers_and_times(self):
        srt = self.dir / "call.srt"
        srt.write_text("1\n00:00:01,000 --> 00:00:03,000\nMia: Hello all.\n\n2\n00:00:03,500 --> 00:00:05,000\nLeo: Hi.\n", encoding="utf-8")
        w = self.open(self.tools(assistant=lambda _a, _s: None))
        w.choose(str(srt))
        self.reach_review(w)
        self.assertEqual(w.transcript, "Mia: Hello all. Leo: Hi.")

    def test_an_unreadable_answer_shows_the_raw_text(self):
        w = self.open(self.tools(assistant=lambda _a, _s: assistant_script(self.dir, "Sorry, I would rather chat about tomatoes.")))
        w.choose(str(self.transcript))
        self.reach_review(w)
        self.assertTrue(w.review_error.isVisibleTo(w))
        self.assertTrue(w.raw.isVisibleTo(w))
        self.assertEqual(w.raw.toPlainText(), "Sorry, I would rather chat about tomatoes.")
        self.assertEqual(w.notes_list.count(), 0)
        w.create_btn.click()  # the card from the transcript alone
        pump()
        note = next(n for n in self.win.vault.notes.for_day(today()) if n.title == f"Call {today()}")
        self.assertEqual((note.text, note.items, len(note.attachments)), ("", [], 1))

    def test_without_an_assistant_the_card_holds_the_transcript_alone(self):
        w = self.open(self.tools(assistant=None))  # the default lookup, with `which` finding nothing
        w.choose(str(self.transcript))
        self.reach_review(w)
        self.assertTrue(w.review_note.isVisibleTo(w))
        self.assertIn("No AI assistant", w.review_note.text())
        self.assertEqual(w.summary.toPlainText(), "")
        w.create_btn.click()
        pump()
        note = next(n for n in self.win.vault.notes.for_day(today()) if n.title == f"Call {today()}")
        self.assertEqual(note.text, "")
        self.assertEqual(note.items, [])
        self.assertEqual(Path(note.attachments[0].path).read_text(encoding="utf-8"), TRANSCRIPT.strip() + "\n")

    def test_a_recording_without_whisper_says_so_and_offers_the_settings(self):
        wav = self.dir / "call.wav"
        wav.write_bytes(wav_header())
        w = self.open(self.tools(exists=lambda _p: False))
        w.choose(str(wav))
        self.assertEqual(w.step, FILE)
        self.assertTrue(w.file_error.isVisibleTo(w))
        self.assertIn("whisper.cpp is not found", w.file_error.text())
        self.assertTrue(w.settings_btn.isVisibleTo(w))
        self.assertIsNone(w.child)

    def test_a_recording_without_a_model_says_so(self):
        binary = self.dir / "whisper-cli.exe"
        binary.write_bytes(b"")
        self.win.session.write_settings({SETTING_BINARY: str(binary)})
        wav = self.dir / "call.wav"
        wav.write_bytes(wav_header())
        w = self.open(self.tools())
        w.choose(str(wav))
        self.assertIn("no model", w.file_error.text())
        self.assertTrue(w.settings_btn.isVisibleTo(w))

    def test_a_compressed_recording_without_ffmpeg_asks_for_a_wav(self):
        binary, model = self.dir / "whisper-cli.exe", self.dir / "ggml-base.bin"
        binary.write_bytes(b"")
        model.write_bytes(b"")
        self.win.session.write_settings({SETTING_BINARY: str(binary), SETTING_MODEL: str(model)})
        mp3 = self.dir / "call.mp3"
        mp3.write_bytes(b"ID3")
        w = self.open(self.tools(ffmpeg=lambda _s, _d: None))
        w.choose(str(mp3))
        self.assertEqual(w.step, FILE)
        self.assertIn("ffmpeg is not found", w.file_error.text())
        self.assertIsNone(w.tmp)  # the temp folder went with the failure

    def whisper_ready(self, threads: int = 0) -> tuple[Path, Path]:
        binary, model = self.dir / "whisper-cli.exe", self.dir / "ggml-base.bin"
        binary.write_bytes(b"")
        model.write_bytes(b"")
        self.win.session.write_settings({SETTING_BINARY: str(binary), SETTING_MODEL: str(model), SETTING_LANGUAGE: "en", SETTING_THREADS: threads})
        wav = self.dir / "call.wav"
        wav.write_bytes(wav_header())
        return model, wav

    def test_a_recording_goes_through_whisper_then_the_digest(self):
        model, wav = self.whisper_ready(threads=3)
        seen = []

        def transcribe(binary, mdl, src, language, threads):
            seen.append((mdl, src, language, threads))
            return [sys.executable, "-c", "print('[00:00:00.000 --> 00:00:02.000]  Mia: Hello garden club.'); print('[BLANK_AUDIO]')"]

        w = self.open(self.tools(transcribe=transcribe))
        w.choose(str(wav))
        self.reach_review(w)
        self.assertEqual(seen, [(str(model), str(wav), "en", 3)])  # a ready WAV goes to whisper as is
        self.assertEqual(w.transcript, "Mia: Hello garden club.")
        self.assertEqual(w.notes_list.count(), 2)

    def test_stop_kills_the_background_process(self):
        _model, wav = self.whisper_ready()
        w = self.open(self.tools(transcribe=lambda *_a: [sys.executable, "-c", "import time; time.sleep(60)"]))
        w.choose(str(wav))
        self.assertIsNotNone(w.child)
        proc = w.child.proc
        tmp = w.tmp
        self.assertIsNone(proc.poll())
        self.assertNotEqual(w.step, REVIEW)
        w.stop_btn.click()
        self.assertTrue(wait_until(lambda: proc.poll() is not None, 10000))
        self.assertEqual(w.step, FILE)
        self.assertEqual(w.file_error.text(), "Stopped")
        self.assertFalse(os.path.exists(tmp))
        pump(100)
        self.assertEqual(w.step, FILE)  # the killed child's end does not move the wizard on

    def test_closing_the_wizard_kills_the_background_process(self):
        _model, wav = self.whisper_ready()
        w = self.open(self.tools(transcribe=lambda *_a: [sys.executable, "-c", "import time; time.sleep(60)"]))
        w.choose(str(wav))
        proc = w.child.proc
        w.close()
        self.wizard = None
        self.assertTrue(wait_until(lambda: proc.poll() is not None, 10000))

    def test_a_failing_whisper_shows_its_error(self):
        _model, wav = self.whisper_ready()
        w = self.open(self.tools(transcribe=lambda *_a: [sys.executable, "-c", "import sys; sys.stderr.write('bad model file'); sys.exit(3)"]))
        w.choose(str(wav))
        self.assertTrue(wait_until(lambda: w.child is None, 10000))
        self.assertEqual(w.step, FILE)
        self.assertIn("bad model file", w.file_error.text())


class CallsSettingsTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.win = open_window(self.tmp.name)

    def tearDown(self):
        close_window(self.win)
        self.tmp.cleanup()
        reset_globals()

    def test_the_calls_group_saves_paths_language_and_threads(self):
        self.win.settings_dialog("general")
        pump()
        dlg = self.win._settings_win
        parts = dlg.calls_parts
        edits = parts["edits"]
        edits[SETTING_BINARY].setText("C:/tools/whisper-cli.exe")
        edits[SETTING_BINARY].editingFinished.emit()
        edits[SETTING_LANGUAGE].setText("-h")
        parts["save_language"]()
        self.assertTrue(parts["error"].isVisibleTo(dlg))
        self.assertNotIn(SETTING_LANGUAGE, self.win.session.settings())
        edits[SETTING_LANGUAGE].setText("RU")
        parts["save_language"]()
        self.assertFalse(parts["error"].isVisibleTo(dlg))
        parts["threads"].setCurrentIndex(parts["threads"].findData(4))
        s = self.win.session.settings()
        self.assertEqual((s[SETTING_BINARY], s[SETTING_LANGUAGE], s[SETTING_THREADS]), ("C:/tools/whisper-cli.exe", "ru", 4))
        dlg.close()
        pump()


class CallWatchTest(unittest.TestCase):
    """The watch over a folder: a transcript that comes there is offered by a toast, a
    click opens the wizard on it. A temp folder stands for Downloads."""

    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.inbox = self.dir / "inbox"
        self.inbox.mkdir()
        self.win = open_window(self.tmp.name)
        self.win.session.write_settings({SETTING_WATCH_FOLDER: str(self.inbox)})
        self.watch = self.win.calls
        self.watch.active = True  # the test window is a preview, which watches nothing
        self.watch.now = lambda: time.time() + 10  # the files written here have settled
        self.watch.tools = CallTools(which=lambda _n: None, exists=os.path.isfile, environ={}, assistant=lambda _a, _s: assistant_script(self.dir, json.dumps(DIGEST)))

    def tearDown(self):
        if self.watch.wizard is not None:
            try:
                self.watch.wizard.close()
            except RuntimeError:
                pass
        close_window(self.win)
        self.tmp.cleanup()
        reset_globals()

    def mark_before_now(self) -> None:
        self.win.session.write_settings({SETTING_WATCH_SINCE: time.time() - 60})

    def test_a_downloaded_transcript_is_offered_and_opens_the_wizard(self):
        self.mark_before_now()
        call = self.inbox / "meeting.txt"
        call.write_text("\n".join(f"00:00:{i * 9:02d} {line}" for i, line in enumerate(TRANSCRIPT.splitlines() * 3)), encoding="utf-8")
        (self.inbox / "list.txt").write_text("milk\nbread\n", encoding="utf-8")
        self.assertEqual(self.watch.look(), [call])
        self.assertEqual(len(self.watch.toasts), 1)
        self.assertIn("meeting.txt", self.watch.toasts[0].text.text())
        self.assertEqual(self.watch.look(), [])  # offered once
        self.watch.toasts[0]._action()
        w = self.watch.wizard
        self.assertEqual(self.win.mode, "notes")
        self.assertTrue(wait_until(lambda: w.step == REVIEW, 15000), w.file_error.text())
        self.assertEqual(w.summary.toPlainText(), DIGEST["summary"])

    def test_the_card_is_named_after_the_meeting_in_the_heading(self):
        self.mark_before_now()
        call = self.inbox / "talk.txt"
        lines = [f"00:00:{i * 9:02d}\t{line.split(':')[0]}\t{line.split(': ', 1)[1]}" for i, line in enumerate(TRANSCRIPT.splitlines() * 3)]
        call.write_text('Transcript of "Recording Garden club " 19 May\n' + "\n".join(lines), encoding="utf-8")
        self.assertEqual(self.watch.look(), [call])
        w = self.watch.open(call)
        self.assertTrue(wait_until(lambda: w.step == REVIEW, 15000), w.file_error.text())
        self.assertEqual(w.title_edit.text(), f"Garden club {today()}")

    def test_the_first_look_only_marks_what_is_there(self):
        (self.inbox / "old.txt").write_text("\n".join(f"0:{i:02d} {TRANSCRIPT}" for i in range(6)), encoding="utf-8")
        self.assertEqual(self.watch.look(), [])
        self.assertIsInstance(self.win.session.settings().get(SETTING_WATCH_SINCE), float)
        self.assertEqual(self.watch.toasts, [])

    def test_off_preview_or_locked_looks_at_nothing(self):
        self.mark_before_now()
        (self.inbox / "c.txt").write_text("\n".join(f"0:{i:02d} {TRANSCRIPT}" for i in range(6)), encoding="utf-8")
        self.win.session.write_settings({SETTING_WATCH: False})
        self.assertEqual(self.watch.look(), [])
        self.win.session.write_settings({SETTING_WATCH: True})
        self.watch.active = False
        self.assertEqual(self.watch.look(), [])
        self.watch.active = True
        self.watch.restart()
        self.assertTrue(self.watch._ticker.running)
        self.win.lock()
        pump()
        self.assertFalse(self.watch._ticker.running)
        self.assertEqual(self.watch.look(), [])

    def test_the_setting_turns_it_off(self):
        self.watch.restart()
        self.win.settings_dialog("general")
        pump()
        dlg = self.win._settings_win
        dlg.calls_parts["watch"].setChecked(False)
        self.assertFalse(self.win.session.settings()[SETTING_WATCH])
        self.assertFalse(self.watch._ticker.running)
        self.assertFalse(dlg.calls_parts["edits"][SETTING_WATCH_FOLDER].isEnabled())
        dlg.close()
        pump()


if __name__ == "__main__":
    unittest.main()
