#!/usr/bin/env python3
"""Atualiza novidades.json com noticias da reforma tributaria (feeds RSS publicos).
Somente biblioteca padrao. Rodado pelo GitHub Actions."""
import hashlib, html, json, os, re, sys, urllib.request, urllib.parse
from xml.sax.saxutils import escape as xesc
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "novidades.json")
MAX_ITEMS = 150
ENRICH_PER_RUN = 40
MIN_DATE = datetime(2025, 1, 1, tzinfo=timezone.utc)
UA = "Mozilla/5.0 (compatible; QG-Reforma-Tributaria/1.0)"


def gnews(q):
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": q, "hl": "pt-BR", "gl": "BR", "ceid": "BR:pt-419"})


SOURCES = [
    {"name": "Agência Senado", "url": "https://www12.senado.leg.br/noticias/rss", "official": True},
    {"name": "Agência Câmara", "url": "https://www.camara.leg.br/noticias/rss/dinamico/ECONOMIA", "official": True},
    {"name": "Receita Federal", "url": "https://www.gov.br/receitafederal/pt-br/assuntos/noticias/RSS", "official": True},
    {"name": "Agência Brasil", "url": "https://agenciabrasil.ebc.com.br/rss/economia/feed.xml", "official": True},
    {"name": "Portal Contábeis", "url": "https://www.contabeis.com.br/rss/noticias/", "official": False},
    {"name": "InfoMoney", "url": "https://www.infomoney.com.br/feed/", "official": False},
    {"name": "Imprensa (Google Notícias)", "url": gnews('"reforma tributária" (CBS OR IBS OR "split payment" OR "imposto seletivo") when:7d'), "official": False},
    {"name": "CGIBS / NF-e (Google Notícias)", "url": gnews('(site:cgibs.gov.br OR site:nfe.fazenda.gov.br OR site:gov.br/receitafederal) (IBS OR CBS) when:14d'), "official": True},
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


def http(url, data=None, headers=None, timeout=25, limit=600000):
    h = {"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(limit)
        ctype = r.headers.get("Content-Type", "")
        m = re.search(r"charset=([\w-]+)", ctype)
        return raw, (m.group(1) if m else "utf-8"), r.geturl()


def categorize(title, body):
    t = f"{title} {body}"
    for cat, tag, rx in CATS:
        if re.search(rx, t, re.I):
            return cat, tag
    return "outros", "Atualização"


def norm(t):
    return re.sub(r"\W+", "", (t or "").lower())


def strip_suffix(title, src):
    if src and title.lower().endswith((" - " + src).lower()):
        return title[: -(len(src) + 3)].strip()
    return title


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


# ---------- resumo: resolve link do Google Noticias e le a descricao da materia ----------
def decode_gnews(url):
    """Converte link news.google.com/rss/articles/<id> no endereco real da materia."""
    m = re.search(r"/articles/([^/?]+)", url)
    if not m:
        return None
    aid = m.group(1)
    raw, cs, _ = http(f"https://news.google.com/rss/articles/{aid}", timeout=20)
    page = raw.decode(cs, "replace")
    sg = re.search(r'data-n-a-sg="([^"]+)"', page)
    ts = re.search(r'data-n-a-ts="([^"]+)"', page)
    if not (sg and ts):
        return None
    inner = json.dumps([
        "garturlreq",
        [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None, None, 0, 1],
         "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
        aid, int(ts.group(1)), sg.group(1)])
    body = urllib.parse.urlencode({"f.req": json.dumps([[["Fbv4je", inner, None, "generic"]]])}).encode()
    raw, cs, _ = http("https://news.google.com/_/DotsSplashUi/data/batchexecute", data=body,
                      headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"}, timeout=20)
    resp = raw.decode(cs, "replace")
    m2 = re.search(r'garturlres\\",\\"(https?:[^"\\]+)', resp)
    return m2.group(1) if m2 else None


def meta_desc(page):
    for pat in (
        r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:description["\']',
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']description["\']',
        r'<meta[^>]+name=["\']twitter:description["\'][^>]+content=["\']([^"\']+)',
    ):
        m = re.search(pat, page, re.I)
        if m:
            d = clean(m.group(1), 260)
            if len(d) >= 40:
                return d
    return ""


def enrich(item):
    """Preenche resumo (e o link direto) de um item sem resumo. Falhas sao ignoradas."""
    link = item.get("link", "")
    try:
        if "news.google.com" in link:
            real = decode_gnews(link)
            if not real:
                return
            item["link"] = real
            link = real
        raw, cs, final = http(link, timeout=20, limit=400000)
        d = meta_desc(raw.decode(cs, "replace"))
        if d and norm(d)[:40] != norm(item.get("title"))[:40]:
            item["resumo"] = d
    except Exception:
        pass


SITE = "https://raphaelnemeth-hub.github.io/qg-reforma/"


def write_feed(items, now_iso):
    """Gera feed.xml (Atom) para assinatura no Outlook, Feedly etc."""
    out = ['<?xml version="1.0" encoding="utf-8"?>',
           '<feed xmlns="http://www.w3.org/2005/Atom">',
           "<title>QG da Reforma Tributária: novidades</title>",
           f'<link href="{SITE}feed.xml" rel="self"/><link href="{SITE}"/>',
           f"<id>{SITE}</id><updated>{now_iso}</updated>"]
    for i in items[:50]:
        out.append("<entry><title>%s</title><link href=\"%s\"/><id>%s</id><updated>%s</updated><summary>%s</summary></entry>" % (
            xesc(i["title"]), xesc(i["link"], {'"': "&quot;"}), xesc(i["link"]), i.get("addedAt", now_iso),
            xesc((i.get("resumo") or "") + " (" + (i.get("source") or "") + ")")))
    out.append("</feed>")
    with open(os.path.join(ROOT, "feed.xml"), "w", encoding="utf-8") as f:
        f.write("\n".join(out))


def write_alert(new_items):
    """Gera novos.md (corpo do alerta por e-mail via Issue do GitHub). Vazio = sem arquivo."""
    path = os.path.join(ROOT, "novos.md")
    if not new_items:
        if os.path.exists(path):
            os.remove(path)
        return
    lines = [f"**{len(new_items)} nova(s) matéria(s) captada(s)** — painel: {SITE}", ""]
    for i in sorted(new_items, key=lambda x: x["date"], reverse=True)[:15]:
        sub = f"\n  {i['resumo']}" if i.get("resumo") else ""
        lines.append(f"- {i['date'][8:]}/{i['date'][5:7]} · [{i['title']}]({i['link']}) ({i['source']}){sub}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    try:
        old = json.load(open(OUT, encoding="utf-8"))
    except Exception:
        old = {"items": []}
    by_id = {i["id"]: i for i in old.get("items", [])}
    seen = {norm(strip_suffix(i.get("title", ""), i.get("source", ""))) for i in by_id.values()}
    first_run = not by_id
    now = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    feeds, added = [], 0

    for s in SOURCES:
        st = {"name": s["name"], "ok": False, "found": 0, "new": 0}
        try:
            raw, _, _ = http(s["url"], timeout=30)
            for title, link, date, desc, src in entries(raw):
                if not title or not link.startswith("https://"):
                    continue
                if not date or date < MIN_DATE or date > now + timedelta(days=1):
                    continue
                body = clean(desc)
                if not KEY.search(f"{title} {body}"):
                    continue
                st["found"] += 1
                iid = hashlib.sha1(norm(title).encode()).hexdigest()[:12]
                gn = "news.google.com" in s["url"]
                veic = clean(src) if src else s["name"]
                shown = strip_suffix(title, veic) if gn else title
                n = norm(shown)
                if iid in by_id or n in seen:
                    continue
                seen.add(n)
                resumo = "" if gn else clean(desc, 260)
                if resumo and norm(resumo)[:40] == n[:40]:
                    resumo = ""
                cat, tag = categorize(shown, body)
                by_id[iid] = {
                    "id": iid,
                    "date": date.astimezone(ZoneInfo("America/Sao_Paulo")).strftime("%Y-%m-%d"),
                    "title": shown[:220],
                    "resumo": resumo,
                    "link": link,
                    "source": veic,
                    "official": bool(s["official"] and not (gn and src and "gov.br" not in src.lower())),
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

    # resumos: itens mais recentes sem resumo (ate ENRICH_PER_RUN por rodada)
    todo = sorted([i for i in by_id.values() if not i.get("resumo") and i.get("tries", 0) < 2],
                  key=lambda x: (x["date"], x["addedAt"]), reverse=True)[:ENRICH_PER_RUN]
    filled = 0
    for it in todo:
        it["tries"] = it.get("tries", 0) + 1
        enrich(it)
        if it.get("resumo"):
            filled += 1

    items = sorted(by_id.values(), key=lambda x: (x["date"], x["addedAt"]), reverse=True)[:MAX_ITEMS]
    new_items = [i for i in items if i.get("addedAt") == now_iso]
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
    write_feed(items, now_iso)
    write_alert([] if first_run else new_items)
    print(f"ok: {added} novo(s); resumos preenchidos {filled}/{len(todo)}; total {len(items)}; feeds: " + ", ".join(
        f"{x['name']}={'ok' if x['ok'] else 'ERRO'}({x['found']})" for x in feeds))
    if not any(x["ok"] for x in feeds):
        sys.exit(1)


if __name__ == "__main__":
    main()
