"""Reproduce measured office landmark matches (optional Pillow, numpy, pypdf).

The source files remain read-only. Diagnostic crop pairs go to --output.
This verifies image registration; it never approves candidate geometry.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]


def window_sums(array, height, width):
    integral = np.pad(array, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    return (
        integral[height:, width:]
        - integral[:-height, width:]
        - integral[height:, :-width]
        + integral[:-height, :-width]
    )


def correlation(search, template):
    height, width = template.shape
    rows, columns = search.shape
    centered = template - template.mean()
    shape = (rows + height - 1, columns + width - 1)
    convolution = np.fft.irfft2(
        np.fft.rfft2(search, s=shape) * np.fft.rfft2(centered[::-1, ::-1], s=shape),
        s=shape,
    )
    variance = np.maximum(
        window_sums(search * search, height, width)
        - window_sums(search, height, width) ** 2 / (height * width),
        0,
    )
    return convolution[height - 1 : rows, width - 1 : columns] / np.maximum(
        np.sqrt(variance * np.sum(centered * centered)), 1e-12
    )


def peak_delta(values):
    left, middle, right = values
    denominator = left - 2 * middle + right
    return (
        float(0.5 * (left - right) / denominator) if abs(denominator) > 1e-12 else 0.0
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    evidence = json.loads(
        (ROOT / "docs/goal-mode/office-registration.json").read_text()
    )
    production_path = ROOT / "apps/web/public/assets/office/office-8192x5460.png"
    pdf = args.pdf.read_bytes()
    assert hashlib.sha256(pdf).hexdigest() == evidence["sourceHashes"]["roomsPdfSha256"]
    # Read the original DCT JPEG stream. page.images data can re-encode the image.
    page = PdfReader(io.BytesIO(pdf)).pages[0]
    embedded_bytes = page["/Resources"]["/XObject"]["/Image"].get_object().get_data()
    assert (
        hashlib.sha256(embedded_bytes).hexdigest()
        == evidence["sourceHashes"]["embeddedOriginalJpegSha256"]
    )
    assert (
        hashlib.sha256(production_path.read_bytes()).hexdigest()
        == evidence["sourceHashes"]["productionPngSha256"]
    )
    embedded = Image.open(io.BytesIO(embedded_bytes)).convert("RGB")
    production = Image.open(production_path).convert("RGB")
    assert embedded.size == (6144, 4096) and production.size == (8192, 5460)
    pixels = np.asarray(production.convert("L"), dtype=np.float64)
    results = []
    args.output.mkdir(parents=True, exist_ok=True)
    font = ImageFont.load_default(size=16)
    for landmark in evidence["landmarks"]:
        x, y = landmark["markup"]["x"], landmark["markup"]["y"]
        template = embedded.crop((x - 48, y - 48, x + 48, y + 48)).resize(
            (128, 128), Image.Resampling.LANCZOS
        )
        expected = np.array([x * 4 / 3, y * 4 / 3 - 2 / 3])
        origin = np.floor(expected - 144).astype(int)
        scores = correlation(
            pixels[origin[1] : origin[1] + 288, origin[0] : origin[0] + 288],
            np.asarray(template.convert("L"), dtype=np.float64),
        )
        py, px = np.unravel_index(scores.argmax(), scores.shape)
        assert 0 < px < scores.shape[1] - 1 and 0 < py < scores.shape[0] - 1
        dx = peak_delta(scores[py, px - 1 : px + 2])
        dy = peak_delta(scores[py - 1 : py + 2, px])
        measured = np.array([origin[0] + px + dx + 64, origin[1] + py + dy + 64])
        saved = np.array([landmark["source"]["x"], landmark["source"]["y"]])
        assert np.linalg.norm(measured - saved) < 0.01, landmark["id"]
        error = float(np.linalg.norm(expected - measured))
        assert abs(error - landmark["residualErrorPixels"]) < 0.01
        results.append(
            {"id": landmark["id"], "measured": measured.tolist(), "errorPixels": error}
        )
        sheet = Image.new("RGB", (510, 285), "white")
        sheet.paste(template.resize((240, 240)), (5, 35))
        sx, sy = measured
        sheet.paste(
            production.crop(
                (round(sx) - 120, round(sy) - 120, round(sx) + 120, round(sy) + 120)
            ),
            (260, 35),
        )
        ImageDraw.Draw(sheet).text(
            (5, 5),
            f"{landmark['id']} PDF / production; residual {error:.3f}px",
            fill="black",
            font=font,
        )
        sheet.save(args.output / f"{landmark['id']}.png")
    (args.output / "measurements.json").write_text(json.dumps(results, indent=2))
    print(
        f"PASS: {len(results)} independently matched landmarks; max residual {max(row['errorPixels'] for row in results):.6f}px. Geometry approval unchanged."
    )


if __name__ == "__main__":
    main()
