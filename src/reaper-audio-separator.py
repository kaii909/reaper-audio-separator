#!/usr/bin/env python3
"""
reaper extension for stem splitting using audio-separator.
load this script as an action in reaper (Actions > Show action list...).
"""

import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import reaper_python as RPR

SCRIPT_DIR = Path(sys.path[0]).parent

# add src directory to python path so we can import reaper_python
SRC_DIR = SCRIPT_DIR / "src"
if SRC_DIR.exists():
    sys.path.insert(0, str(SRC_DIR))

# ============================================================
# CONFIG - edit presets here
# ============================================================
PRESETS = {
    "1": {"name": "stems (4)", "model": "htdemucs_ft.yaml"},
    "2": {"name": "vocals", "model": "5_HP-Karaoke-UVR.pth"},
    "3": {"name": "denoise", "model": "UVR-DeNoise-Lite.pth"},
    "4": {"name": "dereverb", "model": "UVR-DeEcho-DeReverb.pth"},
}

as_binary = "audio-separator.exe" if sys.platform == "win32" else "audio-separator"

# try to find a path to audio-separator, if not possible, fallback to absolute path (adjustable)
AUDIO_SEP_BIN = (
    SCRIPT_DIR / ".venv" / ("Scripts" if sys.platform == "win32" else "bin") / as_binary
)
if not AUDIO_SEP_BIN.exists():
    AUDIO_SEP_BIN = Path(
        "enter a path manually in the script"  # ex: "/home/user/.python/.venv/bin/audio-separator"
    )

# supported audio formats
AUDIO_FORMATS = ["*.wav", "*.flac", "*.mp3", "*.ogg", "*.m4a", "*.aac", "*.wma"]

# ============================================================
# HELPERS
# ============================================================


def log(msg):
    """write message to reaper console."""
    RPR.RPR_ShowConsoleMsg(msg + "\n")


def get_project_sample_rate():
    """return project sample rate in Hz."""
    sr = RPR.RPR_GetSetProjectInfo(0, "projsr", 0.0, False)
    if sr > 0:
        return int(sr)
    # fallback
    item = RPR.RPR_GetMediaItem(0, 0)
    if item:
        take = RPR.RPR_GetActiveTake(item)
        if take:
            source = RPR.RPR_GetMediaItemTake_Source(take)
            if source:
                return RPR.RPR_GetMediaSourceSampleRate(source)
    return 44100


def get_item_source_path(item):
    """return file path of item's active take source."""
    take = RPR.RPR_GetActiveTake(item)
    if not take:
        return None
    source = RPR.RPR_GetMediaItemTake_Source(take)
    if not source:
        return None
    _, filename, _ = RPR.RPR_GetMediaSourceFileName(source, "", 2048)
    return filename if filename else None


def get_output_dir(take_name):
    """return timestamped output dir in project's Media/stems/ folder."""
    project_path, _ = RPR.RPR_GetProjectPath("", 2048)
    if not project_path:
        project_path = str(Path.home())
    stamp = take_name
    out = Path(project_path) / "Media" / "stems" / stamp
    out.mkdir(parents=True, exist_ok=True)
    return out


def probe_audio_file(filepath):
    """detect bit depth, channels, and sample rate of audio file."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=bits_per_raw_sample,channels,sample_fmt,sample_rate",
        "-of",
        "csv=p=0",
        str(filepath),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        parts = result.stdout.strip().split(",")
        bits = int(parts[0]) if len(parts) > 0 and parts[0] else 16
        channels = int(parts[1]) if len(parts) > 1 and parts[1] else 2
        fmt = parts[2] if len(parts) > 2 else "s16"
        sr = int(parts[3]) if len(parts) > 3 and parts[3] else 44100
        return bits, channels, fmt, sr
    except (OSError, subprocess.SubprocessError, ValueError):
        return 16, 2, "s16", 44100


def export_item(item, output_path):
    """extract item as wav preserving bit depth and channels"""
    source = get_item_source_path(item)
    if not source:
        raise ValueError("item has no audio source")

    take = RPR.RPR_GetActiveTake(item)
    length = RPR.RPR_GetMediaItemInfo_Value(item, "D_LENGTH")
    offset = RPR.RPR_GetMediaItemTakeInfo_Value(take, "D_STARTOFFS")
    playrate = RPR.RPR_GetMediaItemTakeInfo_Value(take, "D_PLAYRATE")

    # calculate source duration considering item playrate
    source_duration = length * playrate

    project_sr = get_project_sample_rate()
    bits, channels, fmt, _ = probe_audio_file(source)

    if bits >= 32 or fmt in ("flt", "dbl"):
        codec = "pcm_f32le"
    elif bits >= 24:
        codec = "pcm_s24le"
    else:
        codec = "pcm_s16le"

    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-ss",
        str(offset),
        "-t",
        str(source_duration),
        "-i",
        source,
        "-ar",
        str(project_sr),
        "-ac",
        str(channels),
        "-c:a",
        codec,
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)

def run_separator(input_path, output_dir, model):
    """run audio-separator"""
    if not AUDIO_SEP_BIN.exists():
        raise FileNotFoundError(f"audio-separator not found at {AUDIO_SEP_BIN}")

    cmd = [
        str(AUDIO_SEP_BIN),
        str(input_path),
        "-m",
        model,
        "--output_dir",
        str(output_dir),
        "--output_format",
        "WAV",
    ]

    log(f"  running: {model}")
    log("  this may take a while...")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=None,
            check=False,
        )

        if result.stdout:
            for line in result.stdout.splitlines():
                if line.strip():
                    log(f"  [SEPARATOR] {line}")

        if result.stderr:
            for line in result.stderr.splitlines():
                if line.strip():
                    log(f"  [SEPARATOR] {line}")

        if result.returncode == 0:
            log("  separation completed successfully")
            return True

        log(f"  ERROR: process exited with code {result.returncode}")
        return False
    except (OSError, subprocess.SubprocessError) as e:
        log(f"  ERROR: {e}")
        return False


def import_stems(stem_paths, position, length):
    """create new tracks and import stems as items."""
    n = RPR.RPR_CountTracks(0)

    for i, p in enumerate(stem_paths):
        if not p.exists():
            log(f"  WARNING: file not found: {p.name}")
            continue

        if p.stat().st_size == 0:
            log(f"  WARNING: file is empty: {p.name}")
            continue

        RPR.RPR_InsertTrackAtIndex(n + i, True)
        track = RPR.RPR_GetTrack(0, n + i)

        name = p.stem
        if "(" in name and ")" in name:
            name = name.split("(")[-1].rstrip(")")
        RPR.RPR_GetSetMediaTrackInfo_String(track, "P_NAME", name, True)

        item = RPR.RPR_AddMediaItemToTrack(track)
        RPR.RPR_SetMediaItemInfo_Value(item, "D_POSITION", position)
        RPR.RPR_SetMediaItemInfo_Value(item, "D_LENGTH", length)

        source = RPR.RPR_PCM_Source_CreateFromFile(str(p))
        if not source:
            log(f"  ERROR: failed to create source for {p.name}")
            continue

        take = RPR.RPR_AddTakeToMediaItem(item)
        RPR.RPR_SetMediaItemTake_Source(take, source)
        RPR.RPR_UpdateItemInProject(item)

        log(f"  imported: {p.name}")

    RPR.RPR_UpdateArrange()


# ============================================================
# MAIN
# ============================================================


def main():
    RPR.RPR_ClearConsole()
    log(f"SCRIPT_DIR: {SCRIPT_DIR}")
    log(f"SRC_DIR: {SRC_DIR}")
    log(f"AUDIO_SEP_BIN: {AUDIO_SEP_BIN}")

    log("[stem-splitter] starting")

    item = RPR.RPR_GetSelectedMediaItem(0, 0)
    if not item:
        log("[!] no item selected")
        return

    options = "\n".join(f"{k}: {v['name']}" for k, v in PRESETS.items())
    retval, _, _, _, choice, _ = RPR.RPR_GetUserInputs(
        "Stem Splitter", 1, f"preset:\n{options}\n\nnumber:", "1", 16
    )

    if not retval or choice not in PRESETS:
        log("[!] cancelled or invalid preset")
        return

    preset = PRESETS[choice]
    log(f"[+] preset: {preset['name']} ({preset['model']})")
    log(f"[+] sample rate: {get_project_sample_rate()} Hz")

    item_take = RPR.RPR_GetActiveTake(item)
    out_dir = get_output_dir(RPR.RPR_GetTakeName(item_take))

    # wav_in is the name of the ffmpeg-processed take that enters the audio-separator
    # name the input as user date and time so new stems dont overwrite
    wav_in = (
        out_dir
        / f"input_{datetime.now(tz=timezone.utc).astimezone().strftime("%Y%m%d_%H%M")}.wav"
    )

    # capture the state of the directory before running audio-separator
    # so it loads only freshly created stems
    existing_files = set()
    for pattern in AUDIO_FORMATS:
        existing_files.update(out_dir.glob(pattern))
    existing_files.add(wav_in)

    log(f"[+] exporting item -> {wav_in.name}")
    try:
        export_item(item, wav_in)
    except Exception as e:  # noqa: BLE001
        log(f"[!] export failed: {e}")
        return

    log("[+] separating...")
    if not run_separator(wav_in, out_dir, preset["model"]):
        log("[!] separation failed")
        return

    time.sleep(0.5)

    all_files = set()
    for pattern in AUDIO_FORMATS:
        all_files.update(out_dir.glob(pattern))

    # takes the new files
    new_stems = sorted(all_files - existing_files)

    new_stems = [s for s in new_stems if s.name != "_input.wav"]

    if not new_stems:
        log("[!] no stems generated")
        return

    log(f"[+] importing {len(new_stems)} stems to new tracks")
    pos = RPR.RPR_GetMediaItemInfo_Value(item, "D_POSITION")
    length = RPR.RPR_GetMediaItemInfo_Value(item, "D_LENGTH")
    import_stems(new_stems, pos, length)

    log(f"[done] stems saved in: {out_dir}")


# ============================================================
# EXECUTION
# ============================================================


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001
        RPR.RPR_ShowConsoleMsg(f"CRITICAL ERROR:\n{traceback.format_exc()}\n")
