"""Writes the lightbox fixture's PNGs (glimpse-lightbox.html): screenshot-sized, one colour per group, a big number
and a grid, so a swipe, a zoom and a missing file are visible in a Chrome screenshot. Run once; the PNGs are committed."""
import pathlib
import struct
import zlib

HERE = pathlib.Path(__file__).resolve().parent
DIGITS = {"0": "111101101101111", "1": "010110010010111", "2": "111001111100111", "3": "111001111001111", "4": "101101111001001",
          "5": "111100111001111", "6": "111100111101111", "7": "111001001001001", "8": "111101111101111", "9": "111101111001111"}


def png(path, width, height, colour, label):
    scale = max(8, min(width, height) // 12)
    text_w = len(label) * 4 * scale
    x0, y0 = (width - text_w) // 2, (height - 5 * scale) // 2
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            on = False
            if y0 <= y < y0 + 5 * scale and x0 <= x < x0 + text_w:
                char, cx = divmod((x - x0) // scale, 4)
                if cx < 3:
                    on = DIGITS[label[char]][((y - y0) // scale) * 3 + cx] == "1"
            grid = x % 100 == 0 or y % 100 == 0
            row += bytes((250, 250, 250) if on else tuple(min(255, c + 40) for c in colour) if grid else colour)
        rows.append(b"\x00" + bytes(row))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) +
                     chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


OPUS, FABLE, ASTRA = (40, 70, 140), (120, 60, 110), (40, 110, 80)
for name, size, colour, label in (("o1.png", (1500, 950), OPUS, "1"), ("o2.png", (1500, 950), OPUS, "2"), ("o3.png", (1500, 950), OPUS, "3"),
                                  ("o4.png", (1500, 950), OPUS, "4"), ("f1.png", (1500, 950), FABLE, "5"), ("f2.png", (1500, 950), FABLE, "6"),
                                  ("a1.png", (1500, 950), ASTRA, "7"), ("a2.png", (600, 760), ASTRA, "8"), ("a space/a3.png", (600, 760), ASTRA, "9")):
    png(HERE / name, *size, colour, label)
