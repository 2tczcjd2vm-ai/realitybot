"""Bot pro PLACENOU verzi — cena se poměřuje čtvrtí, ne celou městskou částí.

Běží vedle byty_praha.py, ne místo něj. Bezplatná verze zůstává na starém
botovi beze změny: průměr za Prahu 1–10, denní e-mail, /api/broadcast,
/api/ingest-byty. Tenhle skript se téhle roury nedotýká.

Rozdíly oproti bezplatné verzi:
  1. plochu bere z price_czk_m2, ne regexem z názvu inzerátu
  2. stahuje celou nabídku přes stránkování, ne prvních 60 inzerátů
  3. ceny ukládá do Supabase a průměr počítá z klouzavého okna 90 dní
  4. porovnává byt s jeho čtvrtí (Bubeneč), ne s celou Prahou 6

Zatím jen posílá analýzu na vlastní e-mail. Rozesílka platícím zákazníkům
přijde, až budou tarify (balík 2).
"""
import os
import re
import time
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta

headers = {"User-Agent": "Mozilla/5.0"}

API = "https://www.sreality.cz/api/v1/estates/search"
WEB = os.environ.get("PB_API_BASE", "https://podhodnocenebyty.cz")
KOMU = os.environ.get("PB_PRO_EMAIL", "tomas.tuzar@seznam.cz")

# Strop pro byty, ktere se ukazuji uzivateli (cilovka kupuje pro sebe).
# POZOR: pro vypocet prumeru se tenhle strop nepouziva, viz nize.
CENA_MAX = 8_000_000

# Sreality zvladnou limit 500 na jeden dotaz, cela Praha je pak ~12 requestu.
LIMIT = 500
MAX_STRAN = 5

ZELENA_HRANICE = -10

prahy = {
    "Praha 1": 5001,
    "Praha 2": 5002,
    "Praha 3": 5003,
    "Praha 4": 5004,
    "Praha 5": 5005,
    "Praha 6": 5006,
    "Praha 7": 5007,
    "Praha 8": 5008,
    "Praha 9": 5009,
    "Praha 10": 5010,
}


def je_druzstevni(inzerat, nazev=""):
    """Vrátí True, pokud je inzerát družstevní vlastnictví (nespoléhá jen na API filtr)."""
    texty = [nazev]
    for klic in ("labelsAll", "labels"):
        hodnota = inzerat.get(klic)
        if isinstance(hodnota, list):
            texty.extend(str(x) for x in hodnota)
    for text in texty:
        if text and ("družstev" in text.lower() or "druzstev" in text.lower()):
            return True
    return False


def cena_inzeratu(inzerat):
    return inzerat.get("price_czk") or inzerat.get("price") or 0


def cena_za_m2(inzerat):
    """Cena za m². Sreality ji posílají hotovou v price_czk_m2.

    Bezplatný bot luští plochu regexem z názvu inzerátu — inzeráty, které si
    název napsaly po svém (nebo ho nemají vůbec), se tím tiše zahazují.
    Regex tu zůstává jen jako záchranná brzda, kdyby API pole vynechalo.
    """
    hotova = inzerat.get("price_czk_m2")
    if hotova:
        return float(hotova)

    cena = cena_inzeratu(inzerat)
    m = re.search(r"(\d+)\s*m²", inzerat.get("advert_name", "") or "")
    if m and cena:
        plocha = int(m.group(1))
        if plocha > 0:
            return cena / plocha
    return None


def ctvrt(inzerat):
    lok = inzerat.get("locality", {})
    return (lok.get("citypart") or "").strip() or (lok.get("district") or "").strip()


def stahni(district_id, **navic):
    """Postupně projde stránky výsledků a vrátí všechny inzeráty."""
    vysledky = []
    offset = 0
    for _ in range(MAX_STRAN):
        params = {
            "category_main_cb": 1,
            "category_type_cb": 1,
            "locality_district_id": district_id,
            "locality_country_id": 112,
            "locality_region_id": 10,
            "limit": LIMIT,
            "offset": offset,
            "lang": "cs",
            "ownership": 1,
            "no_auction": 1,
        }
        params.update(navic)
        data = requests.get(API, params=params, headers=headers, timeout=60).json()
        davka = data.get("results", [])
        vysledky.extend(davka)
        if len(davka) < LIMIT:
            break
        offset += LIMIT
        time.sleep(0.5)
    return vysledky


# ---------------------------------------------------------------------------
# 1. Stažení celé nabídky
#
# Vzorky pro průměr se berou z CELÉ nabídky, bez cenového stropu. Strop 8 mil.
# má smysl u toho, co uživateli ukážeme, ne u toho, čím měříme trh: v Praze 1
# jím projde 15 inzerátů z 203, v Bubenči by uřízl horní půlku nabídky
# a levné byty by pak vypadaly dráž, než jsou.
# ---------------------------------------------------------------------------
print("Stahuji nabídku bytů v Praze...")

vzorky = []
for nazev_prahy, district_id in prahy.items():
    inzeraty = stahni(district_id)
    pocet = 0

    for inzerat in inzeraty:
        nazev = inzerat.get("advert_name", "") or ""
        if je_druzstevni(inzerat, nazev):
            continue

        cena = cena_inzeratu(inzerat)
        m2 = cena_za_m2(inzerat)
        hash_id = inzerat.get("hash_id")
        if not m2 or not cena or cena < 100000 or not hash_id:
            continue

        lok = inzerat.get("locality", {})
        vzorky.append({
            "hash_id": str(hash_id),
            "citypart": ctvrt(inzerat),
            "citypart_seo": lok.get("citypart_seo_name") or "",
            "district": nazev_prahy,
            "cena_m2": round(m2),
            "cena": cena,
        })
        pocet += 1

    print(f"  {nazev_prahy}: {pocet} použitelných z {len(inzeraty)} inzerátů")

print(f"\nVzorků na uložení: {len(vzorky)}")

# ---------------------------------------------------------------------------
# 2. Uložení vzorků a průměry za čtvrť z klouzavého okna
# ---------------------------------------------------------------------------
broadcast_secret = os.environ.get("BROADCAST_SECRET")
if not broadcast_secret:
    raise SystemExit("Chybí BROADCAST_SECRET, bez něj se vzorky nemají kam uložit.")

resp = requests.post(
    f"{WEB}/api/ceny",
    json={"vzorky": vzorky},
    headers={"Authorization": f"Bearer {broadcast_secret}"},
    timeout=120,
)
resp.raise_for_status()
data = resp.json()

prumery_ctvrti, prumery_casti = {}, {}
for r in data.get("prumery", []):
    cil = prumery_ctvrti if r["uroven"] == "ctvrt" else prumery_casti
    cil[r["klic"]] = r

podlozene = [k for k, v in prumery_ctvrti.items() if v["zdroj"] == "ctvrt"]
print(f"Uloženo {data.get('ulozeno')} vzorků, okno {data.get('okno_dni')} dní.")
print(f"Čtvrtí s vlastním průměrem: {len(podlozene)} z {len(prumery_ctvrti)} "
      f"(zbytek zatím počítá průměrem celé městské části)")


def reference(nazev_ctvrti, nazev_prahy):
    """Referenční cena za m² pro danou čtvrť. Vrací (cena, zdroj, počet vzorků)."""
    zaznam = prumery_ctvrti.get(nazev_ctvrti) or prumery_casti.get(nazev_prahy)
    if not zaznam or not zaznam.get("median"):
        return None, None, None
    # Medián, ne průměr: na malém vzorku jeden penthouse posune průměr
    # o jednotky procent a levné byty pak vypadají levněji, než jsou.
    return float(zaznam["median"]), zaznam["zdroj"], zaznam["pocet"]


# ---------------------------------------------------------------------------
# 3. Nové byty za posledních 24 hodin, poměřené svou čtvrtí
# ---------------------------------------------------------------------------
print("\nStahuji nové byty za posledních 24 hodin...")

byty = []
for nazev_prahy, district_id in prahy.items():
    inzeraty = stahni(
        district_id,
        sort="-date",
        price_to=CENA_MAX,
        watchdog_last_changed_from=(datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S"),
    )

    for inzerat in inzeraty:
        nazev = inzerat.get("advert_name", "") or ""
        if je_druzstevni(inzerat, nazev):
            continue

        cena = cena_inzeratu(inzerat)
        m2 = cena_za_m2(inzerat)
        if not m2 or not cena or cena < 100000:
            continue

        nazev_ctvrti = ctvrt(inzerat)
        prumer, zdroj, pocet_vzorku = reference(nazev_ctvrti, nazev_prahy)
        if not prumer:
            continue

        lok = inzerat.get("locality", {})
        hash_id = inzerat.get("hash_id")
        disp = inzerat.get("category_sub_cb", {}).get("name", "")
        disp_url = disp.replace("+", "%2B") if disp else ""
        odkaz = (
            f"https://www.sreality.cz/detail/prodej/byt/{disp_url}/"
            f"{lok.get('city_seo_name', '')}-{lok.get('district_seo_name', '')}-"
            f"{lok.get('street_seo_name', '')}/{hash_id}"
        ) if hash_id else "#"

        byty.append({
            "nazev": nazev,
            "lokalita": lok.get("city", ""),
            "cast_prahy": nazev_prahy,
            "ctvrt": nazev_ctvrti,
            "dispozice": disp,
            "cena": cena,
            # Plocha uz neni zdrojem vypoctu, dopocitava se zpetne pro zobrazeni.
            "plocha": round(cena / m2),
            "cena_za_m2": m2,
            "prumer_ctvrti": prumer,
            "odchylka": ((m2 - prumer) / prumer) * 100,
            "zdroj_prumeru": zdroj,
            "pocet_vzorku": pocet_vzorku,
            "odkaz": odkaz,
        })

print(f"Nalezeno {len(byty)} nových bytů.\n")

byty.sort(key=lambda x: x["odchylka"])
pod_cenou = [b for b in byty if b["odchylka"] < ZELENA_HRANICE]

if not pod_cenou:
    raise SystemExit(f"Z {len(byty)} nových bytů není žádný pod cenou své čtvrti – e-mail se neposílá.")

print(f"Pod cenou své čtvrti: {len(pod_cenou)} z {len(byty)} nových bytů.")

datum = datetime.now().strftime("%d. %m. %Y")
karty = ""

for b in pod_cenou:
    odchylka = b["odchylka"]
    barva = "#22c55e" if odchylka < -10 else "#f59e0b" if odchylka < 0 else "#ef4444"

    cena_fmt = f"{b['cena']:,.0f} Kč".replace(",", " ")
    cena_m2_fmt = f"{b['cena_za_m2']:,.0f} Kč/m²".replace(",", " ")
    prumer_fmt = f"{b['prumer_ctvrti']:,.0f} Kč/m²".replace(",", " ")

    # Kdyz ctvrt nema dost vzorku, rekneme to rovnou — cislo je hrubsi.
    zaklad = (
        f"{b['ctvrt']} ({b['pocet_vzorku']} bytů)"
        if b["zdroj_prumeru"] == "ctvrt"
        else f"{b['cast_prahy']} — {b['ctvrt']} zatím nemá dost dat"
    )

    karty += (
        f'<a href="{b["odkaz"]}" style="text-decoration:none;color:inherit;" target="_blank">'
        f'<div style="background:white;border-radius:10px;padding:16px;margin-bottom:12px;box-shadow:0 1px 4px rgba(0,0,0,0.08);border-left:4px solid {barva}">'
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start">'
        f'<div style="flex:1;padding-right:12px">'
        f'<div style="font-weight:700;font-size:15px;color:#1d4ed8;margin-bottom:6px">{b["nazev"]}</div>'
        f'<div style="color:#6b7280;font-size:12px;margin-bottom:4px">📍 {b["ctvrt"]} · {b["cast_prahy"]}</div>'
        f'<div style="color:#6b7280;font-size:13px">💰 Cena: {cena_fmt}</div>'
        f'<div style="color:#6b7280;font-size:13px">📐 Cena/m²: {cena_m2_fmt}</div>'
        f'<div style="color:#6b7280;font-size:13px">📊 Obvyklá cena {zaklad}: {prumer_fmt}</div>'
        f'</div>'
        f'<div style="background:{barva};color:white;padding:8px 12px;border-radius:8px;font-weight:800;font-size:18px;white-space:nowrap;min-width:70px;text-align:center">'
        f'{odchylka:+.1f}%</div></div></div></a>'
    )

html_report = (
    "<!DOCTYPE html><html>"
    '<head><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>'
    '<body style="margin:0;padding:0;background:#f8fafc;font-family:Arial,sans-serif">'
    '<div style="max-width:600px;margin:0 auto;padding:16px">'
    '<div style="background:linear-gradient(135deg,#0d9488,#2dd4bf);padding:28px;text-align:center;border-radius:12px;margin-bottom:16px">'
    '<h1 style="margin:0;color:white;font-size:22px">🏠 Podhodnocené byty · přesná analýza</h1>'
    f'<p style="margin:8px 0 0;color:#ccfbf1;font-size:14px">{datum} · {len(pod_cenou)} bytů pod cenou své čtvrti</p>'
    "</div>"
    f"{karty}"
    '<div style="text-align:center;padding:16px">'
    '<p style="margin:0;color:#9ca3af;font-size:11px">Odchylka = (cena/m² − obvyklá cena ve čtvrti) / obvyklá cena · '
    'Obvyklá cena je medián z nabídky za posledních 90 dní · Data ze Sreality</p>'
    "</div></div></body></html>"
)

zprava = MIMEMultipart("alternative")
zprava["Subject"] = f"🏠 Byty podle čtvrtí · {datum}"
zprava["From"] = "realitybot@seznam.cz"
zprava["To"] = KOMU
zprava.attach(MIMEText(html_report, "html"))

try:
    with smtplib.SMTP_SSL("smtp.email.cz", 465) as server:
        server.login("realitybot@seznam.cz", os.environ.get("SMTP_PASS", "Necum123"))
        server.sendmail("realitybot@seznam.cz", KOMU, zprava.as_string())
    print(f"Report odeslán na {KOMU}. {len(pod_cenou)} bytů pod cenou.")
except Exception as e:
    print("CHYBA: " + str(e))
