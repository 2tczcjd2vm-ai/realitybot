import imaplib
import email
import requests
import re
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header
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
        if "sreality" in href or "visidoo" in href or "bezrealitky" in href:
            return href
    return None

# --- Stáhni nájmy ---
print("Stahuji nájmy ze Sreality...")
najmy = get_najmy(25)
print("Hotovo!\n")

# --- Přečti emaily z posledních 24 hodin ---
mail = imaplib.IMAP4_SSL("imap.seznam.cz")
mail.login("realitybot@seznam.cz", "Necum123")
mail.select("inbox")

vcera = (datetime.now() - timedelta(days=1)).strftime("%d-%b-%Y")