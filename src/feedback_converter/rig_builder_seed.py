from __future__ import annotations

import json
import math
import os
import sqlite3
import urllib.error
import urllib.request
import wave
import base64
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .converter import _extract_metadata, _find_sng_entries, _song_tones_to_feedpak
from .inspector import _load_json_file, _rig_builder_data_dir, _rig_builder_db
from .psarc_format.psarc import PSARC
from .psarc_format.sng import Song


@dataclass(frozen=True)
class SeededTone:
    tone_key: str
    status: str
    stages: int
    missing: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SeedResult:
    db_path: Path
    song_key: str
    tones: list[SeededTone]


GEAR_SLOTS = (
    ("PrePedal1", "pre_pedal"),
    ("PrePedal2", "pre_pedal"),
    ("PrePedal3", "pre_pedal"),
    ("PrePedal4", "pre_pedal"),
    ("Amp", "amp"),
    ("PostPedal1", "post_pedal"),
    ("PostPedal2", "post_pedal"),
    ("PostPedal3", "post_pedal"),
    ("PostPedal4", "post_pedal"),
    ("Rack1", "rack"),
    ("Rack2", "rack"),
    ("Rack3", "rack"),
    ("Rack4", "rack"),
    ("Cabinet", "cabinet"),
)

VST_PARAM_RANGES: dict[str, dict[str, tuple[str, float, float]]] = {
    "mcompressor": {
        "Gain": ("linear", -24.0, 24.0),
        "Output gain": ("linear", -24.0, 24.0),
        "Threshold": ("linear", -80.0, 0.0),
        "Ratio": ("log", 1.0, 100.0),
        "Knee size": ("linear", 0.0, 100.0),
    },
    "studiocomp": {
        "Threshold": ("linear", -40.0, 0.0),
        "Ratio": ("linear", 1.0, 12.0),
        "Attack": ("linear", 0.0, 150.0),
        "Release": ("linear", 20.0, 500.0),
    },
    "mequalizer": {
        "Gain": ("linear", -24.0, 24.0),
        "Dry/Wet": ("linear", 0.0, 100.0),
        "Soft saturation": ("linear", 0.0, 100.0),
        **{f"Gain {i} (EQ {i})": ("linear", -24.0, 24.0) for i in range(1, 17)},
        **{f"Frequency {i} (EQ {i})": ("log", 20.0, 20000.0) for i in range(1, 17)},
        **{f"Q {i} (EQ {i})": ("log", 0.1, 100.0) for i in range(1, 17)},
    },
    "mtremolo": {"Rate": ("log", 0.01, 20.0)},
    "khs compressor": {
        "Threshold": ("linear", -40.0, 6.0),
        "Makeup gain": ("linear", -24.0, 24.0),
        "Ratio": ("log", 1.0, 100.0),
        "Attack": ("log", 1.0, 500.0),
        "Release": ("log", 1.0, 500.0),
    },
    "khs 3-band eq": {
        "Low Gain": ("linear", -24.0, 24.0),
        "Mid Gain": ("linear", -24.0, 24.0),
        "High Gain": ("linear", -24.0, 24.0),
        "Low Freq": ("log", 20.0, 1000.0),
        "High Freq": ("log", 1000.0, 20000.0),
    },
    "studioeq": {
        "BassFreq": ("log", 30.0, 300.0),
        "LoMidFreq": ("log", 120.0, 2000.0),
        "HiMidFreq": ("log", 400.0, 8000.0),
        "TrebleFreq": ("log", 1500.0, 16000.0),
        "LoMidQ": ("log", 0.4, 4.0),
        "HiMidQ": ("log", 0.4, 4.0),
    },
    "studiographiceq": {
        "BassFreq": ("log", 30.0, 400.0),
        "LoMidFreq": ("log", 75.0, 1000.0),
        "HiMidFreq": ("log", 800.0, 12500.0),
        "TrebleFreq": ("log", 2500.0, 20000.0),
    },
}

STEM_RANGE_ALIASES = {
    "hzx": "studiocomp",
    "lng": "studioeq",
    "g-550": "studiographiceq",
}


def seed_rig_builder_routes(input_psarc: Path, *, force: bool = True) -> SeedResult:
    input_psarc = Path(input_psarc)
    db_path = _rig_builder_db() or _default_rig_builder_db_path()
    data_dir = _rig_builder_data_dir()
    if db_path is None:
        raise FileNotFoundError("FeedBack Rig Builder database was not found.")
    if data_dir is None:
        raise FileNotFoundError("FeedBack Rig Builder data folder was not found.")

    with input_psarc.open("rb") as fh:
        content = PSARC(crypto=True).parse_stream(fh)
    metadata = _extract_metadata(content)
    song_key = input_psarc.with_suffix(".feedpak").name

    conn = sqlite3.connect(db_path)
    try:
        _ensure_schema(conn)
        seeded: list[SeededTone] = []
        seen_tone_keys: set[str] = set()
        for source_path, data in _find_sng_entries(content):
            try:
                song = Song.parse(data)
            except Exception:
                continue
            tone_data = _song_tones_to_feedpak(song, source_path, metadata)
            if not tone_data:
                continue
            for definition in (tone_data.get("tones") or {}).get("definitions") or []:
                if not isinstance(definition, dict):
                    continue
                tone_key = str(definition.get("Key") or definition.get("Name") or "").strip()
                if not tone_key:
                    continue
                if tone_key in seen_tone_keys:
                    continue
                seen_tone_keys.add(tone_key)
                if not force and _has_mapping(conn, song_key, tone_key):
                    continue
                seeded.append(_seed_definition(conn, data_dir, song_key, tone_key, definition))
        if any(tone.missing for tone in seeded):
            _enable_feedback_tone3000_fallback()
            if _download_pending_song_assignments(conn, song_key):
                seeded = _refresh_seeded_tone_statuses(conn, song_key, seeded)
        conn.commit()
        return SeedResult(db_path=db_path, song_key=song_key, tones=seeded)
    finally:
        conn.close()


def _seed_definition(
    conn: sqlite3.Connection,
    data_dir: Path,
    song_key: str,
    tone_key: str,
    definition: dict[str, Any],
) -> SeededTone:
    missing: list[str] = []
    pending: list[str] = []
    stages = _stages_from_definition(data_dir, definition, missing)
    if any(stage.get("slot") == "amp" and stage.get("kind") == "vst" for stage in stages):
        _ensure_unit_impulse_ir()
    for stage in stages:
        _reuse_existing_capture_assignment(conn, stage)
    _delete_mapping(conn, song_key, tone_key)
    if not stages:
        return SeededTone(
            tone_key=tone_key,
            status="skipped",
            stages=0,
            missing=[],
        )

    preset_name = f"{song_key}::{tone_key}"
    conn.execute(
        "INSERT INTO presets (name, model_file, ir_file, input_gain, output_gain, gate_threshold, settings_json) "
        "VALUES (?, '', '', 1.0, 1.0, -60.0, '{}')",
        (preset_name,),
    )
    preset_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

    model_file = ""
    ir_file = ""
    stage_count = 0
    for slot_order, stage in enumerate(stages):
        conn.execute(
            "INSERT INTO preset_pieces "
            "(preset_id, slot_order, slot, rs_gear_type, kind, file, params_json, tone3000_id, "
            "assigned_mode, bypassed, vst_path, vst_format, vst_state) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
            (
                preset_id,
                slot_order,
                stage["slot"],
                stage["gear"],
                stage["kind"],
                stage.get("file"),
                json.dumps(stage.get("params") or {}, ensure_ascii=False),
                stage.get("tone3000_id"),
                stage.get("assigned_mode") or "feedforge",
                stage.get("vst_path"),
                stage.get("vst_format"),
                json.dumps(stage.get("vst_state"), ensure_ascii=False) if stage.get("vst_state") else None,
            ),
        )
        stage_count += 1
        if stage["kind"] == "nam" and stage.get("tone3000_id") and not stage.get("file"):
            pending.append(stage["gear"])
        if not model_file and stage["kind"] == "nam" and stage.get("file"):
            model_file = stage["file"]
        if not ir_file and stage["kind"] in {"ir", "rs_ir"} and stage.get("file"):
            ir_file = stage["file"]

    conn.execute(
        "UPDATE presets SET model_file = ?, ir_file = ? WHERE id = ?",
        (model_file, ir_file, preset_id),
    )
    conn.execute(
        "INSERT INTO tone_mappings (filename, tone_key, preset_id) VALUES (?, ?, ?)",
        (song_key, tone_key, preset_id),
    )
    unresolved = missing + [f"{gear} (Tone3000 download pending)" for gear in pending]
    return SeededTone(
        tone_key=tone_key,
        status="partial" if unresolved else "ready",
        stages=stage_count,
        missing=unresolved,
    )


def _stages_from_definition(
    data_dir: Path,
    definition: dict[str, Any],
    missing: list[str],
) -> list[dict[str, Any]]:
    gear_list = definition.get("GearList") if isinstance(definition.get("GearList"), dict) else {}
    stages: list[dict[str, Any]] = []
    for gear_slot, slot_type in GEAR_SLOTS:
        gear = gear_list.get(gear_slot)
        if not isinstance(gear, dict):
            continue
        key = str(gear.get("Key") or gear.get("PedalKey") or gear.get("Type") or "").strip()
        if not key:
            continue
        params = gear.get("KnobValues") if isinstance(gear.get("KnobValues"), dict) else {}
        stage = _resolve_stage(data_dir, slot_type, key, params)
        if stage is None:
            missing.append(key)
            stage = {
                "slot": slot_type,
                "gear": key,
                "kind": "none",
                "file": None,
                "params": params,
                "tone3000_id": None,
                "vst_path": None,
                "vst_format": None,
                "vst_state": None,
            }
        stages.append(stage)
    return stages


def _resolve_stage(data_dir: Path, slot: str, gear_key: str, params: dict[str, Any]) -> dict[str, Any] | None:
    if slot == "amp":
        override_vst = _amp_override_vst(data_dir, gear_key)
        if override_vst:
            return _vst_stage(slot, gear_key, params, override_vst, data_dir)
        override_nam = _amp_override_nam(data_dir, gear_key, params)
        if override_nam:
            return override_nam
        vst = _resolve_vst(data_dir, gear_key)
        if vst:
            return _vst_stage(slot, gear_key, params, vst, data_dir)
        capture = _resolve_tone3000_capture(data_dir, slot, gear_key, params)
        if capture:
            return capture

    vst = _resolve_vst(data_dir, gear_key)
    if vst:
        return _vst_stage(slot, gear_key, params, vst, data_dir)
    if slot == "cabinet":
        cab = _resolve_cab_ir(data_dir, gear_key)
        if cab:
            return {
                "slot": slot,
                "gear": cab["gear"],
                "kind": cab["kind"],
                "file": cab["file"],
                "params": params,
                "vst_path": None,
                "vst_format": None,
                "vst_state": None,
            }
    capture = _resolve_tone3000_capture(data_dir, slot, gear_key, params)
    if capture:
        return capture
    return None


def _vst_stage(slot: str, gear_key: str, params: dict[str, Any], vst: Path, data_dir: Path) -> dict[str, Any]:
    vst_state = _build_vst_state(data_dir, gear_key, vst, params)
    amp_override = _case_insensitive_get(_amp_overrides(), gear_key) if slot == "amp" else None
    if isinstance(amp_override, dict) and str(amp_override.get("cab_sim") or "").lower() in {"off", "false", "0"}:
        vst_state = dict(vst_state or {"params": {}})
        vst_params = dict(vst_state.get("params") or {})
        vst_params["Cab Sim"] = 0.0
        vst_state["params"] = vst_params
    if slot == "amp" and (amp_override is None or isinstance(amp_override, dict)):
        vst_state = _opaque_vst_state(vst, "VST3", vst_state)
    assigned_mode = "manual_vst" if slot == "amp" else "feedforge"
    return {
        "slot": slot,
        "gear": gear_key,
        "kind": "vst",
        "file": None,
        "params": params,
        "assigned_mode": assigned_mode,
        "vst_path": str(vst),
        "vst_format": "VST3",
        "vst_state": vst_state,
    }


def _opaque_vst_state(vst_path: Path, vst_format: str, vst_state: dict[str, Any] | None) -> dict[str, Any]:
    wrapper = {
        "pluginPath": str(vst_path),
        "format": vst_format,
        "pluginState": json.dumps(vst_state or {"params": {}}, ensure_ascii=False),
    }
    payload = json.dumps(wrapper, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return {"opaque": base64.b64encode(payload).decode("ascii")}


def _amp_override_vst(data_dir: Path, gear_key: str) -> Path | None:
    override = _case_insensitive_get(_amp_overrides(), gear_key)
    if (
        not isinstance(override, dict)
        or override.get("enabled") is False
        or str(override.get("prefer") or "").lower() != "vst"
    ):
        return None
    plugin_root = data_dir.parent
    bundled = str(override.get("bundled") or "").strip("/\\")
    if bundled:
        candidate = plugin_root / bundled
        if candidate.exists():
            return candidate
    return _resolve_vst(data_dir, gear_key)


def _amp_override_nam(data_dir: Path, gear_key: str, params: dict[str, Any]) -> dict[str, Any] | None:
    override = _case_insensitive_get(_amp_overrides(), gear_key)
    if (
        not isinstance(override, dict)
        or override.get("enabled") is False
        or str(override.get("prefer") or "").lower() != "nam"
    ):
        return None
    spec = _select_amp_file_override(override, gear_key, params)
    if not isinstance(spec, dict):
        return None
    file_name = str(spec.get("file") or "").strip("/\\")
    if not file_name:
        return None
    config_dir = _rig_builder_config_dir()
    if config_dir is None:
        return None
    relative = file_name.replace("\\", "/")
    if not (config_dir / "nam_models" / relative).exists():
        return None
    return {
        "slot": "amp",
        "gear": gear_key,
        "kind": "nam",
        "file": relative,
        "params": params,
        "tone3000_id": _int_or_none(spec.get("tone3000_id") or override.get("tone3000_id")),
        "vst_path": None,
        "vst_format": None,
        "vst_state": None,
    }


def _select_amp_file_override(override: dict[str, Any], gear_key: str, params: dict[str, Any]) -> dict[str, Any] | None:
    variants = override.get("variants")
    if isinstance(variants, dict):
        gain = _gain_value(gear_key, params)
        if gain is None:
            gain = 50.0
        for spec in variants.values():
            if not isinstance(spec, dict):
                continue
            lo_hi = spec.get("rs_gain_range") or []
            if len(lo_hi) != 2:
                continue
            lo = _float_or_none(lo_hi[0])
            hi = _float_or_none(lo_hi[1])
            if lo is not None and hi is not None and lo <= gain <= hi:
                return spec
    return override


def _amp_overrides() -> dict[str, Any]:
    loaded = _load_json_file(Path(__file__).resolve().parent / "data" / "amp_match_overrides.json")
    return loaded if isinstance(loaded, dict) else {}


def _resolve_tone3000_capture(data_dir: Path, slot: str, gear_key: str, params: dict[str, Any]) -> dict[str, Any] | None:
    spec = _tone3000_spec_from_real_map(data_dir, gear_key, params) or _tone3000_spec_from_defaults(data_dir, gear_key)
    if not spec:
        return None
    tone_id = _int_or_none(spec.get("tone3000_id"))
    if tone_id is None:
        return None
    return {
        "slot": slot,
        "gear": gear_key,
        "kind": str(spec.get("kind") or "nam"),
        "file": None,
        "params": params,
        "tone3000_id": tone_id,
        "vst_path": None,
        "vst_format": None,
        "vst_state": None,
    }


def _reuse_existing_capture_assignment(conn: sqlite3.Connection, stage: dict[str, Any]) -> None:
    if stage.get("kind") not in {"nam", "ir"} or stage.get("file"):
        return
    gear = str(stage.get("gear") or "")
    if not gear:
        return
    tone3000_id = _int_or_none(stage.get("tone3000_id"))
    if tone3000_id is not None:
        row = conn.execute(
            "SELECT kind, file FROM preset_pieces "
            "WHERE rs_gear_type = ? AND tone3000_id = ? "
            "AND kind IN ('nam', 'ir') AND file IS NOT NULL AND file != '' "
            "ORDER BY (assigned_mode IN ('manual', 'manual_vst')) DESC, id DESC LIMIT 1",
            (gear, tone3000_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT kind, file FROM preset_pieces "
            "WHERE rs_gear_type = ? AND kind IN ('nam', 'ir') "
            "AND file IS NOT NULL AND file != '' "
            "ORDER BY (assigned_mode IN ('manual', 'manual_vst')) DESC, id DESC LIMIT 1",
            (gear,),
        ).fetchone()
    if row:
        stage["kind"] = row[0]
        stage["file"] = row[1]


def _download_pending_song_assignments(conn: sqlite3.Connection, song_key: str) -> bool:
    if str(os.environ.get("FEEDFORGE_RIG_BUILDER_AUTO_DOWNLOAD", "1")).lower() in {"0", "false", "no"}:
        return False
    backend = _find_feedback_backend()
    if backend is None:
        return False
    rows = conn.execute(
        "SELECT DISTINCT pp.rs_gear_type, pp.tone3000_id "
        "FROM tone_mappings tm JOIN preset_pieces pp ON pp.preset_id = tm.preset_id "
        "WHERE tm.filename = ? AND pp.kind = 'nam' "
        "AND (pp.file IS NULL OR pp.file = '') AND pp.tone3000_id IS NOT NULL",
        (song_key,),
    ).fetchall()
    downloaded = False
    for gear, tone3000_id in rows:
        if not gear or not tone3000_id:
            continue
        payload = json.dumps({"rs_gear": gear, "tone3000_id": int(tone3000_id)}).encode("utf-8")
        request = urllib.request.Request(
            f"{backend}/api/plugins/rig_builder/download_for_gear",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310 - localhost Rig Builder API
                result = json.loads(response.read().decode("utf-8-sig"))
            downloaded = downloaded or bool(result.get("ok") and result.get("file"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            continue
    return downloaded


def _find_feedback_backend() -> str | None:
    for port in range(18000, 18011):
        url = f"http://127.0.0.1:{port}"
        try:
            with urllib.request.urlopen(f"{url}/api/plugins/rig_builder/settings", timeout=0.3) as response:  # noqa: S310
                if response.status < 500:
                    return url
        except (OSError, urllib.error.URLError):
            continue
    return None


def _refresh_seeded_tone_statuses(
    conn: sqlite3.Connection,
    song_key: str,
    seeded: list[SeededTone],
) -> list[SeededTone]:
    refreshed: list[SeededTone] = []
    for tone in seeded:
        row = conn.execute(
            "SELECT preset_id FROM tone_mappings WHERE filename = ? AND tone_key = ? LIMIT 1",
            (song_key, tone.tone_key),
        ).fetchone()
        if row is None:
            refreshed.append(tone)
            continue
        pieces = conn.execute(
            "SELECT rs_gear_type, kind, file, tone3000_id FROM preset_pieces "
            "WHERE preset_id = ? ORDER BY slot_order",
            (row[0],),
        ).fetchall()
        missing: list[str] = []
        for gear, kind, file, tone3000_id in pieces:
            gear_name = str(gear or "")
            stage_kind = str(kind or "none")
            if stage_kind == "none":
                missing.append(gear_name)
            elif stage_kind == "nam" and not file:
                if tone3000_id:
                    missing.append(f"{gear_name} (Tone3000 download pending)")
                else:
                    missing.append(gear_name)
        refreshed.append(
            SeededTone(
                tone_key=tone.tone_key,
                status="partial" if missing else ("ready" if tone.stages else tone.status),
                stages=tone.stages,
                missing=missing,
            )
        )
    return refreshed


def _tone3000_spec_from_real_map(data_dir: Path, gear_key: str, params: dict[str, Any]) -> dict[str, Any] | None:
    real_map = _load_json_file(data_dir / "rs_to_real.json")
    real = real_map.get(gear_key) if isinstance(real_map, dict) else None
    if not isinstance(real, dict):
        return None
    variants = real.get("gain_variants")
    if isinstance(variants, dict):
        gain = _gain_value(gear_key, params)
        if gain is None:
            gain = 50.0
        for spec in variants.values():
            if not isinstance(spec, dict):
                continue
            lo_hi = spec.get("rs_gain_range") or []
            if len(lo_hi) != 2:
                continue
            lo = _float_or_none(lo_hi[0])
            hi = _float_or_none(lo_hi[1])
            if lo is not None and hi is not None and lo <= gain <= hi:
                return {**spec, "kind": "nam"}
    if real.get("tone3000_id"):
        return {**real, "kind": "nam"}
    return None


def _tone3000_spec_from_defaults(data_dir: Path, gear_key: str) -> dict[str, Any] | None:
    defaults = _load_json_file(data_dir / "default_captures.json")
    spec = defaults.get(gear_key) if isinstance(defaults, dict) else None
    return spec if isinstance(spec, dict) else None


def _gain_value(gear_key: str, params: dict[str, Any]) -> float | None:
    for key in ("Gain", f"{gear_key}_Gain"):
        gain = _float_or_none(params.get(key))
        if gain is not None:
            return gain
    for key, value in params.items():
        if str(key).lower().endswith("_gain"):
            gain = _float_or_none(value)
            if gain is not None:
                return gain
    return None


def _resolve_vst(data_dir: Path, gear_key: str) -> Path | None:
    vst_map = _load_json_file(data_dir / "rs_gear_to_vst.json")
    candidates = vst_map.get(gear_key) if isinstance(vst_map, dict) else None
    if not isinstance(candidates, list):
        return None
    plugin_root = data_dir.parent
    for item in candidates:
        if not isinstance(item, dict) or not item.get("bundled"):
            continue
        candidate = plugin_root / str(item["bundled"])
        if candidate.exists():
            return candidate
    return None


def _build_vst_state(data_dir: Path, gear_key: str, vst_path: Path, params: dict[str, Any]) -> dict[str, Any] | None:
    knob_table = _load_json_file(data_dir / "rs_knob_to_vst_param.json")
    if not isinstance(knob_table, dict):
        return None
    state_params = _translate_vst_params(
        gear_key,
        str(vst_path),
        {str(key): value for key, value in params.items()},
        knob_table,
    )
    return {"params": state_params} if state_params else None


def _translate_vst_params(
    gear_key: str,
    vst_path: str,
    knobs: dict[str, Any],
    knob_table: dict[str, Any],
) -> dict[str, float]:
    stem = _vst_stem(vst_path)
    gear_block = knob_table.get(gear_key)
    vst_block = gear_block.get(stem) if isinstance(gear_block, dict) else None
    if not isinstance(vst_block, dict):
        return {}

    graphic = vst_block.get("_graphic_eq")
    if isinstance(graphic, list) and graphic:
        return _translate_graphic_eq(graphic, knobs, stem)

    output: dict[str, float] = {}
    static = vst_block.get("_static")
    if isinstance(static, dict):
        for name, value in static.items():
            translated = _normalize_static_param(stem, str(name), value)
            if translated is not None:
                output[str(name)] = translated

    for knob, value in knobs.items():
        rule = vst_block.get(knob) or vst_block.get(_short_knob_name(knob))
        if not isinstance(rule, dict):
            continue
        translated = _translate_one_knob(value, rule, stem)
        if translated is None:
            continue
        name, normalized = translated
        output[name] = normalized
    return output


def _short_knob_name(name: str) -> str:
    text = str(name)
    if "_" not in text:
        return text
    return text.rsplit("_", 1)[-1]


def _knob_value(knobs: dict[str, Any], name: str) -> Any:
    if name in knobs:
        return knobs[name]
    for key, value in knobs.items():
        if _short_knob_name(key) == name:
            return value
    raise KeyError(name)


def _translate_graphic_eq(graphic: list[Any], knobs: dict[str, Any], stem: str) -> dict[str, float]:
    output: dict[str, float] = {}
    freq_range = VST_PARAM_RANGES.get(_range_stem(stem), {}).get("Frequency 1 (EQ 1)") or ("log", 20.0, 20000.0)
    gain_range = VST_PARAM_RANGES.get(_range_stem(stem), {}).get("Gain 1 (EQ 1)") or ("linear", -24.0, 24.0)
    for index, band in enumerate(graphic[:16], 1):
        if not isinstance(band, dict):
            continue
        try:
            freq = float(band.get("freq"))
        except (TypeError, ValueError):
            continue
        gains = []
        for key in band.get("rs") or []:
            try:
                gains.append(float(_knob_value(knobs, str(key))))
            except (KeyError, TypeError, ValueError):
                pass
        avg_gain = sum(gains) / len(gains) if gains else 0.0
        output[f"Frequency {index} (EQ {index})"] = _normalize_display(freq, *freq_range)
        output[f"Gain {index} (EQ {index})"] = _normalize_display(avg_gain, *gain_range)
        output[f"Enable {index} (EQ {index})"] = 1.0
    return output


def _translate_one_knob(value: Any, rule: dict[str, Any], stem: str) -> tuple[str, float] | None:
    try:
        translated = float(value) * float(rule.get("scale", 1.0)) + float(rule.get("offset", 0.0))
    except (TypeError, ValueError):
        return None
    if rule.get("invert"):
        translated = 1.0 - translated
    param = rule.get("param")
    if not isinstance(param, str) or not param:
        return None
    value_range = VST_PARAM_RANGES.get(_range_stem(stem), {}).get(param)
    if value_range:
        translated = _normalize_display(translated, *value_range)
    return param, _clamp01(translated)


def _normalize_static_param(stem: str, name: str, value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    value_range = VST_PARAM_RANGES.get(_range_stem(stem), {}).get(name)
    if value_range:
        numeric = _normalize_display(numeric, *value_range)
    return _clamp01(numeric)


def _normalize_display(value: float, kind: str, lo: float, hi: float) -> float:
    if kind == "log":
        if value <= 0 or lo <= 0 or hi <= lo:
            return 0.0
        bounded = max(lo, min(hi, value))
        return _clamp01(math.log(bounded / lo) / math.log(hi / lo))
    if hi == lo:
        return 0.0
    return _clamp01((value - lo) / (hi - lo))


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _float_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _range_stem(stem: str) -> str:
    return STEM_RANGE_ALIASES.get(stem, stem)


def _vst_stem(vst_path: str) -> str:
    name = Path(vst_path).name
    for suffix in (".vst3", ".component"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name.lower()


def _resolve_cab_ir(data_dir: Path, gear_key: str) -> dict[str, str] | None:
    config_dir = _rig_builder_config_dir()
    if config_dir is None:
        return None
    ir_root = config_dir / "nam_irs"
    mic_map = _load_json_file(data_dir / "rs_cab_mic_map.json")
    overrides = _cab_overrides(data_dir)
    if isinstance(mic_map, dict):
        for base, variants in mic_map.items():
            if not isinstance(variants, dict):
                continue
            for spec in variants.values():
                if isinstance(spec, dict) and str(spec.get("effect_name") or "").lower() == gear_key.lower():
                    override = _cab_override_ir(overrides, ir_root, str(base), spec)
                    if not override:
                        override = _default_cab_override_ir(overrides, ir_root, str(base))
                    if override:
                        return {"gear": str(base), "kind": "ir", "file": override}
                    file_name = str(spec.get("ir_file") or "")
                    if file_name and (ir_root / file_name).exists():
                        return {"gear": str(base), "kind": "rs_ir", "file": file_name}
                    fallback = _fallback_ir(ir_root, str(base))
                    if fallback:
                        return {"gear": str(base), "kind": "ir", "file": fallback}
    override = _default_cab_override_ir(overrides, ir_root, gear_key)
    if override:
        return {"gear": gear_key, "kind": "ir", "file": override}
    fallback = _fallback_ir(ir_root, gear_key)
    if fallback:
        return {"gear": gear_key, "kind": "ir", "file": fallback}
    return None


def _cab_override_ir(overrides: Any, ir_root: Path, base: str, spec: dict[str, Any]) -> str | None:
    if not isinstance(overrides, dict):
        return None
    override = _case_insensitive_get(overrides, base)
    if not isinstance(override, dict):
        return None
    exact = _exact_cab_override_ir(override, ir_root)
    if exact:
        return exact
    effect_name = str(spec.get("effect_name") or "")
    parts = effect_name.split("_")
    if len(parts) < 2:
        return None
    mic = {
        "57": "dyn",
        "condenser": "cond",
        "ribbon": "ribbon",
        "tube": "tube",
    }.get(parts[-2].lower())
    position = parts[-1].lower()
    if mic is None or position not in {"cone", "edge", "offaxis"}:
        return None
    ir_dir = str(override.get("ir_dir") or "cabs").strip("/\\")
    prefix = str(override.get("prefix") or "")
    stem = f"{prefix}_{mic}_{position}" if prefix else f"{mic}_{position}"
    candidate = f"{ir_dir}/{stem}.wav"
    return candidate if (ir_root / candidate).exists() else None


def _cab_overrides(data_dir: Path) -> dict[str, Any]:
    installed = _load_json_file(data_dir / "rb_cab_overrides.json")
    feedforge = _load_json_file(Path(__file__).resolve().parent / "data" / "cab_match_overrides.json")
    merged: dict[str, Any] = {}
    if isinstance(installed, dict):
        merged.update(installed)
    if isinstance(feedforge, dict):
        merged.update(feedforge)
    return merged


def _default_cab_override_ir(overrides: Any, ir_root: Path, gear_key: str) -> str | None:
    if not isinstance(overrides, dict):
        return None
    override = _case_insensitive_get(overrides, gear_key)
    if not isinstance(override, dict):
        return None
    exact = _exact_cab_override_ir(override, ir_root)
    if exact:
        return exact
    ir_dir = str(override.get("ir_dir") or "cabs").strip("/\\")
    for stem in ("dyn_cone", "cond_cone", "ribbon_cone", "tube_cone"):
        candidate = f"{ir_dir}/{stem}.wav"
        if (ir_root / candidate).exists():
            return candidate
    return None


def _exact_cab_override_ir(override: dict[str, Any], ir_root: Path) -> str | None:
    exact = str(override.get("file") or "").strip("/\\")
    if exact and (ir_root / exact).exists():
        return exact.replace("\\", "/")
    return None


def _case_insensitive_get(values: dict[Any, Any], key: str) -> Any:
    if key in values:
        return values[key]
    folded = key.lower()
    for candidate, value in values.items():
        if str(candidate).lower() == folded:
            return value
    return None


def _fallback_ir(ir_root: Path, gear_key: str) -> str | None:
    preferred = (
        "other/Bass Cab Sim 2.wav"
        if gear_key.lower().startswith("bass_")
        else "other/greenback 212 1 mono.wav"
    )
    if (ir_root / preferred).exists():
        return preferred
    for item in sorted(ir_root.rglob("*.wav")):
        try:
            return item.relative_to(ir_root).as_posix()
        except ValueError:
            return item.name
    return None


def _delete_mapping(conn: sqlite3.Connection, song_key: str, tone_key: str) -> None:
    rows = conn.execute(
        "SELECT preset_id FROM tone_mappings WHERE filename = ? AND tone_key = ?",
        (song_key, tone_key),
    ).fetchall()
    for (preset_id,) in rows:
        conn.execute("DELETE FROM preset_pieces WHERE preset_id = ?", (preset_id,))
        conn.execute("DELETE FROM presets WHERE id = ?", (preset_id,))
    conn.execute("DELETE FROM tone_mappings WHERE filename = ? AND tone_key = ?", (song_key, tone_key))


def _has_mapping(conn: sqlite3.Connection, song_key: str, tone_key: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM tone_mappings WHERE filename = ? AND tone_key = ? LIMIT 1",
        (song_key, tone_key),
    ).fetchone())


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS presets (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT UNIQUE,
          model_file TEXT,
          ir_file TEXT,
          input_gain REAL DEFAULT 1.0,
          output_gain REAL DEFAULT 1.0,
          gate_threshold REAL DEFAULT -60.0,
          settings_json TEXT DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS tone_mappings (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          filename TEXT,
          tone_key TEXT,
          preset_id INTEGER,
          UNIQUE(filename, tone_key)
        );
        CREATE TABLE IF NOT EXISTS preset_pieces (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          preset_id INTEGER,
          slot_order INTEGER,
          slot TEXT,
          rs_gear_type TEXT,
          kind TEXT,
          file TEXT,
          params_json TEXT,
          tone3000_id INTEGER,
          assigned_mode TEXT DEFAULT 'feedforge',
          bypassed INTEGER DEFAULT 0,
          vst_path TEXT,
          vst_format TEXT,
          vst_state TEXT
        );
        """
    )


def _rig_builder_config_dir() -> Path | None:
    db = _rig_builder_db()
    if db:
        return db.parent
    candidate = _default_rig_builder_db_path()
    return candidate.parent if candidate else None


def _ensure_unit_impulse_ir() -> None:
    config_dir = _rig_builder_config_dir()
    if config_dir is None:
        return
    path = config_dir / "nam_irs" / "other" / "_rb_unit_impulse.wav"
    if _valid_unit_impulse_ir(path):
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(48_000)
            handle.writeframes((32767).to_bytes(2, byteorder="little", signed=True) + (b"\x00\x00" * 255))
    except OSError:
        return


def _valid_unit_impulse_ir(path: Path) -> bool:
    try:
        with wave.open(str(path), "rb") as handle:
            if handle.getnchannels() < 1 or handle.getsampwidth() != 2 or handle.getnframes() < 1:
                return False
            frames = handle.readframes(min(handle.getnframes(), 256))
    except (OSError, EOFError, wave.Error):
        return False
    return any(byte != 0 for byte in frames)


def _enable_feedback_tone3000_fallback() -> None:
    config_dir = _rig_builder_config_dir()
    if config_dir is None:
        return
    settings_path = config_dir / "rig_builder_settings.json"
    settings: dict[str, Any] = {}
    if settings_path.exists():
        try:
            loaded = json.loads(settings_path.read_text(encoding="utf-8-sig"))
            if isinstance(loaded, dict):
                settings = loaded
        except (OSError, json.JSONDecodeError):
            return
    if settings.get("curated_only") is False:
        return
    settings["curated_only"] = False
    try:
        settings_path.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError:
        return


def _default_rig_builder_db_path() -> Path | None:
    import os

    root = os.environ.get("APPDATA")
    if not root:
        return None
    config = Path(root) / "feedback-desktop" / "slopsmith-config"
    return config / "nam_tone.db" if config.is_dir() else None
