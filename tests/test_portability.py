from __future__ import annotations

import io
import wave

from PIL import Image

from feedback_converter import converter


def test_wav_to_ogg_uses_portable_soundfile_encoder(tmp_path):
    source = tmp_path / "source.wav"
    with wave.open(str(source), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x00\x00" * 800)

    output = tmp_path / "output.ogg"
    assert converter._convert_wav_file_to_ogg(source, output)
    assert output.read_bytes().startswith(b"OggS")


def test_dds_to_png_uses_portable_pillow_decoder(tmp_path):
    source = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(source, format="DDS")

    output = tmp_path / "cover.png"
    assert converter._convert_dds_bytes_to_png(source.getvalue(), output)
    assert output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_failed_audio_conversion_removes_partial_output(tmp_path, monkeypatch):
    output = tmp_path / "partial.ogg"
    output.write_bytes(b"stale")
    monkeypatch.setattr(converter.sf, "read", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("bad audio")))

    assert not converter._convert_wav_file_to_ogg(tmp_path / "bad.wav", output)
    assert not output.exists()
