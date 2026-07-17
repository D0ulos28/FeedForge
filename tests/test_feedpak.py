from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
import yaml

from feedback_converter.feedpak import inspect_feedpak, update_feedpak


def make_feedpak(tmp_path: Path) -> Path:
    package = tmp_path / "package"
    (package / "arrangements").mkdir(parents=True)
    (package / "stems").mkdir()
    (package / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "feedpak_version": "1.14.0",
                "title": "Original Title",
                "artist": "Test Artist",
                "authors": [{"name": "Original Charter", "role": "charter"}],
                "arrangements": [
                    {
                        "id": "lead",
                        "name": "Lead",
                        "type": "guitar",
                        "file": "arrangements/lead.json",
                    }
                ],
                "stems": [
                    {
                        "id": "full",
                        "file": "stems/full.ogg",
                        "codec": "vorbis",
                        "default": True,
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (package / "arrangements" / "lead.json").write_text(
        json.dumps({"notes": [], "chords": []}), encoding="utf-8"
    )
    (package / "stems" / "full.ogg").write_bytes(b"OggS-test")
    target = tmp_path / "input.feedpak"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in package.rglob("*"):
            if item.is_file():
                archive.write(item, item.relative_to(package).as_posix())
    return target


def test_inspect_and_update_feedpak_archive(tmp_path):
    source = make_feedpak(tmp_path)

    preview = inspect_feedpak(source)
    assert preview["source_type"] == "feedpak"
    assert preview["title"] == "Original Title"
    assert preview["arrangements"][0]["id"] == "lead"
    assert preview["stems"][0]["size"] == len(b"OggS-test")

    output = tmp_path / "edited.feedpak"
    result = update_feedpak(
        source,
        output,
        metadata={"title": "Edited Title", "language": "en"},
        authors=[{"name": "Cross Platform Charter", "role": "charter"}],
    )

    assert result.output_path == output
    edited = inspect_feedpak(output)
    assert edited["title"] == "Edited Title"
    assert edited["language"] == "en"
    assert edited["authors"] == [{"name": "Cross Platform Charter", "role": "charter"}]
    assert edited["stems"] == preview["stems"]


def test_update_feedpak_rejects_unsafe_archive_paths(tmp_path):
    source = tmp_path / "unsafe.feedpak"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("manifest.yaml", "title: Unsafe\n")
        archive.writestr("../outside.txt", "not allowed")

    with pytest.raises(ValueError, match="Unsafe path"):
        update_feedpak(source, tmp_path / "output.feedpak")

    assert not (tmp_path / "outside.txt").exists()
