import os
import time
import requests
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta

headers = {"User-Agent": "Mozilla/5.0"}


def osobni_vlastnictvi(hash_id):
    """Ověří v detailu inzerátu, že jde o osobní vlastnictví.

    Filtr `ownership=1` v hledání ani slovo "družstevní" v názvu nestačí:
    19. 8. 2026 se do placeného výběru dostal družstevní byt v Čimicích,
    který měl v názvu jen "Prodej bytu 3+1 72 m2", prázdné štítky a v seznamu
    byl vedený jako osobní vlastnictví — na družstvo se přišlo až z popisu.

    Detail inzerátu drží pole ownership (1 = osobní, 2 = družstevní) a to je
    jediný spolehlivý zdroj. Kontroluje se jen hrstka bytů, které se chystáme
    poslat. Při pochybnostech vrací False — radši byt vynechat.
    """
    try:
        d = requests.get(
            "https://www.sreality.cz/api/v1/estates/%s" % hash_id,
            headers=headers, timeout=30,
        ).json().get("result", {})
    except Exception as e:
        print("  VAROVANI: detail %s se nepodarilo nacist (%s), byt vynechan" % (hash_id, e))
        return False

    if (d.get("ownership") or {}).get("value") != 1:
        return False

    popis = (d.get("advert_description") or "").lower()
    if "družstev" in popis or "druzstev" in popis:
        return False

    return True


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


dispozice_map = {
    2: "1+kk", 3: "1+1", 4: "2+kk", 5: "2+1",
    6: "3+kk", 7: "3+1", 8: "4+kk", 9: "4+1",
    10: "5+kk", 11: "5+1", 12: "6-a-vice", 16: "atypicky"
}

print("Stahuji průměrné ceny bytů...")

# Nabidka prechodu na placene clenstvi. Denni report je bezplatna verze,
# takze tohle je misto, kde ma smysl ji nabidnout.
#
# POZOR: report jde celou rozesilkou, tedy i pripadnym platicim clenum —
# ti ho uvidi taky. Az jich bude vic, bude potreba rozesilku rozdelit.
UPGRADE_BLOK = (
    '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:24px 0 0;border-collapse:collapse"><tr><td style="background:linear-gradient(135deg,#0d9488,#22d3ee);border-radius:14px;padding:2px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse"><tr><td style="background:#ffffff;border-radius:12px;padding:22px;font-family:Arial,sans-serif"><p style="margin:0 0 6px;font-size:11px;font-weight:800;color:#0d9488;letter-spacing:1px;text-transform:uppercase">Hledejte přesněji</p><p style="margin:0 0 14px;font-size:16px;font-weight:700;color:#0f172a">Chcete významně kvalitnější analýzu podle jednotlivých městských čtvrtí, lepší monitoring změn cen, pokrytí všech portálů a velmi brzy i další lokality mimo Prahu?</p><p style="margin:0 0 16px;font-size:13px;line-height:1.6;color:#475569">Upgradujte členství.</p><table role="presentation" cellpadding="0" cellspacing="0" style="border-collapse:collapse;margin:0 0 18px"><tr><td style="padding:0 0 8px;width:22px;color:#0d9488;font-weight:700">✓</td><td style="padding:0 0 8px;font-size:13px;line-height:1.5;color:#475569"><strong>Detailní analýza cen</strong> podle jednotlivých čtvrtí</td></tr><tr><td style="padding:0 0 8px;width:22px;color:#0d9488;font-weight:700">✓</td><td style="padding:0 0 8px;font-size:13px;line-height:1.5;color:#475569"><strong>Rozbor důvodů nízké ceny</strong> — stav bytu, panelový dům, patro. A když důvod nenajdeme, řekneme vám to.</td></tr><tr><td style="padding:0 0 8px;width:22px;color:#0d9488;font-weight:700">✓</td><td style="padding:0 0 8px;font-size:13px;line-height:1.5;color:#475569"><strong>Bez družstevních bytů a ateliérů</strong>, u kterých je srovnání ceny za metr zavádějící</td></tr><tr><td style="padding:0 0 8px;width:22px;color:#0d9488;font-weight:700">✓</td><td style="padding:0 0 8px;font-size:13px;line-height:1.5;color:#475569"><strong>Pokrytí všech velkých realitních portálů</strong></td></tr><tr><td style="padding:0 0 8px;width:22px;color:#0d9488;font-weight:700">✓</td><td style="padding:0 0 8px;font-size:13px;line-height:1.5;color:#475569"><strong>Upozornění na zlevnění</strong> u bytů, které jsme vám poslali</td></tr><tr><td style="width:22px;color:#0d9488;font-weight:700">✓</td><td style="font-size:13px;line-height:1.5;color:#475569"><strong>Nedělní souhrn</strong> s desítkou nejlepších bytů týdne</td></tr></table><a href="https://podhodnocenebyty.cz/clenstvi?utm_source=email&utm_medium=email&utm_campaign=denni-report" style="background:#0d9488;color:#ffffff;text-decoration:none;padding:13px 28px;border-radius:999px;font-weight:700;font-size:14px;display:inline-block">Upgradovat členství</a><p style="margin:14px 0 0;font-size:11px;color:#94a3b8">Od 249 Kč měsíčně. Zrušit můžete kdykoliv.</p></td></tr></table></td></tr></table>'
)

# Sesterske sluzby — stejny blok jako v uvitacich e-mailech z webu
# (lib/emailTemplate.ts, PARTNERI_HTML). Kdyz se meni tam, zmenit i tady.
PATICKA_PARTNERI = (
    '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:20px 0 0;border-collapse:collapse"><tr><td style="background:white;border:1px solid #e2e8f0;border-radius:12px;padding:20px 22px;font-family:Arial,sans-serif"><p style="margin:0 0 16px;font-size:13px;font-weight:700;color:#0f172a">Můžeme pomoct i s dalšími kroky</p><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse"><tr><td width="150" valign="middle" style="padding:0 16px 16px 0"><a href="https://www.hypogroup.cz"><img src="https://podhodnocenebyty.cz/hypogroup_zlate.png" alt="HypoGroup" width="132" style="display:block;width:132px;height:auto;border:0"></a></td><td valign="middle" style="padding:0 0 16px;font-size:13px;line-height:1.6;color:#475569">V případě zájmu o financování nás můžete nezávazně kontaktovat na <a href="https://www.hypogroup.cz" style="color:#0ea5b7;text-decoration:none;font-weight:600">www.hypogroup.cz</a>.</td></tr><tr><td width="150" valign="middle" style="padding:0 16px 0 0"><a href="https://www.ttcoreal.cz"><img src="https://podhodnocenebyty.cz/ttco-real.png" alt="TTCO Real" width="74" style="display:block;width:74px;height:auto;border:0;border-radius:6px"></a></td><td valign="middle" style="font-size:13px;line-height:1.6;color:#475569"><strong>Řešíte prodej nemovitosti?</strong> Kontaktujte nás nezávazně na <a href="https://www.ttcoreal.cz" style="color:#0ea5b7;text-decoration:none;font-weight:600">www.ttcoreal.cz</a>. Máme nejnižší provize na trhu.</td></tr></table></td></tr></table>'
)

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
        if je_druzstevni(inzerat, nazev):
            continue
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

        if je_druzstevni(inzerat, nazev):
            continue

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
            "hash_id": str(hash_id),
            "nazev": nazev,
            "lokalita": lokalita,
            "cast_prahy": nazev_prahy,
            "dispozice": disp,
            "cena": cena,
            "plocha": plocha,
            "cena_za_m2": cena_za_m2,
            "prumer_prahy": prumer,
            "odchylka": odchylka,
            "odkaz": odkaz,
        })

print(f"Nalezeno {len(byty)} nových bytů.\n")

byty.sort(key=lambda x: x["odchylka"])

# Do reportu patri VSECHNY zelene byty, tj. aspon 10 % pod prumerem sve casti
# Prahy — stejna hranice, jakou pouziva barva karty nize. Pocet se tedy lisi
# den ode dne podle nabidky trhu, zadny pevny limit.
ZELENA_HRANICE = -10
pod_cenou = [b for b in byty if b["odchylka"] < ZELENA_HRANICE]

# Posledni sito: u bytu, ktere se chystame poslat, overit vlastnictvi primo
# v detailu inzeratu. Az sem se dostane hrstka bytu, takze je to par requestu.
if pod_cenou:
    print()
    print("Overuji vlastnictvi u %d bytu pred odeslanim..." % len(pod_cenou))
    proverene = []
    for b in pod_cenou:
        if osobni_vlastnictvi(b["hash_id"]):
            proverene.append(b)
        else:
            print("  VYRAZEN (neni osobni vlastnictvi): %s" % b["nazev"])
        time.sleep(0.3)
    if len(proverene) != len(pod_cenou):
        print("  Proslo %d z %d." % (len(proverene), len(pod_cenou)))
    pod_cenou = proverene

if not pod_cenou:
    print(f"Z {len(byty)} novych bytu neni zadny pod cenou – email se neposílá.")
else:
    print(f"Pod cenou: {len(pod_cenou)} z {len(byty)} novych bytu.")

    datum = datetime.now().strftime("%d. %m. %Y")
    karty = ""

    for b in pod_cenou:
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
        f'<p style="margin:8px 0 0;color:#ede9fe;font-size:14px">{datum} · {len(pod_cenou)} bytů pod cenou · seřazeno podle odchylky od průměru</p>'
        "</div>"
        f"{karty}"
        f"{UPGRADE_BLOK}"
        f"{PATICKA_PARTNERI}"
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
        print(f"Report odeslán! {len(pod_cenou)} bytů pod cenou.")
    except Exception as e:
        print("CHYBA: " + str(e))

    broadcast_secret = os.environ.get("BROADCAST_SECRET")
    if broadcast_secret:
        try:
            resp = requests.post(
                "https://podhodnocenebyty.cz/api/broadcast",
                json={"subject": f"🏠 Nové byty Praha · {datum}", "html": html_report},
                headers={"Authorization": f"Bearer {broadcast_secret}"},
                timeout=30,
            )
            resp.raise_for_status()
            print("Broadcast odeslán odběratelům.")
        except Exception as e:
            print("CHYBA broadcast: " + str(e))

        try:
            resp = requests.post(
                "https://podhodnocenebyty.cz/api/ingest-byty",
                json={"date": datetime.now().strftime("%Y-%m-%d"), "byty": pod_cenou},
                headers={"Authorization": f"Bearer {broadcast_secret}"},
                timeout=30,
            )
            resp.raise_for_status()
            print(f"Surová data ({len(pod_cenou)} bytů) uložena pro personalizaci.")
        except Exception as e:
            print("CHYBA ingest: " + str(e))