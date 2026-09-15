"""Fase 2: preview, layered on the Fase 0/1 shell.

Architecture (see the Fase 2 build plan and CLAUDE.md — no business logic in
the GUI, no second ingest implementation):

    GUI (this file) -> IngestRunner (desktop/ingest_process.py) -> QProcess
    -> ingest_worker.py -> IngestService -> Storage -> Manifest -> Report

This window never calls IngestService directly and never re-derives anything
it computes (classification, duplicates, ...) — it only renders the
IngestSummary the worker process hands back, and reacts to clicks. Volume
detection stays in desktop/volumes.py, unchanged from Fase 1.

Both the preview and a real ingest (Fase 3) now run the same way: a
one-shot `IngestRunner` wrapping a `QProcess` (see desktop/ingest_process.py
for why — a real, reproduced PySide/Shiboken QThread-lifetime crash, not a
style preference). There is no QThread anywhere in this window; no
lifecycle registry, no thread-teardown guard is needed for either path.

Six states, one window (content is swapped, no new dialogs/screens):
- empty / chooser / selected(-with-form)   [Fase 1, now selected also has
                                             client/project fields + Start]
- analyzing   [new: progress while the preview process runs]
- preview     [new: the preview result, in plain language]
- failed      [new: a friendly message, never a stack trace]

`detect_volumes`, `start_preview` and `start_real_ingest` are all injected
(defaulting to the real implementations) so tests can drive every state
deterministically, without a real /Volumes, a real config, or a real
background process.
"""

from __future__ import annotations

import dataclasses
import time
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from many_ingest.config import load_storage_layout
from many_ingest.core.ingest_service import CLIENT_FOLDER_NAME, ProgressUpdate
from many_ingest.core.report import IngestSummary
from many_ingest.desktop import eject, ingest_process
from many_ingest.desktop.volumes import (
    DestinationInfo,
    VolumeInfo,
    format_size,
    list_candidate_volumes,
    list_destination_volumes,
    scan_media_summary,
)

EMPTY_STATE_TEXT = "Sluit een SD-kaart of SSD aan om te beginnen."
CHOOSE_BUTTON_TEXT = "Kies een map"
CHOOSE_ANOTHER_BUTTON_TEXT = "Andere map kiezen"
OTHER_DISK_TEXT = "Andere schijf gebruiken"
RETRY_TEXT = "Opnieuw zoeken"
CHOOSER_TITLE_TEXT = "Welke schijf wil je gebruiken?"
CLIENT_LABEL_TEXT = "Klant"
PROJECT_LABEL_TEXT = "Project"
DESTINATION_LABEL_TEXT = "Bestemmingsschijf"
NO_DESTINATIONS_TEXT = (
    "Geen schrijfbare externe schijven gevonden. Sluit de bestemmingsschijf aan "
    "en klik op Opnieuw zoeken."
)
RETRY_DESTINATIONS_TEXT = "Opnieuw zoeken"
SELECTED_DESTINATION_PREFIX = "Gekozen: "
PREVIEW_BUTTON_TEXT = "Bekijk inhoud"
ANALYZING_TEXT = "Bezig met bekijken..."
CANCEL_BUTTON_TEXT = "Annuleren"
PREVIEW_TITLE_TEXT = "Wat we hebben gevonden"
UNKNOWN_PROFILE_LABEL = "Onbekend"
AUDIO_PROFILE_LABEL = "Audio"
BACK_TEXT = "Terug"
START_INGEST_BUTTON_TEXT = "Start Ingest"
INGESTING_TEXT = "Bezig met kopiëren..."
INGEST_DONE_TITLE_TEXT = "Klaar"
INGEST_PARTIAL_TITLE_TEXT = "Bijna klaar"
NEW_INGEST_TEXT = "Nieuwe ingest"

SECTION_DURATION = "Duur"
SECTION_METADATA_WARNINGS = "Metadata-waarschuwingen"
METADATA_WARNINGS_NOTE_TEXT = (
    "Deze bestanden zijn inhoudelijk correct gekopieerd en geverifieerd — "
    "alleen een paar niet-kritieke details (zoals tijdstempels) konden niet "
    "volledig worden overgenomen."
)
VERIFIED_SAFE_TEXT = "Alle bestanden zijn geverifieerd."

# Zoveel losse bestand+reden-regels toont het eindscherm maximaal bij fouten
# — voorkomt een onbeheersbare lijst bij een kaart met veel problemen; de
# rest blijft meegeteld in de "+ N meer"-regel, nooit stilzwijgend weggelaten.
_MAX_VISIBLE_ERROR_DETAILS = 10

OPEN_IN_FINDER_BUTTON_TEXT = "Open in Finder"
EJECT_BUTTON_TEXT = "Schijf veilig verwijderen"
EJECT_IN_PROGRESS_TEXT = "Bezig met uitwerpen…"
EJECT_SUCCESS_TEXT = "Bron veilig verwijderd."

DO_NOT_DISCONNECT_TEXT = "Verwijder of koppel geen opslagapparaten los tijdens het kopiëren."
ETA_PLACEHOLDER_TEXT = "Resterende tijd berekenen…"

CANCEL_INGEST_CONFIRM_MESSAGE = (
    "Ingest annuleren?\n"
    "Bestanden die al volledig zijn gekopieerd blijven staan. De huidige "
    "ingest wordt gestopt."
)
CONTINUE_INGEST_TEXT = "Doorgaan met ingest"
CONFIRM_CANCEL_INGEST_TEXT = "Ingest annuleren"

SAFETY_STOP_MESSAGE = (
    "Ingest automatisch gestopt nadat meerdere bestanden achter elkaar niet "
    "konden worden verwerkt. Controleer de verbinding met de bron- en "
    "bestemmingsschijf en probeer het opnieuw."
)

# Aantal opeenvolgende failed_verification-uitkomsten waarna de app proactief
# stopt (zie de safety-stop-analyse: hoog genoeg om niet op een paar losse
# kapotte bestanden te reageren, laag genoeg om te stoppen ruim vóórdat een
# kaart van honderden bestanden alsnog helemaal doorgeploegd wordt).
_SAFETY_STOP_THRESHOLD = 5

# Ondergrens vóór een ETA getoond wordt i.p.v. ETA_PLACEHOLDER_TEXT — te
# weinig samples/tijd geeft een ruisige, onbetrouwbare extrapolatie (bijv.
# één ongewoon groot eerste bestand).
_ETA_MIN_SAMPLES = 2
_ETA_MIN_ELAPSED_SECONDS = 1.0

# Fallback voor de bestemmings-breadcrumb, alleen gebruikt als config.yaml
# (net als voor een echte ingest) niet gelezen kan worden — nooit de
# primaire bron. De normale breadcrumb-niveaus tussen de gekozen schijf en
# klant/project komen uit StorageLayout's footage_subpath (config.py) plus
# CLIENT_FOLDER_NAME (core/ingest_service.py), zie
# _footage_subpath_labels()/_destination_breadcrumb_lines() hieronder — nooit
# hier opnieuw hardcoded, zodat de breadcrumb niet kan afwijken van de
# werkelijke, door de engine aangemaakte structuur (zie CLAUDE.md).
DESTINATION_ORG_LABEL = "ManyFast"

SECTION_SOURCE = "Bron"
SECTION_DESTINATION = "Bestemming"
SECTION_FILES = "Bestanden"
SECTION_CAMERAS = "Camera's"
SECTION_DUPLICATES = "Duplicaten"
SECTION_NAME_CONFLICTS = "Naamconflicten"
SECTION_NOTES = "Bijzonderheden"
SECTION_ERRORS = "Fouten"
SECTION_SOURCE_STATUS = "Bron"
SAFE_TO_DELETE_YES_TEXT = "Veilig om de bron te verwijderen."
SAFE_TO_DELETE_NO_TEXT = "Nog niet veilig om de bron te verwijderen."

DetectVolumes = Callable[[], list[VolumeInfo]]
DetectDestinations = Callable[[Path], list[DestinationInfo]]
StartPreview = Callable[..., object]
StartRealIngest = Callable[..., object]
StartEject = Callable[..., object]


@dataclasses.dataclass(frozen=True)
class _SelectionInfo:
    source_path: Path
    name: str
    media_file_count: int
    media_total_bytes: int
    via_auto_detection: bool


@dataclasses.dataclass(frozen=True)
class _PreviewInput:
    """Everything a preview needs, and everything Start Ingest reuses
    unchanged afterwards (Fase 3.5 adds `destination` alongside the existing
    source/client/project — preview and a real ingest must resolve to
    exactly the same destination, the same guarantee already proven for
    source/client/project: this is the one place that tuple/value is
    captured, never reconstructed)."""

    source: Path
    client: str
    project: str
    destination: DestinationInfo


class MainWindow(QWidget):
    def __init__(
        self,
        detect_volumes: DetectVolumes | None = None,
        detect_destinations: DetectDestinations | None = None,
        start_preview: StartPreview | None = None,
        start_real_ingest: StartRealIngest | None = None,
        start_eject: StartEject | None = None,
        config_path: Path = ingest_process.DEFAULT_CONFIG_PATH,
        camera_profiles_path: Path = ingest_process.DEFAULT_CAMERA_PROFILES_PATH,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Many Ingest")
        # 480x420 is de ondergrens (kleinste bruikbare venstergrootte, o.a. voor
        # het lege scherm) — 560x760 is de vaste openingsgrootte, ruim genoeg
        # om een normale preview (Bron/Bestemming/Bestanden/Camera's/Duplicaten/
        # Naamconflicten + knoppen) direct leesbaar te tonen zonder handmatig
        # uitrekken. Zie _scroll_area hieronder voor wat er gebeurt als de
        # inhoud dit tóch overschrijdt (veel camera-profielen, een lang
        # klant/projectnaam, of een kleiner venster dan dit).
        self.setMinimumSize(480, 420)
        self.resize(560, 760)

        self._detect_volumes: DetectVolumes = detect_volumes or (
            lambda: list_candidate_volumes(storage_root=None)
        )
        self._detect_destinations: DetectDestinations = (
            detect_destinations or list_destination_volumes
        )
        self._preview_starter: StartPreview = start_preview or ingest_process.start_preview
        self._start_real_ingest: StartRealIngest = (
            start_real_ingest or ingest_process.start_real_ingest
        )
        self._start_eject: StartEject = start_eject or eject.start_eject
        self._config_path = config_path
        self._camera_profiles_path = camera_profiles_path

        self.selected_source: Path | None = None
        self._selection: _SelectionInfo | None = None
        self._destination_volumes: list[DestinationInfo] = []
        self._selected_destination: DestinationInfo | None = None
        self._preview_runner: object | None = None

        # Vastgelegd zodra een analyse *start* (zie _start_preview), pas
        # bevestigd zodra hij ook echt succesvol afrondt (zie
        # _on_analysis_finished) — "Start Ingest" mag alleen de exacte
        # (bron, klant, project, bestemming) gebruiken van de laatst getoonde,
        # geslaagde preview, nooit een combinatie die ondertussen gewijzigd is
        # zonder nieuwe preview. Ongeldig gemaakt zodra de gebruiker teruggaat
        # naar het formulier (_return_to_selection) of een nieuwe analyse start.
        self._pending_preview_input: _PreviewInput | None = None
        self._confirmed_preview_input: _PreviewInput | None = None
        self._ingest_runner: object | None = None

        # Fase 3: voortgangs-/veiligheidsstop-state, uitsluitend voor een
        # echte ingest (nooit voor preview — zie _on_start_ingest_clicked en
        # _reset_ingest_progress_state). Altijd samen gereset bij het starten
        # van een nieuwe ingest en bij elke terminale uitkomst (voltooid,
        # mislukt, geannuleerd).
        self._ingest_start_time: float | None = None
        self._last_ingest_progress: ProgressUpdate | None = None
        self._consecutive_ingest_failures = 0
        self._safety_stop_triggered = False
        self._cancel_confirmation_pending = False

        # Fase 4: per-bestand foutdetail, verzameld terwijl een échte ingest
        # loopt (via _on_ingest_asset_processed, hetzelfde asset_processed-
        # event dat de safety-stop-teller al gebruikt) — nooit voor preview
        # (on_asset_processed wordt alleen aan een echte ingest doorgegeven,
        # zie _on_start_ingest_clicked). Alleen geleegd bij het starten van
        # een NIEUWE ingest (_on_start_ingest_clicked), nooit door
        # _reset_ingest_progress_state() — dat draait al vóór
        # _render_ingest_report() de data nog nodig heeft (zie
        # _on_ingest_completed).
        self._ingest_failed_assets: list[tuple[str, str]] = []

        # Fase 4: eindscherm-state voor Open in Finder / Schijf veilig
        # verwijderen — alleen gezet zodra een échte ingest voltooit (zie
        # _on_ingest_completed), nooit voor preview. `_report_generation`
        # bewijst of een later binnenkomend eject-resultaat nog bij het
        # zichtbare eindscherm hoort (zie _on_eject_succeeded/_failed) — een
        # gebruiker kan tijdens het uitwerpen al op "Nieuwe ingest" klikken.
        self._last_ingest_summary: IngestSummary | None = None
        self._report_destination_path: Path | None = None
        self._report_generation = 0
        self._eject_runner: object | None = None
        self._eject_status = "idle"  # idle | ejecting | ejected | failed
        self._eject_source_path: Path | None = None
        self._eject_destination_path: Path | None = None
        self._eject_failure_message = ""

        self._outer_layout = QVBoxLayout(self)

        # Elke schermstaat (leeg/keuze/geselecteerd/analyseren/preview/fout)
        # wordt in dit ene scroll-gebied gehangen (zie _set_content) in plaats
        # van rechtstreeks in _outer_layout. `setWidgetResizable(True)` laat
        # de inhoud altijd de volle breedte van het venster gebruiken; alleen
        # als de inhoud hóger is dan het venster (bv. een preview met veel
        # secties, of het venster kleiner dan het standaardformaat) verschijnt
        # een verticale scrollbalk in plaats van dat labels elkaar overlappen
        # of afgekapt worden.
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self._outer_layout.addWidget(self._scroll_area)

        self._content: QWidget | None = None

        self._run_detection()

    def closeEvent(self, event) -> None:
        """Stops any active preview and any active real ingest before
        letting the window close. Both are `IngestRunner`/`QProcess`
        instances (see desktop/ingest_process.py) — the same kind of object,
        stopped the same way — but kept as two independent calls since they
        track two independent runners (`self._preview_runner`,
        `self._ingest_runner`), not because they need different logic.

        `closeEvent()` alone does not cover every quit path:
        `QApplication.quit()` called directly — what macOS actually sends
        for Cmd+Q / the app-menu Quit item — never delivers a `QCloseEvent`
        to this widget at all, so `closeEvent()` never runs. That's why
        `app.aboutToQuit` (wired in app.py) calls the same two methods —
        whichever quit path fires first does the real work; both methods are
        idempotent (a no-op once their runner is `None`/stopped), so calling
        them twice for the same shutdown is harmless.
        """
        self._wait_for_preview_to_stop()
        self._wait_for_ingest_to_stop()
        self._wait_for_eject_to_stop()
        super().closeEvent(event)

    def _wait_for_preview_to_stop(self) -> None:
        """Stops an active preview's `IngestRunner`/`QProcess` before the
        app is allowed to quit — see `_wait_for_ingest_to_stop` just below,
        which does the exact same thing for a real ingest. `stop_and_wait()`
        is a synchronous terminate-then-kill; a preview never writes
        anything regardless, so there's nothing to leave half-done."""
        if self._preview_runner is not None:
            self._preview_runner.stop_and_wait()

    def _wait_for_ingest_to_stop(self) -> None:
        """The real-ingest equivalent of `_wait_for_preview_to_stop`. A
        `QProcess` object has no fatal-abort-on-destroy failure mode —
        dropping the last Python reference to it while the OS process is
        still running does not crash this process — but leaving it running
        unattended after the window/app is gone would silently keep copying
        files in the background with no UI and no way to see progress or a
        final report, which is its own kind of unsafe surprise for the
        editor. This calls `IngestRunner.stop_and_wait()` — a synchronous
        terminate-then-kill (never removes source files, v0.1 is copy-only)
        — so the app never actually quits while a real ingest could still be
        silently running."""
        if self._ingest_runner is not None:
            self._ingest_runner.stop_and_wait()

    def _wait_for_eject_to_stop(self) -> None:
        """The eject equivalent of `_wait_for_ingest_to_stop` — see
        `eject.EjectRunner.wait()`'s docstring for why this blocks rather
        than terminates."""
        if self._eject_runner is not None:
            self._eject_runner.wait()

    # -- public introspection (used by the app and by tests) ----------------

    def current_message(self) -> str:
        label = self._content.findChild(QLabel, "statusLabel") if self._content else None
        return label.text() if label else ""

    def current_detail(self) -> str:
        label = self._content.findChild(QLabel, "captionLabel") if self._content else None
        return label.text() if label else ""

    def choose_button(self) -> QPushButton | None:
        return self._content.findChild(QPushButton, "primaryButton") if self._content else None

    def secondary_action_button(self) -> QPushButton | None:
        return self._content.findChild(QPushButton, "linkButton") if self._content else None

    def volume_cards(self) -> list[QPushButton]:
        return self._content.findChildren(QPushButton, "volumeCard") if self._content else []

    def destination_cards(self) -> list[QPushButton]:
        return self._content.findChildren(QPushButton, "destinationCard") if self._content else []

    def selected_destination_text(self) -> str:
        label = self._content.findChild(QLabel, "selectedDestinationLabel") if self._content else None
        return label.text() if label else ""

    def client_input(self) -> QLineEdit | None:
        return self._content.findChild(QLineEdit, "clientInput") if self._content else None

    def project_input(self) -> QLineEdit | None:
        return self._content.findChild(QLineEdit, "projectInput") if self._content else None

    def progress_bar(self) -> QProgressBar | None:
        return self._content.findChild(QProgressBar, "analysisProgress") if self._content else None

    def preview_lines(self) -> list[str]:
        if self._content is None:
            return []
        return [label.text() for label in self._content.findChildren(QLabel, "previewLine")]

    def percentage_label_text(self) -> str:
        label = self._content.findChild(QLabel, "percentageLabel") if self._content else None
        return label.text() if label else ""

    def speed_label_text(self) -> str:
        label = self._content.findChild(QLabel, "speedLabel") if self._content else None
        return label.text() if label else ""

    def do_not_disconnect_warning_visible(self) -> bool:
        label = self._content.findChild(QLabel, "warningLabel") if self._content else None
        return label is not None and label.text() == DO_NOT_DISCONNECT_TEXT

    def cancel_confirmation_message(self) -> str:
        label = self._content.findChild(QLabel, "cancelConfirmationLabel") if self._content else None
        return label.text() if label else ""

    def open_in_finder_button(self) -> QPushButton | None:
        return self._content.findChild(QPushButton, "openInFinderButton") if self._content else None

    def eject_button(self) -> QPushButton | None:
        return self._content.findChild(QPushButton, "ejectButton") if self._content else None

    # -- detection ------------------------------------------------------------

    def _run_detection(self) -> None:
        candidates = self._detect_volumes()
        if len(candidates) == 1:
            self._select_volume(candidates[0])
        elif len(candidates) > 1:
            self._render_chooser(candidates)
        else:
            self._render_empty_state()

    def _select_volume(self, volume: VolumeInfo) -> None:
        self.selected_source = volume.path
        self._selection = _SelectionInfo(
            source_path=volume.path,
            name=volume.name,
            media_file_count=volume.media_file_count,
            media_total_bytes=volume.media_total_bytes,
            via_auto_detection=True,
        )
        # Een nieuwe bron maakt een eerder gekozen bestemming ongeldig — de
        # kandidatenlijst hangt af van de bron (dezelfde fysieke schijf mag
        # nooit allebei zijn, zie desktop/volumes.py's default_is_source_volume),
        # dus een oude keuze kan na een bronwissel onterecht (nog) geldig lijken.
        self._selected_destination = None
        self._render_selected()

    # -- manual folder picking (native dialog) --------------------------------

    def _on_choose_source_clicked(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Kies een bronmap")
        if chosen:
            self._set_manual_source(Path(chosen))

    def _set_manual_source(self, path: Path) -> None:
        self.selected_source = path
        media_file_count, media_total_bytes = scan_media_summary(path)
        self._selection = _SelectionInfo(
            source_path=path,
            name=path.name,
            media_file_count=media_file_count,
            media_total_bytes=media_total_bytes,
            via_auto_detection=False,
        )
        self._selected_destination = None  # zie _select_volume's toelichting
        self._render_selected()

    # -- dry-run analysis (via the Controller, never IngestService directly) --

    def _start_preview(self, client: str, project: str) -> None:
        if self._selection is None or self._selected_destination is None:
            return  # niet bereikbaar via de UI (knop staat dan uit), extra zekerheid
        if self._preview_runner is not None and self._preview_runner.is_running():
            return  # een preview loopt al; niet bereikbaar via de UI, extra zekerheid
        # Een nieuwe preview maakt elke eerder bevestigde preview-input
        # ongeldig totdat déze preview ook echt slaagt (zie
        # _on_analysis_finished) — Start Ingest mag nooit een combinatie
        # gebruiken die niet exact overeenkomt met de laatst getoonde preview.
        self._confirmed_preview_input = None
        self._pending_preview_input = _PreviewInput(
            source=self._selection.source_path,
            client=client,
            project=project,
            destination=self._selected_destination,
        )
        self._render_analyzing()
        self._preview_runner = self._preview_starter(
            self._selection.source_path,
            client,
            project,
            destination_root=self._selected_destination.path,
            on_progress=self._on_analysis_progress,
            on_completed=self._on_analysis_finished,
            on_failed=self._on_analysis_failed,
            on_cancelled=self._on_analysis_cancelled,
            config_path=self._config_path,
            camera_profiles_path=self._camera_profiles_path,
        )

    def _on_cancel_clicked(self) -> None:
        """A single, unconfirmed click — unlike a real ingest, cancelling a
        preview has zero risk (nothing is ever written), so the "only
        confirm what's truly consequential" rule (Design Language hoofdstuk
        12) means this deliberately does NOT ask "are you sure?"."""
        if self._preview_runner is not None:
            self._preview_runner.cancel()

    def _on_analysis_progress(self, update: ProgressUpdate) -> None:
        bar = self.progress_bar()
        if bar is not None:
            if bar.maximum() == 0 and update.total:
                bar.setRange(0, update.total)
            bar.setValue(update.processed)

        detail = self._content.findChild(QLabel, "captionLabel") if self._content else None
        if detail is not None:
            detail.setText(f"{update.processed} van {update.total} bestanden bekeken")

    def _on_analysis_finished(self, summary: IngestSummary) -> None:
        self._preview_runner = None
        # Pas hier bevestigen (niet al bij het starten) — dit is het enige
        # moment waarop we weten dat déze exacte (bron, klant, project) ook
        # daadwerkelijk de preview is die de gebruiker nu ziet.
        self._confirmed_preview_input = self._pending_preview_input
        self._render_preview(summary)

    def _on_analysis_failed(self, message: str) -> None:
        self._preview_runner = None
        self._render_analysis_failed(message)

    def _on_analysis_cancelled(self) -> None:
        self._preview_runner = None
        self._return_to_selection()

    def _return_to_selection(self) -> None:
        # Elke terugkeer naar het formulier maakt een bevestigde preview
        # ongeldig — een nieuwe "Start Ingest" vereist altijd eerst weer een
        # verse, geslaagde preview (zie hoofdstuk 14 van de Fase 3-opdracht).
        self._confirmed_preview_input = None
        # Maakt ook een eventueel nog lopende eject van het zojuist verlaten
        # eindscherm ongeldig (zie _on_eject_succeeded/_on_eject_failed) —
        # niet alleen een nieuw voltooide ingest (_on_ingest_completed)
        # ongedaan het eindscherm, ook zelf terugklikken naar "Nieuwe
        # ingest" is een manier om dit eindscherm te verlaten.
        self._report_generation += 1
        if self._selection is not None:
            self._render_selected()

    # -- echte ingest (Fase 3, via IngestRunner/QProcess — nooit IngestService
    # rechtstreeks, zie ingest_process.py) --------------------------------------

    def _on_start_ingest_clicked(self) -> None:
        if self._confirmed_preview_input is None:
            return  # niet bereikbaar via de UI (knop staat dan uit), extra zekerheid
        if self._ingest_runner is not None and self._ingest_runner.is_running():
            return  # een ingest loopt al; niet bereikbaar via de UI, extra zekerheid
        # Geen teardown-race met de voorafgaande preview meer mogelijk: de
        # preview is óók een IngestRunner/QProcess (zie desktop/ingest_process.py),
        # niet meer een QThread — `_on_analysis_finished` heeft `_preview_runner`
        # al op None gezet zodra `_confirmed_preview_input` gezet werd (zie
        # hierboven), en er is geen tweede thread die nog los daarvan iets
        # aan het afbreken kan zijn. Dit venstertje bestond alleen voor het
        # oude QThread-pad (zie git-historie) en is met dat pad verwijderd,
        # niet dichtgetimmerd met een extra guard.

        preview_input = self._confirmed_preview_input
        self._reset_ingest_progress_state()
        self._ingest_failed_assets = []
        self._ingest_start_time = time.monotonic()
        self._render_ingesting()
        self._ingest_runner = self._start_real_ingest(
            preview_input.source,
            preview_input.client,
            preview_input.project,
            destination_root=preview_input.destination.path,
            on_progress=self._on_ingest_progress,
            on_completed=self._on_ingest_completed,
            on_failed=self._on_ingest_failed,
            on_cancelled=self._on_ingest_cancelled,
            on_asset_processed=self._on_ingest_asset_processed,
            config_path=self._config_path,
            camera_profiles_path=self._camera_profiles_path,
        )

    def _reset_ingest_progress_state(self) -> None:
        """De enige plek die alle voortgangs-/snelheids-/veiligheidsstop-
        state voor een echte ingest terugzet — aangeroepen vlak vóór een
        nieuwe ingest start én na elke terminale uitkomst (voltooid, mislukt,
        geannuleerd), zodat niets van run N blijft doorlopen in run N+1."""
        self._ingest_start_time = None
        self._last_ingest_progress = None
        self._consecutive_ingest_failures = 0
        self._safety_stop_triggered = False
        self._cancel_confirmation_pending = False

    def _on_cancel_ingest_clicked(self) -> None:
        """Eerste klik op "Annuleren": toont een korte inline bevestiging
        (Design Language hoofdstuk 12 — annuleren tijdens een lopende actie
        is een van de expliciete uitzonderingen op "nooit bevestigen"), roept
        nog GEEN cancel() aan. Zie _on_confirm_cancel_ingest_clicked voor de
        daadwerkelijke annulering, pas na expliciete bevestiging."""
        self._cancel_confirmation_pending = True
        self._render_ingesting()

    def _on_continue_ingest_clicked(self) -> None:
        """"Doorgaan met ingest" — de primaire, veilige actie: sluit de
        bevestiging, verandert verder niets, de ingest liep onveranderd
        door."""
        self._cancel_confirmation_pending = False
        self._render_ingesting()

    def _on_confirm_cancel_ingest_clicked(self) -> None:
        """Pas hier wordt runner.cancel() echt aangeroepen — na expliciete
        bevestiging, nooit direct vanuit de eerste klik."""
        self._cancel_confirmation_pending = False
        if self._ingest_runner is not None:
            self._ingest_runner.cancel()

    def _on_ingest_progress(self, update: ProgressUpdate) -> None:
        self._last_ingest_progress = update

        bar = self.progress_bar()
        if bar is not None:
            if bar.maximum() == 0 and update.total:
                bar.setRange(0, update.total)
            bar.setValue(update.processed)

        percentage_label = self._content.findChild(QLabel, "percentageLabel") if self._content else None
        if percentage_label is not None:
            percentage = int(update.processed / update.total * 100) if update.total else 0
            percentage_label.setText(f"{percentage}%")

        detail = self._content.findChild(QLabel, "captionLabel") if self._content else None
        if detail is not None:
            detail.setText(
                f"{update.processed} van {update.total} bestanden · "
                f"{format_size(update.bytes_processed)} verwerkt"
            )

        speed_label = self._content.findChild(QLabel, "speedLabel") if self._content else None
        if speed_label is not None:
            speed_label.setText(self._speed_and_eta_text(update))

    def _speed_and_eta_text(self, update: ProgressUpdate) -> str:
        """Snelheid en resterende tijd, uitsluitend berekend in deze app-laag
        (geen engine-wijziging) uit `time.monotonic()` en de al beschikbare
        `bytes_processed`/`processed`/`total` van dit progress-event. Geeft
        nooit een deling door nul, een negatieve waarde of NaN/oneindig terug
        — elke berekening is expliciet geguard."""
        if self._ingest_start_time is None:
            return ""
        elapsed = time.monotonic() - self._ingest_start_time
        if elapsed <= 0:
            return ""

        speed_text = _format_speed(update.bytes_processed / elapsed) if update.bytes_processed > 0 else ""

        remaining_files = max(update.total - update.processed, 0)
        if remaining_files == 0:
            eta_text = ""
        elif update.processed < _ETA_MIN_SAMPLES or elapsed < _ETA_MIN_ELAPSED_SECONDS:
            # Te weinig data voor een betrouwbare extrapolatie — nooit een
            # nep-ETA tonen, wel een nette placeholder.
            eta_text = ETA_PLACEHOLDER_TEXT
        else:
            # Gemiddelde tijd per bestand tot nu toe × resterende bestanden —
            # vereist geen vooraf bekende totale bytes-hoeveelheid (die de
            # engine niet realtime doorgeeft), alleen wat dit event al draagt.
            eta_seconds = elapsed * remaining_files / update.processed
            eta_text = _format_eta(eta_seconds)

        return " · ".join(text for text in (speed_text, eta_text) if text)

    def _on_ingest_asset_processed(self, payload: dict) -> None:
        """De safety-stop-teller (Fase 3) — uitsluitend voor een echte
        ingest, nooit voor preview (deze callback wordt alleen doorgegeven
        aan `_start_real_ingest` hierboven, niet aan `_preview_starter`).
        `failed_verification` telt op, `copied` reset naar 0,
        `duplicate_skipped` is neutraal (zie de safety-stop-analyse voor de
        onderbouwing: een duplicaat-skip bewijst niets over of de
        bestemmingsschijf nog bereikbaar is)."""
        outcome = payload.get("outcome")
        if outcome == "failed_verification":
            self._consecutive_ingest_failures += 1
            name = Path(payload.get("source_path") or "").name or "Onbekend bestand"
            reason = payload.get("error") or "Onbekende fout"
            self._ingest_failed_assets.append((name, reason))
        elif outcome == "copied":
            self._consecutive_ingest_failures = 0
        # duplicate_skipped (en elke andere/toekomstige uitkomst): neutraal,
        # teller blijft ongewijzigd.

        if (
            not self._safety_stop_triggered
            and self._consecutive_ingest_failures >= _SAFETY_STOP_THRESHOLD
            and self._ingest_runner is not None
        ):
            # Triggert precies één keer: zodra de vlag gezet is, blijft deze
            # tak hierna altijd overgeslagen, ook als er nog late
            # asset_processed-events binnenkomen vóórdat het workerproces
            # daadwerkelijk stopt. Hergebruikt het bestaande cancel()-pad
            # (SIGTERM → grace → SIGKILL, zie ingest_process.py) — geen apart
            # stopmechanisme.
            self._safety_stop_triggered = True
            self._ingest_runner.cancel()

    def _on_ingest_completed(self, summary: IngestSummary) -> None:
        self._ingest_runner = None
        self._reset_ingest_progress_state()
        self._report_generation += 1
        self._eject_runner = None
        self._eject_status = "idle"
        self._eject_failure_message = ""
        # Bepaald hier, vóórdat _confirmed_preview_input hieronder geleegd
        # wordt — de enige plek waar de bestemming van déze run nog bekend
        # is (zie _resolve_eject_targets). Een latere re-render van dit
        # eindscherm (bijv. na een eject-resultaat) leest alleen nog
        # self._eject_source_path/_eject_destination_path terug, herleidt ze
        # nooit opnieuw uit _confirmed_preview_input.
        self._eject_source_path, self._eject_destination_path = self._resolve_eject_targets(summary)
        # Gerenderd vóórdat _confirmed_preview_input hieronder geleegd wordt —
        # _render_ingest_report toont via _destination_breadcrumb_lines() nog
        # de bestemming van déze run.
        self._render_ingest_report(summary)
        # Een voltooide ingest maakt de preview die hem startte ongeldig —
        # nogmaals op "Start Ingest" klikken zonder nieuwe preview mag nooit
        # dezelfde run herhalen.
        self._confirmed_preview_input = None

    def _resolve_eject_targets(self, summary: IngestSummary) -> tuple[Path | None, Path | None]:
        """Whether Fase 4's "Schijf veilig verwijderen"-knop may be offered
        at all for this run, and if so, the exact (source, destination) pair
        `eject.can_eject`/`start_eject` must validate against. Returns
        (None, None) whenever any condition isn't met — the caller never has
        to separately check `summary.safe_to_delete_source` again.

        `self._selection.source_path` may itself be a subfolder of the real
        volume (e.g. a manually chosen `/Volumes/Sharpwaves/Test`, see the
        2026-09-15 audit) — `eject.resolve_volume_root()` derives the actual
        mounted volume-root via device identity first, never by string-
        matching. `eject.can_eject`'s destination/boot-volume safety checks
        then run on that resolved root, exactly as before; nothing about
        those checks changes."""
        if not summary.safe_to_delete_source:
            return None, None
        if self._selection is None or self._confirmed_preview_input is None:
            return None, None
        resolved_source = eject.resolve_volume_root(self._selection.source_path)
        if resolved_source is None:
            return None, None
        destination = self._confirmed_preview_input.destination.path
        if not eject.can_eject(resolved_source, destination):
            return None, None
        return resolved_source, destination

    def _on_ingest_failed(self, message: str) -> None:
        self._ingest_runner = None
        self._reset_ingest_progress_state()
        self._render_analysis_failed(message)

    def _on_ingest_cancelled(self) -> None:
        self._ingest_runner = None
        self._confirmed_preview_input = None
        # De vlag moet gelezen worden vóórdat _reset_ingest_progress_state()
        # hem terugzet — dit is het enige moment waarop we nog weten of déze
        # annulering van de safety-stop kwam of van een bevestigde,
        # handmatige klik.
        was_safety_stop = self._safety_stop_triggered
        self._reset_ingest_progress_state()
        if was_safety_stop:
            self._render_analysis_failed(SAFETY_STOP_MESSAGE)
        else:
            self._return_to_selection()

    # -- rendering --------------------------------------------------------------

    def _set_content(self, widget: QWidget) -> None:
        # QScrollArea.setWidget() verwijdert en verwijdert (delete) de vorige
        # inhoud zelf al — een extra deleteLater() hier op de oude widget zou
        # een al verwijderd C++-object aanraken.
        self._content = widget
        self._scroll_area.setWidget(widget)

    def _render_empty_state(self) -> None:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.addStretch()

        label = QLabel(EMPTY_STATE_TEXT)
        label.setObjectName("statusLabel")
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
        layout.addSpacing(20)

        choose_button = QPushButton(CHOOSE_BUTTON_TEXT)
        choose_button.setObjectName("primaryButton")
        choose_button.clicked.connect(self._on_choose_source_clicked)
        layout.addWidget(choose_button, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(12)

        retry_button = QPushButton(RETRY_TEXT)
        retry_button.setObjectName("linkButton")
        retry_button.clicked.connect(self._run_detection)
        layout.addWidget(retry_button, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addStretch()
        self._set_content(content)

    def _render_chooser(self, candidates: list[VolumeInfo]) -> None:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.addStretch()

        title = QLabel(CHOOSER_TITLE_TEXT)
        title.setObjectName("statusLabel")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        layout.addSpacing(16)

        for volume in candidates:
            card = QPushButton(
                f"{volume.name}\n{format_size(volume.capacity_bytes)} · "
                f"{_pluralize_media(volume.media_file_count)}"
            )
            card.setObjectName("volumeCard")
            card.clicked.connect(lambda checked=False, v=volume: self._select_volume(v))
            layout.addWidget(card)
            layout.addSpacing(8)

        layout.addStretch()
        self._set_content(content)

    def _render_selected(self) -> None:
        selection = self._selection
        assert selection is not None

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.addStretch()

        name_label = QLabel(selection.name)
        name_label.setObjectName("statusLabel")
        name_label.setWordWrap(True)
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(name_label)

        detail_label = QLabel(
            f"{_pluralize_media(selection.media_file_count)} · "
            f"{format_size(selection.media_total_bytes)}"
        )
        detail_label.setObjectName("captionLabel")
        detail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(detail_label)
        layout.addSpacing(20)

        client_label = QLabel(CLIENT_LABEL_TEXT)
        client_label.setObjectName("fieldLabel")
        layout.addWidget(client_label)
        client_input = QLineEdit()
        client_input.setObjectName("clientInput")
        layout.addWidget(client_input)
        layout.addSpacing(8)

        project_label = QLabel(PROJECT_LABEL_TEXT)
        project_label.setObjectName("fieldLabel")
        layout.addWidget(project_label)
        project_input = QLineEdit()
        project_input.setObjectName("projectInput")
        layout.addWidget(project_input)
        layout.addSpacing(16)

        preview_button = QPushButton(PREVIEW_BUTTON_TEXT)
        preview_button.setObjectName("primaryButton")
        preview_button.setEnabled(False)

        def _update_enabled() -> None:
            preview_button.setEnabled(
                bool(client_input.text().strip())
                and bool(project_input.text().strip())
                and self._selected_destination is not None
            )

        client_input.textChanged.connect(_update_enabled)
        project_input.textChanged.connect(_update_enabled)
        preview_button.clicked.connect(
            lambda: self._start_preview(client_input.text().strip(), project_input.text().strip())
        )

        self._render_destination_picker(layout, on_change=_update_enabled)
        _update_enabled()  # herstelt een al gekozen bestemming (bv. na terug/opnieuw zoeken)

        layout.addWidget(preview_button, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(12)

        other_button = QPushButton(OTHER_DISK_TEXT if selection.via_auto_detection else CHOOSE_ANOTHER_BUTTON_TEXT)
        other_button.setObjectName("linkButton")
        other_button.clicked.connect(self._on_choose_source_clicked)
        layout.addWidget(other_button, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addStretch()
        self._set_content(content)

    def _render_destination_picker(self, layout: QVBoxLayout, *, on_change: Callable[[], None]) -> None:
        """Fase 3.5: bestemmingsschijf-keuze, ingebed in hetzelfde scherm als
        Klant/Project (niet een apart scherm/klik) — zodat "Bekijk inhoud" in
        één blik toont wat er nog ontbreekt. Klikken op een kaart update
        alleen `self._selected_destination` en een label in-place; het
        her-rendert nooit het hele scherm, anders zou dat de tekst die de
        gebruiker al in Klant/Project typte, wissen."""
        assert self._selection is not None
        self._destination_volumes = self._detect_destinations(self._selection.source_path)

        destination_label = QLabel(DESTINATION_LABEL_TEXT)
        destination_label.setObjectName("fieldLabel")
        layout.addWidget(destination_label)

        selected_label = QLabel("")
        selected_label.setObjectName("selectedDestinationLabel")
        selected_label.setWordWrap(True)
        selected_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        if not self._destination_volumes:
            empty_label = QLabel(NO_DESTINATIONS_TEXT)
            empty_label.setObjectName("captionLabel")
            empty_label.setWordWrap(True)
            layout.addWidget(empty_label)
            layout.addSpacing(8)

            retry_button = QPushButton(RETRY_DESTINATIONS_TEXT)
            retry_button.setObjectName("linkButton")
            retry_button.clicked.connect(lambda: self._render_selected())
            layout.addWidget(retry_button, alignment=Qt.AlignmentFlag.AlignCenter)
            layout.addSpacing(16)
            layout.addWidget(selected_label)
            layout.addSpacing(16)
            return

        # Blijft geldig als de eerder gekozen bestemming nog in de nieuwe
        # kandidatenlijst voorkomt (bijv. een her-render die niets aan de
        # bron veranderde) — anders (schijf verdwenen, of net gewisseld van
        # bron) is er bewust geen automatische vervangende keuze.
        if self._selected_destination is not None and self._selected_destination.path not in {
            d.path for d in self._destination_volumes
        }:
            self._selected_destination = None

        def _select(destination: DestinationInfo) -> None:
            self._selected_destination = destination
            selected_label.setText(
                f"{SELECTED_DESTINATION_PREFIX}{destination.name} · "
                f"{format_size(destination.free_bytes)} vrij"
            )
            on_change()

        for destination in self._destination_volumes:
            card = QPushButton(f"{destination.name}\n{format_size(destination.free_bytes)} vrij")
            card.setObjectName("destinationCard")
            card.clicked.connect(lambda checked=False, d=destination: _select(d))
            layout.addWidget(card)
            layout.addSpacing(8)

        if self._selected_destination is not None:
            selected_label.setText(
                f"{SELECTED_DESTINATION_PREFIX}{self._selected_destination.name} · "
                f"{format_size(self._selected_destination.free_bytes)} vrij"
            )
        layout.addWidget(selected_label)
        layout.addSpacing(16)

    def _render_analyzing(self) -> None:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.addStretch()

        label = QLabel(ANALYZING_TEXT)
        label.setObjectName("statusLabel")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
        layout.addSpacing(16)

        progress = QProgressBar()
        progress.setObjectName("analysisProgress")
        progress.setRange(0, 0)  # onbepaald tot het eerste bestand geteld is
        layout.addWidget(progress)
        layout.addSpacing(8)

        detail_label = QLabel("")
        detail_label.setObjectName("captionLabel")
        detail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(detail_label)
        layout.addSpacing(16)

        cancel_button = QPushButton(CANCEL_BUTTON_TEXT)
        cancel_button.setObjectName("linkButton")
        cancel_button.clicked.connect(self._on_cancel_clicked)
        layout.addWidget(cancel_button, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addStretch()
        self._set_content(content)

    def _render_ingesting(self) -> None:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.addStretch()

        label = QLabel(INGESTING_TEXT)
        label.setObjectName("statusLabel")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
        layout.addSpacing(8)

        percentage_label = QLabel("")
        percentage_label.setObjectName("percentageLabel")
        percentage_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(percentage_label)
        layout.addSpacing(8)

        progress = QProgressBar()
        progress.setObjectName("analysisProgress")
        progress.setRange(0, 0)  # onbepaald tot het eerste bestand geteld is
        layout.addWidget(progress)
        layout.addSpacing(8)

        detail_label = QLabel("")
        detail_label.setObjectName("captionLabel")
        detail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(detail_label)
        layout.addSpacing(4)

        speed_label = QLabel("")
        speed_label.setObjectName("speedLabel")
        speed_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(speed_label)
        layout.addSpacing(16)

        warning_label = QLabel(DO_NOT_DISCONNECT_TEXT)
        warning_label.setObjectName("warningLabel")
        warning_label.setWordWrap(True)
        warning_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(warning_label)
        layout.addSpacing(16)

        if self._cancel_confirmation_pending:
            self._add_cancel_confirmation(layout)
        else:
            cancel_button = QPushButton(CANCEL_BUTTON_TEXT)
            cancel_button.setObjectName("linkButton")
            cancel_button.clicked.connect(self._on_cancel_ingest_clicked)
            layout.addWidget(cancel_button, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addStretch()
        self._set_content(content)

        # Een re-render (bijv. door de annuleer-bevestiging te openen/sluiten)
        # mag de al zichtbare voortgang nooit laten terugspringen naar
        # "onbepaald" — herstel de laatst bekende stand meteen.
        if self._last_ingest_progress is not None:
            self._on_ingest_progress(self._last_ingest_progress)

    def _add_cancel_confirmation(self, layout: QVBoxLayout) -> None:
        """De inline annuleer-bevestiging (Design Language hoofdstuk 12) —
        geen apart scherm/dialoog, gewoon een ander onderste blok binnen
        dezelfde voortgangsweergave, zodat de voortgang zelf zichtbaar en
        actueel blijft terwijl de gebruiker beslist."""
        confirm_label = QLabel(CANCEL_INGEST_CONFIRM_MESSAGE)
        confirm_label.setObjectName("cancelConfirmationLabel")
        confirm_label.setWordWrap(True)
        confirm_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(confirm_label)
        layout.addSpacing(12)

        continue_button = QPushButton(CONTINUE_INGEST_TEXT)
        continue_button.setObjectName("primaryButton")
        continue_button.clicked.connect(self._on_continue_ingest_clicked)
        layout.addWidget(continue_button, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(8)

        confirm_cancel_button = QPushButton(CONFIRM_CANCEL_INGEST_TEXT)
        confirm_cancel_button.setObjectName("linkButton")
        confirm_cancel_button.clicked.connect(self._on_confirm_cancel_ingest_clicked)
        layout.addWidget(confirm_cancel_button, alignment=Qt.AlignmentFlag.AlignCenter)

    def _add_preview_section(
        self, layout: QVBoxLayout, header_text: str, value_lines: list[str], *, tone: str | None = None
    ) -> None:
        header = QLabel(header_text)
        header.setObjectName("fieldLabel")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)
        for value in value_lines:
            self._add_status_line(layout, value, tone=tone)
        layout.addSpacing(14)

    def _add_status_line(self, layout: QVBoxLayout, text: str, *, tone: str | None = None) -> None:
        """One `previewLine`-styled label, optionally tagged with a `tone`
        Qt property (`"success"`/`"warning"`) — theme.py styles these via a
        `QLabel#previewLine[tone="..."]` attribute selector, so the
        objectName stays exactly `previewLine` either way (test helpers like
        `preview_lines()` keep finding every line, toned or not, with no
        separate accessor needed for Fase 4's richer safe-to-delete/error
        styling)."""
        line_label = QLabel(text)
        line_label.setObjectName("previewLine")
        line_label.setWordWrap(True)
        line_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if tone is not None:
            line_label.setProperty("tone", tone)
        layout.addWidget(line_label)

    def _footage_subpath_labels(self) -> tuple[str, ...]:
        """The fixed breadcrumb levels between the chosen destination disk
        and the client — sourced from StorageLayout's `footage_subpath`
        (config.yaml, see config.py's `load_storage_layout`), never
        hardcoded here. Found via a real user report (2026-09-14): the
        breadcrumb used to show only `ManyFast`, skipping the real
        `Footage`/`Klanten` levels the engine actually creates
        (`resolve_ingest_config`/`_build_workspace_path`), so a manually
        browsing user couldn't find the project folder where the GUI implied
        it would be. Reading config.yaml here is presentation-only — it
        changes nothing about how the engine resolves the real destination;
        the worker process still does that independently. A read failure
        (missing/invalid config.yaml) falls back to the single legacy label
        rather than breaking the preview/report screen over it — this is a
        cosmetic breadcrumb, not the safety-critical path (that's the
        worker's own config validation, unchanged)."""
        try:
            layout = load_storage_layout(self._config_path)
        except (OSError, ValueError):
            return (DESTINATION_ORG_LABEL,)
        return layout.footage_subpath.parts

    def _destination_breadcrumb_lines(self, summary: IngestSummary) -> list[str]:
        """Bestemming in mensentaal (Fase 3.5, requirement 8): welke schijf,
        de werkelijke folderstructuur tot aan klant/project (StorageLayout's
        footage_subpath + CLIENT_FOLDER_NAME, zie _footage_subpath_labels()
        hierboven), en de vrije ruimte op die schijf — nooit een technisch
        bestandspad (Design Language hoofdstuk 15). Werkt voor zowel de
        preview als het eindrapport: beide roepen dit aan terwijl
        `self._confirmed_preview_input` nog de bestemming van déze run
        draagt (zie _on_ingest_completed's volgorde)."""
        lines: list[str] = []
        destination = (
            self._confirmed_preview_input.destination
            if self._confirmed_preview_input is not None
            else None
        )
        if destination is not None:
            lines.append(destination.name)
        lines += list(self._footage_subpath_labels())
        lines += [CLIENT_FOLDER_NAME, summary.client, summary.project]
        if destination is not None:
            lines.append(f"{format_size(destination.free_bytes)} vrij")
        return lines

    def _camera_breakdown_lines(self, summary: IngestSummary) -> list[str]:
        counts = summary.camera_profile_counts
        camera_only = {
            label: count
            for label, count in counts.items()
            if label not in (AUDIO_PROFILE_LABEL, UNKNOWN_PROFILE_LABEL) and count > 0
        }
        lines = [f"{label}: {count}" for label, count in sorted(camera_only.items(), key=lambda kv: -kv[1])]

        audio_count = counts.get(AUDIO_PROFILE_LABEL, 0)
        if audio_count:
            lines.append(f"{AUDIO_PROFILE_LABEL}: {audio_count}")

        unknown_count = counts.get(UNKNOWN_PROFILE_LABEL, 0)
        if unknown_count:
            lines.append(f"{UNKNOWN_PROFILE_LABEL}: {unknown_count}")

        return lines

    def _render_preview(self, summary: IngestSummary) -> None:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.addStretch()

        title = QLabel(PREVIEW_TITLE_TEXT)
        title.setObjectName("statusLabel")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        layout.addSpacing(20)

        source_name = self._selection.name if self._selection is not None else ""
        self._add_preview_section(layout, SECTION_SOURCE, [source_name])
        self._add_preview_section(
            layout, SECTION_DESTINATION, self._destination_breadcrumb_lines(summary)
        )
        self._add_preview_section(
            layout,
            SECTION_FILES,
            [_pluralize_files(summary.total_files), format_size(summary.total_bytes)],
        )

        camera_lines = self._camera_breakdown_lines(summary)
        if camera_lines:
            self._add_preview_section(layout, SECTION_CAMERAS, camera_lines)

        self._add_preview_section(layout, SECTION_DUPLICATES, [str(summary.duplicates)])
        self._add_preview_section(layout, SECTION_NAME_CONFLICTS, [str(summary.name_conflicts_resolved)])

        unknown_count = summary.camera_profile_counts.get(UNKNOWN_PROFILE_LABEL, 0)
        if unknown_count:
            self._add_preview_section(layout, SECTION_NOTES, [_unrecognized_files_note(unknown_count)])

        start_ingest_button = QPushButton(START_INGEST_BUTTON_TEXT)
        start_ingest_button.setObjectName("primaryButton")
        start_ingest_button.clicked.connect(self._on_start_ingest_clicked)
        layout.addWidget(start_ingest_button, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(12)

        back_button = QPushButton(BACK_TEXT)
        back_button.setObjectName("linkButton")
        back_button.clicked.connect(self._return_to_selection)
        layout.addWidget(back_button, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addStretch()
        self._set_content(content)

    def _error_detail_lines(self, summary: IngestSummary) -> list[str]:
        """The aggregate count first (unchanged from before Fase 4, so an
        existing test that only ever supplied a bare `errors` count without
        streaming per-asset events still sees exactly that), followed by
        bounded per-file detail (bestandsnaam + de exacte, al-vertaalde
        `AssetResult.error`-reden de engine zelf meegeeft — nooit een nieuw
        eigen foutformat) when it's actually available. `self._ingest_failed_assets`
        is empty whenever `on_completed` was handed a summary directly
        without ever streaming `asset_processed` first (e.g. some tests) —
        the count-only line is exactly the pre-Fase-4 behaviour for that
        case, not a regression."""
        lines = [str(summary.errors)]
        details = self._ingest_failed_assets[:_MAX_VISIBLE_ERROR_DETAILS]
        lines += [f"{name} — {reason}" for name, reason in details]
        remaining = len(self._ingest_failed_assets) - len(details)
        if remaining > 0:
            lines.append(f"+ {remaining} meer")
        return lines

    def _metadata_warning_lines(self, summary: IngestSummary) -> list[str]:
        return [str(summary.metadata_warnings), METADATA_WARNINGS_NOTE_TEXT]

    def _render_ingest_report(self, summary: IngestSummary) -> None:
        """The Fase 3/4 eindscherm — reuses the exact same `IngestSummary`
        and section-rendering helper as `_render_preview` (see
        `_add_preview_section`), for a REAL result instead of a dry-run
        preview.

        Fase 4 adds: duur, metadata-waarschuwingen (nooit van invloed op
        safe-to-delete — zie METADATA_WARNINGS_NOTE_TEXT), per-bestand
        foutdetail (begrensd, zie _error_detail_lines), een visueel
        onderscheiden safe-to-delete-blok (tone="success"/"warning", zie
        _add_status_line), "Open in Finder" (het door de engine resolved
        `summary.destination_path` — nooit hier gereconstrueerd, zie
        IngestSummary/IngestReport in core/), en de "Schijf veilig
        verwijderen"-knop (alleen aangeboden als _on_ingest_completed al
        vastgesteld heeft dat dit veilig mag, zie _resolve_eject_targets)."""
        self._last_ingest_summary = summary
        self._report_destination_path = _existing_directory_or_none(summary.destination_path)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.addStretch()

        title_text = INGEST_DONE_TITLE_TEXT if summary.errors == 0 else INGEST_PARTIAL_TITLE_TEXT
        title = QLabel(title_text)
        title.setObjectName("statusLabel")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        layout.addSpacing(20)

        self._add_preview_section(
            layout, SECTION_DESTINATION, self._destination_breadcrumb_lines(summary)
        )
        self._add_preview_section(
            layout,
            SECTION_FILES,
            [_pluralize_files(summary.total_files), format_size(summary.total_bytes)],
        )
        self._add_preview_section(layout, SECTION_DURATION, [_format_duration_label(summary.duration_seconds)])

        camera_lines = self._camera_breakdown_lines(summary)
        if camera_lines:
            self._add_preview_section(layout, SECTION_CAMERAS, camera_lines)

        self._add_preview_section(layout, SECTION_DUPLICATES, [str(summary.duplicates)])
        self._add_preview_section(layout, SECTION_NAME_CONFLICTS, [str(summary.name_conflicts_resolved)])

        if summary.metadata_warnings:
            self._add_preview_section(
                layout, SECTION_METADATA_WARNINGS, self._metadata_warning_lines(summary)
            )

        if summary.errors:
            self._add_preview_section(
                layout, SECTION_ERRORS, self._error_detail_lines(summary), tone="warning"
            )

        if summary.safe_to_delete_source:
            self._add_preview_section(
                layout,
                SECTION_SOURCE_STATUS,
                [VERIFIED_SAFE_TEXT, SAFE_TO_DELETE_YES_TEXT],
                tone="success",
            )
        else:
            self._add_preview_section(
                layout, SECTION_SOURCE_STATUS, [SAFE_TO_DELETE_NO_TEXT], tone="warning"
            )

        self._add_eject_controls(layout)

        if self._report_destination_path is not None:
            open_finder_button = QPushButton(OPEN_IN_FINDER_BUTTON_TEXT)
            open_finder_button.setObjectName("openInFinderButton")
            open_finder_button.clicked.connect(self._on_open_in_finder_clicked)
            layout.addWidget(open_finder_button, alignment=Qt.AlignmentFlag.AlignCenter)
            layout.addSpacing(8)

        new_ingest_button = QPushButton(NEW_INGEST_TEXT)
        new_ingest_button.setObjectName("linkButton")
        new_ingest_button.clicked.connect(self._return_to_selection)
        layout.addWidget(new_ingest_button, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addStretch()
        self._set_content(content)

    def _add_eject_controls(self, layout: QVBoxLayout) -> None:
        """Fase 4 — Safe Eject. `self._eject_source_path` is `None` unless
        `_on_ingest_completed` already established every condition holds
        (safe_to_delete_source, a genuine mounted volume-root, not the
        destination device, not the boot volume — see
        `_resolve_eject_targets`/`eject.can_eject`); this method only ever
        reflects `self._eject_status`, it never re-derives eligibility."""
        if self._eject_status == "ejected":
            self._add_status_line(layout, EJECT_SUCCESS_TEXT, tone="success")
            layout.addSpacing(12)
            return

        if self._eject_source_path is None:
            return

        eject_button = QPushButton(
            EJECT_IN_PROGRESS_TEXT if self._eject_status == "ejecting" else EJECT_BUTTON_TEXT
        )
        eject_button.setObjectName("ejectButton")
        eject_button.setEnabled(self._eject_status != "ejecting")
        eject_button.clicked.connect(self._on_eject_clicked)
        layout.addWidget(eject_button, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(8)

        if self._eject_status == "failed" and self._eject_failure_message:
            self._add_status_line(layout, self._eject_failure_message, tone="warning")
            layout.addSpacing(8)

    def _on_open_in_finder_clicked(self) -> None:
        if self._report_destination_path is None:
            return  # niet bereikbaar via de UI (knop staat dan uit), extra zekerheid
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._report_destination_path)))

    def _on_eject_clicked(self) -> None:
        if self._eject_source_path is None or self._eject_status in ("ejecting", "ejected"):
            return  # niet bereikbaar via de UI in deze staat, extra zekerheid
        self._eject_status = "ejecting"
        generation = self._report_generation
        self._render_ingest_report(self._last_ingest_summary)
        self._eject_runner = self._start_eject(
            self._eject_source_path,
            self._eject_destination_path,
            on_succeeded=lambda: self._on_eject_succeeded(generation),
            on_failed=lambda message: self._on_eject_failed(generation, message),
        )

    def _on_eject_succeeded(self, generation: int) -> None:
        self._eject_runner = None
        if generation != self._report_generation:
            return  # gebruiker is al weg van dit eindscherm, zie _on_ingest_completed
        self._eject_status = "ejected"
        self._render_ingest_report(self._last_ingest_summary)

    def _on_eject_failed(self, generation: int, message: str) -> None:
        self._eject_runner = None
        if generation != self._report_generation:
            return
        self._eject_status = "failed"
        self._eject_failure_message = message
        self._render_ingest_report(self._last_ingest_summary)

    def _render_analysis_failed(self, message: str) -> None:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.addStretch()

        label = QLabel(message)
        label.setObjectName("statusLabel")
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
        layout.addSpacing(16)

        back_button = QPushButton(BACK_TEXT)
        back_button.setObjectName("linkButton")
        back_button.clicked.connect(self._return_to_selection)
        layout.addWidget(back_button, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addStretch()
        self._set_content(content)


def _existing_directory_or_none(destination_path: str) -> Path | None:
    """`summary.destination_path` (Fase 4, engine-resolved — see
    core/report.py/core/ingest_service.py) is only "geldig/beschikbaar" for
    "Open in Finder" if a directory actually exists there — e.g. a
    zero-file real ingest never creates the Project Workspace folder at all
    (nothing ever calls `Storage.copy()`). Never reconstructs the path
    itself, only checks the one the engine already resolved."""
    if not destination_path:
        return None
    path = Path(destination_path)
    return path if path.is_dir() else None


def _format_duration_label(seconds: float) -> str:
    """Same h/m/s convention as core/report.py's own `_format_duration` —
    kept as a small, deliberate local copy for the GUI layer, same
    established reason desktop/volumes.py's `format_size` already is one
    (see that function's docstring): not a new duration format, the exact
    same one, just needed directly in the GUI since it never calls
    `render_report()` itself."""
    total_seconds = int(seconds)
    minutes, secs = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _pluralize_media(count: int) -> str:
    return f"{count} mediabestand" if count == 1 else f"{count} mediabestanden"


def _pluralize_files(count: int) -> str:
    return f"{count} bestand" if count == 1 else f"{count} bestanden"


def _unrecognized_files_note(count: int) -> str:
    subject = "bestand kon" if count == 1 else "bestanden konden"
    return f"{count} {subject} niet automatisch worden herkend. Ze worden wel meegenomen."


def _format_speed(bytes_per_second: float) -> str:
    """MB/s onder 1 GB/s, GB/s vanaf 1 GB/s — zelfde 1024-gebaseerde
    eenheden en decimaalnotatie als desktop/volumes.py's format_size(), voor
    een consistente stijl door de hele app."""
    mb_per_second = bytes_per_second / (1024 * 1024)
    if mb_per_second < 1024:
        return f"{mb_per_second:.1f} MB/s"
    return f"{mb_per_second / 1024:.1f} GB/s"


def _format_eta(seconds: float) -> str:
    """Geen secondeprecisie, zelfs niet bij lange runs — "± 4 min resterend"
    of "± 1u 12m resterend". Nooit 0 of negatief: een run met nog resterende
    bestanden toont minimaal "± 1 min resterend"."""
    total_minutes = max(round(seconds / 60), 1)
    hours, minutes = divmod(total_minutes, 60)
    if hours == 0:
        return f"± {minutes} min resterend"
    return f"± {hours}u {minutes}m resterend"
