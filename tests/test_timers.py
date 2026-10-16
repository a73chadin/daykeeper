"""Debounce, IdleOnce and Ticker on a fake `after` clock: no Tk needed."""
import unittest

from daykeeper.timers import Debounce, IdleOnce, Ticker


class FakeClock:
    """What the timers need from a widget: after / after_idle / after_cancel, kept in memory."""

    def __init__(self):
        self.jobs: dict[str, tuple[int | str, object]] = {}
        self.n = 0

    def after(self, ms, fn=None):
        self.n += 1
        job = f"after#{self.n}"
        self.jobs[job] = (ms, fn)
        return job

    def after_idle(self, fn):
        return self.after("idle", fn)

    def after_cancel(self, job):
        self.jobs.pop(job, None)

    def run_all(self) -> int:
        """Fire every pending job once, in order; jobs booked meanwhile wait for the next call."""
        due = list(self.jobs.items())
        self.jobs.clear()
        for _job, (_ms, fn) in due:
            fn()
        return len(due)


class DebounceTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.calls = []

    def test_two_touches_one_call_with_the_last_arguments(self):
        d = Debounce(self.clock, 800, lambda *a: self.calls.append(a))
        d.touch("first")
        d.touch("second")
        self.assertEqual(len(self.clock.jobs), 1, "the first job was cancelled")
        self.assertTrue(d.pending)
        self.assertEqual(self.clock.run_all(), 1)
        self.assertEqual(self.calls, [("second",)])
        self.assertFalse(d.pending)

    def test_cancel_before_it_fires_means_no_call(self):
        d = Debounce(self.clock, 100, lambda: self.calls.append("x"))
        d.touch()
        d.cancel()
        self.assertEqual(self.clock.run_all(), 0)
        self.assertEqual(self.calls, [])
        d.cancel()  # twice is fine

    def test_flush_runs_it_now_only_when_waiting(self):
        d = Debounce(self.clock, 100, lambda v: self.calls.append(v))
        d.flush()
        self.assertEqual(self.calls, [])
        d.touch(7)
        d.flush()
        self.assertEqual(self.calls, [7])
        self.assertEqual(self.clock.jobs, {}, "the job was cancelled by the flush")
        self.assertFalse(d.pending)

    def test_delay_is_what_was_asked(self):
        d = Debounce(self.clock, 30_000, lambda: None)
        d.touch()
        self.assertEqual(next(iter(self.clock.jobs.values()))[0], 30_000)


class IdleOnceTest(unittest.TestCase):
    def test_many_touches_one_idle_job(self):
        clock, calls = FakeClock(), []
        once = IdleOnce(clock, lambda: calls.append(1))
        once.touch()
        once.touch()
        once.touch()
        self.assertEqual(len(clock.jobs), 1)
        self.assertEqual(next(iter(clock.jobs.values()))[0], "idle")
        clock.run_all()
        self.assertEqual(calls, [1])
        self.assertFalse(once.pending)
        once.touch()
        self.assertTrue(once.pending, "a new round after it fired")

    def test_cancel(self):
        clock, calls = FakeClock(), []
        once = IdleOnce(clock, lambda: calls.append(1))
        once.touch()
        once.cancel()
        self.assertEqual(clock.run_all(), 0)
        self.assertEqual(calls, [])


class TickerTest(unittest.TestCase):
    def test_ticks_until_stopped(self):
        clock, calls = FakeClock(), []
        t = Ticker(clock, 300, lambda: calls.append(1))
        t.start()
        t.start()
        self.assertEqual(len(clock.jobs), 1, "starting twice books one job")
        clock.run_all()
        clock.run_all()
        self.assertEqual(calls, [1, 1])
        self.assertTrue(t.running)
        t.stop()
        self.assertFalse(t.running)
        self.assertEqual(clock.run_all(), 0)

    def test_answering_false_stops_the_ticks(self):
        clock, left = FakeClock(), [2]

        def work():
            left[0] -= 1
            return left[0] > 0

        t = Ticker(clock, 250, work)
        t.start()
        clock.run_all()
        self.assertTrue(t.running)
        clock.run_all()
        self.assertFalse(t.running)
        self.assertEqual(clock.jobs, {})

    def test_an_error_inside_does_not_end_the_ticks(self):
        clock = FakeClock()

        def boom():
            raise RuntimeError("tick")

        t = Ticker(clock, 100, boom)
        t.start()
        with self.assertRaises(RuntimeError):
            clock.run_all()
        self.assertTrue(t.running)
        self.assertEqual(len(clock.jobs), 1, "the next tick was booked before the error")


if __name__ == "__main__":
    unittest.main()
