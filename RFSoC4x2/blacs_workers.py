"""
BLACS worker for the RFSoC 4x2 device.

Communicates with the QICK web server over HTTP using the ``requests``
library.  All five mandatory BLACS worker methods are implemented:

* ``init``                    – open HTTP session, verify connectivity
* ``program_manual``          – no manual outputs; return values unchanged
* ``transition_to_buffered``  – upload pulse sequence, arm the board
* ``transition_to_manual``    – abort any running sequence
* ``check_remote_values``     – not supported; returns ``{}``

Additional methods that can be called from the BLACS tab:

* ``get_status``    – GET /api/status
* ``arm``           – POST /api/arm
* ``trigger``       – POST /api/trigger (software trigger)
* ``abort``         – POST /api/abort
* ``start_cw``      – POST /api/cw/start
* ``stop_cw``       – POST /api/cw/stop
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from blacs.tab_base_classes import Worker

logger = logging.getLogger(__name__)


class RFSoC4x2Worker(Worker):
    """BLACS worker that talks to the QICK web server via HTTP."""

    # ------------------------------------------------------------------
    # Mandatory BLACS worker lifecycle
    # ------------------------------------------------------------------

    def init(self) -> None:
        """Verify that the server is reachable."""
        self.base_url = f"http://{self.server_host}:{self.server_port}/api"

        if self.mock:
            self.logger.info("RFSoC4x2 worker running in mock mode — no HTTP calls")
            return

        try:
            status = self._get("status")
            self.logger.info(
                f"Connected to QICK server at {self.base_url}. "
                f"armed={status.get('armed')}, running={status.get('running')}"
            )
        except Exception as exc:
            self.logger.warning(
                f"Could not reach QICK server at {self.base_url}: {exc}"
            )

    def program_manual(self, values: dict) -> dict:
        """No standard output widgets — return values unchanged."""
        return values

    def transition_to_buffered(
        self, device_name: str, h5file: str, initial_values: dict, fresh: bool
    ) -> dict:
        """Upload the pulse sequence to the board and arm it.

        Reads the JSON pulse sequence written by ``RFSoC4x2.generate_code``
        from the shot HDF5 file, clears the server queue, sets the new
        sequence, and arms the board so it waits for the hardware trigger.

        Parameters
        ----------
        device_name:
            Labscript device name (used to find the group in the HDF5 file).
        h5file:
            Path to the shot HDF5 file.
        initial_values:
            Current widget values (unused for this device).
        fresh:
            ``True`` if this is the first shot or smart programming is
            disabled.
        """
        import h5py  # imported here to avoid h5_lock import-order issues

        with h5py.File(h5file, "r") as f:
            grp = f[f"devices/{device_name}"]
            raw = grp.attrs.get("pulse_sequence", "{}")
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            sequence = json.loads(raw)

        # Always send the sequence (smart programming not implemented yet)
        self._delete("queue")
        queue_payload = {"sequences": [sequence], "loop": False}
        self._post("queue", queue_payload)
        self._post("arm")
        self.logger.info(
            f"RFSoC4x2: uploaded {len(sequence.get('pulses', []))} pulse(s) and armed"
        )
        return initial_values

    def transition_to_manual(self) -> bool:
        """Abort any running sequence and return the board to idle."""
        try:
            self._post("abort")
        except Exception as exc:
            self.logger.warning(f"RFSoC4x2 abort failed: {exc}")
        return True

    def abort_transition_to_buffered(self) -> bool:
        try:
            self._post("abort")
        except Exception as exc:
            self.logger.warning(f"RFSoC4x2 abort (ttb) failed: {exc}")
        return True

    def abort_buffered(self) -> bool:
        try:
            self._post("abort")
        except Exception as exc:
            self.logger.warning(f"RFSoC4x2 abort (buffered) failed: {exc}")
        return True

    def check_remote_values(self) -> dict:
        """Not implemented — returns an empty dict."""
        return {}

    def shutdown(self) -> None:
        """Nothing to tear down (no persistent session)."""
        pass

    # ------------------------------------------------------------------
    # Tab-callable methods
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        """Return the current board status dict from the server."""
        return self._get("status") or {}

    def arm(self) -> dict:
        """Arm the board (software arm — hardware trigger still required)."""
        return self._post("arm") or {}

    def trigger(self) -> dict:
        """Issue a software trigger to start the sequence."""
        return self._post("trigger") or {}

    def abort(self) -> dict:
        """Abort any running sequence and disarm."""
        return self._post("abort") or {}

    def start_cw(
        self,
        channel: str,
        freq_MHz: float,
        amplitude: float,
        nyquist_zone: int = 1,
        mode: str = "auto",
    ) -> dict:
        """Start a continuous-wave tone on *channel* (manual / monitor use).

        Parameters
        ----------
        channel:
            ``'DAC_A'`` or ``'DAC_B'``.
        freq_MHz:
            Carrier frequency in MHz.
        amplitude:
            Amplitude in (0, 1].
        nyquist_zone:
            Nyquist zone (1 or 2).
        mode:
            Interpolation mode: ``'auto'``, ``'dds'``, or ``'mixer'``.
        """
        payload = {
            "channel": channel,
            "freq_MHz": float(freq_MHz),
            "amplitude": float(amplitude),
            "nyquist_zone": int(nyquist_zone),
            "mode": mode,
        }
        return self._post("cw/start", payload) or {}

    def stop_cw(self) -> dict:
        """Stop the continuous-wave tone on all channels."""
        return self._post("cw/stop") or {}

    # ------------------------------------------------------------------
    # Internal HTTP helpers
    # ------------------------------------------------------------------

    def _send(self, req: urllib.request.Request, path: str) -> dict:
        """Execute *req* and return the parsed JSON body (or ``{}``)."""
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read()
                return json.loads(body.decode("utf-8")) if body else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            try:
                detail = json.loads(raw).get("detail", raw)
            except Exception:
                detail = raw
            raise RuntimeError(f"HTTP {exc.code} from /api/{path}: {detail}")
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Could not reach QICK server at {self.base_url}: {exc.reason}"
            )

    def _get(self, path: str) -> dict | None:
        if self.mock:
            self.logger.debug(f"[MOCK] GET /api/{path}")
            return {}
        try:
            req = urllib.request.Request(f"{self.base_url}/{path}", method="GET")
            return self._send(req, path)
        except Exception as exc:
            self.logger.error(f"GET /api/{path} failed: {exc}")
            return None

    def _post(self, path: str, payload: dict | None = None) -> dict | None:
        if self.mock:
            self.logger.debug(f"[MOCK] POST /api/{path} payload={payload}")
            return {}
        try:
            body = json.dumps(payload).encode("utf-8") if payload is not None else b""
            req = urllib.request.Request(
                f"{self.base_url}/{path}",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            return self._send(req, path)
        except Exception as exc:
            self.logger.error(f"POST /api/{path} failed: {exc}")
            return None

    def _delete(self, path: str) -> dict | None:
        if self.mock:
            self.logger.debug(f"[MOCK] DELETE /api/{path}")
            return {}
        try:
            req = urllib.request.Request(
                f"{self.base_url}/{path}", method="DELETE"
            )
            return self._send(req, path)
        except Exception as exc:
            self.logger.error(f"DELETE /api/{path} failed: {exc}")
            return None
