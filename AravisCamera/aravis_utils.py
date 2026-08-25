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


#: labscript visibility level -> GenICam visibility names, least to most
#: obscure. Converted to Aravis enum members at call time so this module
#: stays free of Aravis imports.
_VISIBILITY_NAMES = {
    'simple': ('BEGINNER',),
    'intermediate': ('BEGINNER', 'EXPERT'),
    'advanced': ('BEGINNER', 'EXPERT', 'GURU'),
}


def visibility_names(level):
    """Map a labscript visibility level to GenICam visibility names.

    Args:
        level (str): 'simple', 'intermediate' or 'advanced'.

    Returns:
        tuple of str: GenICam visibility names, e.g. ``('BEGINNER', 'EXPERT')``.

    Raises:
        ValueError: if the level is not recognised.
    """
    try:
        return _VISIBILITY_NAMES[level.lower()]
    except (AttributeError, KeyError):
        raise ValueError(
            f"invalid visibility level {level!r}, expected one of "
            f"{sorted(_VISIBILITY_NAMES)}"
        ) from None


def find_serial(serials, wanted):
    """Find the index of ``wanted`` in ``serials``, comparing as strings.

    Args:
        serials (sequence): serial numbers as reported by Aravis.
        wanted: the serial being searched for; coerced to str.

    Returns:
        int or None: index of the match, or None if absent.
    """
    wanted = str(wanted)
    for index, serial in enumerate(serials):
        if str(serial) == wanted:
            return index
    return None


def apply_with_retry(setter, attributes):
    """Apply ``attributes`` via ``setter(name, value)``, retrying failures once.

    GenICam features are interdependent: setting ``Width`` before ``OffsetX``
    can fail because the existing offset plus the new width exceeds the
    sensor, yet succeed once the other features have been applied. Rather
    than maintain a dependency graph, this makes one pass in dict order,
    collects failures, then retries them once.

    Args:
        setter (callable): called as ``setter(name, value)``; raises on failure.
        attributes (dict): feature name -> value, applied in insertion order.

    Raises:
        Exception: the second failure for any feature that fails twice, with
            the first failure chained as its cause.
    """
    failures = {}
    for name, value in attributes.items():
        try:
            setter(name, value)
        except Exception as first_error:
            failures[name] = (value, first_error)

    for name, (value, first_error) in failures.items():
        try:
            setter(name, value)
        except Exception as second_error:
            raise RuntimeError(
                f"failed to set attribute {name} to {value!r} on two "
                f"attempts. First error: {first_error}. "
                f"Second error: {second_error}"
            ) from first_error
