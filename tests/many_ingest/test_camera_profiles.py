"""Tests for camera-profile classification (rule-based — not Asset Intelligence)."""

from __future__ import annotations

from pathlib import Path

from many_ingest.classification.camera_profiles import Confidence, classify


def test_dji_matches_via_filename_and_metadata(camera_profiles, make_probe_result):
    result = classify(Path("DJI_0001.MP4"), make_probe_result(make="DJI"), camera_profiles)
    assert result.category == "Drone"
    assert result.camera_profile == "DJI"
    assert result.confidence == Confidence.HIGH


def test_gopro_matches_via_metadata_regardless_of_filename(camera_profiles, make_probe_result):
    result = classify(Path("weird_name.mp4"), make_probe_result(make="GoPro"), camera_profiles)
    assert result.camera_profile == "GoPro"
    assert result.confidence == Confidence.HIGH


def test_fx6_and_fx3_share_a_filename_pattern_and_need_metadata_to_disambiguate(camera_profiles):
    result = classify(Path("C0001.MP4"), None, camera_profiles)
    assert result.category == "Onbekend"
    assert result.confidence == Confidence.LOW


def test_fx6_metadata_disambiguates(camera_profiles, make_probe_result):
    result = classify(Path("C0001.MP4"), make_probe_result(model="ILME-FX6"), camera_profiles)
    assert result.camera_profile == "Sony FX6"
    assert result.confidence == Confidence.HIGH


def test_fx3_metadata_disambiguates(camera_profiles, make_probe_result):
    result = classify(Path("C0001.MP4"), make_probe_result(model="ILME-FX3"), camera_profiles)
    assert result.camera_profile == "Sony FX3"
    assert result.confidence == Confidence.HIGH


def test_audio_via_stream_analysis(camera_profiles, make_probe_result):
    result = classify(
        Path("interview.mov"),
        make_probe_result(has_video_stream=False, has_audio_stream=True),
        camera_profiles,
    )
    assert result.category == "Audio"
    assert result.confidence == Confidence.HIGH


def test_audio_via_extension_without_metadata(camera_profiles):
    result = classify(Path("recording.wav"), None, camera_profiles)
    assert result.category == "Audio"
    assert result.confidence == Confidence.MEDIUM


def test_xavc_brand_without_model_or_container_stays_ambiguous(
    camera_profiles, make_probe_result
):
    """Edge case: brand=XAVC known but container_format somehow unavailable (e.g. a
    future container ffprobe can't identify) — both FX6 and FX3 share brand_contains,
    so without the container tie-breaker this must stay Onbekend rather than guess.
    A real C9666.MP4-style file always has a container_format; see
    test_container_format_resolves_fx3_without_model_tag for that realistic case."""
    probe_result = make_probe_result(major_brand="XAVC", compatible_brands="XAVCmp42iso6")
    result = classify(Path("C9666.MP4"), probe_result, camera_profiles)
    assert result.category == "Onbekend"
    assert result.confidence == Confidence.LOW


def test_container_format_resolves_fx3_without_model_tag(camera_profiles, make_probe_result):
    """The real-world fix: C9666.MP4-style Sony XAVC MP4 files carry no make/model
    tag, but container_contains=["mp4"] is confirmed FX3-specific within ManyFast's
    workflow (see docs/MANY_INGEST_CAMERA_PROFILES_V2_PROPOSAL.md sections 7-8) — no
    longer Onbekend. sony_fx3 has container_requires_brand=true, so this only works
    because major_brand="XAVC" is also present here — see
    test_generic_mp4_without_sony_metadata_is_never_classified_as_fx3 for what
    happens when it's absent."""
    probe_result = make_probe_result(
        major_brand="XAVC",
        compatible_brands="XAVCmp42iso6",
        container_format="mov,mp4,m4a,3gp,3g2,mj2",
    )
    result = classify(Path("C9666.MP4"), probe_result, camera_profiles)
    assert result.category == "Camera"
    assert result.camera_profile == "Sony FX3"
    assert result.confidence == Confidence.HIGH


def test_container_format_resolves_fx6_independent_of_confirmed_filename(
    camera_profiles, make_probe_result
):
    """An MXF file that doesn't match the 611_####.MXF confirmed filename pattern
    (e.g. a different numbering scheme) still resolves to Sony FX6 via the
    container-format signal alone — the container match doesn't depend on the
    filename pattern also matching. FX6 has no container_requires_brand set, so
    this stays unaffected by the FX3 fix below."""
    probe_result = make_probe_result(container_format="mxf")
    result = classify(Path("some_other_name.MXF"), probe_result, camera_profiles)
    assert result.category == "Camera"
    assert result.camera_profile == "Sony FX6"
    assert result.confidence == Confidence.HIGH


def test_generic_mp4_without_sony_metadata_is_never_classified_as_fx3(
    camera_profiles, make_probe_result
):
    """Regression test for a real, confirmed bug (2026-08-03): container_contains
    ["mp4"] alone matched almost any MP4 file. A plain, non-Sony MP4 (real-world
    equivalent: major_brand="isom", no XAVC brand, no Sony tags at all, and a
    filename that doesn't match any pattern) must resolve to Onbekend, never to
    "Sony FX3" — container_requires_brand on sony_fx3 is the fix."""
    probe_result = make_probe_result(
        major_brand="isom",
        compatible_brands="isomiso2avc1mp41",
        container_format="mov,mp4,m4a,3gp,3g2,mj2",
    )
    result = classify(Path("random_export.mp4"), probe_result, camera_profiles)
    assert result.camera_profile != "Sony FX3"
    assert result.category == "Onbekend"
    assert result.confidence == Confidence.LOW


def test_xavc_brand_with_model_still_disambiguates(camera_profiles, make_probe_result):
    probe_result = make_probe_result(
        major_brand="XAVC", compatible_brands="XAVCmp42iso6", model="ILME-FX6"
    )
    result = classify(Path("C9666.MP4"), probe_result, camera_profiles)
    assert result.camera_profile == "Sony FX6"
    assert result.confidence == Confidence.HIGH


def test_sony_mxf_company_name_alone_is_not_enough_for_a_specific_model_match(
    camera_profiles, make_probe_result
):
    """Sony MXF files expose company_name/product_name rather than make/model
    (metadata_extractor.py maps company_name -> make); make="Sony" alone doesn't hit
    the make/model tier for any profile. With a filename that doesn't match any
    pattern either, this correctly stays Onbekend."""
    probe_result = make_probe_result(make="Sony", model="Mem")
    result = classify(Path("randomly_named_export.mxf"), probe_result, camera_profiles)
    assert result.category == "Onbekend"


def test_confirmed_611_pattern_matches_fx6_at_high_confidence(camera_profiles):
    """611_####.MXF is evidence-backed, not illustrative (see
    docs/MANY_INGEST_CAMERA_PROFILES_V2_PROPOSAL.md section 2b: confirmed Sony FX6
    across three independent ManyFast projects with crew-labeled "FX6" folders).
    It lives in confirmed_filename_patterns, so it matches at the HIGH tier even
    without a model tag -- unlike the shared, generic ^C####.* pattern."""
    result = classify(Path("611_3894.MXF"), None, camera_profiles)
    assert result.camera_profile == "Sony FX6"
    assert result.category == "Camera"
    assert result.confidence == Confidence.HIGH


def test_generic_filename_pattern_still_only_reaches_medium_confidence(camera_profiles):
    """DJI_0001.MP4 has no metadata backing it here, only the generic (not
    ManyFast-confirmed) ^DJI_.* pattern -- existing classification retained: still
    identified as DJI, but at MEDIUM rather than HIGH, since only a confirmed
    filename pattern or a make/model metadata hit reaches HIGH."""
    result = classify(Path("DJI_0001.MP4"), None, camera_profiles)
    assert result.category == "Drone"
    assert result.camera_profile == "DJI"
    assert result.confidence == Confidence.MEDIUM


def test_unknown_file_has_low_confidence(camera_profiles):
    """No signal at any tier -> Onbekend at LOW, never guessed."""
    result = classify(Path("random_export.mp4"), None, camera_profiles)
    assert result.category == "Onbekend"
    assert result.confidence == Confidence.LOW
