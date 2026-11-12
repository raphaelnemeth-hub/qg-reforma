#!/usr/bin/env python3
"""Atualiza novidades.json com noticias da reforma tributaria (feeds RSS publicos).
Somente biblioteca padrao. Rodado pelo GitHub Actions."""
import hashlib, html, json, os, re, sys, urllib.request, urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "novidades.json")
MAX_ITEMS = 150
MIN_DATE = datetime(2025, 1, 1, tzinfo=timezone.utc)
UA = "Mozilla/5.0 (compatible; QG-Reforma-Tributaria/1.0)"


def gnews(q):
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": q, "hl": "pt-BR", "gl": "BR", "ceid": "BR:pt-419"})


SOURCES = [
    {"name": "Agência Senado", "url": "https://www12.senado.leg.br/noticias/rss", "tag": "Senado", "official": True},
    {"name": "Agência Câmara", "url": "https://www.camara.leg.br/noticias/rss/dinamico/ECONOMIA", "tag": "Câmara", "official": True},
    {"name": "Receita Federal", "url": "https://www.gov.br/receitafederal/pt-br/assuntos/noticias/RSS", "tag": "Receita", "official": True},
    {"name": "Imprensa (Google Notícias)", "url": gnews('"reforma tributária" (CBS OR IBS OR "split payment" OR "imposto seletivo") when:7d'), "tag": "Imprensa", "official": False},
    {"name": "CGIBS / NF-e (Google Notícias)", "url": gnews('(site:cgibs.gov.br OR site:nfe.fazenda.gov.br OR site:gov.br/receitafederal) (IBS OR CBS) when:14d'), "tag": "Fonte oficial", "official": True},
]

KEY = re.compile(
    r"reforma tribut|\bIBS\b|\bCBS\b|imposto seletivo|split payment|\bCGIBS\b|IVA dual|"
    r"LC 214|LC 227|PLP 108|simples h[ií]brido|nota t[eé]cnica.*(NF-?e|IBS|CBS)|"
    r"comit[eê] gestor do IBS|cashback|MP 1\.?399", re.I)

CATS = [
    ("nt", "Nota técnica", r"nota t[eé]cnica|\bNT 20|NF-?e|NFC-?e|DANFE|documento fiscal"),
    ("pag", "Split payment", r"split payment|pagamento dividido"),
    ("simples", "Simples", r"simples"),
    ("cont", "Contabilidade", r"\bCFC\b|cont[aá]bil|\bCPC\b|contador"),
    ("cap", "Capacitação", r"curso|capacita|treinamento|webinar"),
    ("aliq", "Alíquotas", r"al[ií]quota|imposto seletivo|\bTCU\b"),
    ("lei", "Legislação", r"lei complementar|decreto|medida provis[oó]ria|\bMP\b|resolu[cç][aã]o|regulament|\bPLP\b|projeto|senado|c[aâ]mara|publica"),
]


def local(tag):
    return tag.rsplit("}", 1)[-1]


def txt(el, *names):
    for ch in el.iter():
        if local(ch.tag) in names and (ch.text or "").strip():
            return ch.text.strip()
    return ""


def clean(s, n=None):
    s = re.sub(r"<[^>]+>", " ", html.unescape(s or ""))
    s = re.sub(r"\s+", " ", s).strip()
    if n and len(s) > n:
        s = s[:n].rsplit(" ", 1)[0].rstrip(",.;: ") + "…"
    return s


def parse_date(s):
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except Exception:
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/xml, */*"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def categorize(title, body):
    t = f"{title} {body}"
    for cat, tag, rx in CATS:
        if re.search(rx, t, re.I):
            return cat, tag
    return "outros", "Atualização"


def entries(raw):
    root = ET.fromstring(raw)
    for it in root.iter():
        if local(it.tag) not in ("item", "entry"):
            continue
        title = clean(txt(it, "title"))
        link = txt(it, "link")
        if not link:
            for ch in it:
                if local(ch.tag) == "link" and ch.get("href"):
                    link = ch.get("href")
        about = it.get("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about")
        link = link or about or ""
        date = parse_date(txt(it, "pubDate", "date", "published", "updated"))
        desc = txt(it, "description", "summary", "encoded")
        src = txt(it, "source")
        yield title, link.strip(), date, desc, src


def main():
    try:
        old = json.load(open(OUT, encoding="utf-8"))
    except Exception:
        old = {"items": []}
    by_id = {i["id"]: i for i in old.get("items", [])}
    first_run = not by_id
    now = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    feeds, added = [], 0

    for s in SOURCES:
        st = {"name": s["name"], "ok": False, "found": 0, "new": 0}
        try:
            raw = fetch(s["url"])
            for title, link, date, desc, src in entries(raw):
                if not title or not link.startswith("https://"):
                    continue
                if not date or date < MIN_DATE or date > now + timedelta(days=1):
                    continue
                body = clean(desc)
                if not KEY.search(f"{title} {body}"):
                    continue
                st["found"] += 1
                iid = hashlib.sha1(re.sub(r"\W+", "", title.lower()).encode()).hexdigest()[:12]
                if iid in by_id:
                    continue
                gn = "news.google.com" in s["url"]
                resumo = "" if gn else clean(desc, 260)
                if resumo.lower().startswith(title.lower()[:40]):
                    resumo = ""
                cat, tag = categorize(title, body)
                veic = clean(src) if src else s["name"]
                by_id[iid] = {
                    "id": iid,
                    "date": date.astimezone(ZoneInfo("America/Sao_Paulo")).strftime("%Y-%m-%d"),
                    "title": title[:220],
                    "resumo": resumo,
                    "link": link,
                    "source": veic,
                    "official": s["official"] and not (gn and src and "gov.br" not in src.lower()),
                    "category": cat,
                    "tag": tag,
                    "addedAt": date.strftime("%Y-%m-%dT%H:%M:%SZ") if first_run else now_iso,
                }
                st["new"] += 1
                added += 1
            st["ok"] = True
        except Exception as e:
            st["error"] = type(e).__name__
        feeds.append(st)

    items = sorted(by_id.values(), key=lambda x: (x["date"], x["addedAt"]), reverse=True)[:MAX_ITEMS]
    br = now.astimezone(ZoneInfo("America/Sao_Paulo"))
    out = {
        "lastCheckAt": now_iso,
        "lastCheckBr": br.strftime("%d/%m/%Y %H:%M"),
        "added": 0 if first_run else added,
        "feeds": feeds,
        "items": items,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"ok: {added} novo(s); total {len(items)}; feeds: " + ", ".join(
        f"{x['name']}={'ok' if x['ok'] else 'ERRO'}({x['found']})" for x in feeds))
    if not any(x["ok"] for x in feeds):
        sys.exit(1)


if __name__ == "__main__":
    main()
