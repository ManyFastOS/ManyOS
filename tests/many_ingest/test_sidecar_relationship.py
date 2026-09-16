"""Tests for sidecar_relationship.py — Fase 5.2, evidence-driven association
between a Sony NonRealTimeMeta XML sidecar and its main-media file.

All naming/directory-collision examples here mirror real findings from the
Fase 5.2 audit on /Volumes/Sharpwaves (not run against that disk itself —
purely synthetic paths/fixtures, per the phase's test-strategy instructions).
"""

from __future__ import annotations

from pathlib import Path

from many_ingest.sidecar_relationship import (
    RelationshipEvidence,
    SidecarMetadata,
    find_sidecar_candidates,
    parse_sony_sidecar,
    resolve_relationship,
)

_REAL_UMID = "060A2B340101010501010D43130000006AED1A61791206D210322CFFFE7DA477"


# -- candidate discovery -----------------------------------------------------


def test_same_directory_correct_m01_naming_is_a_candidate():
    source = Path("/scan")
    xml = Path("/scan/PRIVATE/M4ROOT/CLIP/611_4921M01.XML")
    mxf = Path("/scan/PRIVATE/M4ROOT/CLIP/611_4921.MXF")
    assert find_sidecar_candidates([xml, mxf], source) == {xml: mxf}


def test_same_stem_in_a_different_directory_is_never_paired():
    """The audit's decisive collision proof: a real disk had two completely
    different files both named C0100.MP4 in two different directories.
    Candidate discovery must never cross a directory boundary."""
    source = Path("/scan")
    xml = Path("/scan/CardA/C0100M01.XML")
    unrelated_media_elsewhere = Path("/scan/CardB/C0100.MP4")
    assert find_sidecar_candidates([xml, unrelated_media_elsewhere], source) == {}


def test_unrelated_xml_is_not_a_candidate():
    source = Path("/scan")
    xml = Path("/scan/notes.XML")
    mxf = Path("/scan/611_4921.MXF")
    assert find_sidecar_candidates([xml, mxf], source) == {}


def test_xml_without_any_sibling_media_is_not_a_candidate():
    source = Path("/scan")
    xml = Path("/scan/611_4921M01.XML")
    assert find_sidecar_candidates([xml], source) == {}


def test_matches_an_mxf_candidate():
    source = Path("/scan")
    xml = Path("/scan/611_4921M01.XML")
    mxf = Path("/scan/611_4921.MXF")
    assert find_sidecar_candidates([xml, mxf], source) == {xml: mxf}


def test_matches_an_mp4_candidate():
    source = Path("/scan")
    xml = Path("/scan/C9932M01.XML")
    mp4 = Path("/scan/C9932.MP4")
    assert find_sidecar_candidates([xml, mp4], source) == {xml: mp4}


def test_naming_match_is_case_insensitive():
    source = Path("/scan")
    xml = Path("/scan/611_4921m01.xml")
    mxf = Path("/scan/611_4921.mxf")
    assert find_sidecar_candidates([xml, mxf], source) == {xml: mxf}


def test_multiple_independent_pairs_in_different_directories_all_resolve():
    source = Path("/scan")
    xml_a = Path("/scan/CardA/611_4921M01.XML")
    mxf_a = Path("/scan/CardA/611_4921.MXF")
    xml_b = Path("/scan/CardB/C9932M01.XML")
    mp4_b = Path("/scan/CardB/C9932.MP4")
    candidates = find_sidecar_candidates([xml_a, mxf_a, xml_b, mp4_b], source)
    assert candidates == {xml_a: mxf_a, xml_b: mp4_b}


# -- UMID resolution ----------------------------------------------------------


def test_exact_umid_match_is_umid_match(make_probe_result):
    sidecar = SidecarMetadata(umid=_REAL_UMID, duration_seconds=34.84, frame_rate=25)
    probe = make_probe_result(material_package_umid=f"0x{_REAL_UMID}")
    assert resolve_relationship(sidecar, probe) == RelationshipEvidence.UMID_MATCH


def test_umid_comparison_ignores_the_0x_prefix_and_case(make_probe_result):
    sidecar = SidecarMetadata(umid=_REAL_UMID.lower(), duration_seconds=34.84, frame_rate=25)
    probe = make_probe_result(material_package_umid=f"0X{_REAL_UMID}")
    assert resolve_relationship(sidecar, probe) == RelationshipEvidence.UMID_MATCH


def test_umid_mismatch_is_none(make_probe_result):
    sidecar = SidecarMetadata(umid="AAAA", duration_seconds=34.84, frame_rate=25)
    probe = make_probe_result(material_package_umid="0xBBBB", duration_seconds=34.84)
    assert resolve_relationship(sidecar, probe) == RelationshipEvidence.NONE


def test_umid_mismatch_is_never_rescued_by_a_matching_duration(make_probe_result):
    """ZEER BELANGRIJK: een expliciete UMID-mismatch wint altijd, ook als de
    duur perfect overeenkomt."""
    sidecar = SidecarMetadata(umid="AAAA", duration_seconds=34.84, frame_rate=25)
    probe = make_probe_result(material_package_umid="0xBBBB", duration_seconds=34.84)
    assert resolve_relationship(sidecar, probe) == RelationshipEvidence.NONE


def test_umid_missing_on_the_media_side_falls_back_to_duration(make_probe_result):
    sidecar = SidecarMetadata(umid=_REAL_UMID, duration_seconds=34.84, frame_rate=25)
    probe = make_probe_result(material_package_umid=None, duration_seconds=34.84)
    assert resolve_relationship(sidecar, probe) == RelationshipEvidence.NAME_AND_DURATION_MATCH


def test_umid_missing_on_the_xml_side_falls_back_to_duration(make_probe_result):
    sidecar = SidecarMetadata(umid=None, duration_seconds=34.84, frame_rate=25)
    probe = make_probe_result(material_package_umid=f"0x{_REAL_UMID}", duration_seconds=34.84)
    assert resolve_relationship(sidecar, probe) == RelationshipEvidence.NAME_AND_DURATION_MATCH


# -- duration fallback --------------------------------------------------------


def test_exact_duration_match():
    sidecar = SidecarMetadata(umid=None, duration_seconds=34.84, frame_rate=25)
    probe = _probe_with_duration(34.84)
    assert resolve_relationship(sidecar, probe) == RelationshipEvidence.NAME_AND_DURATION_MATCH


def test_duration_within_one_frame_still_matches():
    sidecar = SidecarMetadata(umid=None, duration_seconds=34.84, frame_rate=25)  # tolerantie 0.04s
    probe = _probe_with_duration(34.84 + 0.03)
    assert resolve_relationship(sidecar, probe) == RelationshipEvidence.NAME_AND_DURATION_MATCH


def test_duration_more_than_one_frame_apart_is_none():
    sidecar = SidecarMetadata(umid=None, duration_seconds=34.84, frame_rate=25)
    probe = _probe_with_duration(34.84 + 1.0)
    assert resolve_relationship(sidecar, probe) == RelationshipEvidence.NONE


def test_missing_xml_duration_is_none():
    sidecar = SidecarMetadata(umid=None, duration_seconds=None, frame_rate=25)
    probe = _probe_with_duration(34.84)
    assert resolve_relationship(sidecar, probe) == RelationshipEvidence.NONE


def test_missing_media_duration_is_none():
    sidecar = SidecarMetadata(umid=None, duration_seconds=34.84, frame_rate=25)
    probe = _probe_with_duration(None)
    assert resolve_relationship(sidecar, probe) == RelationshipEvidence.NONE


def test_missing_probe_entirely_is_none():
    sidecar = SidecarMetadata(umid=None, duration_seconds=34.84, frame_rate=25)
    assert resolve_relationship(sidecar, None) == RelationshipEvidence.NONE


def test_missing_sidecar_metadata_entirely_is_none():
    probe = _probe_with_duration(34.84)
    assert resolve_relationship(None, probe) == RelationshipEvidence.NONE


def _probe_with_duration(duration_seconds: float | None):
    from many_ingest.metadata_extractor import ProbeResult

    return ProbeResult(
        has_video_stream=True,
        has_audio_stream=False,
        codec=None,
        width=None,
        height=None,
        frame_rate=None,
        duration_seconds=duration_seconds,
        make=None,
        model=None,
        creation_time=None,
        major_brand=None,
        compatible_brands=None,
        container_format=None,
        material_package_umid=None,
    )


# -- XML parsing ---------------------------------------------------------------


def test_parses_a_valid_nonrealtimemeta_document(tmp_path, make_sony_sidecar_xml):
    path = tmp_path / "611_4921M01.XML"
    path.write_text(make_sony_sidecar_xml(umid=_REAL_UMID, duration_frames=871, fps=25))

    metadata = parse_sony_sidecar(path)

    assert metadata.umid == _REAL_UMID
    assert metadata.duration_seconds == 871 / 25
    assert metadata.frame_rate == 25


def test_malformed_xml_returns_none_and_never_raises(tmp_path):
    path = tmp_path / "broken.XML"
    path.write_text("<NonRealTimeMeta><Unclosed>")

    assert parse_sony_sidecar(path) is None


def test_missing_target_material_leaves_umid_none(tmp_path, make_sony_sidecar_xml):
    path = tmp_path / "no_umid.XML"
    path.write_text(make_sony_sidecar_xml(umid=None, duration_frames=100, fps=25))

    metadata = parse_sony_sidecar(path)

    assert metadata.umid is None
    assert metadata.duration_seconds == 4.0


def test_missing_duration_leaves_duration_seconds_none(tmp_path, make_sony_sidecar_xml):
    path = tmp_path / "no_duration.XML"
    path.write_text(make_sony_sidecar_xml(umid=_REAL_UMID, duration_frames=None, fps=25))

    metadata = parse_sony_sidecar(path)

    assert metadata.umid == _REAL_UMID
    assert metadata.duration_seconds is None


def test_missing_fps_leaves_duration_seconds_none(tmp_path, make_sony_sidecar_xml):
    """Zonder tcFps kan Duration (in frames) niet naar seconden omgerekend
    worden — geen gok, gewoon None."""
    path = tmp_path / "no_fps.XML"
    path.write_text(make_sony_sidecar_xml(umid=_REAL_UMID, duration_frames=100, fps=None))

    metadata = parse_sony_sidecar(path)

    assert metadata.duration_seconds is None


def test_invalid_fps_is_treated_as_missing(tmp_path, make_sony_sidecar_xml):
    path = tmp_path / "bad_fps.XML"
    path.write_text(make_sony_sidecar_xml(umid=_REAL_UMID, duration_frames=100, fps="not-a-number"))

    metadata = parse_sony_sidecar(path)

    assert metadata.duration_seconds is None


def test_zero_fps_is_treated_as_invalid_not_a_division_by_zero(tmp_path, make_sony_sidecar_xml):
    path = tmp_path / "zero_fps.XML"
    path.write_text(make_sony_sidecar_xml(umid=_REAL_UMID, duration_frames=100, fps=0))

    metadata = parse_sony_sidecar(path)

    assert metadata.duration_seconds is None


def test_tolerant_of_a_different_schema_version_namespace(tmp_path, make_sony_sidecar_xml):
    """Real footage in the audit showed both ver.2.10 and ver.2.20 — parsing
    must not hardcode one exact namespace URI."""
    path = tmp_path / "611_0001M01.XML"
    path.write_text(
        make_sony_sidecar_xml(
            umid=_REAL_UMID,
            duration_frames=50,
            fps=24,
            namespace="urn:schemas-professionalDisc:nonRealTimeMeta:ver.2.00",
        )
    )

    metadata = parse_sony_sidecar(path)

    assert metadata.umid == _REAL_UMID
    assert metadata.duration_seconds == 50 / 24
