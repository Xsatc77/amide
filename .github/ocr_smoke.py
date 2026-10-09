"""Smoke test run inside the built image: draw a small made-up price table, read it with the OCR engine, check the text comes back.
Proves the image has what the OCR needs (system libraries, bundled models) on the slim Linux base. Contains no real data."""
import sys

from PIL import Image, ImageDraw, ImageFont

img = Image.new("RGB", (900, 260), "white")
draw = ImageDraw.Draw(img)
font = ImageFont.load_default(size=32)
rows = [("Code", "Product", "Specification", "Price"), ("T001", "Test Peptide Alpha", "5mg*10vials", "$30"), ("T002", "Test Peptide Beta", "10mg*10vials", "$55")]
for r, row in enumerate(rows):
    for c, x in enumerate((20, 160, 450, 720)):
        draw.text((x, 20 + r * 70), row[c], fill="black", font=font)

from app.library.price_lists import ocr  # noqa: E402

if not ocr.engine_available():
    sys.exit("OCR engine is not available in the image")
text = " ".join(ocr.lines_from_words(ocr.recognize(img)))
print("OCR read:", text)
squashed = text.replace(" ", "")      # the engine may add a space inside a word
missing = [w for w in ("T001", "5mg*10vials", "10mg*10vials") if w not in squashed]
if missing:
    sys.exit(f"OCR did not read: {missing}")
print("OCR smoke test passed")
