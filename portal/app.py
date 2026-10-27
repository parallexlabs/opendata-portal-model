"""The model portal: a versioned public API, the DCAT-AP-PL catalog, and an accessible web interface.

Run: .venv/bin/uvicorn portal.app:app --reload   (data in ./data/portal.sqlite3; sample data loaded on first start)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import dcat, importers, security, service
from .db import Store
from .i18n import t

HERE = Path(__file__).parent
API_VERSION = "1.0"
CATALOG_TITLE = "Portal Otwartych Danych (model)"
CATALOG_PUBLISHER = "Urząd Miejski (dane przykładowe)"


def create_app(db_path: str | None = None, base_url: str = "http://127.0.0.1:8000", seed: bool = True,
               rate: int = 30, per: float = 10.0, upload_dir: str | None = None) -> FastAPI:
    store = Store(db_path or os.environ.get("PORTAL_DB", str(HERE.parent / "data" / "portal.sqlite3")))
    if seed:
        from .seed import seed_if_empty
        seed_if_empty(store)
    app = FastAPI(title="Open data portal model API", version=API_VERSION,
                  description="Public, anonymous API by default; resources an administrator protects need an API key "
                              "in the X-API-Key header. Requests are rate-limited per key or address (HTTP 429 with "
                              "Retry-After). Every route is versioned under /api/1.0.",
                  docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    app.state.store, app.state.base = store, base_url.rstrip("/")
    app.state.uploads = Path(upload_dir or os.environ.get("PORTAL_UPLOADS", str(HERE.parent / "data" / "uploads")))
    app.state.limiter = security.RateLimiter(rate=rate, per=per)
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    @app.middleware("http")
    async def limit_api(request: Request, call_next):
        if request.url.path.startswith("/api/") and not request.url.path.startswith(("/api/docs", "/api/openapi")):
            key = request.headers.get("x-api-key")
            client = ("key:" + security.token_hash(key)) if key else ("ip:" + (request.client.host if request.client else "?"))
            wait = app.state.limiter.check(client)
            if wait:
                return JSONResponse({"error": "too many requests", "retry_after_s": round(wait, 1)}, status_code=429,
                                    headers={"Retry-After": str(max(1, int(wait + 0.999)))})
        response = await call_next(request)
        response.headers.setdefault("X-API-Version", API_VERSION)
        return response

    api = APIRouter(prefix=f"/api/{API_VERSION}", tags=["data"])

    def public_meta(d: dict) -> dict:
        return {"id": d["id"], "title": d["title"], "description": d["description"], "publisher": d["publisher"],
                "category": d["category"], "keywords": json.loads(d["keywords"]), "source": d["source"],
                "licence": d["licence"], "frequency": d["frequency"], "modified": d["modified_at"],
                "requires_api_key": bool(d["requires_key"]), "spatial": bool(d["geo"]),
                "formats": ["csv", "json", "xml"] + (["geojson"] if d["geo"] else [])}

    @api.get("/datasets", summary="Search and list datasets")
    def api_datasets(q: str = "", category: str = "", publisher: str = "", keyword: str = "",
                     sort: str = Query("title", pattern="^(title|newest|modified)$")):
        return [public_meta(d) for d in service.list_datasets(store, query=q, category=category, publisher=publisher,
                                                               keyword=keyword, sort=sort)]

    @api.get("/datasets/{ds_id}", summary="One dataset's metadata")
    def api_dataset(ds_id: str):
        d = service.get_dataset(store, ds_id)
        if d is None or d["archived"]:
            raise HTTPException(404, "no such dataset")
        return public_meta(d) | {"versions": service.versions(store, ds_id)}

    @api.get("/datasets/{ds_id}/data", summary="Data in CSV, JSON, XML or GeoJSON; archived versions by number")
    def api_data(ds_id: str, request: Request, format: str = Query("json", pattern="^(json|csv|xml|geojson)$"),
                 version: int | None = None):
        d = service.get_dataset(store, ds_id)
        if d is None:
            raise HTTPException(404, "no such dataset")
        if d["requires_key"] and service.key_user(store, request.headers.get("x-api-key")) is None:
            raise HTTPException(401, "this resource needs a valid API key in the X-API-Key header")
        if format == "geojson" and not d["geo"]:
            raise HTTPException(400, "this dataset is not spatial")
        rows = service.version_rows(store, ds_id, version)
        if rows is None:
            raise HTTPException(404, "no such version")
        body, media = importers.render(rows, json.loads(d["schema_json"]), format)
        return Response(body, media_type=media)

    app.include_router(api)

    @app.get("/catalog.{ext}", tags=["catalog"], summary="The DCAT-AP-PL catalog in RDF")
    def catalog(ext: str):
        if ext not in dcat.SERIALISATIONS:
            raise HTTPException(404, "use .ttl, .rdf, .nt or .jsonld")
        g = dcat.catalog_graph(app.state.base, service.list_datasets(store), CATALOG_TITLE, CATALOG_PUBLISHER)
        body, media = dcat.serialise(g, ext)
        return Response(body, media_type=media)

    # ---------- web interface ----------

    def user_of(request: Request) -> dict | None:
        return service.session_user(store, request.cookies.get("odp_session"))

    def page(request: Request, name: str, **ctx) -> HTMLResponse:
        lang = request.query_params.get("lang") or request.cookies.get("odp_lang") or "pl"
        ctx.update(request=request, user=user_of(request), lang=lang, t=lambda k: t(k, lang),
                   announcement=store.setting("announcement"), catalog_title=CATALOG_TITLE)
        return templates.TemplateResponse(request, name, ctx)

    def need(request: Request, roles: tuple[str, ...]) -> dict:
        u = user_of(request)
        if u is None or u["role"] not in roles:
            raise HTTPException(403, "not allowed")
        return u

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def home(request: Request):
        recent = service.list_datasets(store, sort="modified")[:6]
        return page(request, "home.html", recent=recent)

    @app.get("/datasets", response_class=HTMLResponse, include_in_schema=False)
    def catalog_page(request: Request, q: str = "", category: str = "", publisher: str = "", keyword: str = "",
                     sort: str = "title"):
        filters = json.loads(store.setting("filters"))
        all_ds = service.list_datasets(store)
        found = service.list_datasets(store, query=q, category=category if filters.get("category") else "",
                                      publisher=publisher if filters.get("publisher") else "",
                                      keyword=keyword if filters.get("keyword") else "", sort=sort)
        return page(request, "catalog.html", datasets=found, q=q, sort=sort, filters=filters,
                    categories=sorted({d["category"] for d in all_ds}), publishers=sorted({d["publisher"] for d in all_ds}),
                    keywords=sorted({k for d in all_ds for k in json.loads(d["keywords"])}),
                    sel={"category": category, "publisher": publisher, "keyword": keyword})

    @app.get("/datasets/{ds_id}", response_class=HTMLResponse, include_in_schema=False)
    def dataset_page(request: Request, ds_id: str):
        d = service.get_dataset(store, ds_id)
        if d is None or (d["archived"] and not user_of(request)):
            raise HTTPException(404, "no such dataset")
        schema = json.loads(d["schema_json"])
        rows = service.version_rows(store, ds_id) or []
        numeric = [f["name"] for f in schema if f["type"] in ("integer", "number") and f["name"] not in ("lat", "lon")]
        chart = None
        if numeric and rows:
            label = next((f["name"] for f in schema if f["type"] == "string"), schema[0]["name"])
            vals = [(str(r.get(label)), r.get(numeric[0]) or 0) for r in rows[:12]]
            top = max((v for _, v in vals), default=0) or 1
            chart = {"field": numeric[0], "label": label, "bars": [(n, v, 100 * v / top) for n, v in vals]}
        geojson = importers.render(rows, schema, "geojson")[0].decode() if d["geo"] else None
        with store.tx() as con:
            atts = [dict(r) for r in con.execute("SELECT * FROM attachments WHERE dataset_id=?", (ds_id,))]
            links = [dict(r) for r in con.execute("SELECT * FROM links WHERE dataset_id=?", (ds_id,))]
        return page(request, "dataset.html", d=d, schema=schema, rows=rows[:50], total=len(rows), chart=chart,
                    geojson=geojson, versions=service.versions(store, ds_id), stale=service.staleness(d),
                    keywords=json.loads(d["keywords"]), attachments=atts, links=links)

    @app.get("/register", response_class=HTMLResponse, include_in_schema=False)
    def register_form(request: Request):
        return page(request, "register.html", stamp=security.form_stamp(), error=None, rule=security.PASSWORD_RULE)

    @app.post("/register", response_class=HTMLResponse, include_in_schema=False)
    def register_post(request: Request, email: str = Form(...), name: str = Form(...), organisation: str = Form(""),
                      password: str = Form(...), accept_terms: str = Form(""), stamp: str = Form(""), website: str = Form("")):
        problem = security.bot_problem(stamp, website)
        if problem:
            return page(request, "register.html", stamp=security.form_stamp(), error=t("bot_refused", "pl"), rule=security.PASSWORD_RULE)
        try:
            token = service.register(store, email=email, name=name, organisation=organisation, password=password,
                                     accept_terms=bool(accept_terms))
        except service.Refused as e:
            return page(request, "register.html", stamp=security.form_stamp(), error=str(e), rule=security.PASSWORD_RULE)
        outbox = Path(store.path).with_name("outbox.log") if store.path != ":memory:" else None
        if outbox:
            with open(outbox, "a", encoding="utf-8") as f:   # the model's stand-in for the activation e-mail
                f.write(f"to {email.strip().lower()}: {app.state.base}/activate?token={token}\n")
        return page(request, "message.html", message=t("check_email", "pl"))

    @app.get("/activate", response_class=HTMLResponse, include_in_schema=False)
    def activate(request: Request, token: str = ""):
        ok = service.activate(store, token)
        return page(request, "message.html", message=t("activated" if ok else "activation_failed", "pl"))

    @app.get("/login", response_class=HTMLResponse, include_in_schema=False)
    def login_form(request: Request):
        return page(request, "login.html", error=None)

    @app.post("/login", include_in_schema=False)
    def login_post(request: Request, email: str = Form(...), password: str = Form(...), totp: str = Form("")):
        try:
            token = service.sign_in(store, email=email, password=password, totp=totp,
                                    ip=request.client.host if request.client else "")
        except service.Refused:
            return page(request, "login.html", error=t("login_failed", "pl"))
        r = RedirectResponse("/account", status_code=303)
        r.set_cookie("odp_session", token, httponly=True, samesite="strict", secure=app.state.base.startswith("https"))
        return r

    @app.post("/logout", include_in_schema=False)
    def logout(request: Request):
        token = request.cookies.get("odp_session")
        if token:
            service.sign_out(store, token)
        r = RedirectResponse("/", status_code=303)
        r.delete_cookie("odp_session")
        return r

    @app.get("/account", response_class=HTMLResponse, include_in_schema=False)
    def account(request: Request, new_key: str = "", totp_secret: str = ""):
        u = need(request, ("admin", "internal", "apikey"))
        return page(request, "account.html", keys=service.user_keys(store, u["id"]), new_key=new_key, totp_secret=totp_secret)

    @app.post("/account/keys", include_in_schema=False)
    def account_key(request: Request, label: str = Form("")):
        u = need(request, ("admin", "internal", "apikey"))
        key = service.issue_key(store, u["id"], label)
        return page(request, "account.html", keys=service.user_keys(store, u["id"]), new_key=key, totp_secret="")

    @app.post("/account/keys/revoke", include_in_schema=False)
    def account_revoke(request: Request, key_hash: str = Form(...)):
        u = need(request, ("admin", "internal", "apikey"))
        service.revoke_key(store, u["id"], key_hash)
        return RedirectResponse("/account", status_code=303)

    @app.post("/account/totp", include_in_schema=False)
    def account_totp(request: Request):
        u = need(request, ("admin", "internal", "apikey"))
        secret = service.enable_totp(store, u["id"])
        return page(request, "account.html", keys=service.user_keys(store, u["id"]), new_key="", totp_secret=secret)

    @app.post("/account/delete", include_in_schema=False)
    def account_delete(request: Request, confirm: str = Form("")):
        u = need(request, ("apikey",))
        if confirm != "DELETE":
            raise HTTPException(400, "type DELETE to confirm")
        service.delete_account(store, u["id"])
        r = RedirectResponse("/", status_code=303)
        r.delete_cookie("odp_session")
        return r

    @app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
    def admin(request: Request):
        u = need(request, ("admin", "internal"))
        ds = service.list_datasets(store, include_archived=True)
        history = {d["id"]: service.import_history(store, d["id"])[:5] for d in ds}
        logins = service.login_history(store, 50) if u["role"] == "admin" else []
        return page(request, "admin.html", datasets=ds, history=history, logins=logins,
                    filters=json.loads(store.setting("filters")))

    @app.post("/admin/import", include_in_schema=False)
    async def admin_import(request: Request, ds_id: str = Form(...), file: UploadFile = File(...)):
        need(request, ("admin", "internal"))
        fmt = (file.filename or "").rsplit(".", 1)[-1].lower()
        content = await file.read()
        try:
            rows = importers.parse(content, fmt)
            result = service.import_rows(store, ds_id, rows, mode="manual", source_format=fmt)
        except importers.ImportFailed as e:
            with store.tx() as con:
                con.execute("INSERT INTO imports(dataset_id, at, mode, ok, errors_json) VALUES (?, datetime('now'), 'manual', 0, ?)",
                            (ds_id, json.dumps(e.errors, ensure_ascii=False)))
            result = {"ok": False, "errors": e.errors}
        return page(request, "import_result.html", ds_id=ds_id, result=result)

    @app.post("/admin/dataset/{ds_id}/flags", include_in_schema=False)
    def admin_flags(request: Request, ds_id: str, requires_key: str = Form(""), archived: str = Form("")):
        need(request, ("admin",))
        service.set_requires_key(store, ds_id, bool(requires_key))
        service.archive_dataset(store, ds_id, bool(archived))
        return RedirectResponse("/admin", status_code=303)

    @app.post("/admin/filters", include_in_schema=False)
    def admin_filters(request: Request, category: str = Form(""), publisher: str = Form(""), keyword: str = Form(""),
                      format: str = Form("")):
        need(request, ("admin",))
        store.set_setting("filters", json.dumps({"category": bool(category), "publisher": bool(publisher),
                                                  "keyword": bool(keyword), "format": bool(format)}))
        return RedirectResponse("/admin", status_code=303)

    @app.post("/admin/announcement", include_in_schema=False)
    def admin_announcement(request: Request, text: str = Form("")):
        need(request, ("admin",))
        store.set_setting("announcement", text.strip()[:500])
        return RedirectResponse("/admin", status_code=303)

    @app.post("/admin/resolutions", include_in_schema=False)
    def admin_resolution(request: Request, number: str = Form(...), adopted_on: str = Form(...), title: str = Form(...),
                         subject: str = Form(""), status: str = Form(""), document_url: str = Form("")):
        """OPZ 1.65.5: an internal user adds a new resolution by hand; it becomes a new version of the set."""
        need(request, ("admin", "internal"))
        rows = service.version_rows(store, "uchwaly-rady-miejskiej") or []
        rows = [{k: ("" if v is None else str(v)) for k, v in r.items()} for r in rows]
        rows.append({"number": number, "adopted_on": adopted_on, "title": title, "subject": subject, "status": status,
                     "document_url": document_url})
        result = service.import_rows(store, "uchwaly-rady-miejskiej", rows, mode="manual entry", source_format="form")
        return page(request, "import_result.html", ds_id="uchwaly-rady-miejskiej", result=result)

    @app.post("/admin/resolutions/package", include_in_schema=False)
    async def admin_resolution_package(request: Request, file: UploadFile = File(...)):
        """OPZ 1.3, 1.65.4: a package of resolutions; records from 1990 to 2010 carry PDF/A files, later ones
        metadata and a link only. Files are stored and linked; the set becomes a new version."""
        need(request, ("admin", "internal"))
        try:
            rows, files = importers.resolution_package(await file.read())
        except importers.ImportFailed as e:
            return page(request, "import_result.html", ds_id="uchwaly-rady-miejskiej", result={"ok": False, "errors": e.errors})
        folder = app.state.uploads / "resolutions"
        folder.mkdir(parents=True, exist_ok=True)
        with store.tx() as con:
            for number, data in files.items():
                name = re.sub(r"[^A-Za-z0-9._-]+", "_", number) + ".pdf"
                (folder / name).write_bytes(data)
                url = f"/files/resolutions/{name}"
                con.execute("INSERT INTO attachments(dataset_id, record_key, name, kind, url, sha256) VALUES (?,?,?,?,?,?)",
                            ("uchwaly-rady-miejskiej", number, name, "PDF/A", url, hashlib.sha256(data).hexdigest()))
                for r in rows:
                    if r.get("number") == number:
                        r["document_url"] = url
        result = service.import_rows(store, "uchwaly-rady-miejskiej", rows, mode="package", source_format="zip")
        return page(request, "import_result.html", ds_id="uchwaly-rady-miejskiej", result=result)

    @app.get("/files/resolutions/{name}", include_in_schema=False)
    def resolution_file(name: str):
        if not re.fullmatch(r"[A-Za-z0-9._-]+\.pdf", name):
            raise HTTPException(404, "no such file")
        path = app.state.uploads / "resolutions" / name
        if not path.is_file():
            raise HTTPException(404, "no such file")
        return Response(path.read_bytes(), media_type="application/pdf")

    return app


app = create_app() if os.environ.get("PORTAL_NO_APP") != "1" else None
