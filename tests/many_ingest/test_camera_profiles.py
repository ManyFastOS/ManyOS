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


def test_completely_unknown_file(camera_profiles):
    result = classify(Path("random_export.mp4"), None, camera_profiles)
    assert result.category == "Onbekend"
    assert result.confidence == Confidence.LOW


def test_xavc_brand_without_model_stays_ambiguous_between_fx6_and_fx3(
    camera_profiles, make_probe_result
):
    """Mirrors a real file from the Jan Rotmans footage (C9666.MP4): Sony XAVC MP4
    carries no make/model tags, only major_brand=XAVC — which both FX6 and FX3
    profiles match, so this must stay Onbekend rather than guess."""
    probe_result = make_probe_result(major_brand="XAVC", compatible_brands="XAVCmp42iso6")
    result = classify(Path("C9666.MP4"), probe_result, camera_profiles)
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


def test_611_pattern_matches_fx6_via_filename_at_medium_confidence(camera_profiles):
    """611_####.MXF is evidence-backed, not illustrative (see
    docs/MANY_INGEST_CAMERA_PROFILES_V2_PROPOSAL.md section 2b: confirmed Sony FX6
    across three independent ManyFast projects with crew-labeled "FX6" folders).
    Without a model tag this still only matches at the filename tier, so confidence
    stays MEDIUM -- classify() doesn't yet distinguish confirmed patterns from
    illustrative ones (see proposal section 3c)."""
    result = classify(Path("611_3894.MXF"), None, camera_profiles)
    assert result.camera_profile == "Sony FX6"
    assert result.category == "Camera"
    assert result.confidence == Confidence.MEDIUM
