import os
import requests
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

def get_najmy(locality_district_id):
    url = "https://www.sreality.cz/api/v1/estates/search"
    params = {
        "category_main_cb": 1,
        "category_type_cb": 2,
        "locality_district_id": locality_district_id,
        "locality_country_id": 112,
        "locality_region_id": 4,
        "limit": 60,
        "offset": 0,
        "lang": "cs",
    }
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, params=params, headers=headers)
    data = response.json()

    najmy = {}
    for inzerat in data.get("results", []):
        cena = inzerat.get("price", 0)
        disp = inzerat.get("category_sub_cb", {}).get("name", "")
        if cena and cena < 30000 and disp:
            if disp not in najmy:
                najmy[disp] = []
            najmy[disp].append(cena)

    prumery = {}
    for disp, ceny in najmy.items():
        prumery[disp] = sum(ceny) / len(ceny)
    return prumery


def get_nove_byty(locality_district_id):
    url = "https://www.sreality.cz/api/v1/estates/search"
    params = {
        "category_main_cb": 1,
        "category_type_cb": 1,
        "locality_district_id": locality_district_id,
        "locality_country_id": 112,
        "locality_region_id": 4,
        "limit": 60,
        "offset": 0,
        "lang": "cs",
        "sort": "-date",
        "ownership": 1,
        "price_to": 2600000,
        "watchdog_last_changed_from": (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S"),
    }
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, params=params, headers=headers)
    data = response.json()
    return data.get("results", [])


def navratnost_barva(n):
    if n >= 10:
        return "#22c55e"
    elif n >= 7:
        return "#eab308"
    else:
        return "#ef4444"


def fmt(cena):
    return "{:,}".format(int(cena)).replace(",", " ")


# --- Stahni najmy ---
print("Stahuji najmy ze Sreality...")
najmy = get_najmy(25)
print("Najmy: " + str(najmy))

# --- Stahni nove byty ---
print("Stahuji nove byty...")
vysledky = get_nove_byty(25)
print("Nalezeno: " + str(len(vysledky)) + " bytu za poslednich 24 hodin")

byty = []

for inzerat in vysledky:
    nazev = inzerat.get("advert_name", "")
    cena = inzerat.get("price", 0)
    disp = inzerat.get("category_sub_cb", {}).get("name", "")
    hash_id = inzerat.get("hash_id")
    mesto = inzerat.get("locality", {}).get("city", "")

    url_inzeratu = "https://www.sreality.cz/hledej?id=" + str(hash_id) if hash_id else None

    if cena and disp and disp in najmy:
        najem = najmy[disp]
        rocni_najem = najem * 12
        navratnost = (rocni_najem / cena) * 100
        barva = navratnost_barva(navratnost)

        print(nazev + " " + mesto + " - " + fmt(cena) + " Kc - " + str(round(navratnost, 1)) + "%")

        byty.append({
            "nazev": nazev + " " + mesto,
            "cena": cena,
            "najem": najem,
            "navratnost": navratnost,
            "barva": barva,
            "url": url_inzeratu
        })

# --- Posli email ---
if not byty:
    print("Zadne nove byty za poslednich 24 hodin - email se neposila.")
else:
    byty_sorted = sorted(byty, key=lambda x: x["navratnost"], reverse=True)

    radky = ""
    for b in byty_sorted:
        radky += (
            "<tr style='border-bottom:1px solid #f3f4f6'>"
            "<td style='padding:12px 16px'>"
            "<a href='" + (b["url"] or "#") + "' style='color:#2563eb;font-weight:600;text-decoration:none'>" + b["nazev"] + "</a><br>"
            "<span style='color:#6b7280;font-size:13px'>" + fmt(b["cena"]) + " Kc &nbsp;&middot;&nbsp; najem " + fmt(b["najem"]) + " Kc/mes</span>"
            "</td>"
            "<td style='padding:12px 16px;text-align:center;white-space:nowrap'>"
            "<span style='background:" + b["barva"] + ";color:white;padding:4px 10px;border-radius:20px;font-weight:700;font-size:13px'>" + str(round(b["navratnost"], 1)) + "%</span>"
            "</td>"
            "</tr>"
        )

    datum = datetime.now().strftime("%d. %m. %Y")
    pocet = str(len(byty))

    html_report = (
        "<html>"
        "<body style='font-family:Arial,sans-serif;max-width:600px;margin:auto;padding:20px;background:#f4f4f4'>"
        "<div style='background:linear-gradient(135deg,#3b82f6,#2563eb);padding:30px 20px;text-align:center;border-radius:10px 10px 0 0'>"
        "<h2 style='color:white;margin:0;font-size:24px'>&#127968; Reality Report</h2>"
        "<p style='color:#bfdbfe;margin:8px 0 0;font-size:14px'>" + datum + " &nbsp;&middot;&nbsp; " + pocet + " novych bytu</p>"
        "</div>"
        "<div style='background:white;border-radius:0 0 10px 10px;overflow:hidden'>"
        "<table style='border-collapse:collapse;width:100%'>"
        "<tr style='background:#f9fafb'>"
        "<th style='padding:10px 16px;text-align:left;color:#9ca3af;font-size:11px;text-transform:uppercase;letter-spacing:0.05em;font-weight:600'>Nemovitost</th>"
        "<th style='padding:10px 16px;text-align:center;color:#9ca3af;font-size:11px;text-transform:uppercase;letter-spacing:0.05em;font-weight:600'>Vynos</th>"
        "</tr>"
        + radky +
        "</table>"
        "</div>"
        "<p style='color:#9ca3af;font-size:11px;text-align:center;margin-top:12px'>Realitybot &bull; automaticky report</p>"
        "</body></html>"
    )

    zprava = MIMEMultipart("alternative")
    zprava["Subject"] = "Reality Report - " + pocet + " novych bytu"
    zprava["From"] = "realitybot@seznam.cz"
    zprava["To"] = "tomas.tuzar@seznam.cz"
    zprava.attach(MIMEText(html_report, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.email.cz", 465) as server:
            server.login("realitybot@seznam.cz", "Necum123")
            server.sendmail("realitybot@seznam.cz", "tomas.tuzar@seznam.cz", zprava.as_string())
        print("Report odeslan - " + pocet + " bytu!")
    except Exception as e:
        print("CHYBA: " + str(e))