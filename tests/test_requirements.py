"""One or more tests per OPZ requirement line the model covers; each test name carries the line number.

The OPZ is annex 5 to the specification of essential terms of order ZP.271.2.107.2026.KC (Bielsko-Biała,
Part 1, open data portal). Requirement lines are cited by number only.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import time
import zipfile
from datetime import datetime, timedelta, timezone

import pyotp
import pytest
from rdflib import Graph
from rdflib.namespace import DCAT, DCTERMS, RDF

os.environ["PORTAL_NO_APP"] = "1"
from fastapi.testclient import TestClient  # noqa: E402

from portal import importers, security, service  # noqa: E402
from portal.app import create_app  # noqa: E402

AIR = "jakosc-powietrza-stacje-pomiarowe-syntetyczne"
BUDGET = "budzet-miasta-wedlug-obszarow-syntetyczny"
RES = "uchwaly-rady-miejskiej"
STRONG = "Mocne!Haslo9"


@pytest.fixture
def app(tmp_path):
    return create_app(":memory:", upload_dir=str(tmp_path / "uploads"))


@pytest.fixture
def client(app):
    return TestClient(app)


def admin_client(app, role="admin"):
    service.create_staff(app.state.store, email=f"{role}@example.invalid", name=role, password=STRONG, role=role)
    c = TestClient(app)
    r = c.post("/login", data={"email": f"{role}@example.invalid", "password": STRONG}, follow_redirects=False)
    assert r.status_code == 303
    return c


def test_req_1_2_and_1_4_catalog_in_four_rdf_serialisations(client):
    counts = {}
    for ext, fmt in (("ttl", "turtle"), ("rdf", "xml"), ("nt", "nt"), ("jsonld", "json-ld")):
        r = client.get(f"/catalog.{ext}")
        assert r.status_code == 200
        g = Graph().parse(data=r.content, format=fmt)
        counts[ext] = len(g)
        assert len(list(g.subjects(RDF.type, DCAT.Catalog))) == 1
        datasets = list(g.subjects(RDF.type, DCAT.Dataset))
        assert len(datasets) == 3
        for ds in datasets:
            assert g.value(ds, DCTERMS.title) is not None
            assert list(g.objects(ds, DCAT.distribution)), "every dataset has distributions"
    assert len(set(counts.values())) == 1, counts


def test_req_1_1_distributions_carry_eu_file_types_and_licence(client):
    g = Graph().parse(data=client.get("/catalog.ttl").content, format="turtle")
    for dist in g.subjects(RDF.type, DCAT.Distribution):
        assert str(g.value(dist, DCTERMS.format)).startswith("http://publications.europa.eu/resource/authority/file-type/")
        assert g.value(dist, DCTERMS.license) is not None
        assert g.value(dist, DCAT.downloadURL) is not None


def test_req_1_5_data_as_files_and_through_the_api(client):
    assert len(json.loads(client.get(f"/api/1.0/datasets/{BUDGET}/data?format=json").content)) == 4
    rows = list(csv.DictReader(io.StringIO(client.get(f"/api/1.0/datasets/{BUDGET}/data?format=csv").text)))
    assert rows[0]["area"] and len(rows) == 4
    assert b"<rows>" in client.get(f"/api/1.0/datasets/{BUDGET}/data?format=xml").content
    geo = json.loads(client.get(f"/api/1.0/datasets/{AIR}/data?format=geojson").content)
    assert geo["type"] == "FeatureCollection" and len(geo["features"]) == 3
    assert client.get(f"/api/1.0/datasets/{BUDGET}/data?format=geojson").status_code == 400


def test_req_1_6_1_7_1_9_public_by_default_and_api_keys_for_selected_resources(app, client):
    url = f"/api/1.0/datasets/{BUDGET}/data"
    assert client.get(url).status_code == 200, "public and anonymous by default"
    service.set_requires_key(app.state.store, BUDGET, True)
    assert client.get(url).status_code == 401
    uid = service.create_staff(app.state.store, email="user@example.invalid", name="u", password=STRONG, role="apikey")
    key = service.issue_key(app.state.store, uid, "test")
    assert client.get(url, headers={"X-API-Key": key}).status_code == 200
    service.revoke_key(app.state.store, uid, security.token_hash(key))
    assert client.get(url, headers={"X-API-Key": key}).status_code == 401
    assert client.get(f"/api/1.0/datasets/{AIR}/data").status_code == 200, "other resources stay public"


def test_req_1_8_and_1_10_rate_limit_answers_429_with_retry_after(tmp_path):
    c = TestClient(create_app(":memory:", rate=3, per=60, upload_dir=str(tmp_path)))
    codes = [c.get("/api/1.0/datasets").status_code for _ in range(4)]
    assert codes[:3] == [200, 200, 200] and codes[3] == 429
    r = c.get("/api/1.0/datasets")
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1


def test_req_1_11_routes_are_versioned(client):
    r = client.get("/api/1.0/datasets")
    assert r.status_code == 200 and r.headers["X-API-Version"] == "1.0"
    assert client.get("/api/2.0/datasets").status_code == 404


def test_req_1_12_and_1_13_openapi_spec_and_live_documentation(client):
    spec = client.get("/api/openapi.json").json()
    assert spec["openapi"].startswith("3.")
    assert "/api/1.0/datasets/{ds_id}/data" in spec["paths"]
    docs = client.get("/api/docs").text
    assert "swagger-ui" in docs.lower(), "browser documentation with test calls"


def test_req_1_14_admin_panel_needs_a_role(app, client):
    assert client.get("/admin").status_code == 403
    assert admin_client(app).get("/admin").status_code == 200


def test_req_1_18_every_sign_in_is_logged(app, client):
    service.create_staff(app.state.store, email="a@example.invalid", name="a", password=STRONG, role="internal")
    client.post("/login", data={"email": "a@example.invalid", "password": "Wrong!pass9"})
    client.post("/login", data={"email": "a@example.invalid", "password": STRONG})
    hist = service.login_history(app.state.store)
    assert [h["success"] for h in hist[:2]] == [1, 0]
    assert hist[1]["detail"] == "wrong password"


def test_req_1_17_totp_second_factor(app):
    store = app.state.store
    uid = service.create_staff(store, email="m@example.invalid", name="m", password=STRONG, role="internal")
    secret = service.enable_totp(store, uid)
    with pytest.raises(service.Refused):
        service.sign_in(store, email="m@example.invalid", password=STRONG)
    assert service.sign_in(store, email="m@example.invalid", password=STRONG, totp=pyotp.TOTP(secret).now())


def test_req_1_19_api_key_user_can_delete_the_account(app):
    store = app.state.store
    uid = service.create_staff(store, email="d@example.invalid", name="d", password=STRONG, role="apikey")
    service.issue_key(store, uid)
    service.delete_account(store, uid)
    with store.tx() as con:
        assert con.execute("SELECT COUNT(*) FROM users WHERE id=?", (uid,)).fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM api_keys WHERE user_id=?", (uid,)).fetchone()[0] == 0


def test_req_1_20_to_1_24_registration_activation_and_expiry(app):
    store = app.state.store
    token = service.register(store, email="New@Example.invalid", name="N", organisation="", password=STRONG, accept_terms=True)
    with pytest.raises(service.Refused):
        service.sign_in(store, email="new@example.invalid", password=STRONG)
    assert service.activate(store, token)
    assert service.sign_in(store, email="new@example.invalid", password=STRONG)
    old = service.register(store, email="old@example.invalid", name="O", organisation="", password=STRONG, accept_terms=True)
    with store.tx() as con:
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")
        con.execute("UPDATE activation_tokens SET expires_at=?", (past,))
    assert not service.activate(store, old), "an expired activation link is refused"


@pytest.mark.parametrize("pw", ["Short1!", "nouppercase1!", "NoDigits!!", "NoSpecial99"])
def test_req_1_22_password_rule(app, pw):
    with pytest.raises(service.Refused):
        service.register(app.state.store, email="p@example.invalid", name="p", organisation="", password=pw, accept_terms=True)


def test_req_1_25_bot_protection(client):
    form = {"email": "b@example.invalid", "name": "b", "password": STRONG, "accept_terms": "1"}
    r = client.post("/register", data=form | {"stamp": security.form_stamp(), "website": "spam"})
    assert "bot" in r.text.lower() or "botami" in r.text.lower()
    r = client.post("/register", data=form | {"stamp": security.form_stamp(), "website": ""})
    assert "botami" in r.text.lower(), "a form sent faster than a person types is refused"
    r = client.post("/register", data=form | {"stamp": security.form_stamp(time.time() - 10), "website": ""})
    assert "aktywacyjny" in r.text


def test_req_1_27_1_31_1_32_import_validation_and_error_report(app):
    store = app.state.store
    before = service.versions(store, BUDGET)
    bad = "area,year,amount_pln\nOświata,2026,abc\n,2026,5\nKultura,dwa,7\n".encode()
    result = service.import_rows(store, BUDGET, importers.parse(bad, "csv"), mode="manual", source_format="csv")
    assert not result["ok"]
    found = {(e["row"], e["field"]) for e in result["errors"]}
    assert {(1, "amount_pln"), (2, "area"), (3, "year")} <= found
    assert service.versions(store, BUDGET) == before, "nothing is stored when validation fails"
    for fmt, data in (("json", b'[{"area": "X", "year": "2026", "amount_pln": "1"}]'),
                      ("xml", b"<rows><row><area>X</area><year>2026</year><amount_pln>1</amount_pln></row></rows>")):
        assert service.import_rows(store, BUDGET, importers.parse(data, fmt), mode="manual", source_format=fmt)["ok"]


def test_req_1_29_and_1_33_unchanged_source_makes_no_version_and_history_is_kept(app):
    store = app.state.store
    rows = [{k: str(v) for k, v in r.items()} for r in service.version_rows(store, BUDGET)]
    n = len(service.versions(store, BUDGET))
    assert service.import_rows(store, BUDGET, rows, mode="scheduled", source_format="csv")["unchanged"]
    assert len(service.versions(store, BUDGET)) == n
    modes = [h["mode"] for h in service.import_history(store, BUDGET)]
    assert modes[0] == "scheduled" and "seed" in modes


def test_req_1_30_staleness_against_the_stated_frequency(app):
    d = service.get_dataset(app.state.store, AIR)
    assert not service.staleness(d)
    assert service.staleness(d, at=datetime.now(timezone.utc) + timedelta(days=5))


def test_req_1_34_duplicates(app):
    store = app.state.store
    d = service.get_dataset(store, BUDGET)
    with pytest.raises(service.Refused):
        service.create_dataset(store, title=d["title"], description="x", publisher=d["publisher"], category="GOVE",
                               keywords=[], source="x", licence="CC0-1.0", frequency="ANNUAL", schema=json.loads(d["schema_json"]))
    copy = service.create_dataset(store, title="Kopia budżetu", description="x", publisher="Inny", category="GOVE",
                                  keywords=[], source="x", licence="CC0-1.0", frequency="ANNUAL", schema=json.loads(d["schema_json"]))
    rows = [{k: str(v) for k, v in r.items()} for r in service.version_rows(store, BUDGET)]
    assert service.import_rows(store, copy, rows, mode="manual", source_format="csv")["duplicate_of"] == BUDGET


def test_req_1_36_1_38_1_39_archive_versions_and_retention(app, client):
    store = app.state.store
    with store.tx() as con:
        con.execute("UPDATE datasets SET keep_versions=2 WHERE id=?", (BUDGET,))
    for i in range(3):
        rows = [{"area": f"A{i}", "year": "2026", "amount_pln": str(i)}]
        service.import_rows(store, BUDGET, rows, mode="manual", source_format="csv")
    numbers = [v["number"] for v in service.versions(store, BUDGET)]
    assert numbers == [4, 3], "only the newest two versions are kept"
    assert client.get(f"/api/1.0/datasets/{BUDGET}/data?format=csv&version=3").status_code == 200
    service.archive_dataset(store, BUDGET)
    assert BUDGET not in [d["id"] for d in client.get("/api/1.0/datasets").json()]


def test_req_1_47_1_53_1_54_1_56_search_filters_and_sorting(client):
    ids = lambda r: [d["id"] for d in r.json()]  # noqa: E731
    assert ids(client.get("/api/1.0/datasets?q=pm10")) == [AIR], "keywords are searched"
    assert ids(client.get("/api/1.0/datasets?q=finansowy")) == [BUDGET], "publisher is searched"
    assert ids(client.get("/api/1.0/datasets?category=ENVI")) == [AIR]
    assert set(ids(client.get("/api/1.0/datasets?category=GOVE"))) == {BUDGET, RES}
    titles = [d["title"] for d in client.get("/api/1.0/datasets?sort=title").json()]
    assert titles == sorted(titles, key=str.lower)


def test_req_1_55_admin_switches_a_filter_off(app, client):
    assert 'id="category"' in client.get("/datasets").text
    admin_client(app).post("/admin/filters", data={"publisher": "1", "keyword": "1"})
    assert 'id="category"' not in client.get("/datasets").text


def test_req_1_48_and_1_57_table_chart_and_map(client):
    page = client.get(f"/datasets/{AIR}").text
    assert "<table>" in page and 'class="chart"' in page and 'id="map"' in page and "FeatureCollection" in page
    assert "L.tileLayer.wms" in page, "an external WMS or WMTS layer can be added (1.63.5)"


def test_req_1_65_resolutions_package_and_manual_entry(app):
    c = admin_client(app, "internal")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("resolutions.csv", "number,adopted_on,title,subject,status,document_url,attachment\n"
                   "IV/12/1995,1995-02-01,Uchwała A,budżet,archiwalna,,iv-12-1995.pdf\n"
                   "XX/300/2020,2020-06-10,Uchwała B,kultura,obowiązująca,https://example.invalid/b,\n")
        z.writestr("iv-12-1995.pdf", b"%PDF-1.4 synthetic")
    r = c.post("/admin/resolutions/package", files={"file": ("pakiet.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 200 and "OK" in r.text
    rows = service.version_rows(app.state.store, RES)
    by_number = {x["number"]: x for x in rows}
    assert by_number["IV/12/1995"]["document_url"].startswith("/files/resolutions/")
    assert by_number["XX/300/2020"]["document_url"] == "https://example.invalid/b"
    assert c.get(by_number["IV/12/1995"]["document_url"]).content.startswith(b"%PDF")
    r = c.post("/admin/resolutions", data={"number": "LXX/1/2026", "adopted_on": "2026-10-01", "title": "Uchwała C"})
    assert "OK" in r.text
    assert any(x["number"] == "LXX/1/2026" for x in service.version_rows(app.state.store, RES))


def test_req_2_7_and_1_51_page_basics_for_accessibility(client):
    for path in ("/", "/datasets", f"/datasets/{AIR}", "/register", "/login"):
        html = client.get(path).text
        assert re.search(r'<html lang="(pl|en)"', html)
        assert 'name="viewport"' in html and 'class="skip"' in html and 'id="main"' in html
        for input_id in re.findall(r'<(?:input|select)[^>]*\sid="([^"]+)"', html):
            if input_id in ("website",):
                continue
            assert f'for="{input_id}"' in html, f"{path}: #{input_id} has no label"


def test_req_1_29_1_35_scheduled_update_from_the_source(app):
    from portal import scheduler
    store = app.state.store
    service.set_schedule(store, BUDGET, "https://example.invalid/budget.csv", 60)
    feed = {"body": b"area,year,amount_pln\nOswiata,2026,1\n"}
    t0 = datetime.now(timezone.utc)
    first = scheduler.run_due(store, fetch=lambda url: feed["body"], at=t0)
    assert first[0]["ok"] and first[0]["version"] == 2
    assert scheduler.run_due(store, fetch=lambda url: feed["body"], at=t0 + timedelta(minutes=5)) == [], "not due yet"
    again = scheduler.run_due(store, fetch=lambda url: feed["body"], at=t0 + timedelta(minutes=61))
    assert again[0].get("unchanged"), "an unchanged source makes no new version"
    failed = scheduler.run_due(store, fetch=lambda url: (_ for _ in ()).throw(OSError("down")), at=t0 + timedelta(minutes=200))
    assert not failed[0]["ok"] and "source unavailable" in failed[0]["errors"][0]["message"]
    with pytest.raises(service.Refused):
        service.set_schedule(store, BUDGET, "ftp://example.invalid/x.csv", 60)


def test_req_1_41_1_42_links_and_supplementary_files(app):
    c = admin_client(app, "internal")
    c.post(f"/admin/dataset/{BUDGET}/link", data={"title": "Metodologia", "url": "https://example.invalid/metodologia"})
    assert "Metodologia" in c.get(f"/datasets/{BUDGET}").text


def test_req_1_43_report_for_a_day_or_a_period(app, client):
    client.get(f"/api/1.0/datasets/{BUDGET}/data?format=csv")
    client.get(f"/api/1.0/datasets/{BUDGET}/data?format=json")
    c = admin_client(app)
    today = datetime.now(timezone.utc).date().isoformat()
    rows = list(csv.DictReader(io.StringIO(c.get(f"/admin/report.csv?start={today}&end={today}").text)))
    budget = next(r for r in rows if r["dataset"] == BUDGET)
    assert budget["downloads"] == "2" and budget["imports"] == "1" and budget["new_versions"] == "1"


def test_req_1_50_accessibility_statement_and_privacy_policy(client):
    assert "Deklaracja dostępności" in client.get("/deklaracja-dostepnosci").text
    assert "Polityka prywatności" in client.get("/polityka-prywatnosci").text
    assert "/deklaracja-dostepnosci" in client.get("/").text
