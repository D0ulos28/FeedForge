from __future__ import annotations

import configparser
import importlib.util
import json
import math
import os
import shutil
import sqlite3
import struct
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .converter import ConversionResult, convert_psarc_songs
from .inspector import PsarcPreview, inspect_psarc
from .rig_builder_seed import SeedResult, seed_rig_builder_routes

SAMPLE_RATE = 48_000


@dataclass(frozen=True)
class ToneLabResult:
    input_path: Path
    output_dir: Path
    feedpak_paths: list[Path] = field(default_factory=list)
    fixture_paths: list[Path] = field(default_factory=list)
    report_json: Path | None = None
    report_markdown: Path | None = None
    conversion_ok: bool = False
    seed_ok: bool = False
    route_summary: dict[str, Any] = field(default_factory=dict)
    native_validation: dict[str, Any] = field(default_factory=dict)
    render_probe: dict[str, Any] = field(default_factory=dict)
    wet_render: dict[str, Any] = field(default_factory=dict)
    rocksmith_reference_probe: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def run_tone_lab(
    input_psarc: Path,
    *,
    output_dir: Path | None = None,
    rig_builder_data_dir: Path | None = None,
    overwrite: bool = False,
) -> ToneLabResult:
    input_psarc = Path(input_psarc)
    if output_dir is None:
        output_dir = Path(tempfile.gettempdir()) / f"feedforge-tone-lab-{input_psarc.stem}"
    output_dir = Path(output_dir)
    if output_dir.exists() and any(output_dir.iterdir()) and not overwrite:
        raise FileExistsError(f"Tone lab output already exists: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    fixtures = _write_di_fixtures(output_dir / "fixtures")
    errors: list[str] = []
    conversion_results: list[ConversionResult] = []
    seed_result: SeedResult | None = None
    preview: PsarcPreview | None = None

    old_data_dir = os.environ.get("FEEDFORGE_RIG_BUILDER_DATA_DIR")
    if rig_builder_data_dir is not None:
        os.environ["FEEDFORGE_RIG_BUILDER_DATA_DIR"] = str(rig_builder_data_dir)
    try:
        try:
            conversion_results = convert_psarc_songs(
                input_psarc,
                output_dir / f"{input_psarc.stem}.feedpak",
                archive=True,
                overwrite=True,
                include_tones=True,
                keep_workdir=False,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"conversion: {exc}")

        try:
            seed_result = seed_rig_builder_routes(input_psarc)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"rig_builder_seed: {exc}")

        try:
            preview = inspect_psarc(input_psarc, cover_dir=output_dir / "cover")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"inspect: {exc}")
    finally:
        if rig_builder_data_dir is not None:
            if old_data_dir is None:
                os.environ.pop("FEEDFORGE_RIG_BUILDER_DATA_DIR", None)
            else:
                os.environ["FEEDFORGE_RIG_BUILDER_DATA_DIR"] = old_data_dir

    route_summary = _build_route_summary(input_psarc, preview, seed_result)
    native_validation = _run_native_validation(output_dir, seed_result)
    render_probe = _run_native_render_probe(output_dir)
    wet_render = _run_wet_render(output_dir, fixtures, native_validation, render_probe)
    rocksmith_reference_probe = _run_rocksmith_reference_probe(output_dir)
    feedpak_paths = [result.output_path for result in conversion_results]
    report = _build_report(
        input_psarc,
        output_dir,
        fixtures,
        feedpak_paths,
        seed_result,
        route_summary,
        native_validation,
        render_probe,
        wet_render,
        rocksmith_reference_probe,
        errors,
    )
    report_json = output_dir / "tone-lab-report.json"
    report_markdown = output_dir / "tone-lab-report.md"
    report_json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_markdown.write_text(_format_markdown_report(report), encoding="utf-8")

    return ToneLabResult(
        input_path=input_psarc,
        output_dir=output_dir,
        feedpak_paths=feedpak_paths,
        fixture_paths=fixtures,
        report_json=report_json,
        report_markdown=report_markdown,
        conversion_ok=bool(conversion_results),
        seed_ok=seed_result is not None,
        route_summary=route_summary,
        native_validation=native_validation,
        render_probe=render_probe,
        wet_render=wet_render,
        rocksmith_reference_probe=rocksmith_reference_probe,
        errors=errors,
    )


def _write_di_fixtures(fixtures_dir: Path) -> list[Path]:
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    specs = [
        ("di_single_notes.wav", _single_notes_fixture),
        ("di_chord_stabs.wav", _chord_stabs_fixture),
        ("di_palm_mute_bursts.wav", _palm_mute_fixture),
        ("di_sweep_noise.wav", _sweep_noise_fixture),
    ]
    written: list[Path] = []
    for filename, generator in specs:
        path = fixtures_dir / filename
        _write_wav(path, generator())
        written.append(path)
    return written


def _write_wav(path: Path, samples: list[float]) -> None:
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(SAMPLE_RATE)
        frames = bytearray()
        for sample in samples:
            value = max(-0.95, min(0.95, sample))
            frames.extend(struct.pack("<h", int(value * 32767)))
        fh.writeframes(bytes(frames))


def _single_notes_fixture() -> list[float]:
    pitches = [82.41, 110.0, 146.83, 196.0, 246.94, 329.63]
    samples: list[float] = []
    for pitch in pitches:
        samples.extend(_pluck(pitch, 0.62, level=0.28))
        samples.extend([0.0] * int(SAMPLE_RATE * 0.08))
    return samples


def _chord_stabs_fixture() -> list[float]:
    samples: list[float] = []
    chords = [(82.41, 123.47, 164.81), (110.0, 164.81, 220.0), (146.83, 220.0, 293.66)]
    for chord in chords:
        samples.extend(_chord(chord, 0.72, level=0.22))
        samples.extend([0.0] * int(SAMPLE_RATE * 0.18))
    return samples


def _palm_mute_fixture() -> list[float]:
    samples: list[float] = []
    for index in range(20):
        pitch = 82.41 if index % 2 == 0 else 110.0
        samples.extend(_pluck(pitch, 0.16, level=0.36, decay=18.0))
        samples.extend([0.0] * int(SAMPLE_RATE * 0.06))
    return samples


def _sweep_noise_fixture() -> list[float]:
    duration = 4.0
    count = int(SAMPLE_RATE * duration)
    samples: list[float] = []
    seed = 17
    phase = 0.0
    for i in range(count):
        t = i / SAMPLE_RATE
        freq = 70.0 * ((5000.0 / 70.0) ** (t / duration))
        phase += (2.0 * math.pi * freq) / SAMPLE_RATE
        seed = (1103515245 * seed + 12345) & 0x7FFFFFFF
        noise = ((seed / 0x7FFFFFFF) * 2.0 - 1.0) * 0.025
        fade = min(1.0, t / 0.2, (duration - t) / 0.2)
        samples.append((math.sin(phase) * 0.18 + noise) * max(0.0, fade))
    return samples


def _pluck(pitch: float, duration: float, *, level: float, decay: float = 4.0) -> list[float]:
    count = int(SAMPLE_RATE * duration)
    out: list[float] = []
    for i in range(count):
        t = i / SAMPLE_RATE
        envelope = math.exp(-decay * t) * min(1.0, t / 0.012)
        harmonic = 0.65 * math.sin(2.0 * math.pi * pitch * t)
        harmonic += 0.22 * math.sin(2.0 * math.pi * pitch * 2.01 * t)
        harmonic += 0.13 * math.sin(2.0 * math.pi * pitch * 3.02 * t)
        out.append(level * envelope * harmonic)
    return out


def _chord(pitches: tuple[float, ...], duration: float, *, level: float) -> list[float]:
    count = int(SAMPLE_RATE * duration)
    out: list[float] = []
    for i in range(count):
        t = i / SAMPLE_RATE
        envelope = math.exp(-3.2 * t) * min(1.0, t / 0.02)
        value = sum(math.sin(2.0 * math.pi * pitch * t) for pitch in pitches) / len(pitches)
        value += 0.18 * sum(math.sin(2.0 * math.pi * pitch * 2.0 * t) for pitch in pitches) / len(pitches)
        out.append(level * envelope * value)
    return out


def _build_route_summary(input_psarc: Path, preview: PsarcPreview | None, seed_result: SeedResult | None) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "song_key": input_psarc.with_suffix(".feedpak").name,
        "arrangements": len(preview.arrangements) if preview else 0,
        "tone_arrangements": len(preview.tones) if preview else 0,
        "tone_definitions": sum(len(item.definitions) for item in preview.tones) if preview else 0,
        "mapped_gear": sum(len(definition.gear) for tone in preview.tones for definition in tone.definitions) if preview else 0,
        "routes": {"ready": 0, "partial": 0, "missing": 0, "other": 0},
        "stages": {"vst": 0, "nam": 0, "ir": 0, "rs_ir": 0, "none": 0, "other": 0},
        "issues": [],
        "assets": [],
        "seeded_tones": [],
    }
    if seed_result is not None:
        summary["db_path"] = str(seed_result.db_path)
        summary["seeded_tones"] = [
            {"tone_key": tone.tone_key, "status": tone.status, "stages": tone.stages, "missing": list(tone.missing)}
            for tone in seed_result.tones
        ]
        summary["database"] = _audit_seeded_database(seed_result.db_path, seed_result.song_key)
    if preview is None:
        summary["issues"].append({"level": "error", "message": "PSARC inspection failed."})
        return summary

    for route in preview.rig_builder:
        if route.status in summary["routes"]:
            summary["routes"][route.status] += 1
        else:
            summary["routes"]["other"] += 1
        for stage in route.stages:
            if stage.kind in summary["stages"]:
                summary["stages"][stage.kind] += 1
            else:
                summary["stages"]["other"] += 1
            asset_info = _stage_asset_info(stage.kind, stage.asset, seed_result.db_path.parent if seed_result else None)
            if asset_info:
                summary["assets"].append(asset_info)
            if stage.status != "ready":
                summary["issues"].append(
                    {
                        "level": "warning",
                        "tone_key": route.tone_key,
                        "slot": stage.slot,
                        "gear": stage.gear,
                        "kind": stage.kind,
                        "message": f"{stage.slot} stage is {stage.status}",
                    }
                )
    if not preview.rig_builder:
        summary["issues"].append({"level": "warning", "message": "No Rig Builder routes were found after seeding."})
    return summary


def _run_native_validation(output_dir: Path, seed_result: SeedResult | None) -> dict[str, Any]:
    validation: dict[str, Any] = {
        "status": "skipped",
        "reason": "",
        "backend_url": "",
        "native_presets_dir": "",
        "native_load_report": "",
        "routes": [],
    }
    if seed_result is None:
        validation["reason"] = "Rig Builder seeding did not complete."
        return validation

    backend = _find_feedback_backend()
    if backend is None:
        validation["reason"] = "FeedBack backend was not reachable on localhost ports 18000-18010."
        return validation
    validation["backend_url"] = backend

    preset_rows = _tone_preset_rows(seed_result.db_path, seed_result.song_key)
    if not preset_rows:
        validation["reason"] = "No seeded tone_mappings rows were found for native validation."
        return validation

    native_dir = output_dir / "native-presets"
    native_dir.mkdir(parents=True, exist_ok=True)
    for old_payload in native_dir.glob("*.json"):
        try:
            old_payload.unlink()
        except OSError:
            pass
    fetched: list[dict[str, Any]] = []
    fetch_errors: list[dict[str, Any]] = []
    for row in preset_rows:
        url = f"{backend}/api/plugins/rig_builder/native_preset_full/{row['preset_id']}"
        try:
            with urllib.request.urlopen(url, timeout=20) as response:  # noqa: S310 - localhost diagnostic fetch
                text = response.read().decode("utf-8-sig")
            payload = json.loads(text)
            expected = _expected_native_stage_summary(seed_result.db_path, int(row["preset_id"]))
            native_chain = (payload.get("native_preset") or {}).get("chain") or []
            native = _native_chain_summary(native_chain)
            issues = _native_stage_issues(expected, native)
            preset_path = native_dir / f"{row['preset_id']}.json"
            preset_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            fetched.append(
                {
                    "tone_key": row["tone_key"],
                    "preset_id": row["preset_id"],
                    "path": str(preset_path),
                    "stages": len((payload.get("native_preset") or {}).get("chain") or []),
                    "missing": payload.get("missing") or [],
                    "expected": expected,
                    "native": native,
                    "issues": issues,
                }
            )
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            fetch_errors.append({"tone_key": row["tone_key"], "preset_id": row["preset_id"], "error": str(exc)})

    validation["native_presets_dir"] = str(native_dir)
    validation["routes"] = fetched
    if fetch_errors:
        validation["fetch_errors"] = fetch_errors
    route_issues = [issue for route in fetched for issue in route.get("issues") or []]
    if route_issues:
        validation["chain_issues"] = route_issues
    if not fetched:
        validation["status"] = "failed"
        validation["reason"] = "No native preset payloads could be fetched from FeedBack."
        return validation

    load_report = output_dir / "native-load-report.json"
    load_result = _run_native_load_smoke(native_dir, load_report)
    validation["native_load_report"] = str(load_report)
    validation.update(load_result)
    if route_issues and validation.get("status") == "ok":
        validation["status"] = "partial"
        validation["reason"] = "Native presets load, but one or more expected stages are missing or pending downloads."
    return validation


def _expected_native_stage_summary(db_path: Path, preset_id: int) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "nam": 0,
        "vst": 0,
        "ir": 0,
        "rs_ir": 0,
        "pending_nam": [],
        "pieces": [],
    }
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return summary
    try:
        rows = conn.execute(
            "SELECT slot, rs_gear_type, kind, file, tone3000_id, vst_path, bypassed "
            "FROM preset_pieces WHERE preset_id = ? ORDER BY slot_order",
            (preset_id,),
        ).fetchall()
        for row in rows:
            if int(row["bypassed"] or 0):
                continue
            kind = str(row["kind"] or "none")
            file = str(row["file"] or "")
            piece = {
                "slot": str(row["slot"] or ""),
                "gear": str(row["rs_gear_type"] or ""),
                "kind": kind,
                "file": file,
                "tone3000_id": row["tone3000_id"],
                "vst_path": str(row["vst_path"] or ""),
            }
            summary["pieces"].append(piece)
            if kind == "nam":
                if file:
                    summary["nam"] += 1
                elif row["tone3000_id"]:
                    summary["pending_nam"].append(piece)
            elif kind == "vst" and row["vst_path"]:
                summary["vst"] += 1
            elif kind in {"ir", "rs_ir"} and file:
                summary[kind] += 1
        return summary
    except sqlite3.Error:
        return summary
    finally:
        conn.close()


def _native_chain_summary(chain: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {"nam": 0, "vst": 0, "ir": 0, "stages": []}
    for stage in chain:
        stage_type = stage.get("type")
        kind = "other"
        if stage_type == 1:
            kind = "nam"
            summary["nam"] += 1
        elif stage_type == 0:
            kind = "vst"
            summary["vst"] += 1
        elif stage_type == 2:
            kind = "ir"
            summary["ir"] += 1
        summary["stages"].append(
            {
                "kind": kind,
                "slot": str(stage.get("slot") or ""),
                "gear": str(stage.get("rs_gear") or ""),
                "name": str(stage.get("name") or ""),
            }
        )
    return summary


def _native_stage_issues(expected: dict[str, Any], native: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for pending in expected.get("pending_nam") or []:
        issues.append(
            {
                "level": "warning",
                "message": "NAM stage has a Tone3000 id but no downloaded .nam file, so the native chain omits it",
                "slot": pending.get("slot"),
                "gear": pending.get("gear"),
                "tone3000_id": pending.get("tone3000_id"),
            }
        )
    if int(native.get("nam") or 0) < int(expected.get("nam") or 0):
        issues.append(
            {
                "level": "warning",
                "message": "Native chain has fewer NAM stages than the seeded database expects",
                "expected": expected.get("nam"),
                "actual": native.get("nam"),
            }
        )
    return issues


def _find_feedback_backend() -> str | None:
    for port in range(18000, 18011):
        url = f"http://127.0.0.1:{port}"
        try:
            with urllib.request.urlopen(f"{url}/api/plugins/rig_builder/settings", timeout=1.5) as response:  # noqa: S310
                if response.status < 500:
                    return url
        except (OSError, urllib.error.URLError):
            continue
    return None


def _tone_preset_rows(db_path: Path, song_key: str) -> list[dict[str, Any]]:
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return []
    try:
        rows = conn.execute(
            "SELECT tm.tone_key, tm.preset_id, p.name "
            "FROM tone_mappings tm JOIN presets p ON p.id = tm.preset_id "
            "WHERE tm.filename = ? ORDER BY tm.tone_key",
            (song_key,),
        ).fetchall()
        return [
            {"tone_key": str(row["tone_key"] or ""), "preset_id": int(row["preset_id"]), "name": str(row["name"] or "")}
            for row in rows
        ]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def _run_native_load_smoke(native_dir: Path, report_path: Path) -> dict[str, Any]:
    addon = Path(r"C:\Program Files\feedback\current\resources\app.asar.unpacked\build\Release\slopsmith_audio.node")
    if not addon.is_file():
        return {"status": "skipped", "reason": f"Native audio addon not found: {addon}"}
    script = _native_load_script(addon)
    try:
        result = subprocess.run(
            ["node", "-", str(native_dir)],
            input=script,
            cwd=str(native_dir),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "failed", "reason": f"Native load smoke could not run: {exc}"}

    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        parsed = {"raw_stdout": stdout}
    report_path.write_text(
        json.dumps(
            {
                "returncode": result.returncode,
                "stdout": parsed,
                "stderr": stderr,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    if result.returncode != 0:
        return {"status": "failed", "reason": "Native load smoke returned a non-zero exit code."}
    routes = parsed if isinstance(parsed, list) else []
    failed = [route for route in routes if not ((route.get("loadResult") or {}).get("success") is True)]
    partial = [
        route for route in routes
        if int(route.get("loadedStages") or 0) != int(route.get("expectedStages") or -1)
    ]
    if failed or partial:
        return {
            "status": "failed",
            "reason": "One or more native chains failed to load completely.",
            "load_failures": failed,
            "partial_loads": partial,
        }
    return {
        "status": "ok",
        "reason": "All fetched native presets loaded into the installed audio engine.",
        "loaded_routes": len(routes),
        "loaded_stages": sum(int(route.get("loadedStages") or 0) for route in routes),
    }


def _run_native_render_probe(output_dir: Path) -> dict[str, Any]:
    addon = Path(r"C:\Program Files\feedback\current\resources\app.asar.unpacked\build\Release\slopsmith_audio.node")
    host = Path(r"C:\Program Files\feedback\current\resources\app.asar.unpacked\build\Release\slopsmith-vst-host.exe")
    report_path = output_dir / "native-render-probe.json"
    probe: dict[str, Any] = {
        "status": "blocked",
        "reason": "",
        "native_audio_addon": str(addon),
        "native_vst_host": str(host),
        "native_audio_addon_exists": addon.is_file(),
        "native_vst_host_exists": host.is_file(),
        "report_path": str(report_path),
        "tools": {
            "node": shutil.which("node") or "",
            "ffmpeg": _find_ffmpeg(),
            "sounddevice": bool(importlib.util.find_spec("sounddevice")),
            "soundcard": bool(importlib.util.find_spec("soundcard")),
            "clang++": shutil.which("clang++") or "",
            "cl": shutil.which("cl") or "",
            "cmake": shutil.which("cmake") or "",
        },
        "file_input_api": False,
        "offline_render_api": False,
        "virtual_loopback": {"status": "unknown", "inputs": [], "outputs": []},
        "available_paths": [],
        "blocked_paths": [],
    }
    if not addon.is_file():
        probe["reason"] = "Native audio addon is not installed."
        _write_json(report_path, probe)
        return probe
    if not shutil.which("node"):
        probe["reason"] = "Node is not available to inspect the native audio addon."
        _write_json(report_path, probe)
        return probe

    script = _native_render_probe_script(addon)
    try:
        result = subprocess.run(
            ["node", "-"],
            input=script,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        probe["reason"] = f"Native render probe could not run: {exc}"
        _write_json(report_path, probe)
        return probe

    try:
        native = json.loads(result.stdout.strip() or "{}")
    except json.JSONDecodeError:
        native = {"raw_stdout": result.stdout.strip()}
    probe["native_probe"] = native
    if result.stderr.strip():
        probe["stderr"] = result.stderr.strip()
    if result.returncode != 0:
        probe["reason"] = "Native render probe returned a non-zero exit code."
        _write_json(report_path, probe)
        return probe

    api = set(native.get("api_functions") or [])
    file_input_names = {"renderFile", "renderWav", "processFile", "processWav", "loadInputFile", "setInputFile"}
    offline_names = {"offlineRender", "renderOffline", "processOffline", "bouncePreset"}
    probe["file_input_api"] = bool(api & file_input_names)
    probe["offline_render_api"] = bool(api & offline_names)

    inputs, outputs = _virtual_loopback_devices(native.get("device_types") or [])
    probe["virtual_loopback"] = {
        "status": "available" if inputs and outputs else "missing",
        "inputs": inputs,
        "outputs": outputs,
    }

    if probe["file_input_api"] or probe["offline_render_api"]:
        probe["status"] = "ready"
        probe["reason"] = "Native addon exposes an offline/file input render path."
        probe["available_paths"].append("native_file_render")
    elif inputs and outputs and probe["tools"]["sounddevice"] and probe["tools"]["soundcard"]:
        probe["status"] = "ready_with_loopback"
        probe["reason"] = "A virtual loopback route and Python WASAPI tooling are available for dry playback and wet capture."
        probe["available_paths"].append("virtual_loopback_playback_capture")
    else:
        blocked = []
        if not probe["file_input_api"] and not probe["offline_render_api"]:
            blocked.append("native addon exposes chain loading but no file-input/offline render function")
            probe["blocked_paths"].append("native_file_render")
        if not (inputs and outputs):
            blocked.append("no virtual loopback input/output pair was detected")
            probe["blocked_paths"].append("virtual_loopback_playback_capture")
        if not (probe["tools"]["sounddevice"] and probe["tools"]["soundcard"]):
            blocked.append("Python sounddevice/soundcard packages are not available for automated playback/capture")
        probe["reason"] = "; ".join(blocked)
    _write_json(report_path, probe)
    return probe


def _find_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if path:
        return path
    for parent in Path(__file__).resolve().parents:
        candidate = parent / ".codex" / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
        if candidate.is_file():
            return str(candidate)
    return ""


def _native_render_probe_script(addon: Path) -> str:
    addon_js = str(addon).replace("\\", "/")
    return f"""
const audio = require({json.dumps(addon_js)});
(async () => {{
  const out = {{ api_functions: Object.keys(audio).sort(), device_types: [], input_devices: [] }};
  audio.init();
  try {{ out.device_types = await audio.getDeviceTypes(); }} catch (e) {{ out.device_types_error = e && e.message || String(e); }}
  try {{ out.input_devices = await audio.listInputDevices(); }} catch (e) {{ out.input_devices_error = e && e.message || String(e); }}
  try {{ out.current_device = await audio.getCurrentDevice(); }} catch (e) {{ out.current_device_error = e && e.message || String(e); }}
  audio.shutdown();
  console.log(JSON.stringify(out));
}})().catch(error => {{
  try {{ audio.shutdown(); }} catch (_) {{}}
  console.error(error && error.stack || error);
  process.exit(1);
}});
"""


def _virtual_loopback_devices(device_types: list[dict[str, Any]]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    needles = ("vb-audio", "cable", "voicemeeter", "voicemod", "loopback", "stereo mix", "blackhole")
    inputs: list[dict[str, str]] = []
    outputs: list[dict[str, str]] = []
    for device_type in device_types:
        type_name = str(device_type.get("name") or "")
        for direction, target in (("inputs", inputs), ("outputs", outputs)):
            for name in device_type.get(direction) or []:
                device_name = str(name)
                if any(needle in device_name.lower() for needle in needles):
                    target.append({"type": type_name, "name": device_name})
    return inputs, outputs


def _run_wet_render(
    output_dir: Path,
    fixtures: list[Path],
    native_validation: dict[str, Any],
    render_probe: dict[str, Any],
) -> dict[str, Any]:
    render_dir = output_dir / "wet-renders"
    report_path = output_dir / "wet-render-report.json"
    report: dict[str, Any] = {
        "status": "skipped",
        "reason": "",
        "report_path": str(report_path),
        "render_dir": str(render_dir),
        "fixture": "",
        "renders": [],
        "tools": {
            "node": shutil.which("node") or "",
            "sounddevice": bool(importlib.util.find_spec("sounddevice")),
            "soundcard": bool(importlib.util.find_spec("soundcard")),
        },
    }
    if os.name != "nt":
        report["reason"] = "Wet rendering uses Windows Audio devices and was skipped on this platform."
        _write_json(report_path, report)
        return report
    if native_validation.get("status") not in {"ok", "partial"}:
        report["reason"] = "Native presets were not validated, so wet rendering was skipped."
        _write_json(report_path, report)
        return report
    if render_probe.get("status") not in {"ready", "ready_with_loopback"}:
        report["reason"] = f"Render probe is not ready: {render_probe.get('reason', 'unknown')}"
        _write_json(report_path, report)
        return report
    if not (report["tools"]["node"] and report["tools"]["sounddevice"] and report["tools"]["soundcard"]):
        report["reason"] = "Node, sounddevice, and soundcard are required for live wet rendering."
        _write_json(report_path, report)
        return report
    routes = [route for route in native_validation.get("routes") or [] if route.get("path")]
    if not routes:
        report["reason"] = "No native preset payloads were available to render."
        _write_json(report_path, report)
        return report
    fixture = next((path for path in fixtures if path.name == "di_single_notes.wav"), fixtures[0] if fixtures else None)
    if fixture is None or not fixture.is_file():
        report["reason"] = "No generated dry DI fixture was available to render."
        _write_json(report_path, report)
        return report

    try:
        import numpy as np  # type: ignore[import-not-found]
        import soundcard as sc  # type: ignore[import-not-found]
        import sounddevice as sd  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        report["reason"] = f"Python audio package import failed: {exc}"
        _write_json(report_path, report)
        return report

    output_device = _find_sounddevice_output(sd, "Line (Voicemod")
    loopback_speaker = _find_soundcard_speaker(sc, "Realtek Digital Output")
    if output_device is None or loopback_speaker is None:
        missing = []
        if output_device is None:
            missing.append("Voicemod Line output")
        if loopback_speaker is None:
            missing.append("Realtek Digital Output loopback")
        report["reason"] = "Missing live audio route: " + ", ".join(missing)
        _write_json(report_path, report)
        return report

    render_dir.mkdir(parents=True, exist_ok=True)
    for old in render_dir.glob("*.wav"):
        old.unlink()
    fixture_samples, sample_rate = _read_wav_mono(fixture)
    report["fixture"] = str(fixture)
    report["playback_output_device"] = output_device["name"]
    report["playback_output_index"] = output_device["index"]
    report["capture_loopback_speaker"] = str(loopback_speaker.name)
    max_renders = int(os.environ.get("FEEDFORGE_TONE_LAB_MAX_WET_RENDERS", "5") or "5")
    for route in routes[:max(1, max_renders)]:
        preset_path = Path(str(route["path"]))
        tone_key = str(route.get("tone_key") or preset_path.stem)
        out_path = render_dir / f"{_safe_filename(tone_key)}__{fixture.stem}.wav"
        render = _render_one_wet_route(
            preset_path=preset_path,
            fixture_samples=fixture_samples,
            sample_rate=sample_rate,
            playback_device_index=int(output_device["index"]),
            loopback_speaker=loopback_speaker,
            output_path=out_path,
            np=np,
            sd=sd,
            sc=sc,
        )
        render.update(
            {
                "tone_key": tone_key,
                "preset_id": route.get("preset_id"),
                "preset_path": str(preset_path),
                "fixture": str(fixture),
                "output_path": str(out_path),
            }
        )
        report["renders"].append(render)

    failures = [render for render in report["renders"] if render.get("status") != "ok"]
    successes = [render for render in report["renders"] if render.get("status") == "ok"]
    if successes and not failures:
        report["status"] = "ok"
        report["reason"] = "Dry DI fixtures were rendered through the installed native Rig Builder audio chain."
    elif successes:
        report["status"] = "partial"
        report["reason"] = "Some wet renders completed, but at least one route failed."
    else:
        report["status"] = "failed"
        report["reason"] = "No wet renders completed successfully."
    _write_json(report_path, report)
    return report


def _render_one_wet_route(
    *,
    preset_path: Path,
    fixture_samples: Any,
    sample_rate: int,
    playback_device_index: int,
    loopback_speaker: Any,
    output_path: Path,
    np: Any,
    sd: Any,
    sc: Any,
) -> dict[str, Any]:
    addon = Path(r"C:\Program Files\feedback\current\resources\app.asar.unpacked\build\Release\slopsmith_audio.node")
    if not addon.is_file():
        return {"status": "failed", "reason": "Native audio addon is missing."}
    script = _native_wet_render_script(addon)
    try:
        proc = subprocess.Popen(
            ["node", "-", str(preset_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        return {"status": "failed", "reason": f"Could not start native audio host: {exc}"}
    assert proc.stdin is not None
    proc.stdin.write(script)
    proc.stdin.close()
    ready = False
    assert proc.stdout is not None
    started = time.monotonic()
    while time.monotonic() - started < 12:
        line = proc.stdout.readline()
        if line.strip() == "READY":
            ready = True
            break
        if proc.poll() is not None:
            break
    if not ready:
        stderr = _finish_node_process(proc)
        return {"status": "failed", "reason": "Native audio host did not become ready.", "stderr": stderr[-4000:]}

    recorded: dict[str, Any] = {"chunks": [], "error": ""}
    stop_event = threading.Event()
    record_seconds = max(1.0, len(fixture_samples) / sample_rate) + 3.0

    def record_loop() -> None:
        try:
            microphone = sc.get_microphone(id=str(loopback_speaker.name), include_loopback=True)
            with microphone.recorder(samplerate=sample_rate, channels=2) as recorder:
                end_at = time.monotonic() + record_seconds
                while not stop_event.is_set() and time.monotonic() < end_at:
                    recorded["chunks"].append(recorder.record(numframes=2048))
        except Exception as exc:  # noqa: BLE001
            recorded["error"] = str(exc)

    thread = threading.Thread(target=record_loop, daemon=True)
    thread.start()
    try:
        time.sleep(1.0)
        stereo = np.column_stack([fixture_samples, fixture_samples])
        sd.play(stereo, samplerate=sample_rate, device=playback_device_index, blocking=True)
        time.sleep(1.0)
    except Exception as exc:  # noqa: BLE001
        stop_event.set()
        thread.join(timeout=4)
        stderr = _finish_node_process(proc)
        return {"status": "failed", "reason": f"Fixture playback failed: {exc}", "stderr": stderr[-4000:]}
    stop_event.set()
    thread.join(timeout=4)
    stderr = _finish_node_process(proc)
    if recorded.get("error"):
        return {"status": "failed", "reason": f"Loopback capture failed: {recorded['error']}", "stderr": stderr[-4000:]}
    chunks = recorded.get("chunks") or []
    if not chunks:
        return {"status": "failed", "reason": "Loopback capture returned no samples.", "stderr": stderr[-4000:]}

    captured = np.concatenate(chunks, axis=0)
    if captured.ndim == 2:
        captured_mono = captured.mean(axis=1)
    else:
        captured_mono = captured
    peak = float(np.max(np.abs(captured_mono))) if captured_mono.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(captured_mono)))) if captured_mono.size else 0.0
    _write_wav(output_path, [float(sample) for sample in captured_mono])
    return {
        "status": "ok" if peak > 0.001 else "silent",
        "reason": "Captured wet output from the live native audio engine." if peak > 0.001 else "Captured file was near silence.",
        "duration_seconds": captured_mono.size / sample_rate if sample_rate else 0,
        "frames": int(captured_mono.size),
        "rms_db": _linear_to_db(rms),
        "peak_db": _linear_to_db(peak),
        "stderr_tail": stderr[-4000:],
    }


def _finish_node_process(proc: subprocess.Popen[str]) -> str:
    try:
        proc.terminate()
        _, stderr = proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        _, stderr = proc.communicate(timeout=5)
    return stderr or ""


def _native_wet_render_script(addon: Path) -> str:
    addon_js = str(addon).replace("\\", "/")
    return f"""
const fs = require('fs');
const audio = require({json.dumps(addon_js)});
const presetPath = process.argv[2];
function readJson(file) {{
  let text = fs.readFileSync(file, 'utf8');
  if (text.charCodeAt(0) === 0xFEFF) text = text.slice(1);
  return JSON.parse(text);
}}
(async () => {{
  const payload = readJson(presetPath);
  audio.init();
  const device = await audio.setDevice({{
    inputType: 'Windows Audio',
    inputDevice: 'Microphone (Voicemod Virtual Audio Device (WDM))',
    outputType: 'Windows Audio',
    outputDevice: 'Realtek Digital Output (Realtek(R) Audio)',
    sampleRate: {SAMPLE_RATE},
    bufferSize: 256,
  }});
  if (!device || device.success === false) {{
    throw new Error('setDevice failed: ' + JSON.stringify(device));
  }}
  try {{ await audio.setInputChannel(0); }} catch (_) {{}}
  try {{ await audio.setMonitorMute(false); }} catch (_) {{}}
  try {{ await audio.setMonitorKill(false); }} catch (_) {{}}
  const loadResult = await audio.loadPreset(JSON.stringify(payload.native_preset));
  if (!loadResult || loadResult.success === false) {{
    throw new Error('loadPreset failed: ' + JSON.stringify(loadResult));
  }}
  await audio.startAudio();
  console.log('READY');
  setTimeout(() => {{
    try {{ audio.stopAudio(); audio.clearChain(); audio.shutdown(); }} catch (_) {{}}
    process.exit(0);
  }}, 30000);
}})().catch(error => {{
  try {{ audio.shutdown(); }} catch (_) {{}}
  console.error(error && error.stack || error);
  process.exit(1);
}});
"""


def _find_sounddevice_output(sd: Any, name_fragment: str) -> dict[str, Any] | None:
    hostapis = sd.query_hostapis()
    devices = sd.query_devices()
    for index, device in enumerate(devices):
        hostapi_name = str(hostapis[int(device.get("hostapi", 0))].get("name", ""))
        if (
            int(device.get("max_output_channels") or 0) > 0
            and name_fragment.lower() in str(device.get("name", "")).lower()
            and "wasapi" in hostapi_name.lower()
        ):
            return {"index": index, "name": str(device.get("name", "")), "hostapi": hostapi_name}
    return None


def _find_soundcard_speaker(sc: Any, name_fragment: str) -> Any | None:
    for speaker in sc.all_speakers():
        if name_fragment.lower() in str(speaker.name).lower():
            return speaker
    return None


def _read_wav_mono(path: Path) -> tuple[Any, int]:
    try:
        import numpy as np  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"numpy is required to read WAV fixtures: {exc}") from exc
    try:
        from scipy.io import wavfile  # type: ignore[import-not-found]

        sample_rate, samples = wavfile.read(path)
        samples = np.asarray(samples)
        if samples.ndim > 1:
            samples = samples.mean(axis=1)
        if np.issubdtype(samples.dtype, np.floating):
            return samples.astype(np.float32), int(sample_rate)
        if np.issubdtype(samples.dtype, np.integer):
            max_value = float(np.iinfo(samples.dtype).max)
            return (samples.astype(np.float32) / max_value), int(sample_rate)
    except Exception:
        pass
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        sample_width = handle.getsampwidth()
        sample_rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if sample_width != 2:
        raise ValueError(f"Unsupported WAV sample width: {sample_width}")
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape((-1, channels)).mean(axis=1)
    return samples, sample_rate


def compare_wav_tone(reference_path: Path, candidate_path: Path, *, output_json: Path | None = None) -> dict[str, Any]:
    reference_path = Path(reference_path)
    candidate_path = Path(candidate_path)
    try:
        import numpy as np  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"numpy is required to compare WAV files: {exc}") from exc

    reference, reference_rate = _read_wav_mono(reference_path)
    candidate, candidate_rate = _read_wav_mono(candidate_path)
    if reference_rate != candidate_rate:
        raise ValueError(f"Sample rates differ: {reference_rate} vs {candidate_rate}")

    reference = _trim_to_signal(np.asarray(reference, dtype=np.float32))
    candidate = _trim_to_signal(np.asarray(candidate, dtype=np.float32))
    compared = min(reference.size, candidate.size)
    if compared <= 0:
        result = {
            "status": "failed",
            "reason": "One or both WAV files contained no samples.",
            "reference_path": str(reference_path),
            "candidate_path": str(candidate_path),
        }
        if output_json:
            _write_json(output_json, result)
        return result

    reference = reference[:compared]
    candidate = candidate[:compared]
    reference_rms = _rms_np(reference)
    candidate_rms = _rms_np(candidate)
    gain_match_db = _linear_to_db(reference_rms / candidate_rms) if candidate_rms > 0 else 0.0
    candidate_matched = candidate * (10.0 ** (gain_match_db / 20.0))
    error = reference - candidate_matched
    error_rms = _rms_np(error)
    corr = _correlation_np(reference, candidate_matched)
    band_reference = _band_energy_summary(reference, reference_rate)
    band_candidate = _band_energy_summary(candidate_matched, candidate_rate)
    band_diffs = {band: band_candidate[band] - band_reference[band] for band in band_reference}
    band_mae = sum(abs(value) for value in band_diffs.values()) / max(1, len(band_diffs))
    result = {
        "status": "ok",
        "reference_path": str(reference_path),
        "candidate_path": str(candidate_path),
        "sample_rate": reference_rate,
        "compared_duration": compared / reference_rate,
        "reference_rms_db": _linear_to_db(reference_rms),
        "candidate_rms_db_before_match": _linear_to_db(candidate_rms),
        "candidate_gain_match_db": gain_match_db,
        "waveform_correlation": corr,
        "error_snr_db": _linear_to_db(reference_rms / error_rms) if error_rms > 0 else 120.0,
        "band_mae_db": band_mae,
        "band_diffs_candidate_minus_reference_db": band_diffs,
    }
    if output_json:
        _write_json(output_json, result)
    return result


def _trim_to_signal(samples: Any, threshold: float = 1e-4) -> Any:
    import numpy as np  # type: ignore[import-not-found]

    if samples.size == 0:
        return samples
    active = np.flatnonzero(np.abs(samples) > threshold)
    if active.size == 0:
        return samples
    pad = min(1024, active[0])
    start = max(0, int(active[0]) - pad)
    end = min(samples.size, int(active[-1]) + pad + 1)
    return samples[start:end]


def _rms_np(samples: Any) -> float:
    import numpy as np  # type: ignore[import-not-found]

    return float(np.sqrt(np.mean(np.square(samples)))) if samples.size else 0.0


def _correlation_np(a: Any, b: Any) -> float:
    import numpy as np  # type: ignore[import-not-found]

    if a.size == 0 or b.size == 0:
        return 0.0
    a0 = a - float(np.mean(a))
    b0 = b - float(np.mean(b))
    denom = float(np.linalg.norm(a0) * np.linalg.norm(b0))
    return float(np.dot(a0, b0) / denom) if denom > 0 else 0.0


def _band_energy_summary(samples: Any, sample_rate: int) -> dict[str, float]:
    import numpy as np  # type: ignore[import-not-found]

    if samples.size == 0:
        return {label: -999.0 for label, _lo, _hi in _COMPARISON_BANDS}
    window = np.hanning(samples.size)
    spectrum = np.fft.rfft(samples * window)
    freqs = np.fft.rfftfreq(samples.size, d=1.0 / sample_rate)
    power = np.square(np.abs(spectrum))
    summary: dict[str, float] = {}
    for label, lo, hi in _COMPARISON_BANDS:
        mask = (freqs >= lo) & (freqs < hi)
        energy = float(np.sqrt(np.mean(power[mask]))) if np.any(mask) else 0.0
        summary[label] = _linear_to_db(energy)
    return summary


_COMPARISON_BANDS = (
    ("80-250Hz_db", 80.0, 250.0),
    ("250-500Hz_db", 250.0, 500.0),
    ("500-1000Hz_db", 500.0, 1000.0),
    ("1000-2000Hz_db", 1000.0, 2000.0),
    ("2000-4000Hz_db", 2000.0, 4000.0),
    ("4000-8000Hz_db", 4000.0, 8000.0),
)


def _safe_filename(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "_" for char in value).strip("._") or "tone"


def _linear_to_db(value: float) -> float:
    return 20.0 * math.log10(value) if value > 0 else -999.0


def _run_rocksmith_reference_probe(output_dir: Path) -> dict[str, Any]:
    report_path = output_dir / "rocksmith-reference-probe.json"
    rocksmith_dir = _find_rocksmith_dir()
    exe = rocksmith_dir / "Rocksmith2014.exe" if rocksmith_dir else None
    rocksmith_ini = rocksmith_dir / "Rocksmith.ini" if rocksmith_dir else None
    rs_asio_ini = rocksmith_dir / "RS_ASIO.ini" if rocksmith_dir else None
    asio_drivers = _installed_asio_drivers()
    devices = _python_audio_devices()
    has_voicemod_input = any("voicemod" in device["name"].lower() and device["inputs"] for device in devices)
    has_voicemod_output = any("voicemod" in device["name"].lower() and device["outputs"] for device in devices)
    has_loopback_output = any("realtek" in device["name"].lower() and device["outputs"] for device in devices)
    has_flexasio = any(driver.lower() == "flexasio" for driver in asio_drivers)
    config = {
        "rocksmith_ini": _read_ini_values(rocksmith_ini, "Audio") if rocksmith_ini else {},
        "rs_asio_ini": _read_ini_values(rs_asio_ini, "Config", "Asio.Output", "Asio.Input.0") if rs_asio_ini else {},
    }
    flexasio_toml = output_dir / "rocksmith-flexasio-capture.toml"
    flexasio_toml.write_text(
        "\n".join(
            [
                'backend = "Windows WASAPI"',
                'bufferSizeSamples = 192',
                "",
                "[input]",
                'device = "Microphone (Voicemod Virtual Audio Device (WDM))"',
                "channels = 2",
                "",
                "[output]",
                'device = "Realtek Digital Output (Realtek(R) Audio)"',
                "channels = 2",
                "",
            ]
        ),
        encoding="utf-8",
    )
    recommended_rs_asio = {
        "Config": {
            "EnableWasapiOutputs": "1",
            "EnableWasapiInputs": "0",
            "EnableAsio": "1",
        },
        "Asio.Output": {
            "Driver": "",
        },
        "Asio.Input.0": {
            "Driver": "FlexASIO",
            "Channel": "0",
        },
    }
    prerequisites = {
        "rocksmith_exe": bool(exe and exe.is_file()),
        "rs_asio_ini": bool(rs_asio_ini and rs_asio_ini.is_file()),
        "flexasio_driver": has_flexasio,
        "voicemod_input": has_voicemod_input,
        "voicemod_output": has_voicemod_output,
        "loopback_output": has_loopback_output,
    }
    missing = [name for name, ok in prerequisites.items() if not ok]
    status = "ready_for_rocksmith_capture" if not missing else "blocked"
    reason = (
        "This PC has the pieces needed to attempt Rocksmith DI playback and output capture."
        if not missing
        else "Rocksmith reference capture is not automated yet because prerequisites are missing: " + ", ".join(missing)
    )
    report = {
        "status": status,
        "reason": reason,
        "report_path": str(report_path),
        "rocksmith_dir": str(rocksmith_dir or ""),
        "rocksmith_exe": str(exe or ""),
        "rocksmith_ini": str(rocksmith_ini or ""),
        "rs_asio_ini": str(rs_asio_ini or ""),
        "asio_drivers": asio_drivers,
        "audio_devices": devices,
        "prerequisites": prerequisites,
        "config": config,
        "generated_flexasio_toml": str(flexasio_toml),
        "recommended_rs_asio": recommended_rs_asio,
        "capture_plan": [
            "Install/register FlexASIO so RS_ASIO can consume a WASAPI virtual input as ASIO.",
            "Configure FlexASIO input to Voicemod Microphone and RS_ASIO Input.0 Driver to FlexASIO.",
            "Launch Rocksmith windowed with the target PSARC installed and select the target song/tone.",
            "Play FeedForge's dry DI fixture to Voicemod Line and capture Rocksmith output loopback.",
            "Compare Rocksmith wet WAV against FeedBack wet WAV using loudness, EQ, transient, and spectral metrics.",
        ],
    }
    _write_json(report_path, report)
    return report


def _find_rocksmith_dir() -> Path | None:
    candidates = [
        Path(r"E:\Games\RockSmith 2014\Rocksmith 2014 Edition - Remastered"),
        Path(r"C:\Program Files (x86)\Steam\steamapps\common\Rocksmith2014"),
        Path(r"C:\Program Files\Steam\steamapps\common\Rocksmith2014"),
    ]
    for candidate in candidates:
        if (candidate / "Rocksmith2014.exe").is_file():
            return candidate
    return None


def _installed_asio_drivers() -> list[str]:
    if os.name != "nt":
        return []
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return []
    drivers: set[str] = set()
    for root, subkey in (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\ASIO"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\ASIO"),
    ):
        try:
            with winreg.OpenKey(root, subkey) as key:
                index = 0
                while True:
                    try:
                        drivers.add(str(winreg.EnumKey(key, index)))
                        index += 1
                    except OSError:
                        break
        except OSError:
            continue
    return sorted(drivers)


def _python_audio_devices() -> list[dict[str, Any]]:
    try:
        import sounddevice as sd  # type: ignore[import-not-found]
    except Exception:
        return []
    devices: list[dict[str, Any]] = []
    try:
        hostapis = sd.query_hostapis()
        for index, device in enumerate(sd.query_devices()):
            inputs = int(device.get("max_input_channels") or 0)
            outputs = int(device.get("max_output_channels") or 0)
            if inputs or outputs:
                devices.append(
                    {
                        "index": index,
                        "name": str(device.get("name") or ""),
                        "hostapi": str(hostapis[int(device.get("hostapi", 0))].get("name", "")),
                        "inputs": inputs,
                        "outputs": outputs,
                    }
                )
    except Exception:
        return devices
    return devices


def _read_ini_values(path: Path | None, *sections: str) -> dict[str, dict[str, str]]:
    if path is None or not path.is_file():
        return {}
    parser = configparser.ConfigParser()
    parser.optionxform = str
    try:
        parser.read(path, encoding="utf-8")
    except configparser.Error:
        return {}
    values: dict[str, dict[str, str]] = {}
    for section in sections:
        if parser.has_section(section):
            values[section] = {key: str(value) for key, value in parser.items(section)}
    return values


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _native_load_script(addon: Path) -> str:
    addon_js = str(addon).replace("\\", "/")
    return f"""
const fs = require('fs');
const path = require('path');
const audio = require({json.dumps(addon_js)});
const dir = process.argv[2];
function readJson(file) {{
  let text = fs.readFileSync(file, 'utf8');
  if (text.charCodeAt(0) === 0xFEFF) text = text.slice(1);
  return JSON.parse(text);
}}
function summarizeChain(chain) {{
  return (chain || []).map((s, i) => ({{
    i,
    id: s.id,
    type: s.type,
    name: s.name,
    path: s.path,
    bypassed: s.bypassed,
    slot: s.slot,
    rs_gear: s.rs_gear,
  }}));
}}
(async () => {{
  const report = [];
  audio.init();
  for (const file of fs.readdirSync(dir).filter(f => f.endsWith('.json')).sort()) {{
    const payload = readJson(path.join(dir, file));
    const preset = JSON.stringify(payload.native_preset);
    try {{
      const loadResult = await audio.loadPreset(preset);
      const state = audio.getChainState();
      report.push({{
        file,
        presetName: payload.name,
        loadResult,
        expectedStages: payload.native_preset.chain.length,
        loadedStages: Array.isArray(state) ? state.length : 0,
        chain: summarizeChain(state),
      }});
    }} catch (error) {{
      report.push({{
        file,
        presetName: payload.name,
        error: error && error.message || String(error),
        expectedStages: payload.native_preset.chain.length,
      }});
    }}
    try {{ audio.clearChain(); }} catch (_) {{}}
  }}
  audio.shutdown();
  console.log(JSON.stringify(report));
}})().catch(error => {{
  try {{ audio.shutdown(); }} catch (_) {{}}
  console.error(error && error.stack || error);
  process.exit(1);
}});
"""


def _audit_seeded_database(db_path: Path, song_key: str) -> dict[str, Any]:
    audit: dict[str, Any] = {"mappings": 0, "preset_pieces": 0, "issues": []}
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        return {**audit, "issues": [{"level": "error", "message": f"database open failed: {exc}"}]}
    try:
        rows = conn.execute(
            "SELECT tm.tone_key, tm.preset_id FROM tone_mappings tm WHERE tm.filename = ? ORDER BY tm.tone_key",
            (song_key,),
        ).fetchall()
        audit["mappings"] = len(rows)
        for row in rows:
            pieces = conn.execute(
                "SELECT slot, rs_gear_type, kind, file, tone3000_id, vst_path FROM preset_pieces "
                "WHERE preset_id = ? ORDER BY slot_order",
                (row["preset_id"],),
            ).fetchall()
            audit["preset_pieces"] += len(pieces)
            if not pieces:
                audit["issues"].append({"level": "warning", "tone_key": row["tone_key"], "message": "preset has no pieces"})
            for piece in pieces:
                issue = _piece_issue(db_path.parent, row["tone_key"], piece)
                if issue:
                    audit["issues"].append(issue)
        return audit
    except sqlite3.Error as exc:
        return {**audit, "issues": [{"level": "error", "message": f"database read failed: {exc}"}]}
    finally:
        conn.close()


def _piece_issue(config_dir: Path, tone_key: str, piece: sqlite3.Row) -> dict[str, Any] | None:
    kind = str(piece["kind"] or "none")
    file = str(piece["file"] or "")
    vst_path = str(piece["vst_path"] or "")
    tone3000_id = piece["tone3000_id"]
    base = {"level": "warning", "tone_key": tone_key, "slot": str(piece["slot"] or ""), "gear": str(piece["rs_gear_type"] or ""), "kind": kind}
    if kind == "none":
        return {**base, "message": "unmapped gear stage"}
    if kind == "vst" and (not vst_path or not Path(vst_path).exists()):
        return {**base, "message": "VST asset is missing", "asset": vst_path}
    if kind in {"ir", "rs_ir"} and (not file or not (config_dir / "nam_irs" / file).exists()):
        return {**base, "message": "cab IR asset is missing", "asset": file}
    if kind == "nam" and not file:
        if tone3000_id:
            return {
                **base,
                "message": "NAM stage is pending Tone3000 download",
                "tone3000_id": tone3000_id,
            }
        return {**base, "message": "NAM stage has no file or tone3000 id"}
    return None


def _stage_asset_info(kind: str, asset: str, config_dir: Path | None) -> dict[str, Any] | None:
    if not asset:
        return None
    exists: bool | None = None
    path = ""
    if kind == "vst":
        # The preview only exposes the filename. Full VST path is checked in the DB audit.
        exists = None
    elif kind == "nam" and config_dir is not None:
        full = config_dir / "nam_models" / asset
        exists = full.exists()
        path = str(full)
    elif kind in {"ir", "rs_ir"} and config_dir is not None:
        full = config_dir / "nam_irs" / asset
        exists = full.exists()
        path = str(full)
    return {"kind": kind, "asset": asset, "path": path, "exists": exists}


def _build_report(
    input_psarc: Path,
    output_dir: Path,
    fixtures: list[Path],
    feedpak_paths: list[Path],
    seed_result: SeedResult | None,
    route_summary: dict[str, Any],
    native_validation: dict[str, Any],
    render_probe: dict[str, Any],
    wet_render: dict[str, Any],
    rocksmith_reference_probe: dict[str, Any],
    errors: list[str],
) -> dict[str, Any]:
    rendered = wet_render.get("status") in {"ok", "partial"}
    return {
        "input_path": str(input_psarc),
        "output_dir": str(output_dir),
        "feedpak_paths": [str(path) for path in feedpak_paths],
        "fixtures": [{"path": str(path), "purpose": _fixture_purpose(path.name)} for path in fixtures],
        "conversion_ok": bool(feedpak_paths),
        "seed_ok": seed_result is not None,
        "route_summary": route_summary,
        "native_validation": native_validation,
        "render_probe": render_probe,
        "wet_render": wet_render,
        "rocksmith_reference_probe": rocksmith_reference_probe,
        "audio_render_status": "rendered_native_live_chain" if rendered else "pending_feedback_host_render",
        "evidence_boundary": (
            "This run verifies extraction, conversion, generated dry DI inputs, Rig Builder seeding, "
            "route completeness, asset availability, and native chain loading when FeedBack is running. "
            + (
                "It also pushes generated dry DI WAVs through the live native Rig Builder audio path and captures wet WAVs. "
                if rendered
                else "It does not yet push the dry DI WAV through the live guitar input path. "
            )
            + "A true Rocksmith identity claim still requires a Rocksmith-rendered reference clip for comparison."
        ),
        "next_step": (
            "Compare the captured wet WAVs against Rocksmith-rendered reference audio for the same DI phrases, "
            "then score loudness, EQ curve, transient envelope, and spectral distance. No Rig Builder repository edit is required."
            if rendered
            else "Add a file-input bridge for the native audio engine or use a virtual loopback device so these dry DI fixtures "
            "can be rendered through the loaded chain and captured as wet WAVs. No Rig Builder repository edit is required."
        ),
        "errors": errors,
    }


def _fixture_purpose(filename: str) -> str:
    return {
        "di_single_notes.wav": "note attack, sustain, gain, and noise gate behavior",
        "di_chord_stabs.wav": "chord voicing, compression, modulation, and cab body",
        "di_palm_mute_bursts.wav": "high-gain palm mute response and low-end tightness",
        "di_sweep_noise.wav": "EQ, cabinet filtering, and broad frequency sanity checks",
    }.get(filename, "dry DI test input")


def _format_markdown_report(report: dict[str, Any]) -> str:
    summary = report["route_summary"]
    lines = [
        "# FeedForge Tone Lab Report",
        "",
        f"Input: `{report['input_path']}`",
        f"Output: `{report['output_dir']}`",
        "",
        "## Summary",
        f"- Conversion: {'ok' if report['conversion_ok'] else 'failed'}",
        f"- Rig Builder seed: {'ok' if report['seed_ok'] else 'failed'}",
        f"- Arrangements: {summary.get('arrangements', 0)}",
        f"- Tone definitions: {summary.get('tone_definitions', 0)}",
        f"- Mapped gear items: {summary.get('mapped_gear', 0)}",
        f"- Routes: {json.dumps(summary.get('routes', {}), ensure_ascii=False)}",
        f"- Stages: {json.dumps(summary.get('stages', {}), ensure_ascii=False)}",
        f"- Native validation: {report.get('native_validation', {}).get('status', 'skipped')}",
        "",
        "## Generated DI Fixtures",
    ]
    for fixture in report["fixtures"]:
        lines.append(f"- `{fixture['path']}`: {fixture['purpose']}")
    lines.extend(["", "## FeedPak Outputs"])
    if report["feedpak_paths"]:
        lines.extend(f"- `{path}`" for path in report["feedpak_paths"])
    else:
        lines.append("- none")
    native = report.get("native_validation") or {}
    lines.extend(["", "## Native Engine Validation"])
    lines.append(f"- Status: {native.get('status', 'skipped')}")
    if native.get("reason"):
        lines.append(f"- Reason: {native['reason']}")
    if native.get("backend_url"):
        lines.append(f"- FeedBack backend: `{native['backend_url']}`")
    if native.get("native_presets_dir"):
        lines.append(f"- Native presets: `{native['native_presets_dir']}`")
    if native.get("native_load_report"):
        lines.append(f"- Load report: `{native['native_load_report']}`")
    if native.get("loaded_routes") is not None:
        lines.append(f"- Loaded routes: {native.get('loaded_routes')} routes / {native.get('loaded_stages')} stages")
    for route in native.get("routes") or []:
        lines.append(f"- `{route.get('tone_key')}` preset {route.get('preset_id')}: {route.get('stages')} stages")
    render_probe = report.get("render_probe") or {}
    loopback = render_probe.get("virtual_loopback") or {}
    lines.extend(["", "## Wet Render Probe"])
    lines.append(f"- Status: {render_probe.get('status', 'unknown')}")
    if render_probe.get("reason"):
        lines.append(f"- Reason: {render_probe['reason']}")
    if render_probe.get("report_path"):
        lines.append(f"- Probe report: `{render_probe['report_path']}`")
    lines.append(f"- Native file/offline render API: {bool(render_probe.get('file_input_api') or render_probe.get('offline_render_api'))}")
    lines.append(f"- Virtual loopback: {loopback.get('status', 'unknown')}")
    if loopback.get("inputs"):
        lines.append(f"- Loopback inputs: {json.dumps(loopback.get('inputs'), ensure_ascii=False)}")
    if loopback.get("outputs"):
        lines.append(f"- Loopback outputs: {json.dumps(loopback.get('outputs'), ensure_ascii=False)}")
    wet = report.get("wet_render") or {}
    lines.extend(["", "## Wet Render Output"])
    lines.append(f"- Status: {wet.get('status', 'skipped')}")
    if wet.get("reason"):
        lines.append(f"- Reason: {wet['reason']}")
    if wet.get("report_path"):
        lines.append(f"- Render report: `{wet['report_path']}`")
    if wet.get("fixture"):
        lines.append(f"- Fixture: `{wet['fixture']}`")
    if wet.get("playback_output_device"):
        lines.append(f"- Playback output: `{wet['playback_output_device']}`")
    if wet.get("capture_loopback_speaker"):
        lines.append(f"- Capture loopback: `{wet['capture_loopback_speaker']}`")
    for render in wet.get("renders") or []:
        lines.append(
            "- "
            f"`{render.get('tone_key')}` -> `{render.get('output_path')}` "
            f"status={render.get('status')} peak={render.get('peak_db')} dBFS rms={render.get('rms_db')} dBFS"
        )
    rocksmith = report.get("rocksmith_reference_probe") or {}
    prereqs = rocksmith.get("prerequisites") or {}
    lines.extend(["", "## Rocksmith Reference Probe"])
    lines.append(f"- Status: {rocksmith.get('status', 'skipped')}")
    if rocksmith.get("reason"):
        lines.append(f"- Reason: {rocksmith['reason']}")
    if rocksmith.get("report_path"):
        lines.append(f"- Probe report: `{rocksmith['report_path']}`")
    if rocksmith.get("rocksmith_exe"):
        lines.append(f"- Rocksmith: `{rocksmith['rocksmith_exe']}`")
    if rocksmith.get("generated_flexasio_toml"):
        lines.append(f"- Generated FlexASIO template: `{rocksmith['generated_flexasio_toml']}`")
    if prereqs:
        lines.append(f"- Prerequisites: {json.dumps(prereqs, ensure_ascii=False)}")
    if rocksmith.get("asio_drivers"):
        lines.append(f"- Installed ASIO drivers: {', '.join(rocksmith.get('asio_drivers') or [])}")
    lines.extend(["", "## Route Issues"])
    issues = list(summary.get("issues") or [])
    db_issues = list((summary.get("database") or {}).get("issues") or [])
    if not issues and not db_issues and report["seed_ok"]:
        lines.append("- none found in the seeded route audit")
    for issue in issues + db_issues:
        lines.append(f"- {issue.get('level', 'warning')}: {issue.get('message', '')} {json.dumps(issue, ensure_ascii=False)}")
    if report["errors"]:
        lines.extend(["", "## Errors"])
        lines.extend(f"- {error}" for error in report["errors"])
    lines.extend(
        [
            "",
            "## What This Proves",
            report["evidence_boundary"],
            "",
            "## Next Render Step",
            report["next_step"],
            "",
        ]
    )
    return "\n".join(lines)
