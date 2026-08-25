#####################################################################
#                                                                   #
# /user_devices/AravisCamera/blacs_workers.py                       #
#                                                                   #
# Aravis (GenICam) camera worker for BLACS.                         #
#                                                                   #
#####################################################################
# IMAQdxCameraWorker and decode_buffer are not used by Aravis_Camera itself;
# they are consumed by the acquisition half and the BLACS worker subclass that
# follow in this same module.
from labscript_devices.IMAQdxCamera.blacs_workers import IMAQdxCameraWorker

from user_devices.AravisCamera.aravis_utils import (
    apply_with_retry,
    decode_buffer,
    find_serial,
    visibility_names,
)

# Imported lazily in Aravis_Camera.__init__ so this module can be imported on
# machines without Aravis: mock mode, runmanager, and the unit tests.
Aravis = None


class Aravis_Camera(object):
    """Adapts an Aravis GenICam camera to the interface IMAQdxCameraWorker uses.

    Attribute access goes through ``ArvDevice``'s typed feature accessors
    (``get_string_feature_value`` and friends), which take a bare GenICam
    feature name such as ``'ExposureTime'`` -- never an IMAQdx-style
    ``'Category::Feature'`` path.
    """

    def __init__(self, serial_number):
        global Aravis

        try:
            import gi
        except ImportError as e:
            raise ImportError(
                "PyGObject ('gi') is not available in this environment. It is "
                "not pip-installable here; symlink the system package into the "
                "venv as described in AravisCamera/README.md, section 2."
            ) from e

        try:
            gi.require_version('Aravis', '0.8')
            from gi.repository import Aravis as _Aravis
        except (ImportError, ValueError) as e:
            raise ImportError(
                "The Aravis GObject-introspection typelib is not installed. "
                "Run: sudo apt install libaravis-0.8-0 gir1.2-aravis-0.8 "
                "aravis-tools-cli  (see AravisCamera/README.md, section 1)."
            ) from e

        Aravis = _Aravis

        self.serial_number = str(serial_number)
        self.exception_on_failed_shot = True
        self._abort_acquisition = False
        self.stream = None

        if self.serial_number.startswith('Fake'):
            # Aravis' built-in fake GenICam device, addressed by id not serial.
            Aravis.enable_interface('Fake')
            self.camera = Aravis.Camera.new(self.serial_number)
        else:
            print("Finding camera...")
            Aravis.update_device_list()
            serials = [
                Aravis.get_device_serial_nbr(i)
                for i in range(Aravis.get_n_devices())
            ]
            index = find_serial(serials, self.serial_number)
            if index is None:
                raise ValueError(
                    f"No connected camera with serial number "
                    f"{self.serial_number!r}. Found: {serials}. "
                    f"Note that Aravis reports the GenICam DeviceSerialNumber, "
                    f"which differs from an IMAQdx-format serial. "
                    f"Run 'arv-tool-0.8' to list cameras."
                )
            print("Connecting to camera...")
            self.camera = Aravis.Camera.new(Aravis.get_device_id(index))

        self.device = self.camera.get_device()

    # ---------------------------------------------------------------- attributes

    def _feature_node(self, name):
        """Return the ``ArvGcNode`` for a feature, or raise if absent.

        ``ArvDevice.get_feature`` returns None for an unknown name rather
        than raising, so the check has to be explicit.
        """
        node = self.device.get_feature(name)
        if node is None:
            raise ValueError(
                f"camera has no GenICam feature named {name!r}. Run "
                f"'arv-tool-0.8 features' to list available features. Note "
                f"that names are bare, not IMAQdx-style 'Category::Feature'."
            )
        return node

    def _readers(self):
        """Node types to the ArvDevice accessor that reads them, in dispatch order.

        The single source of truth for which features are readable, shared by
        get_attribute() and get_attribute_names() so the two cannot drift.

        Order matters. ArvGcEnumeration is a concrete node that *implements*
        the ArvGcInteger interface as well as ArvGcString, so
        ``isinstance(node, Aravis.GcInteger)`` is True for an enumeration.
        Tested in the other order, PixelFormat would read back as the raw PFNC
        integer 17301505 rather than 'Mono8'.
        """
        return (
            ((Aravis.GcEnumeration, Aravis.GcString), 'get_string_feature_value'),
            ((Aravis.GcBoolean,), 'get_boolean_feature_value'),
            ((Aravis.GcInteger,), 'get_integer_feature_value'),
            ((Aravis.GcFloat,), 'get_float_feature_value'),
        )

    def get_attribute(self, name):
        """Return the current value of the named GenICam feature."""
        node = self._feature_node(name)
        for types, accessor in self._readers():
            if isinstance(node, types):
                try:
                    return getattr(self.device, accessor)(name)
                except Exception as e:
                    raise Exception(f"Failed to get attribute {name}") from e
        raise TypeError(
            f"cannot read GenICam feature {name!r} of type "
            f"{type(node).__name__}"
        )

    def set_attribute(self, name, value):
        """Set the named GenICam feature to the given value.

        A command feature has no value: writing to it executes it, whatever
        ``value`` is.
        """
        node = self._feature_node(name)
        # Same ordering rationale as _readers(): enumerations must be matched
        # before the integer interface they also implement.
        if isinstance(node, Aravis.GcCommand):
            writer, cast = None, None
        elif isinstance(node, (Aravis.GcEnumeration, Aravis.GcString)):
            writer, cast = 'set_string_feature_value', str
        elif isinstance(node, Aravis.GcBoolean):
            writer, cast = 'set_boolean_feature_value', bool
        elif isinstance(node, Aravis.GcInteger):
            writer, cast = 'set_integer_feature_value', int
        elif isinstance(node, Aravis.GcFloat):
            writer, cast = 'set_float_feature_value', float
        else:
            # Raised outside the try below so it is not re-wrapped: an
            # unwritable node type is a programming error, not a device error.
            raise TypeError(
                f"cannot write GenICam feature {name!r} of type "
                f"{type(node).__name__}"
            )

        try:
            if writer is None:
                self.device.execute_command(name)
            else:
                getattr(self.device, writer)(name, cast(value))
        except Exception as e:
            raise Exception(f"failed to set attribute {name} to {value}") from e

    def set_attributes(self, attr_dict):
        """Set many features, retrying failures once (see apply_with_retry)."""
        apply_with_retry(self.set_attribute, attr_dict)

    def get_attribute_names(self, visibility_level, writeable_only=False):
        """List readable feature names at or below the given visibility level.

        The names are gathered by walking the GenICam category tree from
        'Root', since ArvGc exposes no flat feature listing.

        Excluded are: commands (reading one would execute it), write-only
        features (they have no value to read), and features whose node type
        none of ``get_attribute``'s typed accessors handles, such as the raw
        ``ArvGcRegisterNode`` behind ``LUTValueAll``.

        This is a static filter, and GenICam offers no static predicate for
        the remaining case: a handful of features -- the ``Chunk*`` and
        ``Event*`` families on this FLIR -- report themselves available and
        implemented yet raise on read, because they are backed by a data port
        that is only populated during acquisition. Callers that read every
        listed name should tolerate a per-feature failure.

        Args:
            visibility_level (str): 'simple', 'intermediate' or 'advanced'.
            writeable_only (bool): if True, restrict the result to features
                that are read-write, dropping the read-only ones.
        """
        wanted_visibility = {
            getattr(Aravis.GcVisibility, name)
            for name in visibility_names(visibility_level)
        }
        if writeable_only:
            wanted_access = {Aravis.GcAccessMode.RW}
        else:
            wanted_access = {Aravis.GcAccessMode.RO, Aravis.GcAccessMode.RW}

        readable_types = tuple(
            t for types, _ in self._readers() for t in types
        )

        genicam = self.device.get_genicam()
        names = []
        seen = set()

        def walk(category_node):
            for feature_name in category_node.get_features():
                if feature_name in seen:
                    continue
                seen.add(feature_name)
                node = genicam.get_node(feature_name)
                if node is None:
                    continue
                if isinstance(node, Aravis.GcCategory):
                    walk(node)
                    continue
                if isinstance(node, Aravis.GcCommand):
                    # Reading a command would execute it.
                    continue
                if not isinstance(node, Aravis.GcFeatureNode):
                    continue
                if not isinstance(node, readable_types):
                    # get_attribute() has no typed accessor for this node.
                    continue
                try:
                    if not node.is_available():
                        continue
                    if not node.is_implemented():
                        continue
                    if node.get_visibility() not in wanted_visibility:
                        continue
                    if node.get_actual_access_mode() not in wanted_access:
                        continue
                except Exception:
                    # A feature whose availability cannot even be evaluated
                    # cannot be read either; skip it rather than abort the walk.
                    continue
                names.append(feature_name)

        root = genicam.get_node('Root')
        if isinstance(root, Aravis.GcCategory):
            walk(root)
        return names

    # ---------------------------------------------------------------- lifecycle

    def close(self):
        self.stream = None
        self.device = None
        self.camera = None
