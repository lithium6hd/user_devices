"""The ADwin worker must make its hardware calls on one long-lived thread.

libadwin keeps its TCP connection to a networked ("TYPE = net") ADwin in
thread-local storage: the first call on a thread opens a new connection to
port 6543, and nothing closes it when the thread ends -- verified by counting
sockets while calling from the main thread (constant) versus from fresh
threads (one more each time).

transition_to_manual runs its ADwin calls off the worker's main thread so a
hung device cannot freeze BLACS. Spawning that thread per shot leaked one
connection per shot until the ADwin stopped servicing its protocol and needed
a power cycle. These tests pin the thread down to one, reused.

The worker is built with object.__new__() so Worker.__init__ -- which starts
zmq sockets and other blacs process-tree machinery -- never runs. Matches
user_devices/AravisCamera/testing/test_worker.py.
"""
import labscript_utils.h5_lock  # noqa: F401  (must precede h5py)

import logging
import threading
import time

import pytest
from labscript import LabscriptError

from user_devices.ADwinProII.blacs_workers import ADwinProIIWorker


@pytest.fixture
def worker():
    w = object.__new__(ADwinProIIWorker)
    w.logger = logging.getLogger("test_adwin_thread_reuse")
    return w


def test_repeated_calls_all_run_on_one_thread(worker):
    """The regression test: one thread means one leaked-free connection.

    A thread per call is what leaked a TCP connection per shot.
    """
    idents = []
    for _ in range(20):
        worker._submit_adwin_call(lambda: idents.append(threading.get_ident()), 5)

    assert len(idents) == 20
    assert len(set(idents)) == 1, f"used {len(set(idents))} threads, expected 1"


def test_calls_do_not_run_on_the_calling_thread(worker):
    """The whole point of the thread is that a hung call cannot block BLACS."""
    ran_on = []
    worker._submit_adwin_call(lambda: ran_on.append(threading.get_ident()), 5)

    assert ran_on[0] != threading.get_ident()


def test_return_value_is_propagated(worker):
    assert worker._submit_adwin_call(lambda: 42, 5) == 42


def test_exception_is_propagated_to_the_caller(worker):
    def boom():
        raise ValueError("hardware said no")

    with pytest.raises(ValueError, match="hardware said no"):
        worker._submit_adwin_call(boom, 5)

    # and the executor survives, still on the same thread
    assert worker._submit_adwin_call(lambda: "alive", 5) == "alive"


def test_timeout_raises_and_the_worker_recovers(worker):
    """A hung ADwin call must not wedge the worker for good.

    Abandoning the stuck thread costs one leaked connection, which is
    acceptable for a fault that should not happen per shot.
    """
    release = threading.Event()
    first = []

    def hang():
        first.append(threading.get_ident())
        release.wait(30)

    with pytest.raises(LabscriptError, match="timed out"):
        worker._submit_adwin_call(hang, 0.2)

    # A fresh executor takes over rather than the worker being stuck forever.
    second = worker._submit_adwin_call(threading.get_ident, 5)
    assert second != first[0]
    release.set()
