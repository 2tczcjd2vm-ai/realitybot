"""Denni report novych chat a stavebnich pozemku.

Dve sekce v jednom mailu:
  - chaty do 2 000 000 Kc
  - STAVEBNI pozemky do 1 500 000 Kc (category_sub_cb 19, nic jineho -
    zahrady, pole, louky a lesy maji vlastni podkategorie a do reportu nepatri)

Oboji ve Stredoceskem, Usteckem a Libereckem kraji, bez dalsiho filtru -
vsechno, co za poslednich 24 hodin pribylo, at uz to inzeruje realitka
nebo majitel.

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

# category_main_cb: 2 = domy, 3 = pozemky
# category_sub_cb:  33 = Chata, 19 = Stavebni pozemek
# Podkategorie 19 je overena proti meta_title odpovedi ("Stavební pozemek
# k prodeji ..."), 18 je komercni a 20-24 zemedelska puda, les, louka a zahrada.
SEKCE = [
    {
        "klic": "chaty",
        "nadpis": "Chaty do 2 mil.",
        "ikona": "🏡",
        "barva": "#16a34a",
        "barva_textu": "#15803d",
        "category_main_cb": 2,
        "category_sub_cb": 33,
        "cena_do": 2_000_000,
        "detail_cesta": "dum/chata",
        # nazev ma tvar "Prodej chaty 38 m2, pozemek 223 m2" - dve cisla
        "popisky_ploch": ["chata", "pozemek"],
    },
    {
        "klic": "pozemky",
        "nadpis": "Stavební pozemky do 1,5 mil.",
        "ikona": "📐",
        "barva": "#b45309",
        "barva_textu": "#92400e",
        "category_main_cb": 3,
        "category_sub_cb": 19,
        "cena_do": 1_500_000,
        "detail_cesta": "pozemek/bydleni",
        # nazev ma tvar "Prodej stavebniho pozemku 535 m2" - jedno cislo
        "popisky_ploch": ["pozemek"],
    },
]

PRIJEMCE = "tomas.tuzar@seznam.cz"
ODESILATEL = "realitybot@seznam.cz"


def fmt(cena):
    return "{:,}".format(int(cena)).replace(",", " ")


def sklonuj(pocet, jedna, dve_ctyri, pet_vic):
    if pocet == 1:
        return f"1 {jedna}"
    if 2 <= pocet <= 4:
        return f"{pocet} {dve_ctyri}"
    return f"{pocet} {pet_vic}"


def odkaz_na_detail(inzerat, detail_cesta):
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
    return f"https://www.sreality.cz/detail/prodej/{detail_cesta}/{slug}/{hash_id}"


def plochy_z_nazvu(nazev):
    """Vymery jsou jen v nazvu inzeratu, ve vypisu je API jinde nema."""
    return [int(x) for x in re.findall(r"(\d+)\s*m²", nazev or "")]


def stahni(sekce, kraj, region_id, od):
    params = {
        "category_main_cb": sekce["category_main_cb"],
        "category_sub_cb": sekce["category_sub_cb"],
        "category_type_cb": 1,
        "locality_region_id": region_id,
        "locality_country_id": 112,
        "price_to": sekce["cena_do"],
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

    nalezene = []
    for inzerat in response.json().get("results", []):
        # POZOR: u casti pozemku je `price` cena ZA METR (price_unit_cb.value == 3),
        # takze by se v mailu misto 1 455 300 Kc objevilo 3 300 Kc. Celkova cena
        # za nemovitost je vzdy v `price_summary_czk`.
        cena = inzerat.get("price_summary_czk") or inzerat.get("price_czk") or 0
        # pojistka: cenovy strop hlidame i po svem, ne jen serverovym filtrem
        if not cena or cena > sekce["cena_do"]:
            continue

        nazev = inzerat.get("advert_name", "")
        plochy = plochy_z_nazvu(nazev)
        rozmery = [
            f"{fmt(hodnota)} m² {popisek}"
            for popisek, hodnota in zip(sekce["popisky_ploch"], plochy)
        ]
        lok = inzerat.get("locality", {})

        nalezene.append({
            "kraj": kraj,
            "obec": lok.get("city", ""),
            "okres": lok.get("district", ""),
            "cena": cena,
            "rozmery": " · ".join(rozmery),
            "cena_za_m2": inzerat.get("price_czk_m2"),
            "odkaz": odkaz_na_detail(inzerat, sekce["detail_cesta"]),
        })
    return nalezene


def karta(polozka, sekce):
    radek_m2 = f"{fmt(polozka['cena_za_m2'])} Kč/m²" if polozka["cena_za_m2"] else ""
    return (
        f'<a href="{polozka["odkaz"]}" style="text-decoration:none;color:inherit;" target="_blank">'
        f'<div style="background:white;border-radius:10px;padding:16px;margin-bottom:12px;'
        f'box-shadow:0 1px 4px rgba(0,0,0,0.08);border-left:4px solid {sekce["barva"]}">'
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start">'
        f'<div style="flex:1;padding-right:12px">'
        f'<div style="font-weight:700;font-size:15px;color:{sekce["barva_textu"]};margin-bottom:6px">'
        f'{polozka["obec"]}</div>'
        f'<div style="color:#6b7280;font-size:12px;margin-bottom:4px">📍 okres {polozka["okres"]} · '
        f'{polozka["kraj"]}</div>'
        + (f'<div style="color:#6b7280;font-size:13px">📐 {polozka["rozmery"]}</div>' if polozka["rozmery"] else "")
        + (f'<div style="color:#6b7280;font-size:13px">💰 {radek_m2}</div>' if radek_m2 else "")
        + f'</div>'
        f'<div style="background:{sekce["barva"]};color:white;padding:8px 12px;border-radius:8px;'
        f'font-weight:800;font-size:16px;white-space:nowrap;min-width:70px;text-align:center">'
        f'{fmt(polozka["cena"])} Kč</div>'
        f'</div></div></a>'
    )


od = datetime.now() - timedelta(hours=24)
print("Stahuji novinky za poslednich 24 hodin...")

for sekce in SEKCE:
    sekce["polozky"] = []
    print(f"{sekce['nadpis']}:")
    for kraj, region_id in KRAJE.items():
        nalezene = stahni(sekce, kraj, region_id, od)
        print(f"  {kraj}: {len(nalezene)}")
        sekce["polozky"].extend(nalezene)
    sekce["polozky"].sort(key=lambda x: x["cena"])

celkem = sum(len(s["polozky"]) for s in SEKCE)
print(f"\nCelkem {celkem} novych inzeratu.")

if not celkem:
    print("Nic noveho - email se neposila.")
    sys.exit(0)

pocet_chat = len(SEKCE[0]["polozky"])
pocet_pozemku = len(SEKCE[1]["polozky"])
souhrn = " · ".join(
    text
    for text in (
        sklonuj(pocet_chat, "nová chata", "nové chaty", "nových chat") if pocet_chat else "",
        sklonuj(pocet_pozemku, "nový pozemek", "nové pozemky", "nových pozemků") if pocet_pozemku else "",
    )
    if text
)

datum = datetime.now().strftime("%d. %m. %Y")
telo = ""

for sekce in SEKCE:
    # Prazdna sekce se nevykresluje vubec - nadpis bez obsahu jen zabira misto.
    if not sekce["polozky"]:
        continue
    telo += (
        f'<div style="margin:0 0 10px;padding:0 4px">'
        f'<span style="font-size:15px;font-weight:800;color:{sekce["barva_textu"]}">'
        f'{sekce["ikona"]} {sekce["nadpis"]}</span>'
        f'<span style="color:#9ca3af;font-size:13px"> · {len(sekce["polozky"])}</span>'
        f"</div>"
    )
    telo += "".join(karta(p, sekce) for p in sekce["polozky"])
    telo += '<div style="height:14px"></div>'

html_report = (
    "<!DOCTYPE html><html>"
    '<head><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>'
    '<body style="margin:0;padding:0;background:#f8fafc;font-family:Arial,sans-serif">'
    '<div style="max-width:600px;margin:0 auto;padding:16px">'
    '<div style="background:linear-gradient(135deg,#15803d,#4ade80);padding:28px;text-align:center;'
    'border-radius:12px;margin-bottom:16px">'
    '<h1 style="margin:0;color:white;font-size:22px">🏡 Chaty a stavební pozemky</h1>'
    f'<p style="margin:8px 0 0;color:#dcfce7;font-size:14px">{datum} · {souhrn}<br>'
    "Středočeský, Ústecký a Liberecký kraj</p>"
    "</div>"
    f"{telo}"
    '<div style="text-align:center;padding:16px">'
    '<p style="margin:0;color:#9ca3af;font-size:11px">Chaty do 2 000 000 Kč a stavební pozemky '
    "do 1 500 000 Kč přidané za posledních 24 hodin · v každé sekci seřazeno od nejlevnějšího · "
    "Data ze Sreality</p>"
    "</div></div></body></html>"
)

if "--nahled" in sys.argv:
    with open("nahled-chaty.html", "w", encoding="utf-8") as f:
        f.write(html_report)
    print("Nahled ulozen do nahled-chaty.html - email se neposila.")
    sys.exit(0)

zprava = MIMEMultipart("alternative")
zprava["Subject"] = f"🏡 {souhrn} · {datum}"
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
