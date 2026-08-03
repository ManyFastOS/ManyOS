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
