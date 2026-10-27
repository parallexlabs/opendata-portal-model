"""The metadata catalog as DCAT-AP RDF, following the Polish application profile DCAT-AP-PL (OPZ 1.1, 1.2, 1.4).

Themes, frequencies, file types and languages use the EU Publications Office authority tables, as DCAT-AP
expects. The exact property set required by the current DCAT-AP-PL version is to be confirmed at source;
README lists what this model emits.
"""

from __future__ import annotations

import json

from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCAT, DCTERMS, FOAF, RDF, XSD

VCARD = Namespace("http://www.w3.org/2006/vcard/ns#")
EU = "http://publications.europa.eu/resource/authority/"
THEMES = {"AGRI", "ECON", "EDUC", "ENER", "ENVI", "GOVE", "HEAL", "INTR", "JUST", "REGI", "SOCI", "TECH", "TRAN"}
FREQUENCIES = {"DAILY", "WEEKLY", "MONTHLY", "QUARTERLY", "ANNUAL", "IRREG", "UNKNOWN"}
FILE_TYPES = {"csv": ("CSV", "text/csv"), "json": ("JSON", "application/json"), "xml": ("XML", "application/xml"),
              "geojson": ("GEOJSON", "application/geo+json")}
SERIALISATIONS = {"ttl": ("turtle", "text/turtle"), "rdf": ("xml", "application/rdf+xml"),
                  "nt": ("nt", "application/n-triples"), "jsonld": ("json-ld", "application/ld+json")}
LICENCES = {"CC-BY-4.0": "https://creativecommons.org/licenses/by/4.0/", "CC0-1.0": "https://creativecommons.org/publicdomain/zero/1.0/"}


def catalog_graph(base: str, datasets: list[dict], title: str, publisher: str) -> Graph:
    g = Graph()
    for prefix, ns in (("dcat", DCAT), ("dct", DCTERMS), ("foaf", FOAF), ("vcard", VCARD)):
        g.bind(prefix, ns)
    catalog = URIRef(base + "/catalog")
    g.add((catalog, RDF.type, DCAT.Catalog))
    g.add((catalog, DCTERMS.title, Literal(title, lang="pl")))
    g.add((catalog, DCTERMS.description, Literal("Katalog otwartych danych (model)", lang="pl")))
    g.add((catalog, DCTERMS.language, URIRef(EU + "language/POL")))
    g.add((catalog, FOAF.homepage, URIRef(base + "/")))
    pub = URIRef(base + "/publisher")
    g.add((pub, RDF.type, FOAF.Agent))
    g.add((pub, FOAF.name, Literal(publisher, lang="pl")))
    g.add((catalog, DCTERMS.publisher, pub))
    for d in datasets:
        if d["archived"]:
            continue
        ds = URIRef(f"{base}/datasets/{d['id']}")
        g.add((catalog, DCAT.dataset, ds))
        g.add((ds, RDF.type, DCAT.Dataset))
        g.add((ds, DCTERMS.identifier, Literal(d["id"])))
        g.add((ds, DCTERMS.title, Literal(d["title"], lang="pl")))
        g.add((ds, DCTERMS.description, Literal(d["description"], lang="pl")))
        for kw in json.loads(d["keywords"]):
            g.add((ds, DCAT.keyword, Literal(kw, lang="pl")))
        if d["category"] in THEMES:
            g.add((ds, DCAT.theme, URIRef(EU + "data-theme/" + d["category"])))
        if d["frequency"] in FREQUENCIES:
            g.add((ds, DCTERMS.accrualPeriodicity, URIRef(EU + "frequency/" + d["frequency"])))
        g.add((ds, DCTERMS.publisher, pub))
        g.add((ds, DCTERMS.modified, Literal(d["modified_at"], datatype=XSD.dateTime)))
        g.add((ds, DCTERMS.issued, Literal(d["created_at"], datatype=XSD.dateTime)))
        contact = BNode()
        g.add((ds, DCAT.contactPoint, contact))
        g.add((contact, RDF.type, VCARD.Organization))
        g.add((contact, VCARD.fn, Literal(d["publisher"])))
        formats = ["csv", "json", "xml"] + (["geojson"] if d["geo"] else [])
        for fmt in formats:
            code, media = FILE_TYPES[fmt]
            dist = URIRef(f"{base}/datasets/{d['id']}/distribution/{fmt}")
            g.add((ds, DCAT.distribution, dist))
            g.add((dist, RDF.type, DCAT.Distribution))
            g.add((dist, DCTERMS.title, Literal(f"{d['title']} ({code})", lang="pl")))
            url = URIRef(f"{base}/api/1.0/datasets/{d['id']}/data?format={fmt}")
            g.add((dist, DCAT.accessURL, url))
            g.add((dist, DCAT.downloadURL, url))
            g.add((dist, DCTERMS.format, URIRef(EU + "file-type/" + code)))
            g.add((dist, DCAT.mediaType, URIRef("https://www.iana.org/assignments/media-types/" + media)))
            g.add((dist, DCTERMS.license, URIRef(LICENCES.get(d["licence"], LICENCES["CC-BY-4.0"]))))
    return g


def serialise(g: Graph, ext: str) -> tuple[bytes, str]:
    fmt, media = SERIALISATIONS[ext]
    data = g.serialize(format=fmt)
    return (data.encode() if isinstance(data, str) else data), media
