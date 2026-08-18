"""Fase 2: dry-run preview, layered on the Fase 0/1 shell.

Architecture (see the Fase 2 build plan and CLAUDE.md — no business logic in
the GUI, no second ingest implementation):

    GUI (this file) -> Controller (desktop/controller.py) -> IngestService
    -> Storage -> Manifest -> Report

This window never calls IngestService directly and never re-derives anything
it computes (classification, duplicates, ...) — it only renders the
IngestSummary the Controller hands back, and reacts to clicks. Volume
detection stays in desktop/volumes.py, unchanged from Fase 1.

Six states, one window (content is swapped, no new dialogs/screens):
- empty / chooser / selected(-with-form)   [Fase 1, now selected also has
                                             client/project fields + Start]
- analyzing   [new: progress while the background dry-run runs]
- preview     [new: the dry-run result, in plain language]
- failed      [new: a friendly message, never a stack trace]

`detect_volumes` and `start_dry_run` are both injected (defaulting to the
real implementations) so tests can drive every state deterministically,
without a real /Volumes, a real config, or a real background thread.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QThread, QTimer
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

from many_ingest.core.ingest_service import ProgressUpdate
from many_ingest.core.report import IngestSummary
from many_ingest.desktop import controller, ingest_process, thread_lifecycle
from many_ingest.desktop.volumes import (
    VolumeInfo,
    format_size,
    list_candidate_volumes,
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

# Vaste naam van de organisatie in de bestemmings-breadcrumb (Bestemming →
# ManyFast → klant → project) — geen configwaarde, geen storage_root: dit is
# altijd hetzelfde omdat dit ManyFast's eigen tool is (zie CLAUDE.md).
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
StartDryRun = Callable[..., tuple[QThread | None, object | None]]
StartRealIngest = Callable[..., object]


@dataclasses.dataclass(frozen=True)
class _SelectionInfo:
    source_path: Path
    name: str
    media_file_count: int
    media_total_bytes: int
    via_auto_detection: bool


class MainWindow(QWidget):
    def __init__(
        self,
        detect_volumes: DetectVolumes | None = None,
        start_dry_run: StartDryRun | None = None,
        start_real_ingest: StartRealIngest | None = None,
        config_path: Path = controller.DEFAULT_CONFIG_PATH,
        camera_profiles_path: Path = controller.DEFAULT_CAMERA_PROFILES_PATH,
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
        self._start_dry_run: StartDryRun = start_dry_run or controller.start_dry_run
        self._start_real_ingest: StartRealIngest = (
            start_real_ingest or ingest_process.start_real_ingest
        )
        self._config_path = config_path
        self._camera_profiles_path = camera_profiles_path

        self.selected_source: Path | None = None
        self._selection: _SelectionInfo | None = None
        self._analysis_thread: QThread | None = None
        self._analysis_worker: object | None = None

        # Vastgelegd zodra een analyse *start* (zie _start_preview), pas
        # bevestigd zodra hij ook echt succesvol afrondt (zie
        # _on_analysis_finished) — "Start Ingest" mag alleen de exacte
        # (bron, klant, project) gebruiken van de laatst getoonde, geslaagde
        # preview, nooit een combinatie die ondertussen gewijzigd is zonder
        # nieuwe preview. Ongeldig gemaakt zodra de gebruiker teruggaat naar
        # het formulier (_return_to_selection) of een nieuwe analyse start.
        self._pending_preview_input: tuple[Path, str, str] | None = None
        self._confirmed_preview_input: tuple[Path, str, str] | None = None
        self._ingest_runner: object | None = None

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
        """Waits for every active QThread (not just this window's own
        `_analysis_thread`) before letting the window close. See
        `_wait_for_analysis_to_stop` for why this alone is NOT sufficient —
        `app.aboutToQuit` (wired in app.py) is the other required half.

        Also stops a real ingest (a QProcess, Fase 3 — see
        `_wait_for_ingest_to_stop`) if one is still active. These are two
        independent guarantees for two different kinds of background work
        (QThread vs. QProcess), deliberately not merged into one — see
        ingest_process.py's module docstring for why a real ingest is a
        separate OS process rather than another QThread.
        """
        self._wait_for_analysis_to_stop()
        self._wait_for_ingest_to_stop()
        super().closeEvent(event)

    def _wait_for_analysis_to_stop(self) -> None:
        """Blocks until every QThread the desktop app has created has
        genuinely stopped — delegates to `thread_lifecycle.shutdown()` (see
        that module's docstring), the single central choke point for this
        guarantee, rather than checking only `self._analysis_thread` here.

        Fixes a reproducible crash: dropping the last Python reference to a
        *running* QThread makes Qt's own destructor treat that as fatal
        ("QThread: Destroyed while thread is still running") and abort the
        whole process — confirmed against real macOS crash reports. The
        original version of this method only ever checked
        `self._analysis_thread`, which is correct for the one thread
        MainWindow itself tracks, but is a guarantee that has to be
        re-derived by hand for every future background QThread this app
        might grow — `thread_lifecycle.shutdown()` covers all of them by
        construction, this window's analysis thread included, without
        MainWindow needing to enumerate them.

        `closeEvent()` alone does not cover this: `QApplication.quit()`
        called directly — which is what macOS actually does for Cmd+Q / the
        app-menu Quit item, confirmed by reproduction — never sends this
        widget a `QCloseEvent` at all, so `closeEvent()` never runs, and
        Python's interpreter shutdown then destroys any still-running thread
        while it's still running. This method is therefore called from *two*
        places: here (closeEvent) and from `QApplication.aboutToQuit` (see
        app.py) — whichever fires first does the real work;
        `thread_lifecycle.shutdown()` is itself idempotent, so calling it
        twice for the same run is harmless.

        `thread.quit()` cannot interrupt a synchronous, non-Qt-event-loop
        call like `IngestService.run()` mid-flight — the thread's event loop
        can't process the quit request until `run()` returns control to it.
        The actual safety guarantee is `thread.wait()` blocking until the
        thread has *actually* finished, however long that takes — not
        `quit()`, which is a request, not an interruption.
        """
        thread_lifecycle.shutdown()

    def _wait_for_ingest_to_stop(self) -> None:
        """The Fase 3 equivalent of `_wait_for_analysis_to_stop`, for a real
        ingest's `IngestRunner`/`QProcess` (see desktop/ingest_process.py)
        rather than the preview's `QThread`. A `QProcess` object has none of
        `QThread`'s fatal-abort-on-destroy failure mode — dropping the last
        Python reference to it while the OS process is still running does
        not crash this process — but leaving it running unattended after the
        window/app is gone would silently keep copying files in the
        background with no UI and no way to see progress or a final report,
        which is its own kind of unsafe surprise for the editor. This calls
        `IngestRunner.stop_and_wait()` — a synchronous terminate-then-kill
        (never removes source files, v0.1 is copy-only) — so the app never
        actually quits while a real ingest could still be silently running.
        """
        if self._ingest_runner is not None:
            self._ingest_runner.stop_and_wait()

    def _on_analysis_thread_finished(self) -> None:
        """The only place references are cleared — connected to
        `thread.finished`, which Qt emits only once the thread has genuinely
        stopped (not merely once `quit()` was requested). Never cleared
        eagerly from `on_finished`/`on_failed`, which fire slightly earlier
        in the same event-processing pass (see `_start_preview`).

        Guarded with `self.sender()`: this signal is queued, so it can still
        be delivered *after* a second analysis has already legitimately
        started (its own `isRunning()` guard in `_start_preview` already
        passed because the first thread had genuinely stopped). Without this
        check, a late-arriving `finished` from the *first* thread would wipe
        out the reference to the *second*, already-running one — found via a
        real crash while testing several sequential analyses in a row.
        """
        if self.sender() is self._analysis_thread:
            self._analysis_thread = None
            self._analysis_worker = None

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
        self._render_selected()

    # -- dry-run analysis (via the Controller, never IngestService directly) --

    def _start_preview(self, client: str, project: str) -> None:
        if self._selection is None:
            return
        if self._analysis_thread is not None and self._analysis_thread.isRunning():
            return  # een analyse loopt al; niet bereikbaar via de UI, extra zekerheid
        # Een nieuwe analyse maakt elke eerder bevestigde preview-input
        # ongeldig totdat déze analyse ook echt slaagt (zie
        # _on_analysis_finished) — Start Ingest mag nooit een combinatie
        # gebruiken die niet exact overeenkomt met de laatst getoonde preview.
        self._confirmed_preview_input = None
        self._pending_preview_input = (self._selection.source_path, client, project)
        self._render_analyzing()
        self._analysis_thread, self._analysis_worker = self._start_dry_run(
            self._selection.source_path,
            client,
            project,
            on_progress=self._on_analysis_progress,
            on_finished=self._on_analysis_finished,
            on_failed=self._on_analysis_failed,
            on_cancelled=self._on_analysis_cancelled,
            config_path=self._config_path,
            camera_profiles_path=self._camera_profiles_path,
        )
        if self._analysis_thread is not None:
            # Registreren bij het startpunt van MainWindow's eigen tracking
            # — niet alleen in controller.start_dry_run() — zodat elke
            # thread die MainWindow ooit als `_analysis_thread` bijhoudt
            # gegarandeerd in de centrale registry zit, ook wanneer een
            # geïnjecteerde (test-)implementatie van `start_dry_run` zijn
            # eigen QThread aanmaakt zonder ooit door controller.py te gaan.
            # Zie thread_lifecycle.py's moduledocstring voor de regressie
            # die dit dichttimmert. `register()` is idempotent, dus dit is
            # onschadelijk voor het gewone pad waar controller.start_dry_run()
            # dezelfde thread al registreerde.
            thread_lifecycle.register(self._analysis_thread)
            self._analysis_thread.finished.connect(self._on_analysis_thread_finished)

    def _on_cancel_clicked(self) -> None:
        """A single, unconfirmed click — unlike a real ingest, cancelling a
        dry-run has zero risk (nothing is ever written), so the "only
        confirm what's truly consequential" rule (Design Language hoofdstuk
        12) means this deliberately does NOT ask "are you sure?"."""
        if self._analysis_worker is not None:
            self._analysis_worker.request_cancel()

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
        # Pas hier bevestigen (niet al bij het starten) — dit is het enige
        # moment waarop we weten dat déze exacte (bron, klant, project) ook
        # daadwerkelijk de preview is die de gebruiker nu ziet.
        self._confirmed_preview_input = self._pending_preview_input
        self._render_preview(summary)

    def _on_analysis_failed(self, message: str) -> None:
        self._render_analysis_failed(message)

    def _on_analysis_cancelled(self) -> None:
        self._return_to_selection()

    def _return_to_selection(self) -> None:
        # Elke terugkeer naar het formulier maakt een bevestigde preview
        # ongeldig — een nieuwe "Start Ingest" vereist altijd eerst weer een
        # verse, geslaagde preview (zie hoofdstuk 14 van de Fase 3-opdracht).
        self._confirmed_preview_input = None
        if self._selection is not None:
            self._render_selected()

    # -- echte ingest (Fase 3, via IngestRunner/QProcess — nooit IngestService
    # rechtstreeks, zie ingest_process.py) --------------------------------------

    def _on_start_ingest_clicked(self) -> None:
        if self._confirmed_preview_input is None:
            return  # niet bereikbaar via de UI (knop staat dan uit), extra zekerheid
        if self._ingest_runner is not None and self._ingest_runner.is_running():
            return  # een ingest loopt al; niet bereikbaar via de UI, extra zekerheid
        if self._analysis_thread is not None:
            # De preview die dit scherm liet zien is al klaar (on_finished is
            # al geweest, anders zou deze knop niet bestaan) — maar de
            # onderliggende QThread kan een paar milliseconden later pas
            # aantoonbaar volledig gestopt zijn (zie
            # _on_analysis_thread_finished, gekoppeld aan thread.finished,
            # niet aan worker.finished). Een echte klik is hier in de
            # praktijk altijd ruim op tijd, maar een zeer snel opeenvolgende
            # klik + venster sluiten kan deze paar milliseconden wél raken —
            # gereproduceerd als een QThread-teardownrace (SIGSEGV in de
            # QThread zelf, "faultingThread: QThread") bij het meteen
            # starten van een echte ingest (nieuwe QProcess-activiteit) vlak
            # ná een preview. Nooit een tweede zware achtergrondtaak starten
            # terwijl de vorige QThread nog niet aantoonbaar weg is (zie
            # thread_lifecycle.py) — een korte retry i.p.v. de klik stil
            # laten verdwijnen.
            QTimer.singleShot(10, self._on_start_ingest_clicked)
            return

        source, client, project = self._confirmed_preview_input
        self._render_ingesting()
        self._ingest_runner = self._start_real_ingest(
            source,
            client,
            project,
            on_progress=self._on_ingest_progress,
            on_completed=self._on_ingest_completed,
            on_failed=self._on_ingest_failed,
            on_cancelled=self._on_ingest_cancelled,
            config_path=self._config_path,
            camera_profiles_path=self._camera_profiles_path,
        )

    def _on_cancel_ingest_clicked(self) -> None:
        """Zoals bij annuleren van een preview: een enkele klik. In
        tegenstelling tot de preview annuleert dit een actie die al wél
        bestanden heeft weggeschreven — de precieze bevestigingsvraag
        daarvoor (Design Language hoofdstuk 12, UX-blauwdruk hoofdstuk 6) is
        bewust niet in deze fase gebouwd (niet in de Fase 3-opdracht), maar
        blijft een expliciete, latere toevoeging, geen vergeten stap."""
        if self._ingest_runner is not None:
            self._ingest_runner.cancel()

    def _on_ingest_progress(self, update: ProgressUpdate) -> None:
        bar = self.progress_bar()
        if bar is not None:
            if bar.maximum() == 0 and update.total:
                bar.setRange(0, update.total)
            bar.setValue(update.processed)

        detail = self._content.findChild(QLabel, "captionLabel") if self._content else None
        if detail is not None:
            detail.setText(
                f"{update.processed} van {update.total} bestanden · "
                f"{format_size(update.bytes_processed)} verwerkt"
            )

    def _on_ingest_completed(self, summary: IngestSummary) -> None:
        self._ingest_runner = None
        # Een voltooide ingest maakt de preview die hem startte ongeldig —
        # nogmaals op "Start Ingest" klikken zonder nieuwe preview mag nooit
        # dezelfde run herhalen.
        self._confirmed_preview_input = None
        self._render_ingest_report(summary)

    def _on_ingest_failed(self, message: str) -> None:
        self._ingest_runner = None
        self._render_analysis_failed(message)

    def _on_ingest_cancelled(self) -> None:
        self._ingest_runner = None
        self._confirmed_preview_input = None
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
                bool(client_input.text().strip()) and bool(project_input.text().strip())
            )

        client_input.textChanged.connect(_update_enabled)
        project_input.textChanged.connect(_update_enabled)
        preview_button.clicked.connect(
            lambda: self._start_preview(client_input.text().strip(), project_input.text().strip())
        )
        layout.addWidget(preview_button, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(12)

        other_button = QPushButton(OTHER_DISK_TEXT if selection.via_auto_detection else CHOOSE_ANOTHER_BUTTON_TEXT)
        other_button.setObjectName("linkButton")
        other_button.clicked.connect(self._on_choose_source_clicked)
        layout.addWidget(other_button, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addStretch()
        self._set_content(content)

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
        cancel_button.clicked.connect(self._on_cancel_ingest_clicked)
        layout.addWidget(cancel_button, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addStretch()
        self._set_content(content)

    def _add_preview_section(self, layout: QVBoxLayout, header_text: str, value_lines: list[str]) -> None:
        header = QLabel(header_text)
        header.setObjectName("fieldLabel")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)
        for value in value_lines:
            line_label = QLabel(value)
            line_label.setObjectName("previewLine")
            line_label.setWordWrap(True)
            line_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(line_label)
        layout.addSpacing(14)

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
            layout,
            SECTION_DESTINATION,
            [DESTINATION_ORG_LABEL, summary.client, summary.project],
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

    def _render_ingest_report(self, summary: IngestSummary) -> None:
        """The Fase 3 eindscherm — reuses the exact same `IngestSummary` and
        section-rendering helper as `_render_preview` (see
        `_add_preview_section`), for a REAL result instead of a dry-run
        preview. Never a functional "verwijder de bron"-knop: only the text
        status the engine itself already computes
        (`summary.safe_to_delete_source`) — an actual delete/eject action is
        out of scope for this phase (see CLAUDE.md: v0.1 is copy-only)."""
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
            layout,
            SECTION_DESTINATION,
            [DESTINATION_ORG_LABEL, summary.client, summary.project],
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

        if summary.errors:
            self._add_preview_section(layout, SECTION_ERRORS, [str(summary.errors)])

        safe_text = SAFE_TO_DELETE_YES_TEXT if summary.safe_to_delete_source else SAFE_TO_DELETE_NO_TEXT
        self._add_preview_section(layout, SECTION_SOURCE_STATUS, [safe_text])

        new_ingest_button = QPushButton(NEW_INGEST_TEXT)
        new_ingest_button.setObjectName("linkButton")
        new_ingest_button.clicked.connect(self._return_to_selection)
        layout.addWidget(new_ingest_button, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addStretch()
        self._set_content(content)

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


def _pluralize_media(count: int) -> str:
    return f"{count} mediabestand" if count == 1 else f"{count} mediabestanden"


def _pluralize_files(count: int) -> str:
    return f"{count} bestand" if count == 1 else f"{count} bestanden"


def _unrecognized_files_note(count: int) -> str:
    subject = "bestand kon" if count == 1 else "bestanden konden"
    return f"{count} {subject} niet automatisch worden herkend. Ze worden wel meegenomen."
