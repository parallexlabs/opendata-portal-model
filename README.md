# Open data portal model

A working model of a municipal open data portal, built against the requirement list of a public tender for an
open data portal (Bielsko-Biała, Poland, proceeding ZP.271.2.107.2026.KC, Part 1). The tender's first stage is
a model of the basic functions, an administration panel and a partial data set; this repository is that kind
of model, built before any contract to show what the delivery would look like.

All data in it is sample or synthetic, and every page says so. Requirement lines are cited by number only; the
tender text is not copied here.

## Run it

```
make run     # creates .venv, installs the pinned requirements, serves http://127.0.0.1:8000
make test    # the requirement tests
```

Python 3.11 or later. Data goes to `data/portal.sqlite3`; three sample datasets load on first start: air
quality stations (spatial), a city budget by area, and council resolutions.

What to look at:

- `/datasets`: search, filters, sorting; each dataset page shows a table, a chart and, for spatial data, a map
  with an external WMS or WMTS layer control laid over it.
- `/api/docs`: the OpenAPI documentation, with live test calls.
- `/catalog.ttl`, `/catalog.rdf`, `/catalog.nt`, `/catalog.jsonld`: the DCAT-AP catalogue.
- `/admin`: imports, the resolutions package, flags, filters, map style, report, sign-in history (sign in as
  a staff account; see `portal/service.py`, `create_staff`).

## Requirement lines and tests

Results at commit `82043bb`: 41 test functions (44 cases with parameters), all passing.

| Line | What the model does | Test | Result |
|---|---|---|---|
| 1.1 | Distributions carry EU file-type and licence URIs and a download URL, so a national catalogue can harvest them | `test_req_1_1_distributions_carry_eu_file_types_and_licence` | pass |
| 1.2, 1.4 | DCAT-AP catalogue in RDF/XML, Turtle, N-Triples and JSON-LD, the same graph in each | `test_req_1_2_and_1_4_catalog_in_four_rdf_serialisations` | pass |
| 1.3, 1.65 | Council resolutions: a ZIP package of metadata with PDF/A files or links, and manual entry by an internal user | `test_req_1_3_and_1_65_resolutions_package_and_manual_entry`<br>`test_req_1_3_package_members_over_the_size_cap_are_refused` | pass |
| 1.5 | Every dataset as files (CSV, JSON, XML; GeoJSON for spatial data) and through the API | `test_req_1_5_data_as_files_and_through_the_api` | pass |
| 1.6, 1.7, 1.9 | API public and anonymous by default. An administrator can require a key per dataset. Keys are issued and revoked | `test_req_1_6_1_7_1_9_public_by_default_and_api_keys_for_selected_resources` | pass |
| 1.8, 1.10 | Rate limit per valid key or per address, HTTP 429 with Retry-After; invented keys do not get their own allowance | `test_req_1_8_and_1_10_rate_limit_answers_429_with_retry_after`<br>`test_req_1_8_invented_api_keys_do_not_escape_the_rate_limit` | pass |
| 1.11 | Versioned routes under /api/1.0 and an X-API-Version header | `test_req_1_11_routes_are_versioned` | pass |
| 1.12, 1.13 | OpenAPI document and live documentation with test calls from the browser | `test_req_1_12_and_1_13_openapi_spec_and_live_documentation` | pass |
| 1.14 | Browser administration panel, by role | `test_req_1_14_admin_panel_needs_a_role` | pass |
| 1.17 | Optional TOTP second factor | `test_req_1_17_totp_second_factor` | pass |
| 1.18 | Every sign-in attempt logged, refused ones included | `test_req_1_18_every_sign_in_is_logged` | pass |
| 1.19 | A user with an API key can delete the account | `test_req_1_19_api_key_user_can_delete_the_account` | pass |
| 1.20 to 1.24 | Self-registration, activation link with an expiry, password rule | `test_req_1_20_to_1_24_registration_activation_and_expiry`<br>`test_req_1_22_password_rule` | pass |
| 1.25 | Bot protection on the registration form (timed form stamp and a hidden field) | `test_req_1_25_bot_protection` | pass |
| 1.27, 1.31, 1.32 | CSV, JSON and XML import with structure validation and an error report by row and field | `test_req_1_27_1_31_1_32_import_validation_and_error_report` | pass |
| 1.29, 1.33, 1.35 | Scheduled updates from a source address. Unchanged content makes no new version. Import history is kept | `test_req_1_29_and_1_33_unchanged_source_makes_no_version_and_history_is_kept`<br>`test_req_1_29_1_35_scheduled_update_from_the_source` | pass |
| 1.30 | Staleness against the stated update frequency | `test_req_1_30_staleness_against_the_stated_frequency` | pass |
| 1.34 | Duplicate datasets detected by content | `test_req_1_34_duplicates` | pass |
| 1.36, 1.38, 1.39 | Archiving, downloadable older versions, retention of the newest N versions | `test_req_1_36_1_38_1_39_archive_versions_and_retention` | pass |
| 1.41, 1.42 | Links and supplementary files on a dataset | `test_req_1_41_1_42_links_and_supplementary_files` | pass |
| 1.43 | Report for a day or a period: downloads, keyed downloads, imports, failed imports, new versions | `test_req_1_43_report_for_a_day_or_a_period`<br>`test_req_1_43_a_file_that_does_not_parse_counts_as_a_failed_import` | pass |
| 1.47, 1.53, 1.54, 1.56 | Catalogue with full-text search, filters and sorting | `test_req_1_47_1_53_1_54_1_56_search_filters_and_sorting` | pass |
| 1.55 | The administrator switches each filter on or off | `test_req_1_55_admin_switches_a_filter_off` | pass |
| 1.48, 1.57 | Structured data shown in the browser as a table, a chart and, for spatial data, a map | `test_req_1_48_and_1_57_table_chart_and_map` | pass |
| 1.50 | Accessibility statement and privacy policy pages (placeholders until agreed with the city) | `test_req_1_50_accessibility_statement_and_privacy_policy` | pass |
| 1.51, 2.7 | Page basics: language, viewport, skip link, labelled fields (plus the scan below) | `test_req_2_7_and_1_51_page_basics_for_accessibility` | pass |
| 1.58, 1.59, 1.61 | Map window, OpenStreetMap base layer, controls laid over the map | `test_req_1_58_1_59_1_61_map_window_base_layer_and_controls_over_the_map` | pass |
| 1.62 | The administrator sets colour, line width and point size per spatial dataset | `test_req_1_62_admin_sets_colour_line_width_and_point_size` | pass |
| 1.63.5 | External WMS or WMTS layer added from the map. The test checks the page wiring; the browser check below drew a local stand-in WMS | `test_req_1_63_5_external_wms_or_wmts_layer` | pass |
| 2.5 | Presentation, logic and storage separated: no SQL in the web layer, no web framework in logic or storage | `test_req_2_5_presentation_logic_and_storage_are_separate_layers` | pass |
| 2.6 | Every interface string exists in Polish and English; another language is one more table | `test_req_2_6_every_string_in_every_language_and_unknown_languages_fall_back` | pass |
| 2.10 | Session cookie HttpOnly, SameSite=Strict and Secure over HTTPS. The token is stored as a hash. Sign-out and expiry end it | `test_req_2_10_session_cookie_flags_sign_out_and_expiry` | pass |
| Hardening | Security headers. Map data and pop-ups cannot carry script. Only web links render as links. Sample data is marked on every page | `test_security_headers_on_pages_api_and_catalog`<br>`test_map_data_and_popups_cannot_carry_script`<br>`test_links_and_document_addresses_only_render_web_links`<br>`test_sample_data_is_marked_synthetic_on_every_page` | pass |

## Browser checks, 2026-10-27

Run in Chromium 155 against a clean clone served on 127.0.0.1.

- Accessibility: axe-core 4.14.0 with the WCAG 2.0 and 2.1 A and AA rules, on 12 pages (home, catalogue, the
  three dataset pages, registration, sign-in, both statements, the English home page, and the signed-in account
  and administration pages): 0 violations. On the map page axe asks for a human review of three items in the
  map library's own controls and attribution over the tiles (colour contrast, zoom button names, attribution links).
- What a scanner cannot check, still to do by hand: keyboard use of the map, screen-reader reading order and
  announcements, and the clarity of texts and error messages.
- Map: the spatial dataset draws its three points in the administrator's style over OpenStreetMap tiles, the layer
  control sits over the map, and adding a WMS through it made the browser send GetMap requests for the chosen
  layer in EPSG:3857 to a local stand-in service.

## Not in the model

- Visual design (2.2), agreed with the city during the pre-implementation analysis.
- EU funding marks (2.8, 3.4.1) and the documentation requirements.
- Production hosting, TLS (2.14) and load testing at 150 anonymous and 20 signed-in sessions (2.3).
- Account fields beyond the basics (1.15, 1.16), a published terms-of-use page (1.19.1) and the full
  registration field list (1.21).
- Lines, polygons, KML and SHP on the map, and point clustering (1.63.2, 1.63.3); the model draws points.
- Breadcrumbs, glossary, "how to use the data" page, rich-text editor, format icons and the "report an error
  in the data" form (1.49).
- The city's own data sets (1.64) and the city's sample of digitised resolutions.
- Manual accessibility checks with screen readers (1.52), which a scanner cannot do.
- SHACL validation of the catalogue against the DCAT-AP-PL profile: not run yet.

## Layout

```
portal/app.py         presentation: API routes, pages, sessions (no SQL)
portal/service.py     logic: datasets, imports, versions, users, keys, reports
portal/importers.py   parsing, validation and rendering of CSV, JSON, XML, GeoJSON; the resolutions package
portal/dcat.py        the DCAT-AP graph and its four serialisations
portal/scheduler.py   scheduled updates from source addresses
portal/security.py    password hashing, form stamps, rate limiter, TOTP
portal/db.py          storage: SQLite schema and transactions
portal/i18n.py        interface strings, Polish and English
tests/                one or more tests per requirement line
```

## Licence

Apache-2.0. Map tiles and data from OpenStreetMap contributors, used under the ODbL, are loaded at view time.
