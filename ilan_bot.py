import os, re, json, html
import datetime as dt
import urllib.request, urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

TOKEN = os.environ["TELEGRAM_TOKEN"].strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

QUERIES = [
    "KPSS ön lisans personel alımı",
    "elektrik teknisyeni alımı kamu",
    "tekniker alımı sözleşmeli personel KPSS",
    "sürekli işçi alımı İŞKUR",
    "kamu işçi alımı elektrikçi",
    "memur alımı ilanı ön lisans",
    "sözleşmeli personel alımı Ankara",
    "belediye personel alımı elektrik",
    "TEİAŞ EÜAŞ personel alımı",
    "Türksat PTT Karayolları personel alımı",
]
KEYS = ["ön lisans", "önlisans", "kpss", "p93", "teknisyen", "tekniker",
        "elektrik", "elektronik", "sürekli işçi", "işçi alım",
        "memur alım", "sözleşmeli personel", "personel alım"]
PRIO = ["elektrik", "teknisyen", "tekniker", "elektronik"]
LOCAL = ["ankara", "kırıkkale", "kırşehir", "çankırı", "eskişehir", "bolu",
         "konya", "aksaray", "yozgat", "çorum", "karabük", "nevşehir"]
SKIP = ["akademik", "öğretim üyesi", "öğretim görevlisi", "araştırma görevlisi",
        "doktor", "hekim", "öğretmen", "hemşire", "avukat", "subay",
        "astsubay", "uzman erbaş", "polis", "bekçi"]
MONTHS = {"ocak": 1, "şubat": 2, "mart": 3, "nisan": 4, "mayıs": 5,
          "haziran": 6, "temmuz": 7, "ağustos": 8, "eylül": 9,
          "ekim": 10, "kasım": 11, "aralık": 12}


def tl(s):
    return s.replace("I", "ı").replace("İ", "i").lower()


def fetch(q):
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": q + " when:7d", "hl": "tr", "gl": "TR", "ceid": "TR:tr"})
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        root = ET.fromstring(r.read())
    out = []
    for it in root.iter("item"):
        try:
            pub = parsedate_to_datetime(it.findtext("pubDate"))
        except Exception:
            continue
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        desc = re.sub(r"<[^>]+>", " ", it.findtext("description") or "")
        out.append((title, link, desc, pub))
    return out


def deadline(text, today):
    t = tl(text)
    if "başvuru" not in t and "son gün" not in t:
        return None
    found = []
    for d, m, y in re.findall(r"(\d{1,2})[./](\d{1,2})[./](20\d{2})", t):
        try:
            found.append(dt.date(int(y), int(m), int(d)))
        except ValueError:
            pass
    pat = r"(\d{1,2})\s+(" + "|".join(MONTHS) + r")(?:\s+(20\d{2}))?"
    for d, mn, y in re.findall(pat, t):
        try:
            found.append(dt.date(int(y) if y else today.year, MONTHS[mn], int(d)))
        except ValueError:
            pass
    near = [x for x in found if 0 <= (x - today).days <= 3]
    return min(near) if near else None


def api(method, params=None):
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    data = urllib.parse.urlencode(params).encode() if params else None
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=30) as r:
        return json.load(r)


def get_chat_id():
    if CHAT_ID:
        return CHAT_ID
    for u in reversed(api("getUpdates").get("result", [])):
        if u.get("message"):
            return str(u["message"]["chat"]["id"])
    raise SystemExit("Chat ID yok")


def send(chat, text):
    chunk = ""
    for ln in text.split("\n"):
        if len(chunk) + len(ln) > 3800:
            api("sendMessage", {"chat_id": chat, "text": chunk,
                                "parse_mode": "HTML",
                                "disable_web_page_preview": "true"})
            chunk = ""
        chunk += ln + "\n"
    if chunk.strip():
        api("sendMessage", {"chat_id": chat, "text": chunk,
                            "parse_mode": "HTML",
                            "disable_web_page_preview": "true"})


def split_src(title):
    if " - " in title:
        t, s = title.rsplit(" - ", 1)
        return t.strip(), s.strip()
    return title, ""


def fmt(i, x, extra=""):
    t, s = split_src(x["title"])
    href = html.escape(x["link"], quote=True)
    src = f" <i>({html.escape(s)})</i>" if s else ""
    fl = (x["flags"] + " ") if x["flags"] else ""
    return f'{i}. {fl}<a href="{href}">{html.escape(t)}</a>{src}{extra}'


def section(title, items, limit, counter, extra_fn=None):
    if not items:
        return ""
    out = f"\n<b>{title}</b>\n"
    for x in items[:limit]:
        counter[0] += 1
        out += fmt(counter[0], x, extra_fn(x) if extra_fn else "") + "\n"
    if len(items) > limit:
        out += f"+{len(items) - limit} ilan daha\n"
    return out


def main():
    chat = get_chat_id()
    now = dt.datetime.now(dt.timezone.utc)
    today = (now + dt.timedelta(hours=3)).date()
    seen = {}
    for q in QUERIES:
        try:
            items = fetch(q)
        except Exception:
            continue
        for title, link, desc, pub in items:
            key = re.sub(r"\W+", "", tl(title))[:50]
            text = tl(title + " " + desc)
            if key in seen or not any(k in text for k in KEYS):
                continue
            has_prio = any(k in text for k in PRIO)
            if any(k in tl(title) for k in SKIP) and not has_prio:
                continue
            has_local = any(k in text for k in LOCAL)
            seen[key] = dict(
                title=title, link=link, pub=pub,
                score=2 * has_prio + has_local,
                flags=("⚡" if has_prio else "") + ("📍" if has_local else ""),
                dl=deadline(title + " " + desc, today))
    urgent = sorted([x for x in seen.values() if x["dl"]], key=lambda x: x["dl"])
    fresh = [x for x in seen.values() if not x["dl"]
             and now - x["pub"] <= dt.timedelta(hours=26)]
    both = [x for x in fresh if x["score"] == 3]
    tech = [x for x in fresh if x["score"] == 2]
    local = [x for x in fresh if x["score"] == 1]
    other = [x for x in fresh if x["score"] == 0]

    c = [0]
    msg = f"☀️ <b>Günaydın! Kamu ilan özeti</b> ({today.strftime('%d.%m.%Y')})\n"
    msg += section("⏰ SON GÜNÜ YAKLAŞANLAR", urgent, 8, c,
                   lambda x: f" — son: {x['dl'].strftime('%d.%m')}")
    msg += section("⚡📍 ELEKTRİK + ANKARA/ÇEVRE", both, 8, c)
    msg += section("⚡ ELEKTRİK / TEKNİK", tech, 8, c)
    msg += section("📍 ANKARA VE ÇEVRE", local, 6, c)
    msg += section("🆕 DİĞER KAMU İLANLARI", other, 5, c)
    if c[0] == 0:
        msg += "\nBugün yeni ilan bulunamadı. Bot çalışıyor."
    msg += "\n<i>Başlığa dokun, ilan açılır. ⚡ elektrik/teknik · 📍 Ankara ve çevre</i>"
    send(chat, msg)


main()
