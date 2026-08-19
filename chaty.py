"""Denni report novych chat do 2 milionu.

Stredocesky, Ustecky a Liberecky kraj. Zadny dalsi filtr - vsechno, co za
poslednich 24 hodin pribylo do nabidky, at uz to inzeruje realitka nebo majitel.

Spousti se z .github/workflows/report.yml spolu s ostatnimi reporty.
Prepinac --nahled misto odeslani ulozi HTML do souboru (na ladeni vzhledu).
"""

import os
import re
import sys
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta

headers = {"User-Agent": "Mozilla/5.0"}

# Sreality nezna "Severocesky kraj" - ten se rozpadl na Ustecky a Liberecky.
# ID overena proti API 2026-08-19 (nazev kraje sedi v meta_title odpovedi).
KRAJE = {
    "Středočeský kraj": 11,
    "Ústecký kraj": 4,
    "Liberecký kraj": 5,
}

CENA_DO = 2_000_000
CATEGORY_CHATA = 33  # category_sub_cb, hodnota "Chata"

PRIJEMCE = "tomas.tuzar@seznam.cz"
ODESILATEL = "realitybot@seznam.cz"


def fmt(cena):
    return "{:,}".format(int(cena)).replace(",", " ")


def nove_chaty_text(pocet):
    """1 nová chata / 2 nové chaty / 5 nových chat."""
    if pocet == 1:
        return "1 nová chata"
    if 2 <= pocet <= 4:
        return f"{pocet} nové chaty"
    return f"{pocet} nových chat"


def odkaz_na_detail(inzerat):
    """Detail chaty. Na SEO slugu nezalezi, sreality presmeruje podle hash_id."""
    hash_id = inzerat.get("hash_id")
    if not hash_id:
        return "#"
    lok = inzerat.get("locality", {})
    slug = "-".join(
        x for x in (lok.get("city_seo_name"), lok.get("citypart_seo_name"), lok.get("street_seo_name")) if x
    ) or "x"
    return f"https://www.sreality.cz/detail/prodej/dum/chata/{slug}/{hash_id}"


def plocha_z_nazvu(nazev):
    """Nazev ma tvar "Prodej chaty 38 m2, pozemek 223 m2" - jinde plocha ve vypisu neni."""
    plochy = [int(x) for x in re.findall(r"(\d+)\s*m²", nazev or "")]
    chata = plochy[0] if plochy else None
    pozemek = plochy[1] if len(plochy) > 1 else None
    return chata, pozemek


def stahni_nove_chaty(kraj, region_id, od):
    params = {
        "category_main_cb": 2,
        "category_sub_cb": CATEGORY_CHATA,
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
    response = requests.get(
        "https://www.sreality.cz/api/v1/estates/search", params=params, headers=headers, timeout=30
    )
    response.raise_for_status()

    chaty = []
    for inzerat in response.json().get("results", []):
        cena = inzerat.get("price_czk") or inzerat.get("price") or 0
        # pojistka: cenovy strop hlidame i po svem, ne jen serverovym filtrem
        if not cena or cena > CENA_DO:
            continue
        nazev = inzerat.get("advert_name", "")
        plocha, pozemek = plocha_z_nazvu(nazev)
        lok = inzerat.get("locality", {})
        chaty.append({
            "nazev": nazev,
            "kraj": kraj,
            "obec": lok.get("city", ""),
            "okres": lok.get("district", ""),
            "cena": cena,
            "plocha": plocha,
            "pozemek": pozemek,
            "cena_za_m2": inzerat.get("price_czk_m2"),
            "odkaz": odkaz_na_detail(inzerat),
        })
    return chaty


print("Stahuji nove chaty za poslednich 24 hodin...")
od = datetime.now() - timedelta(hours=24)

chaty = []
for kraj, region_id in KRAJE.items():
    nalezene = stahni_nove_chaty(kraj, region_id, od)
    print(f"  {kraj}: {len(nalezene)}")
    chaty.extend(nalezene)

chaty.sort(key=lambda x: x["cena"])
print(f"Celkem {len(chaty)} novych chat.\n")

if not chaty:
    print("Zadne nove chaty - email se neposila.")
    sys.exit(0)

datum = datetime.now().strftime("%d. %m. %Y")
karty = ""

for c in chaty:
    rozmery = []
    if c["plocha"]:
        rozmery.append(f"{c['plocha']} m² chata")
    if c["pozemek"]:
        rozmery.append(f"{fmt(c['pozemek'])} m² pozemek")
    radek_rozmery = " · ".join(rozmery)
    radek_m2 = f"{fmt(c['cena_za_m2'])} Kč/m²" if c["cena_za_m2"] else ""

    karty += (
        f'<a href="{c["odkaz"]}" style="text-decoration:none;color:inherit;" target="_blank">'
        f'<div style="background:white;border-radius:10px;padding:16px;margin-bottom:12px;'
        f'box-shadow:0 1px 4px rgba(0,0,0,0.08);border-left:4px solid #16a34a">'
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start">'
        f'<div style="flex:1;padding-right:12px">'
        f'<div style="font-weight:700;font-size:15px;color:#15803d;margin-bottom:6px">{c["obec"]}</div>'
        f'<div style="color:#6b7280;font-size:12px;margin-bottom:4px">📍 okres {c["okres"]} · {c["kraj"]}</div>'
        + (f'<div style="color:#6b7280;font-size:13px">📐 {radek_rozmery}</div>' if radek_rozmery else "")
        + (f'<div style="color:#6b7280;font-size:13px">💰 {radek_m2}</div>' if radek_m2 else "")
        + f'</div>'
        f'<div style="background:#16a34a;color:white;padding:8px 12px;border-radius:8px;font-weight:800;'
        f'font-size:16px;white-space:nowrap;min-width:70px;text-align:center">{fmt(c["cena"])} Kč</div>'
        f'</div></div></a>'
    )

html_report = (
    "<!DOCTYPE html><html>"
    '<head><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>'
    '<body style="margin:0;padding:0;background:#f8fafc;font-family:Arial,sans-serif">'
    '<div style="max-width:600px;margin:0 auto;padding:16px">'
    '<div style="background:linear-gradient(135deg,#15803d,#4ade80);padding:28px;text-align:center;'
    'border-radius:12px;margin-bottom:16px">'
    '<h1 style="margin:0;color:white;font-size:22px">🏡 Nové chaty do 2 mil.</h1>'
    f'<p style="margin:8px 0 0;color:#dcfce7;font-size:14px">{datum} · {nove_chaty_text(len(chaty))} · '
    'Středočeský, Ústecký a Liberecký kraj</p>'
    "</div>"
    f"{karty}"
    '<div style="text-align:center;padding:16px">'
    '<p style="margin:0;color:#9ca3af;font-size:11px">Chaty do 2 000 000 Kč přidané za posledních '
    "24 hodin · seřazeno od nejlevnější · Data ze Sreality</p>"
    "</div></div></body></html>"
)

if "--nahled" in sys.argv:
    with open("nahled-chaty.html", "w", encoding="utf-8") as f:
        f.write(html_report)
    print("Nahled ulozen do nahled-chaty.html - email se neposila.")
    sys.exit(0)

zprava = MIMEMultipart("alternative")
zprava["Subject"] = f"🏡 {nove_chaty_text(len(chaty))} do 2 mil. · {datum}"
zprava["From"] = ODESILATEL
zprava["To"] = PRIJEMCE
zprava.attach(MIMEText(html_report, "html"))

try:
    with smtplib.SMTP_SSL("smtp.email.cz", 465) as server:
        server.login(ODESILATEL, os.environ.get("SMTP_PASS", "Necum123"))
        server.sendmail(ODESILATEL, PRIJEMCE, zprava.as_string())
    print(f"Report odeslán! {len(chaty)} nových chat.")
except Exception as e:
    print("CHYBA: " + str(e))
