import imaplib
import email
import requests
import re
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header
from urllib.parse import unquote, urlparse, parse_qs
from bs4 import BeautifulSoup


def get_najmy(locality_district_id):
    url = "https://www.sreality.cz/api/cs/v2/estates"
    params = {
        "category_main_cb": 1,
        "category_type_cb": 2,
        "locality_district_id": locality_district_id,
        "per_page": 60,
    }
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, params=params, headers=headers)
    data = response.json()

    najmy = {}
    for inzerat in data["_embedded"]["estates"]:
        nazev = inzerat.get("name", "")
        cena = inzerat.get("price", 0)
        if cena and cena < 30000:
            for disp in ["1+kk", "1+1", "2+kk", "2+1", "3+kk", "3+1"]:
                if disp in nazev:
                    if disp not in najmy:
                        najmy[disp] = []
                    najmy[disp].append(cena)

    prumery = {}
    for disp, ceny in najmy.items():
        prumery[disp] = sum(ceny) / len(ceny)
    return prumery


def dekoduj(text):
    parts = decode_header(text)
    result = ""
    for part, encoding in parts:
        if isinstance(part, bytes):
            result += part.decode(encoding or "utf-8")
        else:
            result += part
    return result


def get_body(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                return part.get_payload(decode=True).decode("utf-8", errors="ignore")
    else:
        return msg.get_payload(decode=True).decode("utf-8", errors="ignore")


def vytahni_cenu(text):
    matches = re.findall(r"([\d\s\xa0]+)\s*Kč", text)
    ceny = []
    for m in matches:
        try:
            cislo = int(re.sub(r'\s+', '', m).replace("\xa0", ""))
            if 200000 < cislo < 10000000:
                ceny.append(cislo)
        except:
            pass
    if ceny:
        return min(ceny)
    return None


def vytahni_dispozici(text):
    for disp in ["1+kk", "1+1", "2+kk", "2+1", "3+kk", "3+1"]:
        if disp in text:
            return disp
    return None


def vytahni_url(soup):
    for a in soup.find_all("a", href=True):
        href = a["href"]
        nazev = a.get_text(strip=True)
        if "Prodej" in nazev and "byt" in nazev.lower():
            return href
    return None


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
print("Hotovo!\n")

# --- Prectи emaily ---
mail = imaplib.IMAP4_SSL("imap.seznam.cz")
mail.login("realitybot@seznam.cz", "Necum123")
mail.select("inbox")

vcera = (datetime.now() - timedelta(hours=24)).strftime("%d-%b-%Y")
_, messages = mail.search(None, "SINCE", vcera)

if not messages[0]:
    print("Zadne nove emaily za poslednich 24 hodin.")
    mail.logout()
    exit()

print("=" * 50)
print("BYTY Z POSLEDNICH 24 HODIN")
print("=" * 50)

byty = []

for msg_id in messages[0].split():
    _, msg_data = mail.fetch(msg_id, "(RFC822)")
    msg = email.message_from_bytes(msg_data[0][1])

    predmet = dekoduj(msg["subject"])
    if "Fwd:" not in predmet and "Prodej" not in predmet:
        continue

    html = get_body(msg)
    if not html:
        continue

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator=" ", strip=True)

    cena = vytahni_cenu(text)
    disp = vytahni_dispozici(predmet)
    url = vytahni_url(soup)

    if cena and disp and disp in najmy:
        najem = najmy[disp]
        rocni_najem = najem * 12
        navratnost = (rocni_najem / cena) * 100
        barva = navratnost_barva(navratnost)

        print(predmet.replace("Fwd: ", ""))
        print("  Cena:       " + fmt(cena) + " Kc")
        print("  Navratnost: " + str(round(navratnost, 1)) + "%")
        if url:
            print("  URL:        " + url)

        byty.append({
            "nazev": predmet.replace("Fwd: ", ""),
            "cena": cena,
            "najem": najem,
            "navratnost": navratnost,
            "barva": barva,
            "url": url
        })

mail.logout()

# --- Posli email ---
if not byty:
    print("Zadne nove byty - email se neposila.")
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
        with smtplib.SMTP_SSL("smtp.seznam.cz", 465) as server:
            server.login("realitybot@seznam.cz", "Necum123")
            server.sendmail("realitybot@seznam.cz", "tomas.tuzar@seznam.cz", zprava.as_string())
        print("Report odeslan - " + pocet + " bytu!")
    except Exception as e:
        print("CHYBA: " + str(e))
