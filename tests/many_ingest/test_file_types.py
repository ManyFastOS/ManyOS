"""Tests for rule-based file-type recognition."""

from __future__ import annotations

from pathlib import Path

from many_ingest.classification.file_types import FileType, detect_file_type


def test_extension_only_classification():
    assert detect_file_type(Path("clip.mp4"), None) == FileType.VIDEO
    assert detect_file_type(Path("clip.mov"), None) == FileType.VIDEO
    assert detect_file_type(Path("clip.mxf"), None) == FileType.VIDEO
    assert detect_file_type(Path("take.wav"), None) == FileType.AUDIO
    assert detect_file_type(Path("take.mp3"), None) == FileType.AUDIO
    assert detect_file_type(Path("Jan Rotmans.m4a"), None) == FileType.AUDIO
    assert detect_file_type(Path("weird.xyz"), None) == FileType.UNKNOWN


def test_video_container_with_only_audio_stream_is_reclassified(make_probe_result):
    audio_only = make_probe_result(has_video_stream=False, has_audio_stream=True)
    assert detect_file_type(Path("interview.mov"), audio_only) == FileType.AUDIO


def test_video_container_with_no_streams_is_unknown(make_probe_result):
    empty = make_probe_result(has_video_stream=False, has_audio_stream=False)
    assert detect_file_type(Path("broken.mov"), empty) == FileType.UNKNOWN
