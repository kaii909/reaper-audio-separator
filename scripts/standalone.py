#!/usr/bin/env python3
"""
standalone test for audio-separator without Reaper.
use this to verify models work before integrating with Reaper.
"""

import argparse
import subprocess
from pathlib import Path

AUDIO_SEP_BIN = Path(__file__).parent.parent / ".venv" / "bin" / "audio-separator"


def main():
    parser = argparse.ArgumentParser(description="test audio-separator standalone")
    parser.add_argument("input", type=Path, help="input audio file")
    parser.add_argument(
        "--model", "-m", default="UVR_MDXNET_KARA_2.onnx", help="model filename"
    )
    parser.add_argument(
        "--output", "-o", type=Path, default=Path("./output"), help="output directory"
    )
    args = parser.parse_args()

    if not args.input.exists():
        print(f"error: file not found: {args.input}")
        return

    args.output.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(AUDIO_SEP_BIN),
        str(args.input),
        "-m",
        args.model,
        "--output_dir",
        str(args.output),
    ]

    print(f"running: {' '.join(cmd)}")
    result = subprocess.run(cmd)

    if result.returncode == 0:
        print(f"\nstems generated in {args.output}:")
        for f in sorted(args.output.glob("*.wav")):
            print(f"  - {f.name}")
    else:
        print(f"\nerror: process exited with code {result.returncode}")


if __name__ == "__main__":
    main()
