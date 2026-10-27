"""Reading uploaded or fetched files into rows, validating them against a dataset's schema, and writing rows
back out as CSV, JSON, XML or GeoJSON (OPZ 1.5, 1.27, 1.31, 1.32).

A schema is a list of fields: {"name": ..., "type": "string" | "integer" | "number" | "date", "required": bool}.
Spatial datasets carry "lat" and "lon" number fields, and GeoJSON is built from them.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import xml.etree.ElementTree as ET
import zipfile
from datetime import date


class ImportFailed(ValueError):
    def __init__(self, errors: list[dict]) -> None:
        super().__init__(f"{len(errors)} error(s)")
        self.errors = errors


def parse(content: bytes, fmt: str) -> list[dict]:
    """Rows from a CSV, JSON (a list of objects) or XML (<rows><row><field>...</field></row></rows>) file."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise ImportFailed([{"row": None, "field": None, "message": f"not UTF-8 text: {e.reason}"}]) from None
    if fmt == "csv":
        return [dict(r) for r in csv.DictReader(io.StringIO(text))]
    if fmt == "json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ImportFailed([{"row": None, "field": None, "message": f"bad JSON at line {e.lineno}: {e.msg}"}]) from None
        if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
            raise ImportFailed([{"row": None, "field": None, "message": "JSON must be a list of objects"}])
        return [{k: ("" if v is None else str(v)) for k, v in r.items()} for r in data]
    if fmt == "xml":
        try:
            root = ET.fromstring(text)
        except ET.ParseError as e:
            raise ImportFailed([{"row": None, "field": None, "message": f"bad XML: {e}"}]) from None
        return [{child.tag: (child.text or "") for child in row} for row in root.findall("row")]
    raise ImportFailed([{"row": None, "field": None, "message": f"unsupported format {fmt}"}])


def _typed(value: str, kind: str):
    if kind == "integer":
        return int(value)
    if kind == "number":
        return float(value)
    if kind == "date":
        return date.fromisoformat(value).isoformat()
    return value


def validate(rows: list[dict], schema: list[dict]) -> tuple[list[dict], list[dict]]:
    """Typed rows and an error report naming the row (1-based), field and reason for every problem."""
    errors, typed = [], []
    names = [f["name"] for f in schema]
    if not rows:
        errors.append({"row": None, "field": None, "message": "the file holds no rows"})
    for i, row in enumerate(rows, start=1):
        unknown = sorted(set(row) - set(names))
        if unknown:
            errors.append({"row": i, "field": ", ".join(unknown), "message": "column not in the dataset schema"})
        out = {}
        for f in schema:
            raw = (row.get(f["name"]) or "").strip()
            if not raw:
                if f.get("required"):
                    errors.append({"row": i, "field": f["name"], "message": "required value missing"})
                out[f["name"]] = None
                continue
            try:
                out[f["name"]] = _typed(raw, f["type"])
            except ValueError:
                errors.append({"row": i, "field": f["name"], "message": f"not a valid {f['type']}: {raw[:40]}"})
        typed.append(out)
    return typed, errors


def content_hash(rows: list[dict]) -> str:
    return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def render(rows: list[dict], schema: list[dict], fmt: str) -> tuple[bytes, str]:
    names = [f["name"] for f in schema]
    if fmt == "json":
        return json.dumps(rows, ensure_ascii=False, indent=1).encode(), "application/json"
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=names, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in names})
        return buf.getvalue().encode(), "text/csv; charset=utf-8"
    if fmt == "xml":
        root = ET.Element("rows")
        for r in rows:
            el = ET.SubElement(root, "row")
            for k in names:
                ET.SubElement(el, k).text = "" if r.get(k) is None else str(r.get(k))
        return ET.tostring(root, encoding="utf-8", xml_declaration=True), "application/xml"
    if fmt == "geojson":
        feats = []
        for r in rows:
            if r.get("lat") is None or r.get("lon") is None:
                continue
            props = {k: v for k, v in r.items() if k not in ("lat", "lon")}
            feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
                          "properties": props})
        return json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False).encode(), "application/geo+json"
    raise ValueError(f"unsupported format {fmt}")


RESOLUTION_SCHEMA = [
    {"name": "number", "type": "string", "required": True},
    {"name": "adopted_on", "type": "date", "required": True},
    {"name": "title", "type": "string", "required": True},
    {"name": "subject", "type": "string", "required": False},
    {"name": "status", "type": "string", "required": False},
    {"name": "document_url", "type": "string", "required": False},
]


def resolution_package(content: bytes) -> tuple[list[dict], dict[str, bytes]]:
    """OPZ 1.3, 1.65: a ZIP with resolutions.csv and, for the 1990 to 2010 records, PDF/A files named in its
    'attachment' column; later records carry metadata and a link only. Returns rows and the attached files."""
    try:
        z = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise ImportFailed([{"row": None, "field": None, "message": "not a ZIP package"}]) from None
    names = set(z.namelist())
    if "resolutions.csv" not in names:
        raise ImportFailed([{"row": None, "field": None, "message": "resolutions.csv missing from the package"}])
    rows = parse(z.read("resolutions.csv"), "csv")
    files, errors = {}, []
    for i, row in enumerate(rows, start=1):
        att = (row.pop("attachment", "") or "").strip()
        if att:
            if att not in names:
                errors.append({"row": i, "field": "attachment", "message": f"file {att} not in the package"})
                continue
            data = z.read(att)
            if not data.startswith(b"%PDF"):
                errors.append({"row": i, "field": "attachment", "message": f"{att} is not a PDF"})
                continue
            files[row.get("number", f"row{i}")] = data
    if errors:
        raise ImportFailed(errors)
    return rows, files
