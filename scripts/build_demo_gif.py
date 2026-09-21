"""Assemble the recorded frames into the demo animation.

Kept separate from the recording because they are different problems. Playwright
is good at driving a page and bad at making a small animation; Pillow is the
reverse.

Two decisions worth stating. The palette is quantized once, over the whole
sequence rather than per frame, because a per frame palette makes flat areas
shimmer between frames as the quantizer picks slightly different colours for
the same fill. And the frames are cropped to the chart rather than to the page:
a full page capture of a dashboard scaled to fit a README column is a grey
rectangle.

    python scripts/build_demo_gif.py web/tests/demo-frames docs/demo.gif
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

# Milliseconds per frame in the emitted animation. The recorder shoots at 120ms
# and this plays a little slower, which reads better for a chart that changes.
FRAME_DURATION_MS = 150

# Target width. Wide enough to read a heatmap cell, narrow enough that a README
# renders it without scaling.
TARGET_WIDTH = 900

# The animation is for a README, so it has a budget like anything else shipped.
MAX_BYTES = 6_000_000


def load(directory: Path) -> list[Image.Image]:
    paths = sorted(directory.glob("frame-*.png"))
    if not paths:
        raise FileNotFoundError(
            f"No frames in {directory}. Run 'node tests/record-demo.mjs' from "
            "web/ first; it needs the built export in web/out."
        )
    return [Image.open(path).convert("RGB") for path in paths]


def normalize(frames: list[Image.Image]) -> list[Image.Image]:
    """One size for every frame, scaled to the target width.

    Frames come from two different charts and are not the same shape. Pasting
    each onto a canvas the size of the largest, rather than stretching them to
    match, keeps both charts at their own aspect ratio; a stretched heatmap cell
    stops being square and that is the one thing a reader would notice.
    """
    width = max(frame.width for frame in frames)
    height = max(frame.height for frame in frames)
    background = frames[0].getpixel((2, 2))

    canvased: list[Image.Image] = []
    for frame in frames:
        canvas = Image.new("RGB", (width, height), background)
        canvas.paste(frame, ((width - frame.width) // 2, (height - frame.height) // 2))
        canvased.append(canvas)

    if width <= TARGET_WIDTH:
        return canvased
    scale = TARGET_WIDTH / width
    size = (TARGET_WIDTH, max(1, round(height * scale)))
    return [frame.resize(size, Image.LANCZOS) for frame in canvased]


def quantize(frames: list[Image.Image], colors: int) -> list[Image.Image]:
    """One palette for the whole sequence, derived from a montage of samples.

    A per frame palette makes a flat fill shimmer between frames, because the
    quantizer picks a slightly different colour for the same pixel each time.
    """
    sample = frames[:: max(1, len(frames) // 12)]
    strip = Image.new("RGB", (frames[0].width, frames[0].height * len(sample)))
    for index, frame in enumerate(sample):
        strip.paste(frame, (0, index * frame.height))
    palette = strip.quantize(colors=colors, method=Image.MEDIANCUT)
    return [frame.quantize(palette=palette, dither=Image.FLOYDSTEINBERG) for frame in frames]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("frames", help="Directory of frame-NNNN.png files.")
    parser.add_argument("output", help="Where to write the animation.")
    parser.add_argument("--duration", type=int, default=FRAME_DURATION_MS)
    args = parser.parse_args(argv)

    frames = normalize(load(Path(args.frames)))
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)

    # Drop the palette until it fits the budget rather than dropping frames:
    # fewer colours costs a little banding, fewer frames costs the motion that
    # is the entire point of an animation.
    for colors in (128, 96, 64, 48, 32):
        quantized = quantize(frames, colors)
        quantized[0].save(
            target,
            save_all=True,
            append_images=quantized[1:],
            duration=args.duration,
            loop=0,
            optimize=True,
            disposal=2,
        )
        size = target.stat().st_size
        if size <= MAX_BYTES:
            print(
                f"{target}: {len(frames)} frames, {quantized[0].width}x"
                f"{quantized[0].height}, {colors} colours, "
                f"{size / 1_000_000:.2f} MB, "
                f"{len(frames) * args.duration / 1000:.1f} seconds"
            )
            return 0

    print(
        f"{target} is {target.stat().st_size / 1_000_000:.2f} MB at 32 colours, "
        f"over the {MAX_BYTES / 1_000_000:.0f} MB budget. Record fewer frames.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
