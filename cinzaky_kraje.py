"""Denni report novych cinzovnich domu do 16 mil. s aspon 4 bytovymi jednotkami.

Stredocesky, Ustecky a Liberecky kraj. Kdyz za poslednich 24 hodin nic
nepribude, mail se neposila.

POCET JEDNOTEK NENI V DATECH. Sreality ho nema ani jako filtr, ani jako pole -
je jen v textu inzeratu ("6 bytovych jednotek", "dum se 4 byty"). Cte se proto
vzorcem z popisu (modul jednotky.py) a do karty se vedle cisla dava i veta,
ze ktere se cetlo, aby sla spravnost overit okem.

Dusledek: dum, jehoz inzerat pocet jednotek nikde neuvadi, se do mailu nedostane.
Na vzorku 147 dnesnich inzeratu jich takovych bylo 85 z 147.

Spousti se z .github/workflows/report.yml spolu s ostatnimi reporty.
Prepinac --nahled misto odeslani ulozi HTML do souboru,
prepinac --hodin=720 rozsiri okno (na ladeni, kdyz za den nic nepribylo).
"""

import os
import sys
import time
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta

from jednotky import pocet_jednotek

headers = {"User-Agent": "Mozilla/5.0"}

# Sreality nezna "Severocesky kraj" - ten se rozpadl na Ustecky a Liberecky.
KRAJE = {
    "Středočeský kraj": 11,
    "Ústecký kraj": 4,
    "Liberecký kraj": 5,
}

CENA_DO = 16_000_000
MIN_JEDNOTEK = 4
CATEGORY_MAIN_KOMERCNI = 4
CATEGORY_SUB_CINZOVNI_DUM = 38

PRIJEMCE = "tomas.tuzar@seznam.cz"
ODESILATEL = "realitybot@seznam.cz"


def fmt(cislo):
    return "{:,}".format(int(cislo)).replace(",", " ")


def sklonuj_domy(pocet):
    if pocet == 1:
        return "1 nový činžák"
    if 2 <= pocet <= 4:
        return f"{pocet} nové činžáky"
    return f"{pocet} nových činžáků"


def odkaz_na_detail(inzerat):
    """Na SEO slugu nezalezi, sreality presmeruje podle hash_id."""
    hash_id = inzerat.get("hash_id")
    if not hash_id:
        return "#"
    lok = inzerat.get("locality", {})
    slug = "-".join(
        x
        for x in (lok.get("city_seo_name"), lok.get("citypart_seo_name"), lok.get("street_seo_name"))
        if x
    ) or "x"
    return f"https://www.sreality.cz/detail/prodej/komercni/cinzovni-dum/{slug}/{hash_id}"


def stahni_popis(hash_id):
    """Popis je jen v detailu, ve vypisu neni. Vraci (popis, uzitna_plocha)."""
    try:
        r = requests.get(
            f"https://www.sreality.cz/api/v1/estates/{hash_id}", headers=headers, timeout=30
        )
        r.raise_for_status()
        res = r.json().get("result") or {}
        return res.get("advert_description") or "", res.get("usable_area")
    except Exception as e:
        print(f"  ! detail {hash_id}: {e}")
        return "", None


def stahni_nove(kraj, region_id, od):
    params = {
        "category_main_cb": CATEGORY_MAIN_KOMERCNI,
        "category_sub_cb": CATEGORY_SUB_CINZOVNI_DUM,
        "category_type_cb": 1,
        "locality_region_id": region_id,
        "locality_country_id": 112,
        "price_to": CENA_DO,
        "limit": 60,
        "offset": 0,
        "lang": "cs",
        "sort": "-date",
        "watchdog_last_changed_from": od.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    r = requests.get(
        "https://www.sreality.cz/api/v1/estates/search", params=params, headers=headers, timeout=30
    )
    r.raise_for_status()

    nalezene = []
    for inzerat in r.json().get("results", []):
        # u casti inzeratu je `price` cena za metr, celek je vzdy v price_summary_czk
        cena = inzerat.get("price_summary_czk") or inzerat.get("price_czk") or 0
        # pojistka: cenovy strop hlidame i po svem, ne jen serverovym filtrem
        if not cena or cena > CENA_DO:
            continue

        nazev = inzerat.get("advert_name", "")
        popis, uzitna_plocha = stahni_popis(inzerat.get("hash_id"))
        time.sleep(0.4)

        jednotek, veta = pocet_jednotek(f"{nazev}. {popis}")
        if not jednotek or jednotek < MIN_JEDNOTEK:
            continue

        lok = inzerat.get("locality", {})
        nalezene.append({
            "kraj": kraj,
            "obec": lok.get("city", ""),
            "okres": lok.get("district", ""),
            "cena": cena,
            "jednotek": jednotek,
            "veta": veta,
            "plocha": uzitna_plocha,
            "cena_za_jednotku": cena / jednotek,
            "odkaz": odkaz_na_detail(inzerat),
        })
    return nalezene


def karta(d):
    plocha = f'<div style="color:#6b7280;font-size:13px">📐 Užitná plocha: {fmt(d["plocha"])} m²</div>' if d["plocha"] else ""
    return (
        f'<a href="{d["odkaz"]}" style="text-decoration:none;color:inherit;display:block" target="_blank">'
        f'<div style="background:white;border-radius:10px;padding:16px;margin-bottom:12px;'
        f'box-shadow:0 1px 4px rgba(0,0,0,0.08);border-left:4px solid #7c3aed">'
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start">'
        f'<div style="flex:1;padding-right:12px">'
        f'<div style="font-weight:700;font-size:15px;color:#6d28d9;margin-bottom:6px">{d["obec"]}</div>'
        f'<div style="color:#6b7280;font-size:12px;margin-bottom:4px">📍 okres {d["okres"]} · {d["kraj"]}</div>'
        f'<div style="color:#6b7280;font-size:13px">💰 {fmt(d["cena"])} Kč · '
        f'{fmt(d["cena_za_jednotku"])} Kč na jednotku</div>'
        f"{plocha}"
        f'<div style="color:#9ca3af;font-size:11px;margin-top:6px;font-style:italic">'
        f'„{d["veta"]}"</div>'
        f"</div>"
        f'<div style="background:#7c3aed;color:white;padding:8px 12px;border-radius:8px;font-weight:800;'
        f'font-size:18px;white-space:nowrap;min-width:64px;text-align:center">{d["jednotek"]}<br>'
        f'<span style="font-size:10px;font-weight:600">jednotek</span></div>'
        f"</div></div></a>"
    )


hodin = 24
for arg in sys.argv[1:]:
    if arg.startswith("--hodin="):
        hodin = int(arg.split("=")[1])

od = datetime.now() - timedelta(hours=hodin)
print(f"Stahuji nove cinzovni domy za poslednich {hodin} h...")

domy = []
for kraj, region_id in KRAJE.items():
    nalezene = stahni_nove(kraj, region_id, od)
    print(f"  {kraj}: {len(nalezene)} s aspon {MIN_JEDNOTEK} jednotkami")
    domy.extend(nalezene)

# nejvic jednotek nahore - to je to, co na cinzaku rozhoduje
domy.sort(key=lambda x: (-x["jednotek"], x["cena"]))
print(f"\nCelkem {len(domy)} domu do reportu.")

if not domy:
    print("Nic noveho - email se neposila.")
    sys.exit(0)

datum = datetime.now().strftime("%d. %m. %Y")
souhrn = sklonuj_domy(len(domy))

html_report = (
    "<!DOCTYPE html><html>"
    '<head><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>'
    '<body style="margin:0;padding:0;background:#f8fafc;font-family:Arial,sans-serif">'
    '<div style="max-width:600px;margin:0 auto;padding:16px">'
    '<div style="background:linear-gradient(135deg,#6d28d9,#a78bfa);padding:28px;text-align:center;'
    'border-radius:12px;margin-bottom:16px">'
    '<h1 style="margin:0;color:white;font-size:22px">🏢 Nové činžovní domy</h1>'
    f'<p style="margin:8px 0 0;color:#ede9fe;font-size:14px">{datum} · {souhrn} · '
    f"do 16 mil. · aspoň {MIN_JEDNOTEK} jednotky<br>"
    "Středočeský, Ústecký a Liberecký kraj</p>"
    "</div>"
    + "".join(karta(d) for d in domy)
    + '<div style="text-align:center;padding:16px">'
    '<p style="margin:0;color:#9ca3af;font-size:11px">Kurzívou je věta z inzerátu, ze které se '
    "počet jednotek četl · sreality počet jednotek v datech nemá, čte se z popisu · "
    "seřazeno podle počtu jednotek · Data ze Sreality</p>"
    "</div></div></body></html>"
)

if "--nahled" in sys.argv:
    with open("nahled-cinzaky.html", "w", encoding="utf-8") as f:
        f.write(html_report)
    print("Nahled ulozen do nahled-cinzaky.html - email se neposila.")
    sys.exit(0)

zprava = MIMEMultipart("alternative")
zprava["Subject"] = f"🏢 {souhrn} do 16 mil. · {datum}"
zprava["From"] = ODESILATEL
zprava["To"] = PRIJEMCE
zprava.attach(MIMEText(html_report, "html"))

try:
    with smtplib.SMTP_SSL("smtp.email.cz", 465) as server:
        server.login(ODESILATEL, os.environ.get("SMTP_PASS", "Necum123"))
        server.sendmail(ODESILATEL, PRIJEMCE, zprava.as_string())
    print(f"Report odeslán! {souhrn}.")
except Exception as e:
    print("CHYBA: " + str(e))
