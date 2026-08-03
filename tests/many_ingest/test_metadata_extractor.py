"""Tests for the ffprobe wrapper's tag-name aliases.

Learned from real Jan Rotmans footage (2026-08-03): Sony MXF files carry
company_name/product_name instead of make/model, and Sony XAVC MP4 files carry no
make/model at all but a distinctive major_brand.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from many_ingest.metadata_extractor import _first_tag, probe

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe niet geïnstalleerd",
)


def test_first_tag_is_case_insensitive_and_prefers_first_matching_key():
    tags = {"Make": "DJI"}
    assert _first_tag(tags, ["make", "com.apple.quicktime.make", "company_name"]) == "DJI"


def test_first_tag_falls_back_to_sony_mxf_tag_names():
    tags = {"company_name": "Sony", "product_name": "Mem"}
    assert _first_tag(tags, ["make", "com.apple.quicktime.make", "company_name"]) == "Sony"
    assert _first_tag(tags, ["model", "com.apple.quicktime.model", "product_name"]) == "Mem"


def test_first_tag_returns_none_when_nothing_matches():
    assert _first_tag({"unrelated": "x"}, ["make", "company_name"]) is None


@requires_ffmpeg
def test_probe_reads_major_brand_and_compatible_brands(tmp_path):
    clip = tmp_path / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "quiet", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=320x240:d=1",
            "-c:v", "libx264",
            str(clip),
        ],
        check=True,
    )

    result = probe(clip)

    assert result.has_video_stream is True
    assert result.major_brand is not None
