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
