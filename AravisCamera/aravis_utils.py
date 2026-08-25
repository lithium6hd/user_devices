#####################################################################
#                                                                   #
# /user_devices/AravisCamera/aravis_utils.py                        #
#                                                                   #
# Pure helpers for the AravisCamera driver.                         #
#                                                                   #
# This module imports only numpy: no Aravis, no gi, no blacs. That  #
# keeps it unit-testable on machines with neither Aravis nor a      #
# camera installed.                                                 #
#                                                                   #
#####################################################################
import numpy as np

#: Bytes per pixel -> numpy dtype. Only unpacked mono formats are supported.
_DTYPES = {1: np.uint8, 2: np.uint16}


def bytes_per_pixel(n_bytes, width, height):
    """Infer bytes per pixel from a buffer size and image dimensions.

    Args:
        n_bytes (int): size of the raw buffer.
        width (int): image width in pixels.
        height (int): image height in pixels.

    Returns:
        int: 1 for Mono8, 2 for Mono16.

    Raises:
        ValueError: if the buffer is not a whole number of 1- or 2-byte
            pixels. Packed formats such as Mono12Packed land here.
    """
    n_pixels = width * height
    if n_pixels <= 0:
        raise ValueError(
            f"cannot decode an image with {width}x{height} pixels"
        )
    bpp, remainder = divmod(n_bytes, n_pixels)
    if remainder or bpp not in _DTYPES:
        raise ValueError(
            f"cannot decode {n_bytes} bytes as a {width}x{height} image: "
            f"got {n_bytes / n_pixels:.4g} bytes per pixel, expected 1 or 2. "
            f"Packed pixel formats are not supported; use Mono8 or Mono16."
        )
    return bpp


def decode_buffer(data, width, height):
    """Convert raw Aravis buffer bytes into a 2D numpy array.

    The returned array is always a copy that owns its memory. This is
    required: the source buffer is pushed back to the Aravis stream and
    overwritten by the next frame, and the BLACS worker later transmits
    images over zmq with ``copy=False``.

    Args:
        data (bytes): raw image bytes from ``ArvBuffer.get_data()``.
        width (int): image width in pixels.
        height (int): image height in pixels.

    Returns:
        numpy.ndarray: shape ``(height, width)``, dtype uint8 or uint16.
    """
    dtype = _DTYPES[bytes_per_pixel(len(data), width, height)]
    return np.frombuffer(data, dtype=dtype).reshape(height, width).copy()
