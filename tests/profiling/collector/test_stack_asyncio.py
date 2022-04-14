import asyncio
import collections
import os

import pytest

from ddtrace.profiling import _asyncio
from ddtrace.profiling import profiler
from ddtrace.profiling.collector import stack_event

from . import _asyncio_compat


TESTING_GEVENT = os.getenv("DD_PROFILE_TEST_GEVENT", False)


@pytest.mark.skipif(not _asyncio_compat.PY36_AND_LATER, reason="Python > 3.5 needed")
def test_asyncio(tmp_path, monkeypatch) -> None:
    sleep_time = 0.2
    sleep_times = 5

    async def stuff() -> None:
        await asyncio.sleep(sleep_time)

    async def hello() -> None:
        t1 = _asyncio_compat.create_task(stuff(), name="sleep 1")
        t2 = _asyncio_compat.create_task(stuff(), name="sleep 2")
        for _ in range(sleep_times):
            await stuff()
        return (t1, t2)

    monkeypatch.setenv("DD_PROFILING_CAPTURE_PCT", "100")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", str(tmp_path / "pprof"))
    # start a complete profiler so asyncio policy is setup
    p = profiler.Profiler()
    p.start()
    t1, t2 = _asyncio_compat.run(hello())
    events = p._profiler._recorder.reset()
    p.stop()

    wall_time_ns = collections.defaultdict(lambda: 0)

    t1_name = _asyncio._task_get_name(t1)
    t2_name = _asyncio._task_get_name(t2)

    cpu_time_found = False
    for event in events[stack_event.StackSampleEvent]:

        wall_time_ns[event.task_name] += event.wall_time_ns

        # This assertion does not work reliably on Python < 3.7
        if _asyncio_compat.PY37_AND_LATER:
            if event.task_name == "Task-1":
                assert event.thread_name == "MainThread"
                assert event.frames == [(__file__, 29, "hello")]
                assert event.nframes == 1
            elif event.task_name == t1_name:
                assert event.thread_name == "MainThread"
                assert event.frames == [(__file__, 23, "stuff")]
                assert event.nframes == 1
            elif event.task_name == t2_name:
                assert event.thread_name == "MainThread"
                assert event.frames == [(__file__, 23, "stuff")]
                assert event.nframes == 1

        if event.thread_name == "MainThread" and (
            # The task name is empty in asyncio (it's not a task) but the main thread is seen as a task in gevent
            (event.task_name is None and not TESTING_GEVENT)
            or (event.task_name == "MainThread" and TESTING_GEVENT)
        ):
            # Make sure we account CPU time
            if event.cpu_time_ns > 0:
                cpu_time_found = True

            for frame in event.frames:
                if frame[0] == __file__ and frame[2] == "test_asyncio":
                    break
            else:
                pytest.fail("unable to find expected main thread frame: %r" % event.frames)

    if _asyncio_compat.PY38_AND_LATER:
        # We don't know the name of this task for Python < 3.8
        assert wall_time_ns["Task-1"] > 0

    assert wall_time_ns[t1_name] > 0
    assert wall_time_ns[t2_name] > 0
    assert cpu_time_found
