import os
import requests
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta

headers = {"User-Agent": "Mozilla/5.0"}

def vytahni_podil(nazev):
    m = re.search(r"(\d+)\s*/\s*(\d+)", nazev)
    if m:
        citatel = int(m.group(1))
        jmenovatel = int(m.group(2))
        if jmenovatel > 0 and citatel <= jmenovatel:
            return citatel / jmenovatel
    return 1.0

print("Stahuji ceny bytů podle části Prahy...")

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

ceny_prahy = {}
for nazev_prahy, district_id in prahy.items():
    url = "https://www.sreality.cz/api/v1/estates/search"
    params = {
        "category_main_cb": 1,
        "category_type_cb": 1,
        "locality_district_id": district_id,
        "locality_country_id": 112,
        "locality_region_id": 10,
        "limit": 60,
        "offset": 0,
        "lang": "cs",
        "ownership": 1,  # jen osobní vlastnictví, vyřadí družstevní byty
    }
    response = requests.get(url, params=params, headers=headers)
    data = response.json()
    ceny_za_m2 = []
    for inzerat in data.get("results", []):
        cena = inzerat.get("price", 0)
        nazev = inzerat.get("advert_name", "")
        if "družstev" in nazev.lower() or "druzstev" in nazev.lower():
            continue
        m = re.search(r"(\d+)\s*m²", nazev)
        if m and cena and cena > 100000:
            plocha = int(m.group(1))
            if plocha > 0:
                ceny_za_m2.append(cena / plocha)
    if ceny_za_m2:
        ceny_prahy[nazev_prahy] = sum(ceny_za_m2) / len(ceny_za_m2)

print(f"Hotovo! Načteno {len(ceny_prahy)} částí Prahy.\n")
print("Stahuji nové činžovní domy za posledních 24 hodin...")

url = "https://www.sreality.cz/api/v1/estates/search"
params = {
    "category_main_cb": 4,
    "category_sub_cb": 38,
    "category_type_cb": 1,
    "locality_region_id": 10,
    "locality_country_id": 112,
    "limit": 60,
    "offset": 0,
    "lang": "cs",
    "sort": "-date",
    "watchdog_last_changed_from": (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S"),
}
response = requests.get(url, params=params, headers=headers)
data = response.json()
print(f"Nalezeno {data.get('pagination', {}).get('total', 0)} nových inzerátů.\n")

domy = []
for inzerat in data.get("results", []):
    hash_id = inzerat.get("hash_id")
    nazev = inzerat.get("advert_name", "")
    lokalita = inzerat.get("locality", {}).get("city", "")
    cena_raw = inzerat.get("price", 0)
    mesto_seo = inzerat.get("locality", {}).get("city_seo_name", "")
    okres_seo = inzerat.get("locality", {}).get("district_seo_name", "")
    ulice = inzerat.get("locality", {}).get("street_seo_name", "")
    odkaz = f"https://www.sreality.cz/detail/prodej/komercni/cinzovni-dum/{mesto_seo}-{okres_seo}-{ulice}/{hash_id}"

    cast_prahy = None
    ward = inzerat.get("locality", {}).get("ward") or ""
    district = inzerat.get("locality", {}).get("district") or ""
    for p in prahy.keys():
        cislo = p.replace("Praha ", "")
        if f"Praha {cislo}" in district or f"Praha {cislo}" in ward:
            cast_prahy = p
            break
    if not cast_prahy and "Praha" in lokalita:
        cast_prahy = "Praha 1"

    detail_url = f"https://www.sreality.cz/api/v1/estates/{hash_id}"
    try:
        detail = requests.get(detail_url, headers=headers).json()
    except:
        detail = {}

    uzitna_plocha = None
    kupni_cena = None
    for item in detail.get("items", []):
        item_name = item.get("name", "")
        item_value = item.get("value", "")
        if "Užitná ploch" in item_name:
            try:
                uzitna_plocha = float(str(item_value).replace(" ", "").replace(",", "."))
            except:
                pass
        if "Celková cena" in item_name:
            try:
                kupni_cena = float(str(item_value).replace(" ", "").replace(",", "."))
            except:
                pass

    if not kupni_cena and cena_raw and cena_raw > 1:
        kupni_cena = cena_raw

    domy.append({
        "nazev": nazev,
        "lokalita": lokalita,
        "cast_prahy": cast_prahy,
        "uzitna_plocha": uzitna_plocha,
        "kupni_cena": kupni_cena,
        "odkaz": odkaz,
    })

print(f"Zpracováno {len(domy)} domů.\n")

CENA_REKONSTRUKCE_M2 = 50000
CENA_REKONSTRUKCE_M2_BEZ_PLOCHY = 40000
SLEVA_BEZ_PLOCHY = 0.20

vysledky = []
for dum in domy:
    uzitna_plocha = dum["uzitna_plocha"]
    kupni_cena = dum["kupni_cena"]
    cast_prahy = dum["cast_prahy"]
    podil = vytahni_podil(dum["nazev"])

    if not cast_prahy or cast_prahy not in ceny_prahy:
        continue

    prumerna_cena_m2 = ceny_prahy[cast_prahy]

    if uzitna_plocha and kupni_cena and kupni_cena > 1:
        cena_rekonstrukce = uzitna_plocha * podil * CENA_REKONSTRUKCE_M2
        potencialni_prodej = uzitna_plocha * podil * prumerna_cena_m2
        zisk = potencialni_prodej - (kupni_cena + cena_rekonstrukce)
        ma_plochu = True
    elif uzitna_plocha and (not kupni_cena or kupni_cena <= 1):
        cena_rekonstrukce = uzitna_plocha * podil * CENA_REKONSTRUKCE_M2
        potencialni_prodej = uzitna_plocha * podil * prumerna_cena_m2
        zisk = None
        ma_plochu = True
    elif kupni_cena and kupni_cena > 1 and not uzitna_plocha:
        m = re.search(r"(\d+)\s*m²", dum["nazev"])
        if m:
            uzitna_plocha = int(m.group(1))
            cena_rekonstrukce = uzitna_plocha * podil * CENA_REKONSTRUKCE_M2_BEZ_PLOCHY
            potencialni_prodej = uzitna_plocha * podil * prumerna_cena_m2 * (1 - SLEVA_BEZ_PLOCHY)
            zisk = potencialni_prodej - (kupni_cena + cena_rekonstrukce)
            ma_plochu = False
        else:
            continue
    else:
        zisk = None
        cena_rekonstrukce = None
        potencialni_prodej = None
        ma_plochu = False

    podil_text = f" (podíl {int(podil*100)}%)" if podil < 1.0 else ""

    vysledky.append({
        "nazev": dum["nazev"],
        "lokalita": dum["lokalita"],
        "cast_prahy": cast_prahy,
        "uzitna_plocha": uzitna_plocha,
        "kupni_cena": kupni_cena,
        "cena_rekonstrukce": cena_rekonstrukce,
        "potencialni_prodej": potencialni_prodej,
        "zisk": zisk,
        "ma_plochu": ma_plochu,
        "podil_text": podil_text,
        "odkaz": dum["odkaz"],
    })

vysledky.sort(key=lambda x: x["zisk"] if x["zisk"] else -999999999, reverse=True)

if not vysledky:
    print("Žádné nové činžáky za posledních 24 hodin – email se neposílá.")
else:
    datum = datetime.now().strftime("%d. %m. %Y")
    karty = ""

    for v in vysledky:
        kupni_fmt = f"{v['kupni_cena']:,.0f} Kč".replace(",", " ") if v["kupni_cena"] and v["kupni_cena"] > 1 else "N/A"
        plocha_fmt = f"{v['uzitna_plocha']:,.0f} m²".replace(",", " ") if v["uzitna_plocha"] else "N/A"
        rekonstrukce_fmt = f"{v['cena_rekonstrukce']:,.0f} Kč".replace(",", " ") if v["cena_rekonstrukce"] else "N/A"
        prodej_fmt = f"{v['potencialni_prodej']:,.0f} Kč".replace(",", " ") if v["potencialni_prodej"] else "N/A"

        if v["zisk"] and v["zisk"] > 0:
            zisk_fmt = f"+{v['zisk']:,.0f} Kč".replace(",", " ")
            zisk_barva = "#22c55e"
        elif v["zisk"] and v["zisk"] <= 0:
            zisk_fmt = f"{v['zisk']:,.0f} Kč".replace(",", " ")
            zisk_barva = "#ef4444"
        else:
            zisk_fmt = "N/A"
            zisk_barva = "#9ca3af"

        poznamka = "" if v["ma_plochu"] else '<div style="color:#f59e0b;font-size:11px;margin-top:4px">⚠️ Plocha odhadnuta z názvu, cena snížena o 20%</div>'

        karty += (
            f'<a href="{v["odkaz"]}" style="text-decoration:none;color:inherit;display:block;" target="_blank">'
            f'<div style="background:white;border-radius:10px;padding:16px;margin-bottom:12px;box-shadow:0 1px 4px rgba(0,0,0,0.08);border-left:4px solid {zisk_barva}">'
            f'<div style="display:flex;justify-content:space-between;align-items:flex-start">'
            f'<div style="flex:1;padding-right:12px">'
            f'<div style="font-weight:700;font-size:15px;color:#1d4ed8;margin-bottom:6px">{v["nazev"]}{v["podil_text"]}</div>'
            f'<div style="color:#6b7280;font-size:12px;margin-bottom:4px">📍 {v["lokalita"]} · {v["cast_prahy"]}</div>'
            f'<div style="color:#6b7280;font-size:13px">💰 Kupní cena: {kupni_fmt}</div>'
            f'<div style="color:#6b7280;font-size:13px">📐 Užitná plocha: {plocha_fmt}</div>'
            f'<div style="color:#6b7280;font-size:13px">🔨 Rekonstrukce: {rekonstrukce_fmt}</div>'
            f'<div style="color:#6b7280;font-size:13px">🏷️ Pot. prodejní cena: {prodej_fmt}</div>'
            f'{poznamka}'
            f'</div>'
            f'<div style="background:{zisk_barva};color:white;padding:8px 12px;border-radius:8px;font-weight:800;font-size:15px;white-space:nowrap;min-width:80px;text-align:center">'
            f'{zisk_fmt}</div></div></div></a>'
        )

    html_report = (
        "<!DOCTYPE html><html>"
        '<head><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>'
        '<body style="margin:0;padding:0;background:#f8fafc;font-family:Arial,sans-serif">'
        '<div style="max-width:600px;margin:0 auto;padding:16px">'
        '<div style="background:linear-gradient(135deg,#1d4ed8,#3b82f6);padding:28px;text-align:center;border-radius:12px;margin-bottom:16px">'
        f'<h1 style="margin:0;color:white;font-size:22px">🏢 Nové činžovní domy Praha</h1>'
        f'<p style="margin:8px 0 0;color:#bfdbfe;font-size:14px">{datum} · {len(vysledky)} nemovitostí</p>'
        "</div>"
        f"{karty}"
        '<div style="text-align:center;padding:16px">'
        '<p style="margin:0;color:#9ca3af;font-size:11px">Zisk = pot. prodejní cena minus kupní cena plus rekonstrukce · Ceny bytů z aktuálních inzerátů Sreality</p>'
        "</div></div></body></html>"
    )

    zprava = MIMEMultipart("alternative")
    zprava["Subject"] = f"🏢 Nové činžovní domy Praha – {len(vysledky)} nemovitostí · {datum}"
    zprava["From"] = "realitybot@seznam.cz"
    zprava["To"] = "tomas.tuzar@seznam.cz"
    zprava.attach(MIMEText(html_report, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.email.cz", 465) as server:
            server.login("realitybot@seznam.cz", os.environ.get("SMTP_PASS", "Necum123"))
            server.sendmail("realitybot@seznam.cz", "tomas.tuzar@seznam.cz", zprava.as_string())
        print(f"Report odeslán! {len(vysledky)} nemovitostí.")
    except Exception as e:
        print("CHYBA: " + str(e))