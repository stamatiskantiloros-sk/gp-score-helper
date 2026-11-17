"""Reading and writing the Guitar Pro 7/8 `.gp` container.

A `.gp` file is a zip. The score is the XML in `Content/score.gpif`; everything else
(stylesheets, layout, score views, preferences) is copied through untouched, in the
original order and with the original zip metadata.
"""
from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path

GPIF_PATH = "Content/score.gpif"


class FormatError(ValueError):
    pass


def sniff(path: str | Path) -> str:
    """'gp' (7/8 zip), 'gpx' (6), 'gp5' (3/4/5) or 'unknown'."""
    head = Path(path).read_bytes()[:32]
    if head[:2] == b"PK":
        return "gp"
    if head[:4] in (b"BCFZ", b"BCFS"):
        return "gpx"
    if b"FICHIER GUITAR PRO" in head or b"FICHIER GUITARE PRO" in head:
        return "gp5"
    return "unknown"


@dataclass
class GpContainer:
    path: Path
    entries: list[tuple[zipfile.ZipInfo, bytes]]
    gpif: bytes

    @classmethod
    def load(cls, path: str | Path) -> "GpContainer":
        path = Path(path)
        kind = sniff(path)
        if kind == "gpx":
            raise FormatError(f"{path.name} is a Guitar Pro 6 (.gpx) file. Open it in Guitar Pro 8 and use "
                              "File > Save As… to save a .gp copy, then work on that copy.")
        if kind == "gp5":
            raise FormatError(f"{path.name} is a Guitar Pro 3/4/5 file. Open it in Guitar Pro 8 and use "
                              "File > Save As… to save a .gp copy, then work on that copy.")
        if kind != "gp":
            raise FormatError(f"{path.name} is not a Guitar Pro 7/8 .gp file")
        entries = []
        gpif = None
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                data = z.read(info)
                entries.append((info, data))
                if info.filename == GPIF_PATH:
                    gpif = data
        if gpif is None:
            raise FormatError(f"{path.name}: no {GPIF_PATH} inside")
        return cls(path, entries, gpif)

    def to_bytes(self, gpif: bytes) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for info, data in self.entries:
                new = zipfile.ZipInfo(info.filename, date_time=_valid_date(info.date_time))
                new.compress_type = info.compress_type
                new.external_attr = info.external_attr
                new.create_system = info.create_system
                z.writestr(new, gpif if info.filename == GPIF_PATH else data)
        return buf.getvalue()

    def save(self, gpif: bytes, out: str | Path, overwrite: bool = False) -> Path:
        out = Path(out)
        if out.resolve() == self.path.resolve():
            raise FormatError("refusing to overwrite the input file; write to a new file instead")
        if out.exists() and not overwrite:
            raise FormatError(f"{out} already exists (use --force to replace it)")
        out.write_bytes(self.to_bytes(gpif))
        return out


def _valid_date(dt: tuple) -> tuple:
    # Guitar Pro writes 1980-00-00, which zipfile accepts; keep it as is.
    y, mo, d, h, mi, s = dt
    return (max(1980, y), mo, d, h, mi, s)
