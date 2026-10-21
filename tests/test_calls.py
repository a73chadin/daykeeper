"""Call → note: finding whisper.cpp, the command lines, reading whisper's output and the
assistant's digest, the note card and the transcript file. Fakes for PATH and the file
system; temporary folders only. No whisper, ffmpeg or assistant is ever started."""

import os
import struct
import tempfile
import unittest
from pathlib import Path

from daykeeper import calls
from daykeeper.i18n import language, set_language

WIN_ENV = {"LOCALAPPDATA": r"C:\Users\sam\AppData\Local"}
GECKIT_WIN = r"C:\Users\sam\AppData\Local\Programs\geckit\resources\whisper\whisper-cli.exe"


def which_of(found: dict):
    return lambda name: found.get(name)


def files(*paths):
    present = set(paths)
    return lambda p: p in present


class WhisperBinaryTest(unittest.TestCase):
    def test_the_setting_comes_first(self):
        got = calls.whisper_binary({calls.SETTING_BINARY: r"D:\tools\whisper-cli.exe"}, which_of({"whisper-cli": "/usr/bin/whisper-cli"}), files(r"D:\tools\whisper-cli.exe", GECKIT_WIN), WIN_ENV, "win32")
        self.assertEqual(got, r"D:\tools\whisper-cli.exe")

    def test_a_missing_setting_falls_through_to_path(self):
        got = calls.whisper_binary({calls.SETTING_BINARY: r"D:\gone.exe"}, which_of({"whisper-cli": r"C:\bin\whisper-cli.exe"}), files(GECKIT_WIN), WIN_ENV, "win32")
        self.assertEqual(got, r"C:\bin\whisper-cli.exe")

    def test_homebrew_name_on_path(self):
        got = calls.whisper_binary({}, which_of({"whisper-cpp": "/opt/homebrew/bin/whisper-cpp"}), files(), {}, "darwin")
        self.assertEqual(got, "/opt/homebrew/bin/whisper-cpp")

    def test_geckit_copy_on_windows(self):
        self.assertEqual(calls.whisper_binary({}, which_of({}), files(GECKIT_WIN), WIN_ENV, "win32"), GECKIT_WIN)

    def test_geckit_bundle_on_macos(self):
        user_bundle = "/Users/sam/Applications/GeckIt.app/Contents/Resources/whisper/whisper-cli"
        self.assertEqual(calls.whisper_binary({}, which_of({}), files(user_bundle), {"HOME": "/Users/sam"}, "darwin"), user_bundle)
        system = "/Applications/GeckIt.app/Contents/Resources/whisper/whisper-cli"
        self.assertEqual(calls.whisper_binary({}, which_of({}), files(system, user_bundle), {"HOME": "/Users/sam"}, "darwin"), system)

    def test_nothing_found(self):
        self.assertIsNone(calls.whisper_binary({}, which_of({}), files(), WIN_ENV, "win32"))
        self.assertIsNone(calls.whisper_binary({}, which_of({}), files(), {}, "win32"))
        self.assertIsNone(calls.whisper_binary({}, which_of({}), files(), {"HOME": "/home/sam"}, "linux"))


class WhisperModelTest(unittest.TestCase):
    folder = r"C:\w"

    def listing(self, tree: dict):
        def listdir(path):
            if path not in tree:
                raise FileNotFoundError(path)
            return tree[path]

        return listdir

    def test_the_setting_comes_first(self):
        got = calls.whisper_model({calls.SETTING_MODEL: r"D:\m\ggml-small.bin"}, r"C:\w\whisper-cli.exe", self.listing({}), files(r"D:\m\ggml-small.bin"), "win32")
        self.assertEqual(got, r"D:\m\ggml-small.bin")

    def test_the_best_model_next_to_the_binary(self):
        names = ["readme.txt", "ggml-tiny.bin", "ggml-medium.bin", "ggml-base.bin", "whisper-cli.exe"]
        present = files(*(rf"C:\w\{n}" for n in names))
        got = calls.whisper_model({}, r"C:\w\whisper-cli.exe", self.listing({self.folder: names}), present, "win32")
        self.assertEqual(got, r"C:\w\ggml-medium.bin")

    def test_models_subfolder(self):
        listdir = self.listing({self.folder: ["whisper-cli.exe"], r"C:\w\models": ["ggml-base.en.bin"]})
        got = calls.whisper_model({}, r"C:\w\whisper-cli.exe", listdir, files(r"C:\w\models\ggml-base.en.bin"), "win32")
        self.assertEqual(got, r"C:\w\models\ggml-base.en.bin")

    def test_no_model(self):
        self.assertIsNone(calls.whisper_model({}, r"C:\w\whisper-cli.exe", self.listing({}), files(), "win32"))
        self.assertIsNone(calls.whisper_model({}, None, self.listing({}), files(), "win32"))


class ArgvTest(unittest.TestCase):
    def test_transcribe(self):
        self.assertEqual(
            calls.transcribe_argv("whisper-cli", "m.bin", "call.wav", "en", 4),
            ["whisper-cli", "-m", "m.bin", "-f", "call.wav", "-l", "en", "-t", "4", "-nt", "-np"],
        )

    def test_transcribe_defaults(self):
        argv = calls.transcribe_argv("w", "m", "a.wav", "", 0)
        self.assertEqual(argv[argv.index("-l") + 1], calls.DEFAULT_LANGUAGE)
        self.assertGreaterEqual(int(argv[argv.index("-t") + 1]), 1)

    def test_transcribe_language_is_a_code_or_auto(self):
        for given, expected in (("en", "en"), (" RU ", "ru"), ("yue", "yue"), ("auto", "auto"), (None, "auto"),
                                ("-h", "auto"), ("--help", "auto"), ("e", "auto"), ("engl", "auto"), ("e1", "auto"),
                                ("en -t 64", "auto"), ("ру", "auto")):
            with self.subTest(given=given):
                argv = calls.transcribe_argv("w", "m", "a.wav", given, 2)  # type: ignore[arg-type]
                self.assertEqual(argv[argv.index("-l") + 1], expected)
                self.assertEqual(len(argv), 11)

    def test_default_threads(self):
        self.assertEqual(calls.default_threads(1), 1)
        self.assertEqual(calls.default_threads(8), 4)
        self.assertEqual(calls.default_threads(64), 8)
        self.assertEqual(calls.default_threads(0), 1)

    def test_ffmpeg_only_when_found(self):
        self.assertIsNone(calls.ffmpeg_argv("a.mp3", "a.wav", which_of({})))
        argv = calls.ffmpeg_argv("a.mp3", "a.wav", which_of({"ffmpeg": "/usr/bin/ffmpeg"}))
        assert argv is not None
        self.assertEqual(argv[0], "/usr/bin/ffmpeg")
        self.assertEqual(argv[argv.index("-i") + 1], "file:a.mp3")
        self.assertEqual(argv[argv.index("-ar") + 1], "16000")
        self.assertEqual(argv[argv.index("-ac") + 1], "1")
        self.assertEqual(argv[-1], "a.wav")
        self.assertIn("-nostdin", argv)

    def test_ffmpeg_reads_the_input_as_a_file_only(self):
        found = which_of({"ffmpeg": "ffmpeg"})
        for src in ("concat:a.mp3|b.mp3", "http://example.com/a.mp3", "-i.mp3", r"C:\Users\me\call.mp3", "/home/me/call.mp3"):
            with self.subTest(src=src):
                argv = calls.ffmpeg_argv(src, "out.wav", found)
                assert argv is not None
                self.assertEqual(argv[argv.index("-i") + 1], "file:" + src)
        argv = calls.ffmpeg_argv(Path("dir") / "call.mp3", "out.wav", found)
        assert argv is not None
        self.assertEqual(argv[argv.index("-i") + 1], "file:" + str(Path("dir") / "call.mp3"))


def wav_head(rate=16000, channels=1, bits=16, fmt=1) -> bytes:
    block = channels * bits // 8
    return b"RIFF" + struct.pack("<I", 36) + b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, fmt, channels, rate, rate * block, block, bits)


class ConversionTest(unittest.TestCase):
    def test_compressed_audio(self):
        for name in ("a.mp3", "b.M4A", "c.ogg", "d.webm"):
            self.assertTrue(calls.needs_conversion(name), name)

    def test_wav_and_text(self):
        self.assertFalse(calls.needs_conversion("a.wav"))
        self.assertFalse(calls.needs_conversion("notes.txt"))
        self.assertTrue(calls.is_transcript("notes.md"))
        self.assertFalse(calls.is_transcript("a.wav"))
        self.assertTrue(calls.is_audio("a.wav"))
        self.assertTrue(calls.is_audio("a.mp3"))

    def test_wav_by_its_header(self):
        self.assertFalse(calls.needs_conversion("a.wav", wav_head()))
        self.assertTrue(calls.needs_conversion("a.wav", wav_head(rate=44100)))
        self.assertTrue(calls.needs_conversion("a.wav", wav_head(channels=2)))
        self.assertTrue(calls.needs_conversion("a.wav", wav_head(bits=24)))
        self.assertTrue(calls.needs_conversion("a.wav", wav_head(fmt=3)))
        self.assertTrue(calls.needs_conversion("a.wav", b"not a wav"))


class TranscriptTest(unittest.TestCase):
    def test_plain_segments(self):
        out = " Good morning, everyone.\n\n Let's start with the release.\n"
        self.assertEqual(calls.parse_transcript(out), "Good morning, everyone. Let's start with the release.")

    def test_timestamps_are_stripped(self):
        out = "[00:00:00.000 --> 00:00:02.500]   Hello team.\n[00:00:02.500 --> 00:00:05.000]  The demo is on Friday.\n"
        self.assertEqual(calls.parse_transcript(out), "Hello team. The demo is on Friday.")

    def test_subtitle_format(self):
        out = "WEBVTT\n\n1\n00:00.000 --> 00:02.000\nFirst line.\n\n2\n00:02.000 --> 00:04.000\nSecond line.\n"
        self.assertEqual(calls.parse_transcript(out), "First line. Second line.")

    def test_markers_are_dropped(self):
        out = "[BLANK_AUDIO]\n (music)\n Okay [Music] so the plan is ready.\n *laughter*\n[ Silence ]\n(тишина)\n"
        self.assertEqual(calls.parse_transcript(out), "Okay so the plan is ready.")

    def test_speech_in_brackets_stays(self):
        out = "Ask Mia (she owns the design) about it. [OK?]\n"
        self.assertEqual(calls.parse_transcript(out), "Ask Mia (she owns the design) about it. [OK?]")

    def test_empty(self):
        self.assertEqual(calls.parse_transcript(""), "")
        self.assertEqual(calls.parse_transcript("[BLANK_AUDIO]\n"), "")


CLEAN = '{"summary": "The team planned the garden app demo.", "notes": ["Demo on Friday", "Use the new icons"], "tasks": [{"text": "Prepare the slides", "owner": "Mia", "due": "Thursday"}, {"text": "Book the room", "owner": "", "due": ""}]}'


class DigestTest(unittest.TestCase):
    def test_prompt(self):
        prompt = calls.digest_prompt("  We agreed to ship on Friday.  ")
        self.assertIn("JSON only", prompt)
        for key in ('"summary"', '"notes"', '"tasks"', '"owner"', '"due"'):
            self.assertIn(key, prompt)
        self.assertTrue(prompt.rstrip().endswith("We agreed to ship on Friday."))

    def test_clean(self):
        d = calls.parse_digest(CLEAN)
        self.assertEqual(d.summary, "The team planned the garden app demo.")
        self.assertEqual(d.notes, ["Demo on Friday", "Use the new icons"])
        self.assertEqual(d.tasks, [calls.Task("Prepare the slides", "Mia", "Thursday"), calls.Task("Book the room")])

    def test_prose_and_fence(self):
        text = "Sure! Here is the digest:\n```json\n" + CLEAN + "\n```\nLet me know if you need more."
        self.assertEqual(calls.parse_digest(text), calls.parse_digest(CLEAN))
        self.assertEqual(calls.parse_digest("Here it is: " + CLEAN + " Done."), calls.parse_digest(CLEAN))

    def test_braces_in_prose_before_the_object(self):
        text = "Format {like this}: " + CLEAN
        self.assertEqual(calls.parse_digest(text).summary, "The team planned the garden app demo.")

    def test_no_tasks_and_missing_fields(self):
        d = calls.parse_digest('{"summary": "Short sync.", "notes": []}')
        self.assertEqual((d.summary, d.notes, d.tasks), ("Short sync.", [], []))
        d = calls.parse_digest('{"notes": "Only one note"}')
        self.assertEqual((d.summary, d.notes, d.tasks), ("", ["Only one note"], []))

    def test_tasks_as_strings(self):
        d = calls.parse_digest('{"summary": "x", "tasks": ["Fix the login page", "", 42, {"text": "Call Leo", "owner": null}]}')
        self.assertEqual(d.tasks, [calls.Task("Fix the login page"), calls.Task("42"), calls.Task("Call Leo")])

    def test_broken_is_empty_not_an_error(self):
        for text in ("", "no json here", '{"summary": "cut off', "[1, 2, 3]", "```json\n{broken\n```", None):
            d = calls.parse_digest(text)  # type: ignore[arg-type]
            self.assertTrue(d.is_empty(), repr(text))

    def test_deep_nesting_is_empty_not_an_error(self):
        deep = "[" * 100_000 + "]" * 100_000  # past the parser's stack limit: a RecursionError inside
        for text in ('{"a": ' + deep + "}", '{"a": ' + "[" * 100_000, "```json\n" + '{"a": ' + deep + "}\n```"):
            with self.subTest(size=len(text)):
                self.assertTrue(calls.parse_digest(text).is_empty())
        self.assertEqual(calls.parse_digest('{"x": ' + deep + '} {"summary": "ok"}').summary, "ok", "the next object is still found")

    def test_odd_types(self):
        d = calls.parse_digest('{"summary": {"a": 1}, "notes": 5, "tasks": "Write the report"}')
        self.assertEqual((d.summary, d.notes, d.tasks), ("", [], [calls.Task("Write the report")]))


class NoteTest(unittest.TestCase):
    def setUp(self):
        self.lang = language()
        self.addCleanup(set_language, self.lang)
        set_language("en")

    def test_card(self):
        draft = calls.note_from_digest(calls.parse_digest(CLEAN), "2026-10-18")
        self.assertEqual(draft.date, "2026-10-18")
        self.assertEqual(draft.title, "Call 2026-10-18")
        self.assertEqual(draft.text, "The team planned the garden app demo.")
        self.assertEqual(draft.items, ["Demo on Friday", "Use the new icons", "→ Prepare the slides (Mia, Thursday)", "→ Book the room"])

    def test_title_prefix_and_translation(self):
        self.assertEqual(calls.note_from_digest(calls.Digest(), "2026-10-18", "Retro").title, "Retro 2026-10-18")
        set_language("ru")
        self.assertEqual(calls.note_from_digest(calls.Digest(), "2026-10-18").title, "Созвон 2026-10-18")

    def test_owner_or_due_alone(self):
        self.assertEqual(calls.task_line(calls.Task("Ship", due="Monday")), "→ Ship (Monday)")
        self.assertEqual(calls.task_line(calls.Task("Ship", owner="Leo")), "→ Ship (Leo)")


class TranscriptFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)

    def test_location_and_content(self):
        path = calls.transcript_file(self.data, "abc123", "Hello team.")
        self.assertEqual(path, self.data / "calls" / "abc123.md")
        self.assertEqual(path.read_text(encoding="utf-8"), "Hello team.\n")
        self.assertFalse(path.with_suffix(".md.tmp").exists())
        if os.name == "posix":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)

    def test_rewrite(self):
        calls.transcript_file(self.data, "abc", "one")
        self.assertEqual(calls.transcript_file(self.data, "abc", "two\n").read_text(encoding="utf-8"), "two\n")

    def test_new_ids_are_safe(self):
        cid = calls.new_call_id()
        self.assertEqual(calls.transcript_path(self.data, cid).parent, self.data / "calls")

    def test_bad_id_refused(self):
        for bad in ("../x", "a/b", "a\\b", "", "x" * 65, "a.b", "abc\n", "abc\r\n", "\nabc"):
            with self.assertRaises(ValueError, msg=bad):
                calls.transcript_path(self.data, bad)




# The shapes a meeting service's transcript may take: a header line with the speaker and
# the time over the speech, or both on one line.
SPEECH = [
    ("Мария Петрова", "Давайте пройдёмся по плану выпуска на эту неделю и решим, что успеваем."),
    ("Олег Смирнов", "Сборка готова, осталось проверить установщик на чистой машине и обновить описание."),
    ("Мария Петрова", "Хорошо. Тогда описание пишу я до четверга, а ты берёшь проверку установщика."),
    ("Олег Смирнов", "Договорились. Ещё нужно решить, кто отвечает на вопросы пользователей в пятницу."),
    ("Мария Петрова", "В пятницу дежурит Анна, я ей напишу сегодня после встречи и пришлю ссылку."),
    ("Олег Смирнов", "Отлично, тогда на этом всё, следующая встреча в понедельник в то же время."),
]


def header_style() -> str:
    return "\n\n".join(f"{name} 00:{i:02d}:{(i * 7) % 60:02d}\n{text}" for i, (name, text) in enumerate(SPEECH))


def inline_style() -> str:
    return "\n".join(f"[{i}:{(i * 13) % 60:02d}] {name}: {text}" for i, (name, text) in enumerate(SPEECH))


class LooksLikeCallTest(unittest.TestCase):
    def test_speaker_lines_with_time_codes(self):
        self.assertTrue(calls.looks_like_call(header_style()))
        self.assertTrue(calls.looks_like_call(inline_style()))

    def test_subtitles(self):
        self.assertTrue(calls.looks_like_call("WEBVTT\n\n00:00.000 --> 00:02.000\nHello"))
        srt = "".join(f"{i}\n00:00:0{i},000 --> 00:00:0{i},900\nline {i}\n\n" for i in range(1, 7))
        self.assertTrue(calls.looks_like_call(srt))

    def test_plain_notes_and_a_short_agenda_are_not_calls(self):
        self.assertFalse(calls.looks_like_call("\n".join(text for _name, text in SPEECH)))
        agenda = "\n".join(f"{h}:00 {topic}" for h, topic in zip(range(9, 15), ("Standup", "Review", "Lunch", "Planning", "Demo", "Retro"), strict=True))
        self.assertFalse(calls.looks_like_call(agenda))

    def test_times_that_jump_about_are_not_a_call(self):
        lines = [f"{(i * 37) % 24}:{(i * 11) % 60:02d} {text}" for i, (_n, text) in enumerate(SPEECH * 2)]
        self.assertFalse(calls.looks_like_call("\n".join(lines)))

    def test_a_heading_then_time_speaker_and_speech_by_tabs(self):
        body = "\n".join(f"00:{3 + i:02d}:{(i * 17) % 60:02d}\t{name}\t{text}" for i, (name, text) in enumerate(SPEECH))
        text = 'Транскрипция записи "Запись Дейли команды " 19 октября 2026 г\n' + body
        self.assertTrue(calls.looks_like_call(text))
        self.assertEqual(calls.meeting_name(text), "Дейли команды")

    def test_meeting_name_needs_a_quoted_heading(self):
        self.assertEqual(calls.meeting_name("Transcript of «Weekly sync» 3 May\n00:01 Ann: hi"), "Weekly sync")
        self.assertEqual(calls.meeting_name(inline_style()), "")  # the first line is speech
        self.assertEqual(calls.meeting_name("Notes from the call\n0:01 hi"), "")

    def test_dates_and_versions_are_not_time_codes(self):
        lines = [f"Release 1.2.3 on 12.10.2026: {text}" for _n, text in SPEECH]
        self.assertFalse(calls.looks_like_call("\n".join(lines)))


class DecodeTextTest(unittest.TestCase):
    def test_utf8_with_and_without_the_mark(self):
        self.assertEqual(calls.decode_text("Привет".encode()), "Привет")
        self.assertEqual(calls.decode_text("﻿Привет".encode()), "Привет")

    def test_utf16_and_windows_1251(self):
        self.assertEqual(calls.decode_text("Привет".encode("utf-16")), "Привет")
        self.assertEqual(calls.decode_text("Привет".encode("cp1251")), "Привет")


class NewTranscriptsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.now = 1_000_000.0

    def put(self, name: str, text: str, encoding: str = "utf-8") -> Path:
        path = self.dir / name
        path.write_bytes(text.encode(encoding))
        return path

    def look(self, since, times: dict):
        """A look with each file's time as given: the real change and arrival times of a
        file just written are now, and the arrival time cannot be set."""

        def stat(path):
            st = os.stat(path)
            t = times[Path(path).name]
            return os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink, st.st_uid, st.st_gid, st.st_size, t, t, t))

        return calls.new_transcripts(self.dir, since, self.now, stat=stat)

    def test_the_first_look_only_sets_the_mark(self):
        self.put("old.txt", header_style())
        got = self.look(None, {"old.txt": self.now - 100})
        self.assertEqual((got.found, got.since), ([], self.now))

    def test_a_new_transcript_is_found_and_moves_the_mark(self):
        self.put("call.txt", header_style())
        got = self.look(self.now - 60, {"call.txt": self.now - 10})
        self.assertEqual(got.found, [self.dir / "call.txt"])
        self.assertEqual(got.since, self.now - 10)
        again = self.look(got.since, {"call.txt": self.now - 10})
        self.assertEqual(again.found, [])  # never offered twice

    def test_other_new_files_move_the_mark_too(self):
        self.put("notes.txt", "just a line")
        self.put("photo.jpg", "x")
        got = self.look(self.now - 60, {"notes.txt": self.now - 10, "photo.jpg": self.now - 5})
        self.assertEqual(got.found, [])
        self.assertEqual(got.since, self.now - 10)  # the picture is not even looked at

    def test_a_file_being_written_waits(self):
        self.put("call.txt", header_style())
        got = self.look(self.now - 60, {"call.txt": self.now - 0.5})
        self.assertEqual((got.found, got.since), ([], self.now - 60))

    def test_older_files_empty_ones_and_hidden_ones_are_passed_over(self):
        times = {}
        for name, text, when in (("before.txt", header_style(), 100), ("empty.txt", "", 10), (".partial.txt", header_style(), 10), ("~lock.txt", header_style(), 10)):
            self.put(name, text)
            times[name] = self.now - when
        self.assertEqual(self.look(self.now - 60, times).found, [])

    def test_oldest_first_any_encoding(self):
        self.put("b.txt", inline_style(), "cp1251")
        self.put("a.vtt", "WEBVTT\n\n00:00.000 --> 00:01.000\nhi")
        got = self.look(self.now - 60, {"b.txt": self.now - 10, "a.vtt": self.now - 20})
        self.assertEqual(got.found, [self.dir / "a.vtt", self.dir / "b.txt"])

    def test_a_missing_folder_keeps_the_mark(self):
        got = calls.new_transcripts(self.dir / "gone", 5.0, self.now)
        self.assertEqual((got.found, got.since), ([], 5.0))

    def test_the_folder_from_the_settings_or_downloads(self):
        home = Path("/home/sam")
        self.assertEqual(calls.watch_folder({}, home), home / "Downloads")
        self.assertEqual(calls.watch_folder({calls.SETTING_WATCH_FOLDER: " /data/in "}, home), Path("/data/in"))


if __name__ == "__main__":
    unittest.main()
