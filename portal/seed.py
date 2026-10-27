"""Synthetic sample data so the model shows every function on first start. Nothing here is real city data."""

from __future__ import annotations

from . import importers, service
from .db import Store

AIR = [
    {"station": "Stacja A (syntetyczna)", "pm10": 21.5, "pm25": 13.1, "lat": 49.8224, "lon": 19.0444},
    {"station": "Stacja B (syntetyczna)", "pm10": 34.2, "pm25": 22.7, "lat": 49.8102, "lon": 19.0312},
    {"station": "Stacja C (syntetyczna)", "pm10": 18.9, "pm25": 10.4, "lat": 49.8301, "lon": 19.0587},
]
BUDGET = [
    {"area": "Oświata", "year": 2026, "amount_pln": 410000000},
    {"area": "Transport", "year": 2026, "amount_pln": 265000000},
    {"area": "Kultura", "year": 2026, "amount_pln": 61000000},
    {"area": "Pomoc społeczna", "year": 2026, "amount_pln": 152000000},
]
RESOLUTIONS = [
    {"number": "XII/101/1991", "adopted_on": "1991-03-14", "title": "Uchwała w sprawie budżetu (przykład syntetyczny)",
     "subject": "budżet", "status": "archiwalna", "document_url": "/static/sample-resolution.pdf"},
    {"number": "LI/1188/2018", "adopted_on": "2018-11-07", "title": "Uchwała w sprawie programu współpracy (przykład syntetyczny)",
     "subject": "współpraca", "status": "obowiązująca", "document_url": "https://example.invalid/uchwala/LI-1188-2018"},
]


def seed_if_empty(store: Store) -> None:
    if service.list_datasets(store, include_archived=True):
        return
    air = service.create_dataset(
        store, title="Jakość powietrza: stacje pomiarowe (syntetyczne)", description="Średnie dobowe PM10 i PM2,5 ze stacji przykładowych.",
        publisher="Wydział Ochrony Środowiska (przykład)", category="ENVI", keywords=["powietrze", "PM10", "środowisko"],
        source="Dane syntetyczne", licence="CC-BY-4.0", frequency="DAILY", geo=True,
        schema=[{"name": "station", "type": "string", "required": True}, {"name": "pm10", "type": "number", "required": True},
                {"name": "pm25", "type": "number", "required": False}, {"name": "lat", "type": "number", "required": True},
                {"name": "lon", "type": "number", "required": True}])
    service.import_rows(store, air, [{k: str(v) for k, v in r.items()} for r in AIR], mode="seed", source_format="json")
    budget = service.create_dataset(
        store, title="Budżet miasta według obszarów (syntetyczny)", description="Planowane wydatki według obszarów, kwoty przykładowe.",
        publisher="Wydział Finansowy (przykład)", category="GOVE", keywords=["budżet", "finanse"],
        source="Dane syntetyczne", licence="CC0-1.0", frequency="ANNUAL",
        schema=[{"name": "area", "type": "string", "required": True}, {"name": "year", "type": "integer", "required": True},
                {"name": "amount_pln", "type": "integer", "required": True}])
    service.import_rows(store, budget, [{k: str(v) for k, v in r.items()} for r in BUDGET], mode="seed", source_format="csv")
    res = service.create_dataset(
        store, title="Uchwały Rady Miejskiej", description="Zestawienie uchwał z metadanymi i załącznikami PDF/A lub odnośnikami (przykłady syntetyczne).",
        publisher="Biuro Rady Miejskiej (przykład)", category="GOVE", keywords=["uchwały", "rada miejska"],
        source="Dane syntetyczne", licence="CC-BY-4.0", frequency="MONTHLY", schema=importers.RESOLUTION_SCHEMA)
    assert res == "uchwaly-rady-miejskiej"
    service.import_rows(store, res, [dict(r) for r in RESOLUTIONS], mode="seed", source_format="csv")
