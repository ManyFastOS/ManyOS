"""Safe eject for the SOURCE volume only (Fase 4 — Completion, Reporting &
Safe Eject). A small, self-contained, testable OS-integration layer, the
same pattern as desktop/volumes.py — never business logic in the GUI, never
imported by anything outside desktop/ (see CLAUDE.md).

Ejects via `diskutil eject <source>` as a one-shot QProcess — never a
QThread, for the exact same architectural reason desktop/ingest_process.py
already documents (no second thread, no shared Qt/Python object graph for
two threads to race on teardown; a real, previously-reproduced PySide/
Shiboken crash, not a style preference). Never `-force`: a volume that's
still busy must fail cleanly with a friendly message, never be forced off
while something still has an open handle on it — a forced eject risks
leaving the filesystem in a bad state, which is the opposite of "safe."

Defense in depth: `validate_eject`/`can_eject` re-derive the same safety
rules a caller (desktop/main_window.py) uses to decide whether to even show
an eject button. Button visibility must never be the only thing standing
between a click and `diskutil eject` actually running against the wrong
disk — `EjectRunner.start()` calls `validate_eject` again, immediately
before starting the process, independent of whatever already gated
construction. Both share device_identity.py's `same_physical_device` — the
one safety-net already used everywhere else in this project for "is this
the same physical disk" (see config.py's Fase 3.5 decision).

The destination is never eject-eligible, under any circumstance:
`validate_eject` always refuses if `source` and `destination` are the same
physical device, and always refuses the boot/system volume too.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QProcess, Signal

from many_ingest.desktop.volumes import VOLUMES_ROOT
from many_ingest.device_identity import same_physical_device

BOOT_PATH = Path("/")

_EJECT_FAILED_MESSAGE = (
    "Kon de schijf niet veilig uitwerpen — mogelijk is er nog een bestand of "
    "venster van deze schijf open. Sluit dat en probeer het opnieuw."
)
_EJECT_COULD_NOT_START_MESSAGE = (
    "Kon het uitwerpen niet starten op deze Mac. Neem contact op met de beheerder."
)


class EjectRefused(Exception):
    """Raised by `validate_eject` when `source` must never be ejected. Never
    caught anywhere in order to proceed with the eject anyway — only to
    translate into a friendly message or a `can_eject() -> False`."""


def is_ejectable_volume_root(source: Path, volumes_root: Path = VOLUMES_ROOT) -> bool:
    """True only if `source` is itself a top-level entry directly under
    `volumes_root` (e.g. `/Volumes/SD_CARD_1`) — never a subfolder within a
    volume. A manually chosen folder via the file picker (e.g.
    `/Volumes/SD_CARD_1/DCIM`, or any non-`/Volumes` path) is not itself an
    ejectable unit, even if it happens to live on a removable disk."""
    source = Path(source)
    volumes_root = Path(volumes_root)
    return source.parent == volumes_root and source != volumes_root


def resolve_volume_root(
    source: Path,
    volumes_root: Path = VOLUMES_ROOT,
    *,
    is_same_physical_device: Callable[[Path, Path], bool] = same_physical_device,
) -> Path | None:
    """Finds the actual mounted volume-root directly under `volumes_root`
    that `source` physically lives on — even when `source` is a subfolder
    of it (e.g. a manually chosen `/Volumes/Sharpwaves/Test`, found via a
    real manual test, 2026-09-15: an otherwise fully successful, verified
    ingest never offered eject at all, because `source` itself wasn't the
    volume-root). Matches purely via device identity
    (`is_same_physical_device`, defaulting to the real `st_dev` check via
    device_identity.py) — deliberately never by string-prefix/name matching
    and never by simply returning `source.parent`: a subfolder several
    levels deep, a symlink, or one volume's name being a substring of
    another's must never produce a false match.

    Returns `None` when no such volume-root can be found — `source` isn't
    on any currently mounted `/Volumes` entry at all (e.g. it was itself
    unmounted since being selected), or `volumes_root` isn't a real,
    readable directory. `None` is not an error, it means "no eject can
    safely be offered for this source" — the caller (`main_window.py`)
    simply doesn't show the button, same as today's behaviour for a source
    that was never on a removable volume to begin with. This function only
    ever finds a candidate; it never itself decides whether that candidate
    may actually be ejected — see `validate_eject`/`can_eject` for the
    destination/boot-volume safety checks, always run afterwards on
    whatever this returns.
    """
    source = Path(source)
    volumes_root = Path(volumes_root)
    try:
        entries = sorted(volumes_root.iterdir())
    except OSError:
        return None
    for entry in entries:
        if is_same_physical_device(source, entry):
            return entry
    return None


def validate_eject(
    source: Path,
    destination: Path,
    *,
    volumes_root: Path = VOLUMES_ROOT,
    boot_path: Path = BOOT_PATH,
    is_same_physical_device: Callable[[Path, Path], bool] = same_physical_device,
) -> None:
    """Defense-in-depth: independently re-derives whether `source` may be
    ejected, never trusting that a caller's own gating was correct. Raises
    `EjectRefused` (never returns a bare bool) so this can never be
    accidentally ignored by a caller that forgets to check a return value.

    `is_same_physical_device` defaults to the real device-identity check
    (device_identity.py's `same_physical_device`, via `st_dev`) and is
    injectable purely for tests — the same convention desktop/volumes.py
    already uses for `is_boot_volume`/`is_destination_volume`, needed for
    the same reason: an automated test has no portable way to fake a second
    real physical disk."""
    if not is_ejectable_volume_root(source, volumes_root):
        raise EjectRefused("Dit is geen volledige, uitwerpbare schijf.")
    if is_same_physical_device(source, destination):
        raise EjectRefused(
            "De bron is dezelfde fysieke schijf als de bestemming — de "
            "bestemming wordt nooit uitgeworpen."
        )
    if is_same_physical_device(source, boot_path):
        raise EjectRefused("De opstartschijf van deze Mac kan niet worden uitgeworpen.")


def can_eject(
    source: Path,
    destination: Path,
    *,
    volumes_root: Path = VOLUMES_ROOT,
    boot_path: Path = BOOT_PATH,
    is_same_physical_device: Callable[[Path, Path], bool] = same_physical_device,
) -> bool:
    """Same checks as `validate_eject`, as a plain bool — for gating button
    visibility only. The eject action itself still calls `validate_eject`
    again right before running `diskutil` (see `EjectRunner.start`); this
    function existing is never a substitute for that second check."""
    try:
        validate_eject(
            source,
            destination,
            volumes_root=volumes_root,
            boot_path=boot_path,
            is_same_physical_device=is_same_physical_device,
        )
    except EjectRefused:
        return False
    return True


def _diskutil_path() -> str:
    return shutil.which("diskutil") or "/usr/sbin/diskutil"


class EjectRunner(QObject):
    """Wraps one `diskutil eject` invocation as a one-shot QProcess. Plain
    signals out — no widgets, and never the raw diskutil output (Design
    Language hoofdstuk 15: no technical detail as the main UX)."""

    succeeded = Signal()
    failed = Signal(str)  # friendly message, never a raw exception/stderr dump

    def __init__(
        self,
        source: Path,
        destination: Path,
        *,
        volumes_root: Path = VOLUMES_ROOT,
        boot_path: Path = BOOT_PATH,
        is_same_physical_device: Callable[[Path, Path], bool] = same_physical_device,
        _command_override: list[str] | None = None,
    ) -> None:
        super().__init__()
        self._source = Path(source)
        self._destination = Path(destination)
        self._volumes_root = volumes_root
        self._boot_path = boot_path
        self._is_same_physical_device = is_same_physical_device
        self._terminal_signal_sent = False

        self._process = QProcess(self)
        if _command_override is not None:
            # Test-only seam (mirrors desktop/ingest_process.py's
            # IngestRunner) — points the underlying QProcess at a different
            # program so success/failure/crash can be tested deterministically
            # without ever really calling diskutil. Never used in production.
            self._process.setProgram(_command_override[0])
            self._process.setArguments(_command_override[1:])
        else:
            self._process.setProgram(_diskutil_path())
            # Deliberately never "-force" — see this module's docstring.
            self._process.setArguments(["eject", str(self._source)])
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_error_occurred)

    def wait(self, timeout_ms: int = 5_000) -> None:
        """Blocks until the eject process finishes, or `timeout_ms` elapses.
        Called only from MainWindow's shutdown path (`closeEvent`/
        `aboutToQuit`), mirroring `IngestRunner.stop_and_wait`'s guarantee
        that the app never quits while a QProcess object it owns could
        still be silently running — see desktop/ingest_process.py.
        Deliberately never terminates/kills: `diskutil eject` is a fast,
        one-shot, non-destructive operation with nothing that needs
        interrupting partway through."""
        if self._process.state() == QProcess.ProcessState.NotRunning:
            return
        self._process.waitForFinished(timeout_ms)

    def command(self) -> list[str]:
        """The exact command this runner will execute — test/debug
        introspection only, never used to decide anything at runtime."""
        return [self._process.program(), *self._process.arguments()]

    def start(self) -> None:
        # Re-validated here, immediately before the process actually starts —
        # defense in depth, independent of whatever already gated whether
        # this object got constructed at all (see this module's docstring).
        try:
            validate_eject(
                self._source,
                self._destination,
                volumes_root=self._volumes_root,
                boot_path=self._boot_path,
                is_same_physical_device=self._is_same_physical_device,
            )
        except EjectRefused as exc:
            self._terminal_signal_sent = True
            self.failed.emit(str(exc))
            return
        self._process.start()

    def _on_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        if self._terminal_signal_sent:
            return
        self._terminal_signal_sent = True
        if exit_code == 0 and exit_status == QProcess.ExitStatus.NormalExit:
            self.succeeded.emit()
        else:
            # Never surface diskutil's own stderr (e.g. "Resource busy") —
            # translated to one consistent, friendly message instead,
            # regardless of the underlying reason (Design Language hoofdstuk 15).
            self.failed.emit(_EJECT_FAILED_MESSAGE)

    def _on_error_occurred(self, error: QProcess.ProcessError) -> None:
        # Only FailedToStart needs separate handling here — that is the one
        # QProcess failure for which `finished` never fires per Qt's own
        # contract (see ingest_process.py's identical reasoning). Any other
        # error still triggers `finished`, already handled above.
        if self._terminal_signal_sent:
            return
        if error == QProcess.ProcessError.FailedToStart:
            self._terminal_signal_sent = True
            self.failed.emit(_EJECT_COULD_NOT_START_MESSAGE)


StartEject = Callable[..., EjectRunner]


def start_eject(
    source: Path,
    destination: Path,
    *,
    on_succeeded,
    on_failed,
    is_same_physical_device: Callable[[Path, Path], bool] = same_physical_device,
    _command_override: list[str] | None = None,
) -> EjectRunner:
    """Starts ejecting `source` in its own OS process and wires the given
    callbacks. Never called automatically — always the direct result of one
    explicit user click (see desktop/main_window.py)."""
    runner = EjectRunner(
        source,
        destination,
        is_same_physical_device=is_same_physical_device,
        _command_override=_command_override,
    )
    runner.succeeded.connect(on_succeeded)
    runner.failed.connect(on_failed)
    runner.start()
    return runner
