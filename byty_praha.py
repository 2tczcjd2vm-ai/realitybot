import os
import requests
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta

headers = {"User-Agent": "Mozilla/5.0"}

dispozice_map = {
    2: "1+kk", 3: "1+1", 4: "2+kk", 5: "2+1",
    6: "3+kk", 7: "3+1", 8: "4+kk", 9: "4+1",
    10: "5+kk", 11: "5+1", 12: "6-a-vice", 16: "atypicky"
}

print("Stahuji průměrné ceny bytů...")

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
        "ownership": 1,
        "price_to": 8000000,
        "no_auction": 1,
    }
    response = requests.get(url, params=params, headers=headers)
    data = response.json()
    ceny_za_m2 = []
    for inzerat in data.get("results", []):
        cena = inzerat.get("price", 0)
        nazev = inzerat.get("advert_name", "")
        m = re.search(r"(\d+)\s*m²", nazev)
        if m and cena and cena > 100000:
            plocha = int(m.group(1))
            if plocha > 0:
                ceny_za_m2.append(cena / plocha)
    if ceny_za_m2:
        ceny_prahy[nazev_prahy] = sum(ceny_za_m2) / len(ceny_za_m2)
        print(f"  {nazev_prahy}: {ceny_prahy[nazev_prahy]:,.0f} Kč/m²")

print()
print("Stahuji nové byty za posledních 24 hodin...")

byty = []
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
        "sort": "-date",
        "ownership": 1,
        "price_to": 8000000,
        "no_auction": 1,
        "watchdog_last_changed_from": (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S"),
    }
    response = requests.get(url, params=params, headers=headers)
    data = response.json()

    for inzerat in data.get("results", []):
        cena = inzerat.get("price", 0)
        nazev = inzerat.get("advert_name", "")
        lokalita = inzerat.get("locality", {}).get("city", "")
        hash_id = inzerat.get("hash_id")
        disp = inzerat.get("category_sub_cb", {}).get("name", "")
        mesto_seo = inzerat.get("locality", {}).get("city_seo_name", "")
        okres_seo = inzerat.get("locality", {}).get("district_seo_name", "")
        ulice = inzerat.get("locality", {}).get("street_seo_name", "")
        disp_url = disp.replace("+", "%2B") if disp else ""
        odkaz = f"https://www.sreality.cz/detail/prodej/byt/{disp_url}/{mesto_seo}-{okres_seo}-{ulice}/{hash_id}" if hash_id else "#"

        m = re.search(r"(\d+)\s*m²", nazev)
        if not m or not cena or cena < 100000:
            continue

        plocha = int(m.group(1))
        if plocha <= 0:
            continue

        cena_za_m2 = cena / plocha

        if nazev_prahy not in ceny_prahy:
            continue

        prumer = ceny_prahy[nazev_prahy]
        odchylka = ((cena_za_m2 - prumer) / prumer) * 100

        byty.append({
            "nazev": nazev,
            "lokalita": lokalita,
            "cast_prahy": nazev_prahy,
            "cena": cena,
            "plocha": plocha,
            "cena_za_m2": cena_za_m2,
            "prumer_prahy": prumer,
            "odchylka": odchylka,
            "odkaz": odkaz,
        })

print(f"Nalezeno {len(byty)} nových bytů.\n")

if not byty:
    print("Žádné nové byty – email se neposílá.")
else:
    byty.sort(key=lambda x: x["odchylka"])
    top20 = byty[:20]

    datum = datetime.now().strftime("%d. %m. %Y")
    karty = ""

    for b in top20:
        odchylka = b["odchylka"]
        if odchylka < -10:
            barva = "#22c55e"
        elif odchylka < 0:
            barva = "#f59e0b"
        else:
            barva = "#ef4444"

        odchylka_fmt = f"{odchylka:+.1f}%"
        cena_fmt = f"{b['cena']:,.0f} Kč".replace(",", " ")
        cena_m2_fmt = f"{b['cena_za_m2']:,.0f} Kč/m²".replace(",", " ")
        prumer_fmt = f"{b['prumer_prahy']:,.0f} Kč/m²".replace(",", " ")

        karty += (
            f'<a href="{b["odkaz"]}" style="text-decoration:none;color:inherit;" target="_blank">'
            f'<div style="background:white;border-radius:10px;padding:16px;margin-bottom:12px;box-shadow:0 1px 4px rgba(0,0,0,0.08);border-left:4px solid {barva}">'
            f'<div style="display:flex;justify-content:space-between;align-items:flex-start">'
            f'<div style="flex:1;padding-right:12px">'
            f'<div style="font-weight:700;font-size:15px;color:#1d4ed8;margin-bottom:6px">{b["nazev"]}</div>'
            f'<div style="color:#6b7280;font-size:12px;margin-bottom:4px">📍 {b["lokalita"]} · {b["cast_prahy"]}</div>'
            f'<div style="color:#6b7280;font-size:13px">💰 Cena: {cena_fmt}</div>'
            f'<div style="color:#6b7280;font-size:13px">📐 Cena/m²: {cena_m2_fmt}</div>'
            f'<div style="color:#6b7280;font-size:13px">📊 Průměr {b["cast_prahy"]}: {prumer_fmt}</div>'
            f'</div>'
            f'<div style="background:{barva};color:white;padding:8px 12px;border-radius:8px;font-weight:800;font-size:18px;white-space:nowrap;min-width:70px;text-align:center">'
            f'{odchylka_fmt}</div></div></div></a>'
        )

    html_report = (
        "<!DOCTYPE html><html>"
        '<head><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>'
        '<body style="margin:0;padding:0;background:#f8fafc;font-family:Arial,sans-serif">'
        '<div style="max-width:600px;margin:0 auto;padding:16px">'
        '<div style="background:linear-gradient(135deg,#7c3aed,#a78bfa);padding:28px;text-align:center;border-radius:12px;margin-bottom:16px">'
        f'<h1 style="margin:0;color:white;font-size:22px">🏠 Nové podhodnocené byty Praha</h1>'
        f'<p style="margin:8px 0 0;color:#ede9fe;font-size:14px">{datum} · {len(top20)} bytů · seřazeno podle odchylky od průměru</p>'
        "</div>"
        f"{karty}"
        '<div style="text-align:center;padding:16px">'
        '<p style="margin:0;color:#9ca3af;font-size:11px">Odchylka = (cena/m² − průměr části Prahy) / průměr · Data ze Sreality</p>'
        "</div></div></body></html>"
    )

    zprava = MIMEMultipart("alternative")
    zprava["Subject"] = f"🏠 Nové byty Praha · {datum}"
    zprava["From"] = "realitybot@seznam.cz"
    zprava["To"] = "tomas.tuzar@seznam.cz"
    zprava.attach(MIMEText(html_report, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.email.cz", 465) as server:
            server.login("realitybot@seznam.cz", os.environ.get("SMTP_PASS", "Necum123"))
            server.sendmail("realitybot@seznam.cz", "tomas.tuzar@seznam.cz", zprava.as_string())
        print(f"Report odeslán! {len(top20)} bytů.")
    except Exception as e:
        print("CHYBA: " + str(e))