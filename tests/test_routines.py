"""Daily routine: schedules, launch plans, order and storage."""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from daykeeper.routines import Routine, Routines, Schedule, describe_schedule, interpreter_for, launch_plan, split_args, subtitle
from daykeeper.vault import Vault

WED_0930 = datetime(2026, 10, 14, 9, 30)  # a Wednesday


class ScheduleTest(unittest.TestCase):
    def test_due_once_a_day_after_the_time(self):
        s = Schedule(at="09:15")
        self.assertTrue(s.due(WED_0930, last_run=0))
        ran = datetime(2026, 10, 14, 9, 16).timestamp()
        self.assertFalse(s.due(WED_0930, last_run=ran), "already ran today")
        yesterday = datetime(2026, 10, 13, 9, 16).timestamp()
        self.assertTrue(s.due(WED_0930, last_run=yesterday))
        self.assertFalse(s.due(datetime(2026, 10, 14, 9, 0), last_run=0), "not yet")

    def test_weekdays_and_bad_times(self):
        self.assertFalse(Schedule(at="09:00", days=[5, 6]).due(WED_0930, 0))
        for bad in ("", "9", "25:00", "09:60", "ab:cd"):
            self.assertFalse(Schedule(at=bad).due(WED_0930, 0), bad)

    def test_next_run(self):
        self.assertEqual(Schedule(at="10:00").next_run(WED_0930), datetime(2026, 10, 14, 10, 0))
        self.assertEqual(Schedule(at="09:00").next_run(WED_0930), datetime(2026, 10, 15, 9, 0))
        self.assertEqual(Schedule(at="09:00", days=[0]).next_run(WED_0930), datetime(2026, 10, 19, 9, 0))
        self.assertIsNone(Schedule(at="").next_run(WED_0930))

    def test_describe(self):
        names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        self.assertEqual(describe_schedule(Schedule(at="09:30"), names), "09:30 Mon–Fri")
        self.assertEqual(describe_schedule(Schedule(at="18:00", days=list(range(7))), names), "18:00")
        self.assertEqual(describe_schedule(Schedule(at="08:00", days=[5, 6]), names), "08:00 Sat, Sun")
        self.assertEqual(describe_schedule(Schedule(), names), "")


class LaunchPlanTest(unittest.TestCase):
    def which(self, name):
        return {"py": "C:/py.exe", "python3": "/usr/bin/python3", "bash": "/bin/bash"}.get(name)

    def test_interpreters(self):
        self.assertEqual(interpreter_for("a.py", "win32", self.which), ["py", "-3"])
        self.assertEqual(interpreter_for("a.py", "darwin", self.which), ["/usr/bin/python3"])
        self.assertEqual(interpreter_for("a.ps1", "win32", lambda n: None)[0], "powershell")
        self.assertEqual(interpreter_for("a.BAT", "win32", self.which), ["cmd", "/c"])
        self.assertEqual(interpreter_for("a.sh", "linux", self.which), ["/bin/bash"])
        self.assertIsNone(interpreter_for("tool.exe", "win32", self.which))

    def test_script_plan(self):
        r = Routine(kind="script", target="C:/s/report.py", args='--day today "two words"')
        plan = launch_plan(r, "win32", self.which, exists=lambda p: True)
        self.assertEqual(plan.argv, ["py", "-3", "C:/s/report.py", "--day", "today", "two words"])
        self.assertEqual(plan.cwd.replace("\\", "/"), "C:/s")
        self.assertEqual(launch_plan(r, "win32", self.which, exists=lambda p: False).problem, "missing")

    def test_other_kinds(self):
        self.assertEqual(launch_plan(Routine(kind="url", target="example.com")).target, "https://example.com")
        cmd = launch_plan(Routine(kind="command", target="git -C E:/x pull", cwd="E:/x"))
        self.assertEqual((cmd.action, cmd.shell, cmd.cwd), ("run", "git -C E:/x pull", "E:/x"))
        self.assertEqual(launch_plan(Routine(kind="folder", target="Z:/nope"), exists=lambda p: False).problem, "missing")
        self.assertEqual(launch_plan(Routine(target="  ")).problem, "empty")

    def test_subtitle_names_the_script_not_the_interpreter(self):
        ps = Routine(kind="script", target=r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", args=r'-NoProfile -File "D:\jobs\update monitoring.ps1"')
        self.assertEqual(subtitle(ps), "update monitoring.ps1 · powershell")
        self.assertEqual(subtitle(Routine(kind="script", target="/home/me/bin/backup.sh", args="-v")), "backup.sh")
        self.assertEqual(subtitle(Routine(kind="folder", target="D:\\work\\repo\\")), "repo")
        self.assertEqual(subtitle(Routine(kind="url", target="https://grafana.example.com/")), "grafana.example.com")
        self.assertEqual(subtitle(Routine(kind="command", target="git pull")), "git pull")

    def test_split_args(self):
        self.assertEqual(split_args("-a 'b c'", "linux"), ["-a", "b c"])
        self.assertEqual(split_args("", "win32"), [])
        self.assertEqual(split_args('--out "C:/My Files/r.txt" -v', "win32"), ["--out", "C:/My Files/r.txt", "-v"])


class RoutinesTest(unittest.TestCase):
    def test_order_due_and_unlock(self):
        rs = Routines()
        a = rs.add(name="a", target="x", schedule=Schedule(on_unlock=True))
        b = rs.add(name="b", target="y", schedule=Schedule(at="09:00"))
        self.assertEqual(rs.on_unlock(), [a])
        self.assertEqual(rs.due(WED_0930), [b])
        self.assertTrue(rs.move(b, -1))
        self.assertEqual(rs.ordered(), [b, a])
        self.assertFalse(rs.move(b, -1))

    def test_round_trip_in_the_vault(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "v.pkv"
            v = Vault.create(path, "master-pass-1")
            v.routines.add(name="Pull", kind="command", target="git pull", schedule=Schedule(True, "08:45", [0, 2]), terminal=True)
            v.routines.items[0].last_code = 3
            v.save()
            r = Vault.open(path, "master-pass-1").routines.items[0]
            self.assertEqual((r.name, r.kind, r.terminal, r.last_code), ("Pull", "command", True, 3))
            self.assertEqual(r.schedule.to_dict(), {"on_unlock": True, "at": "08:45", "days": [0, 2]})
        self.assertEqual(Routine.from_dict({"kind": "evil"}).kind, "script")


if __name__ == "__main__":
    unittest.main()
