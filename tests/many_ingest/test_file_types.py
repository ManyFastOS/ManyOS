"""Tests for rule-based file-type recognition."""

from __future__ import annotations

from pathlib import Path

from many_ingest.classification.file_types import (
    FileType,
    MediaType,
    detect_file_type,
    detect_media_type,
)


def test_extension_only_classification():
    assert detect_file_type(Path("clip.mp4"), None) == FileType.VIDEO
    assert detect_file_type(Path("clip.mov"), None) == FileType.VIDEO
    assert detect_file_type(Path("clip.mxf"), None) == FileType.VIDEO
    assert detect_file_type(Path("take.wav"), None) == FileType.AUDIO
    assert detect_file_type(Path("take.mp3"), None) == FileType.AUDIO
    assert detect_file_type(Path("Jan Rotmans.m4a"), None) == FileType.AUDIO
    assert detect_file_type(Path("recording.aif"), None) == FileType.AUDIO
    assert detect_file_type(Path("recording.aiff"), None) == FileType.AUDIO
    assert detect_file_type(Path("weird.xyz"), None) == FileType.UNKNOWN


def test_video_container_with_only_audio_stream_is_reclassified(make_probe_result):
    audio_only = make_probe_result(has_video_stream=False, has_audio_stream=True)
    assert detect_file_type(Path("interview.mov"), audio_only) == FileType.AUDIO


def test_video_container_with_no_streams_is_unknown(make_probe_result):
    empty = make_probe_result(has_video_stream=False, has_audio_stream=False)
    assert detect_file_type(Path("broken.mov"), empty) == FileType.UNKNOWN


# -- Fase 5.0: MediaType (additive, independent of FileType above) ---------


def test_detect_media_type_video():
    assert detect_media_type(Path("clip.mp4"), None) == MediaType.VIDEO
    assert detect_media_type(Path("clip.mov"), None) == MediaType.VIDEO
    assert detect_media_type(Path("clip.mxf"), None) == MediaType.VIDEO


def test_detect_media_type_audio():
    assert detect_media_type(Path("take.wav"), None) == MediaType.AUDIO
    assert detect_media_type(Path("take.mp3"), None) == MediaType.AUDIO


def test_detect_media_type_photo():
    assert detect_media_type(Path("still.jpg"), None) == MediaType.PHOTO
    assert detect_media_type(Path("still.jpeg"), None) == MediaType.PHOTO
    assert detect_media_type(Path("raw.arw"), None) == MediaType.PHOTO
    assert detect_media_type(Path("raw.cr2"), None) == MediaType.PHOTO


def test_detect_media_type_sidecar():
    """Extension-only recognition, no pairing to a parent clip (Fase 5.2) and
    no XML parsing — a Sony non-real-time metadata sidecar or a GoPro/Canon
    thumbnail is recognized purely by its companion-file extension."""
    assert detect_media_type(Path("C0001M01.XML"), None) == MediaType.SIDECAR
    assert detect_media_type(Path("clip.xmp"), None) == MediaType.SIDECAR
    assert detect_media_type(Path("clip.thm"), None) == MediaType.SIDECAR


def test_detect_media_type_unknown_for_unrecognized_extension():
    assert detect_media_type(Path("weird.xyz"), None) == MediaType.UNKNOWN


def test_detect_media_type_reclassifies_audio_only_video_container(make_probe_result):
    """Mirrors detect_file_type's stream-presence disambiguation for
    consistency between the two, for the containers they share."""
    audio_only = make_probe_result(has_video_stream=False, has_audio_stream=True)
    assert detect_media_type(Path("interview.mov"), audio_only) == MediaType.AUDIO


def test_detect_media_type_never_changes_detect_file_type_semantics():
    """Regression guard: adding MediaType must not alter FileType's own
    behaviour for any of the same inputs."""
    assert detect_file_type(Path("clip.mp4"), None) == FileType.VIDEO
    assert detect_file_type(Path("take.wav"), None) == FileType.AUDIO
    assert detect_file_type(Path("still.jpg"), None) == FileType.UNKNOWN
    assert detect_file_type(Path("clip.xmp"), None) == FileType.UNKNOWN
