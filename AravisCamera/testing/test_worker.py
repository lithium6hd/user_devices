"""Unit tests for AravisCameraWorker.get_attributes_as_dict.

This is the override that keeps every buffered shot alive: transition_to_
buffered calls it to snapshot attributes into the shot file, and the base
IMAQdxCameraWorker implementation builds that snapshot with an unguarded
dict comprehension that aborts the whole shot the moment one GenICam
feature raises on read (see the docstring on the override itself).

These tests exercise only that override, against a duck-typed fake camera,
so they need neither `gi` nor a real camera -- matching every other test in
this suite (see the AravisCamera/README.md Testing section: the whole
project's test suite is meant to run gi-free).

The worker is constructed with object.__new__() rather than
AravisCameraWorker(...) so that Worker.__init__ -- which starts zmq sockets
and other blacs process-tree machinery -- never runs; only self.camera is
set before calling the method under test.
"""
from user_devices.AravisCamera.blacs_workers import AravisCameraWorker


class _FakeCamera:
    """Duck-typed stand-in for Aravis_Camera's attribute-reading interface.

    get_attribute_names() returns a fixed list of names; get_attribute()
    returns a canned value per name, or raises for names in `raising`.
    """

    def __init__(self, values, raising=()):
        self.values = values
        self.raising = set(raising)
        self.requested_names = []
        self.requested_visibility = []

    def get_attribute_names(self, visibility_level):
        self.requested_visibility.append(visibility_level)
        return list(self.values)

    def get_attribute(self, name):
        self.requested_names.append(name)
        if name in self.raising:
            raise RuntimeError(f"simulated failure reading {name}")
        return self.values[name]


def _make_worker(camera):
    worker = object.__new__(AravisCameraWorker)
    worker.camera = camera
    return worker


def test_all_attributes_readable_are_all_returned():
    camera = _FakeCamera({'ExposureTime': 5000.0, 'Gain': 0, 'Width': 1440})
    worker = _make_worker(camera)

    result = worker.get_attributes_as_dict('intermediate')

    assert result == {'ExposureTime': 5000.0, 'Gain': 0, 'Width': 1440}


def test_a_raising_name_is_skipped_and_the_rest_are_returned():
    camera = _FakeCamera(
        {'ExposureTime': 5000.0, 'ChunkExposureTime': 1.0, 'Gain': 0},
        raising={'ChunkExposureTime'},
    )
    worker = _make_worker(camera)

    result = worker.get_attributes_as_dict('intermediate')

    assert result == {'ExposureTime': 5000.0, 'Gain': 0}
    assert 'ChunkExposureTime' not in result


def test_skipped_names_are_reported(capsys):
    camera = _FakeCamera(
        {'ExposureTime': 5000.0, 'ChunkExposureTime': 1.0, 'EventExposureEnd': 2},
        raising={'ChunkExposureTime', 'EventExposureEnd'},
    )
    worker = _make_worker(camera)

    worker.get_attributes_as_dict('intermediate')

    printed = capsys.readouterr().out
    assert 'Skipped 2 attribute' in printed
    assert 'ChunkExposureTime' in printed
    assert 'EventExposureEnd' in printed


def test_all_attributes_raising_still_returns_without_raising():
    camera = _FakeCamera(
        {'ChunkExposureTime': 1.0, 'EventExposureEnd': 2},
        raising={'ChunkExposureTime', 'EventExposureEnd'},
    )
    worker = _make_worker(camera)

    result = worker.get_attributes_as_dict('intermediate')

    assert result == {}


def test_no_names_returns_empty_dict_without_raising():
    camera = _FakeCamera({})
    worker = _make_worker(camera)

    assert worker.get_attributes_as_dict('simple') == {}


def test_visibility_level_is_forwarded_to_get_attribute_names():
    camera = _FakeCamera({'Width': 1440})
    worker = _make_worker(camera)

    worker.get_attributes_as_dict('advanced')

    assert camera.requested_visibility == ['advanced']
