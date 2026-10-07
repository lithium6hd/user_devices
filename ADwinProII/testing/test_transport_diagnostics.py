"""Tests for the ADwin transport-failure diagnostic.

libadwin runs every ADwin operation over one persistent TCP connection and
reports *any* transport failure on it as error 2001, "Network timeout" --
including a read that fails instantly because the device already dropped the
connection. The message alone cannot distinguish "our session broke" (the
device is fine, a new session would work) from "the device is gone", which is
the one thing needed to know whether retrying is worthwhile.

_diagnose_transport_failure answers that by trying to open a second session.
That probe MUST run on its own thread: libadwin keeps the connection in
thread-local storage, so a new ADwin() object on the calling thread silently
reuses the very connection that just failed and the probe would always report
the device as dead.

The worker is built with object.__new__() so Worker.__init__ -- which starts
zmq sockets and other blacs process-tree machinery -- never runs. Matches
user_devices/AravisCamera/testing/test_worker.py.
"""
import labscript_utils.h5_lock  # noqa: F401  (must precede h5py)

import logging
import threading

import pytest

from user_devices.ADwinProII import blacs_workers
from user_devices.ADwinProII.blacs_workers import ADwinProIIWorker


class _FakeADwinModule:
    """Stand-in for the `ADwin` module the worker imports at init().

    A session either constructs and answers Test_Version(), or raises at the
    point configured by `fail_at`.
    """

    class ADwinError(Exception):
        pass

    def __init__(self, fail_at=None, test_version=0):
        self.fail_at = fail_at
        self.test_version = test_version
        self.sessions_opened = 0
        self.opened_on = []

    def ADwin(self, device_no, raise_exceptions):
        self.sessions_opened += 1
        self.opened_on.append(threading.get_ident())
        if self.fail_at == "connect":
            raise self.ADwinError("Function Boot, errorNumber 2001: Network timeout.")
        return self._Session(self)

    class _Session:
        def __init__(self, module):
            self._module = module

        def Test_Version(self):
            if self._module.fail_at == "test_version":
                raise self._module.ADwinError(
                    "Function Test_Version, errorNumber 2001: Network timeout."
                )
            return self._module.test_version


@pytest.fixture
def worker(monkeypatch):
    w = object.__new__(ADwinProIIWorker)
    w.device_no = 1
    w.device_name = "ADwin"
    w.logger = logging.getLogger("test_adwin_diagnostics")
    w.mock = False
    return w


def _install(monkeypatch, module):
    monkeypatch.setattr(blacs_workers, "ADwin", module, raising=False)
    return module


def test_fresh_session_succeeds_means_the_session_broke(worker, monkeypatch):
    """Device answers a new connection -> only our TCP session was dropped."""
    mod = _install(monkeypatch, _FakeADwinModule())

    verdict = worker._diagnose_transport_failure("Get_Par", elapsed=0.008)

    assert mod.sessions_opened == 1
    assert verdict.device_reachable is True
    assert "session" in verdict.summary.lower()


def test_fresh_session_fails_means_the_device_is_gone(worker, monkeypatch):
    """A new connection fails too -> the device itself is unreachable."""
    _install(monkeypatch, _FakeADwinModule(fail_at="connect"))

    verdict = worker._diagnose_transport_failure("Get_Par", elapsed=0.008)

    assert verdict.device_reachable is False
    assert "device" in verdict.summary.lower()


def test_bad_test_version_counts_as_unreachable(worker, monkeypatch):
    """A session that connects but fails Test_Version is not usable either."""
    _install(monkeypatch, _FakeADwinModule(fail_at="test_version"))

    verdict = worker._diagnose_transport_failure("Get_Par", elapsed=0.008)

    assert verdict.device_reachable is False


def test_elapsed_time_classifies_timeout_versus_dropped_connection(worker, monkeypatch):
    """Milliseconds means the connection was already broken; seconds is a
    real timeout against the 5 s socket timeout libadwin sets."""
    _install(monkeypatch, _FakeADwinModule())

    fast = worker._diagnose_transport_failure("Get_Par", elapsed=0.008)
    slow = worker._diagnose_transport_failure("Get_Par", elapsed=5.2)

    assert fast.timed_out is False
    assert slow.timed_out is True


def test_diagnostic_never_raises(worker, monkeypatch):
    """It runs on the failure path, so it must not mask the original error."""

    class _Exploding:
        def ADwin(self, *a, **kw):
            raise RuntimeError("something completely unexpected")

    _install(monkeypatch, _Exploding())

    verdict = worker._diagnose_transport_failure("Get_Par", elapsed=0.008)

    assert verdict.device_reachable is False


def test_probe_runs_on_its_own_thread(worker, monkeypatch):
    """libadwin's connection is thread-local, so probing on the calling
    thread would reuse the broken connection and always report the device
    dead. The probe has to open its session from a fresh thread."""
    mod = _install(monkeypatch, _FakeADwinModule())

    worker._diagnose_transport_failure("Get_Par", elapsed=0.008)

    assert mod.opened_on, "no session was opened"
    assert mod.opened_on[0] != threading.get_ident()


def test_probe_that_hangs_does_not_hang_the_caller(worker, monkeypatch):
    """The device may accept the connection then never answer."""

    class _Hanging:
        def ADwin(self, *a, **kw):
            threading.Event().wait(30)

    _install(monkeypatch, _Hanging())

    verdict = worker._diagnose_transport_failure("Get_Par", elapsed=0.008)

    assert verdict.device_reachable is False
