"""Bot pro PLACENOU verzi — cena se poměřuje čtvrtí, ne celou městskou částí.

Běží vedle byty_praha.py, ne místo něj. Bezplatná verze zůstává na starém
botovi beze změny: průměr za Prahu 1–10, denní e-mail, /api/broadcast,
/api/ingest-byty. Tenhle skript se téhle roury nedotýká.

Rozdíly oproti bezplatné verzi:
  1. plochu bere z price_czk_m2, ne regexem z názvu inzerátu
  2. stahuje celou nabídku přes stránkování, ne prvních 60 inzerátů
  3. ceny ukládá do Supabase a průměr počítá z klouzavého okna 90 dní
  4. porovnává byt s jeho čtvrtí (Bubeneč), ne s celou Prahou 6

Nálezy ukládá na /api/ingest-pro, odkud je aplikace ukazuje platícím členům.
Rozesílka e-mailem platícím zákazníkům přijde později.
"""
import os
import re
import time
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta

import bezrealitky

headers = {"User-Agent": "Mozilla/5.0"}

API = "https://www.sreality.cz/api/v1/estates/search"
WEB = os.environ.get("PB_API_BASE", "https://podhodnocenebyty.cz")
KOMU = os.environ.get("PB_PRO_EMAIL", "tomas.tuzar@seznam.cz")

# Strop pro byty, ktere se ukazuji uzivateli (cilovka kupuje pro sebe).
# POZOR: pro vypocet prumeru se tenhle strop nepouziva, viz nize.
CENA_MAX = 8_000_000

# Sreality zvladnou limit 500 na jeden dotaz, cela Praha je pak ~12 requestu.
LIMIT = 500
MAX_STRAN = 5

ZELENA_HRANICE = -10

# Spodni mez duveryhodnosti. Byt vyrazne pod cenou ctvrti neni nalez, ale
# varovny signal — 19. 8. 2026 se objevil inzerat za 58 tis./m2 v Libni, kde
# je obvykla cena 166 tis. Mel strojove generovany popis ("Zrna vytahu"),
# rozporne udaje (v datech 3+1, v textu 4+1) a byt v suterenu.
#
# Skutecne vyhodne koupe se drzi zhruba do -35 %. Co je pod timhle prahem,
# byva podvod, prodej podilu, nebo chyba v inzeratu. Vypisuje se do logu,
# aby se nic neztracelo potichu.
PODEZRELE_LEVNE = -45

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


def osobni_vlastnictvi(hash_id):
    """Ověří v detailu inzerátu, že jde o osobní vlastnictví.

    Filtr `ownership=1` v hledání ani slovo „družstevní“ v názvu nestačí:
    19. 8. 2026 se do výběru dostal družstevní byt v Čimicích, který měl
    v názvu jen „Prodej bytu 3+1 72 m²“, prázdné štítky a v seznamu byl
    vedený jako osobní vlastnictví — na družstvo se přišlo až z popisu.
    Detail inzerátu drží pole ownership (1 = osobní, 2 = družstevní), a to
    je jediný spolehlivý zdroj.

    Kontroluje se jen hrstka bytů, které se chystáme poslat, takže je to
    pár requestů denně. Při pochybnostech vrací False — radši byt vynechat
    než poslat družstevní.
    """
    try:
        d = requests.get(
            f"https://www.sreality.cz/api/v1/estates/{hash_id}",
            headers=headers, timeout=30,
        ).json().get("result", {})
    except Exception as e:
        print(f"  VAROVANI: detail {hash_id} se nepodařilo načíst ({e}), byt vynechán")
        return False

    vlastnictvi = (d.get("ownership") or {}).get("value")
    if vlastnictvi != 1:
        return False

    # Druhá síť: makléři občas nechají štítek špatně, ale v popisu to přiznají.
    popis = (d.get("advert_description") or "").lower()
    if "družstev" in popis or "druzstev" in popis:
        return False

    return True


def cena_inzeratu(inzerat):
    return inzerat.get("price_czk") or inzerat.get("price") or 0


def cena_za_m2(inzerat):
    """Cena za m². Sreality ji posílají hotovou v price_czk_m2.

    Bezplatný bot luští plochu regexem z názvu inzerátu — inzeráty, které si
    název napsaly po svém (nebo ho nemají vůbec), se tím tiše zahazují.
    Regex tu zůstává jen jako záchranná brzda, kdyby API pole vynechalo.
    """
    hotova = inzerat.get("price_czk_m2")
    if hotova:
        return float(hotova)

    cena = cena_inzeratu(inzerat)
    m = re.search(r"(\d+)\s*m²", inzerat.get("advert_name", "") or "")
    if m and cena:
        plocha = int(m.group(1))
        if plocha > 0:
            return cena / plocha
    return None


def ctvrt(inzerat):
    lok = inzerat.get("locality", {})
    return (lok.get("citypart") or "").strip() or (lok.get("district") or "").strip()


def stahni(district_id, **navic):
    """Postupně projde stránky výsledků a vrátí všechny inzeráty."""
    vysledky = []
    offset = 0
    for _ in range(MAX_STRAN):
        params = {
            "category_main_cb": 1,
            "category_type_cb": 1,
            "locality_district_id": district_id,
            "locality_country_id": 112,
            "locality_region_id": 10,
            "limit": LIMIT,
            "offset": offset,
            "lang": "cs",
            "ownership": 1,
            "no_auction": 1,
        }
        params.update(navic)
        data = requests.get(API, params=params, headers=headers, timeout=60).json()
        davka = data.get("results", [])
        vysledky.extend(davka)
        if len(davka) < LIMIT:
            break
        offset += LIMIT
        time.sleep(0.5)
    return vysledky


# ---------------------------------------------------------------------------
# 1. Stažení celé nabídky
#
# Vzorky pro průměr se berou z CELÉ nabídky, bez cenového stropu. Strop 8 mil.
# má smysl u toho, co uživateli ukážeme, ne u toho, čím měříme trh: v Praze 1
# jím projde 15 inzerátů z 203, v Bubenči by uřízl horní půlku nabídky
# a levné byty by pak vypadaly dráž, než jsou.
# ---------------------------------------------------------------------------
print("Stahuji nabídku bytů v Praze...")

vzorky = []
# Aktuální ceny celé nabídky — proti nim se porovnávají dřív poslané byty.
nabidka_cen = []
for nazev_prahy, district_id in prahy.items():
    inzeraty = stahni(district_id)
    pocet = 0

    for inzerat in inzeraty:
        nazev = inzerat.get("advert_name", "") or ""
        if je_druzstevni(inzerat, nazev):
            continue

        cena = cena_inzeratu(inzerat)
        m2 = cena_za_m2(inzerat)
        hash_id = inzerat.get("hash_id")
        if not m2 or not cena or cena < 100000 or not hash_id:
            continue

        lok = inzerat.get("locality", {})
        nabidka_cen.append({"hash_id": str(hash_id), "cena": cena})
        vzorky.append({
            "hash_id": str(hash_id),
            "citypart": ctvrt(inzerat),
            "citypart_seo": lok.get("citypart_seo_name") or "",
            "district": nazev_prahy,
            "cena_m2": round(m2),
            "cena": cena,
            # Souradnice slouzi k urceni mestske casti u inzeratu z jinych
            # portalu, ktere znaji jen nazev ctvrti. Viz pb_district_podle_gps.
            "gps_lat": lok.get("gps_lat"),
            "gps_lon": lok.get("gps_lon"),
        })
        pocet += 1

    print(f"  {nazev_prahy}: {pocet} použitelných z {len(inzeraty)} inzerátů")

print(f"\nVzorků na uložení: {len(vzorky)}")

# ---------------------------------------------------------------------------
# 2. Uložení vzorků a průměry za čtvrť z klouzavého okna
# ---------------------------------------------------------------------------
broadcast_secret = os.environ.get("BROADCAST_SECRET")
if not broadcast_secret:
    raise SystemExit("Chybí BROADCAST_SECRET, bez něj se vzorky nemají kam uložit.")

resp = requests.post(
    f"{WEB}/api/ceny",
    json={"vzorky": vzorky},
    headers={"Authorization": f"Bearer {broadcast_secret}"},
    timeout=120,
)
resp.raise_for_status()
data = resp.json()

# Klic je dvojice (mestska cast, ctvrt), ne jen nazev ctvrti. Katastralni
# uzemi se v Praze casto deli mezi dve mestske casti -- Bubenec lezi v Praze 6
# i v Praze 7, Liben dokonce ve trech. Pri klicovani jen podle nazvu jeden
# radek prepsal druhy a byt z Bubence v Praze 6 se meril cislem za Prahu 7.
prumery_ctvrti, prumery_casti = {}, {}
for r in data.get("prumery", []):
    if r["uroven"] == "ctvrt":
        prumery_ctvrti[(r["district"], r["klic"])] = r
    else:
        prumery_casti[r["klic"]] = r

podlozene = [k for k, v in prumery_ctvrti.items() if v["zdroj"] == "ctvrt"]
print(f"Uloženo {data.get('ulozeno')} vzorků, okno {data.get('okno_dni')} dní.")
print(f"Čtvrtí s vlastním průměrem: {len(podlozene)} z {len(prumery_ctvrti)} "
      f"(zbytek zatím počítá průměrem celé městské části)")


def reference(nazev_ctvrti, nazev_prahy):
    """Referenční cena za m² pro danou čtvrť. Vrací (cena, zdroj, počet vzorků)."""
    zaznam = prumery_ctvrti.get((nazev_prahy, nazev_ctvrti)) or prumery_casti.get(nazev_prahy)
    if not zaznam or not zaznam.get("median"):
        return None, None, None
    # Medián, ne průměr: na malém vzorku jeden penthouse posune průměr
    # o jednotky procent a levné byty pak vypadají levněji, než jsou.
    return float(zaznam["median"]), zaznam["zdroj"], zaznam["pocet"]


# ---------------------------------------------------------------------------
# 3. Nové byty za posledních 24 hodin, poměřené svou čtvrtí
# ---------------------------------------------------------------------------
print("\nStahuji nové byty za posledních 24 hodin...")

byty = []
for nazev_prahy, district_id in prahy.items():
    inzeraty = stahni(
        district_id,
        sort="-date",
        price_to=CENA_MAX,
        watchdog_last_changed_from=(datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S"),
    )

    for inzerat in inzeraty:
        nazev = inzerat.get("advert_name", "") or ""
        if je_druzstevni(inzerat, nazev):
            continue

        cena = cena_inzeratu(inzerat)
        m2 = cena_za_m2(inzerat)
        if not m2 or not cena or cena < 100000:
            continue

        nazev_ctvrti = ctvrt(inzerat)
        prumer, zdroj, pocet_vzorku = reference(nazev_ctvrti, nazev_prahy)
        if not prumer:
            continue

        lok = inzerat.get("locality", {})
        hash_id = inzerat.get("hash_id")
        disp = inzerat.get("category_sub_cb", {}).get("name", "")
        disp_url = disp.replace("+", "%2B") if disp else ""
        odkaz = (
            f"https://www.sreality.cz/detail/prodej/byt/{disp_url}/"
            f"{lok.get('city_seo_name', '')}-{lok.get('district_seo_name', '')}-"
            f"{lok.get('street_seo_name', '')}/{hash_id}"
        ) if hash_id else "#"

        byty.append({
            "hash_id": str(hash_id),
            "nazev": nazev,
            "lokalita": lok.get("city", ""),
            "cast_prahy": nazev_prahy,
            "ctvrt": nazev_ctvrti,
            "dispozice": disp,
            "cena": cena,
            # Plocha uz neni zdrojem vypoctu, dopocitava se zpetne pro zobrazeni.
            "plocha": round(cena / m2),
            "cena_za_m2": m2,
            "prumer_ctvrti": prumer,
            "odchylka": ((m2 - prumer) / prumer) * 100,
            "zdroj_prumeru": zdroj,
            "pocet_vzorku": pocet_vzorku,
            "odkaz": odkaz,
        })

# ---------------------------------------------------------------------------
# 3b. Druhý zdroj: Bezrealitky
#
# Soukromí prodávající, kteří na sreality většinou nejsou. Měří se proti
# stejným mediánům za čtvrť, jen inzeráty přitékají odjinud. Do cenové báze
# se zatím nezapočítávají — báze zůstává postavená na sreality, aby se
# nemíchaly dvě různé populace prodávajících.
#
# Když Bezrealitky z jakéhokoliv důvodu selžou, bot pokračuje jen se sreality.
# ---------------------------------------------------------------------------
def urci_mestskou_cast(nazev_ctvrti, lat, lon):
    """Městská část pro čtvrť z cizího portálu."""
    if not nazev_ctvrti:
        return None
    moznosti = {d for (d, c) in prumery_ctvrti if c == nazev_ctvrti}
    if len(moznosti) == 1:
        return moznosti.pop()
    # Název čtvrti nestačí (Vinohrady leží ve třech částech) — rozhodnou souřadnice.
    if lat is None or lon is None:
        return None
    try:
        r = requests.post(
            f"{WEB}/api/mestska-cast",
            json={"lat": lat, "lon": lon},
            headers={"Authorization": f"Bearer {broadcast_secret}"},
            timeout=30,
        )
        r.raise_for_status()
        return r.json().get("district")
    except Exception:
        return None


print()
print("Stahuji Bezrealitky...")
try:
    z_bezrealitek = bezrealitky.nacti_prazske_byty(urci_mestskou_cast)

    # Bezrealitky neumi "co pribylo za 24 hodin" — ze sitemapy pada vzdycky
    # cela nabidka. Bez tehle evidence by se tytez byty hlasily kazdy den
    # znovu; pri prvnim behu jich napadalo 91.
    #
    # PB_SEED_BEZREALITKY=1 pri uplne prvnim behu: stavajici nabidka se jen
    # zaeviduje a nic se neohlasi.
    seed = os.environ.get("PB_SEED_BEZREALITKY") == "1"
    r = requests.post(
        f"{WEB}/api/nove-inzeraty",
        json={"zdroj": "bezrealitky",
              "hash_ids": [b["hash_id"] for b in z_bezrealitek],
              "seed": seed},
        headers={"Authorization": f"Bearer {broadcast_secret}"},
        timeout=60,
    )
    r.raise_for_status()
    nove = set(r.json().get("nove", []))
    print(f"  nových od minule: {len(nove)} z {len(z_bezrealitek)}"
          + (" (zakládací běh, nic se nehlásí)" if seed else ""))
    z_bezrealitek = [b for b in z_bezrealitek if b["hash_id"] in nove]

    for b in z_bezrealitek:
        prumer, zdroj, pocet_vzorku = reference(b["ctvrt"], b["cast_prahy"])
        if not prumer:
            continue
        b.update({
            "prumer_ctvrti": prumer,
            "odchylka": ((b["cena_za_m2"] - prumer) / prumer) * 100,
            "zdroj_prumeru": zdroj,
            "pocet_vzorku": pocet_vzorku,
        })
        byty.append(b)
except Exception as e:
    print("CHYBA Bezrealitky: " + str(e) + " — pokračuji jen se sreality")

print(f"Nalezeno {len(byty)} nových bytů.\n")

byty.sort(key=lambda x: x["odchylka"])
pod_cenou = [b for b in byty if b["odchylka"] < ZELENA_HRANICE]

# Podezřele levné ven. Radši přijít o jeden skutečný trhák než poslat podvod.
podezrele = [b for b in pod_cenou if b["odchylka"] < PODEZRELE_LEVNE]
if podezrele:
    print()
    print(f"Vyřazeno jako podezřele levné (pod {PODEZRELE_LEVNE} %):")
    for b in podezrele:
        print(f"  {b['odchylka']:+6.1f}%  {b['ctvrt']} · {b['cena']/1e6:.2f} mil · {b['odkaz']}")
    pod_cenou = [b for b in pod_cenou if b["odchylka"] >= PODEZRELE_LEVNE]

# Poslední síto: u bytů, které se chystáme poslat, ověřit vlastnictví přímo
# v detailu inzerátu. Až sem se dostane hrstka bytů, takže je to pár requestů.
if pod_cenou:
    print()
    print(f"Ověřuji vlastnictví u {len(pod_cenou)} bytů před odesláním...")
    proverene = []
    for b in pod_cenou:
        if b.get("zdroj") == "bezrealitky" or osobni_vlastnictvi(b["hash_id"]):
            proverene.append(b)
        else:
            print(f"  VYŘAZEN (není osobní vlastnictví): {b['nazev']} · {b['ctvrt']}")
        time.sleep(0.3)
    if len(proverene) != len(pod_cenou):
        print(f"  Prošlo {len(proverene)} z {len(pod_cenou)}.")
    pod_cenou = proverene

# ---------------------------------------------------------------------------
# 4. Změny ceny u bytů, které jsme už někdy poslali
#
# Aktuální ceny máme z kroku 1, takže se kvůli tomu nestahuje nic navíc.
# Endpoint zároveň zařadí dnešní výběr mezi sledované, a to v tomhle pořadí:
# obráceně by se dnešním bytům rovnou zapsala aktuální cena a změna by zanikla.
# ---------------------------------------------------------------------------
zmeny_cen = []
try:
    resp = requests.post(
        f"{WEB}/api/zmeny-cen",
        json={"aktualni": nabidka_cen, "pridat": pod_cenou},
        headers={"Authorization": f"Bearer {broadcast_secret}"},
        timeout=120,
    )
    resp.raise_for_status()
    data_zmen = resp.json()
    zmeny_cen = data_zmen.get("zmeny", [])
    print(f"Sledovaných bytů zařazeno/aktualizováno: {data_zmen.get('sledovano')}")
    print(f"Zlevněných bytů dnes: {len(zmeny_cen)}")
except Exception as e:
    print("CHYBA zmeny-cen: " + str(e))

if not pod_cenou and not zmeny_cen:
    raise SystemExit(
        f"Z {len(byty)} nových bytů není žádný pod cenou v dané čtvrti "
        f"a nikdo ze sledovaných nezlevnil – e-mail se neposílá."
    )

print(f"Pod cenou své čtvrti: {len(pod_cenou)} z {len(byty)} nových bytů.")

datum = datetime.now().strftime("%d. %m. %Y")
karty = ""

for b in pod_cenou:
    odchylka = b["odchylka"]
    barva = "#22c55e" if odchylka < -10 else "#f59e0b" if odchylka < 0 else "#ef4444"

    cena_fmt = f"{b['cena']:,.0f} Kč".replace(",", " ")
    cena_m2_fmt = f"{b['cena_za_m2']:,.0f} Kč/m²".replace(",", " ")
    prumer_fmt = f"{b['prumer_ctvrti']:,.0f} Kč/m²".replace(",", " ")

    # Kdyz ctvrt nema dost vzorku, rekneme to rovnou — cislo je hrubsi.
    zaklad = (
        f"{b['ctvrt']} ({b['pocet_vzorku']} bytů)"
        if b["zdroj_prumeru"] == "ctvrt"
        else f"{b['cast_prahy']} — {b['ctvrt']} zatím nemá dost dat"
    )

    karty += (
        f'<a href="{b["odkaz"]}" style="text-decoration:none;color:inherit;" target="_blank">'
        f'<div style="background:white;border-radius:10px;padding:16px;margin-bottom:12px;box-shadow:0 1px 4px rgba(0,0,0,0.08);border-left:4px solid {barva}">'
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start">'
        f'<div style="flex:1;padding-right:12px">'
        f'<div style="font-weight:700;font-size:15px;color:#1d4ed8;margin-bottom:6px">{b["nazev"]}</div>'
        f'<div style="color:#6b7280;font-size:12px;margin-bottom:4px">📍 {b["ctvrt"]} · {b["cast_prahy"]}</div>'
        f'<div style="color:#6b7280;font-size:13px">💰 Cena: {cena_fmt}</div>'
        f'<div style="color:#6b7280;font-size:13px">📐 Cena/m²: {cena_m2_fmt}</div>'
        f'<div style="color:#6b7280;font-size:13px">📊 Obvyklá cena {zaklad}: {prumer_fmt}</div>'
        f'</div>'
        f'<div style="background:{barva};color:white;padding:8px 12px;border-radius:8px;font-weight:800;font-size:18px;white-space:nowrap;min-width:70px;text-align:center">'
        f'{odchylka:+.1f}%</div></div></div></a>'
    )

# --- sekce Změny ceny ------------------------------------------------------
karty_zmen = ""
# Endpoint vraci vyhradne zlevnene byty; zdrazeni nikoho nezajima.
for z in zmeny_cen:
    stara = float(z["cena_stara"])
    nova = float(z["cena_nova"])
    sleva = stara - nova
    procent = sleva / stara * 100

    barva = "#22c55e"
    stara_fmt = f"{stara:,.0f} Kč".replace(",", " ")
    nova_fmt = f"{nova:,.0f} Kč".replace(",", " ")
    sleva_fmt = f"{sleva:,.0f} Kč".replace(",", " ")
    misto = " · ".join(x for x in (z.get("ctvrt"), z.get("cast_prahy")) if x)

    karty_zmen += (
        f'<a href="{z["odkaz"]}" style="text-decoration:none;color:inherit;" target="_blank">'
        f'<div style="background:white;border-radius:10px;padding:16px;margin-bottom:12px;box-shadow:0 1px 4px rgba(0,0,0,0.08);border-left:4px solid {barva}">'
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start">'
        f'<div style="flex:1;padding-right:12px">'
        f'<div style="font-weight:700;font-size:15px;color:#1d4ed8;margin-bottom:6px">{z["nazev"]}</div>'
        f'<div style="color:#6b7280;font-size:12px;margin-bottom:4px">📍 {misto}</div>'
        f'<div style="color:#6b7280;font-size:13px">'
        f'<span style="text-decoration:line-through">{stara_fmt}</span> → '
        f'<span style="color:{barva};font-weight:700">{nova_fmt}</span></div>'
        f'<div style="color:#6b7280;font-size:12px">Zlevnil o {sleva_fmt}</div>'
        f'</div>'
        f'<div style="background:{barva};color:white;padding:8px 12px;border-radius:8px;font-weight:800;font-size:16px;white-space:nowrap;min-width:70px;text-align:center">'
        f'▼ {procent:.1f}%</div></div></div></a>'
    )

sekce_zmen = (
    (
        '<div style="margin-top:28px">'
        '<h2 style="color:#334155;font-size:17px;margin:0 0 4px">💸 Změny ceny</h2>'
        f'<p style="color:#94a3b8;font-size:12px;margin:0 0 14px">'
        f'{len(zmeny_cen)} {"byt zlevnil" if len(zmeny_cen) == 1 else "bytů zlevnilo"} od doby, '
        f'co jsme vám je poslali</p>'
        f'{karty_zmen}</div>'
    )
    if zmeny_cen else ""
)

html_report = (
    "<!DOCTYPE html><html>"
    '<head><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>'
    '<body style="margin:0;padding:0;background:#f8fafc;font-family:Arial,sans-serif">'
    '<div style="max-width:600px;margin:0 auto;padding:16px">'
    '<div style="background:linear-gradient(135deg,#0d9488,#2dd4bf);padding:28px;text-align:center;border-radius:12px;margin-bottom:16px">'
    '<h1 style="margin:0;color:white;font-size:22px">🏠 Podhodnocené byty · přesná analýza</h1>'
    f'<p style="margin:8px 0 0;color:#ccfbf1;font-size:14px">{datum} · {len(pod_cenou)} bytů pod cenou v dané čtvrti'
    f'{f" · {len(zmeny_cen)} zlevnilo" if zmeny_cen else ""}</p>'
    "</div>"
    f"{karty}"
    f"{sekce_zmen}"
    f"{PATICKA_PARTNERI}"
    '<div style="text-align:center;padding:16px">'
    '<p style="margin:0;color:#9ca3af;font-size:11px">Odchylka = (cena/m² − obvyklá cena ve čtvrti) / obvyklá cena · '
    'Obvyklá cena je medián z nabídky za posledních 90 dní · Data ze Sreality</p>'
    "</div></div></body></html>"
)

zprava = MIMEMultipart("alternative")
zprava["Subject"] = f"🏠 Byty podle čtvrtí · {datum}"
zprava["From"] = "realitybot@seznam.cz"
zprava["To"] = KOMU
zprava.attach(MIMEText(html_report, "html"))

# Ulozeni pro aplikaci. Zamerne to NENI /api/ingest-byty, ktery patri
# bezplatne verzi — kdyby do nej sypaly oba boti, jeden by druhemu prepsal
# denni data.
try:
    resp = requests.post(
        f"{WEB}/api/ingest-pro",
        json={"date": datetime.now().strftime("%Y-%m-%d"), "byty": pod_cenou},
        headers={"Authorization": f"Bearer {broadcast_secret}"},
        timeout=30,
    )
    resp.raise_for_status()
    print(f"Uloženo pro aplikaci: {resp.json().get('ulozeno')} bytů.")
except Exception as e:
    print("CHYBA ingest-pro: " + str(e))

try:
    with smtplib.SMTP_SSL("smtp.email.cz", 465) as server:
        server.login("realitybot@seznam.cz", os.environ.get("SMTP_PASS", "Necum123"))
        server.sendmail("realitybot@seznam.cz", KOMU, zprava.as_string())
    print(f"Report odeslán na {KOMU}. {len(pod_cenou)} bytů pod cenou.")
except Exception as e:
    print("CHYBA: " + str(e))
