"""Unit tests for AravisCamera pure helpers.

These deliberately import nothing from Aravis or blacs, so they run on a
machine with no camera and no Aravis installation.
"""
import numpy as np
import pytest

from user_devices.AravisCamera.aravis_utils import (
    bytes_per_pixel,
    decode_buffer,
)


def test_bytes_per_pixel_mono8():
    assert bytes_per_pixel(100 * 50, 100, 50) == 1


def test_bytes_per_pixel_mono16():
    assert bytes_per_pixel(2 * 100 * 50, 100, 50) == 2


def test_bytes_per_pixel_rejects_packed_formats():
    """Mono12Packed gives 1.5 bytes/pixel and must fail loudly rather than
    silently producing a garbled image."""
    n_bytes = 3 * (100 * 50) // 2
    with pytest.raises(ValueError, match="Mono8 or Mono16"):
        bytes_per_pixel(n_bytes, 100, 50)


def test_bytes_per_pixel_rejects_wrong_size():
    with pytest.raises(ValueError):
        bytes_per_pixel(7, 100, 50)


def test_bytes_per_pixel_rejects_zero_pixels():
    with pytest.raises(ValueError):
        bytes_per_pixel(0, 0, 0)


def test_decode_buffer_mono8_shape_and_dtype():
    width, height = 4, 3
    data = bytes(range(width * height))
    image = decode_buffer(data, width, height)
    assert image.shape == (height, width)
    assert image.dtype == np.uint8


def test_decode_buffer_mono8_values():
    width, height = 4, 3
    data = bytes(range(width * height))
    image = decode_buffer(data, width, height)
    expected = np.arange(width * height, dtype=np.uint8).reshape(height, width)
    np.testing.assert_array_equal(image, expected)


def test_decode_buffer_mono16_values():
    width, height = 4, 3
    expected = np.arange(width * height, dtype=np.uint16).reshape(height, width)
    image = decode_buffer(expected.tobytes(), width, height)
    assert image.dtype == np.uint16
    np.testing.assert_array_equal(image, expected)


def test_decode_buffer_returns_owned_copy():
    """The Aravis buffer is pushed back to the stream and overwritten by the
    next frame, and the worker later sends images over zmq with copy=False.
    The returned array must own its memory."""
    width, height = 4, 3
    data = bytes(width * height)
    image = decode_buffer(data, width, height)
    assert image.flags['OWNDATA']
    assert image.flags['WRITEABLE']


from user_devices.AravisCamera.aravis_utils import (
    apply_with_retry,
    find_serial,
    visibility_names,
)


def test_visibility_names_levels():
    assert visibility_names('simple') == ('BEGINNER',)
    assert visibility_names('intermediate') == ('BEGINNER', 'EXPERT')
    assert visibility_names('advanced') == ('BEGINNER', 'EXPERT', 'GURU')


def test_visibility_names_is_case_insensitive():
    assert visibility_names('Simple') == ('BEGINNER',)


def test_visibility_names_rejects_unknown_level():
    with pytest.raises(ValueError, match="invalid visibility level"):
        visibility_names('nonsense')


def test_find_serial_matches_string():
    assert find_serial(['111', '222', '333'], '222') == 1


def test_find_serial_compares_as_strings():
    """Aravis returns serials as strings; a user may write an int."""
    assert find_serial(['111', '222'], 222) == 1


def test_find_serial_returns_none_when_absent():
    assert find_serial(['111', '222'], '999') is None


def test_find_serial_handles_empty_list():
    assert find_serial([], '111') is None


class _RecordingSetter:
    """Test double for a camera attribute setter.

    Fails for names in ``fail_names`` until each has been attempted
    ``fail_times`` times, then succeeds.
    """

    def __init__(self, fail_names=(), fail_times=1):
        self.calls = []
        self.attempts = {}
        self.fail_names = set(fail_names)
        self.fail_times = fail_times

    def __call__(self, name, value):
        self.calls.append((name, value))
        self.attempts[name] = self.attempts.get(name, 0) + 1
        if name in self.fail_names and self.attempts[name] <= self.fail_times:
            raise RuntimeError(f"simulated failure setting {name}")


def test_apply_with_retry_applies_all_attributes_once():
    setter = _RecordingSetter()
    apply_with_retry(setter, {'A': 1, 'B': 2})
    assert setter.calls == [('A', 1), ('B', 2)]


def test_apply_with_retry_preserves_dict_order():
    """Users control GenICam ordering through dict insertion order."""
    setter = _RecordingSetter()
    apply_with_retry(setter, {'OffsetX': 0, 'Width': 100, 'OffsetY': 0})
    assert [name for name, _ in setter.calls] == ['OffsetX', 'Width', 'OffsetY']


def test_apply_with_retry_retries_a_failure_once():
    """The motivating case: setting Width before OffsetX can fail because the
    old offset plus the new width exceeds the sensor, then succeed on retry."""
    setter = _RecordingSetter(fail_names={'Width'}, fail_times=1)
    apply_with_retry(setter, {'Width': 100, 'OffsetX': 0})
    assert setter.attempts['Width'] == 2
    assert setter.attempts['OffsetX'] == 1


def test_apply_with_retry_raises_when_failure_persists():
    setter = _RecordingSetter(fail_names={'Bogus'}, fail_times=99)
    with pytest.raises(RuntimeError, match="Bogus"):
        apply_with_retry(setter, {'Bogus': 1})
    assert setter.attempts['Bogus'] == 2


def test_apply_with_retry_does_not_retry_successes():
    setter = _RecordingSetter(fail_names={'B'}, fail_times=1)
    apply_with_retry(setter, {'A': 1, 'B': 2, 'C': 3})
    assert setter.attempts['A'] == 1
    assert setter.attempts['C'] == 1


def test_apply_with_retry_accepts_empty_dict():
    setter = _RecordingSetter()
    apply_with_retry(setter, {})
    assert setter.calls == []
