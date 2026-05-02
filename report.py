import imaplib
import email
import requests
import re
import smtplib
import json
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header
from bs4 import BeautifulSoup

VIDENE_SOUBOR = r"C:\Users\tomas.tuzar.TUZARNB2\Downloads\videne.json"

def nacti_videne():
    if os.path.exists(VIDENE_SOUBOR):
        with open(VIDENE_SOUBOR, "r") as f:
            return set(json.load(f))
    return set()

def uloz_videne(videne):
    with open(VIDENE_SOUBOR, "w") as f:
        json.dump(list(videne), f)

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
        if "sreality" in href or "visidoo" in href or "bezrealitky" in href:
            return href
    return None

# --- Stáhni nájmy ---
print("Stahuji nájmy ze Sreality...")
najmy = get_najmy(25)
print("Hotovo!\n")

# --- Načti už viděné byty ---
videne = nacti_videne()

# --- Přečti emaily ---
mail = imaplib.IMAP4_SSL("imap.seznam.cz")
mail.login("realitybot@seznam.cz", "Necum123")
mail.select("inbox")
_, messages = mail.search(None, "ALL")

byty = []
nove_videne = set()

for msg_id in messages[0].split():
    _, msg_data = mail.fetch(msg_id, "(RFC822)")
    msg = email.message_from_bytes(msg_data[0][1])

    predmet = dekoduj(msg["subject"])
    if "Fwd:" not in predmet:
        continue

    html = get_body(msg)
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator=" ", strip=True)

    cena = vytahni_cenu(text)
    disp = vytahni_dispozici(predmet)
    url = vytahni_url(soup)

    # Unikátní ID bytu = název + cena
    uid = f"{predmet}_{cena}"
    nove_videne.add(uid)

    if uid in videne:
        print(f"Přeskakuji (už viděno): {predmet}")
        continue

    if cena and disp and disp in najmy:
        najem = najmy[disp]
        navratnost = (najem * 12 / cena) * 100
        byty.append({
            "nazev": predmet.replace("Fwd: ", ""),
            "cena": cena,
            "najem": najem,
            "navratnost": navratnost,
            "url": url
        })

mail.logout()

# --- Ulož viděné ---
uloz_videne(videne | nove_videne)

if not byty:
    print("Žádné nové byty dnes.")
else:
    # --- Sestav email ---
    radky = ""
    for b in sorted(byty, key=lambda x: x["navratnost"], reverse=True):
        cena_fmt = f"{b['cena']:,}".replace(",", " ")
        najem_fmt = f"{b['najem']:,.0f}".replace(",", " ")
        nazev_link = f'<a href="{b["url"]}">{b["nazev"]}</a>' if b["url"] else b["nazev"]
        radky += f"""
<tr>
  <td style="padding:8px;border-bottom:1px solid #eee">{nazev_link}</td>
  <td style="padding:8px;border-bottom:1px solid #eee">{cena_fmt} Kč</td>
  <td style="padding:8px;border-bottom:1px solid #eee">{najem_fmt} Kč/měsíc</td>
  <td style="padding:8px;border-bottom:1px solid #eee;font-weight:bold">{b['navratnost']:.1f}%</td>
</tr>"""

    html_report = f"""
<html><body>
<h2>Reality Report – nové byty</h2>
<table style="border-collapse:collapse;width:100%">
  <tr style="background:#f0f0f0">
    <th style="padding:8px;text-align:left">Byt</th>
    <th style="padding:8px;text-align:left">Cena</th>
    <th style="padding:8px;text-align:left">Odh. nájem</th>
    <th style="padding:8px;text-align:left">Návratnost</th>
  </tr>
  {radky}
</table>
</body></html>
"""

    zprava = MIMEMultipart("alternative")
    zprava["Subject"] = f"Reality Report – {len(byty)} nových bytů"
    zprava["From"] = "realitybot@seznam.cz"
    zprava["To"] = "tomas.tuzar@seznam.cz"
    zprava.attach(MIMEText(html_report, "html"))

    with smtplib.SMTP_SSL("smtp.seznam.cz", 465) as server:
        server.login("realitybot@seznam.cz", "Necum123")
        server.sendmail("realitybot@seznam.cz", "tomas.tuzar@seznam.cz", zprava.as_string())

    print(f"Report odeslán – {len(byty)} nových bytů!")