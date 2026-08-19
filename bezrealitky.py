"""Načítání pražských bytů na prodej z Bezrealitky.cz.

Používá to byty_praha_pro.py jako druhý zdroj vedle sreality. Smysl je dvojí:
soukromí prodávající, kteří na sreality většinou nejsou, a nezávislost na
jediném zdroji dat.

Jak se k datům jde: robots.txt zakazuje vyhledávání, ale detaily inzerátů
povoluje a sám nabízí sitemapu. Bereme tedy seznam ze sitemapy a čteme
jednotlivé detaily. Adresy jsou samopopisné
(`…-nabidka-prodej-bytu-<ulice>-praha`), takže se dá filtrovat na pražské byty
na prodej ještě před stažením čehokoliv.

Data jsou ve stránce jako hotový datový blok, nemusí se parsovat HTML.
"""
import json
import re
import time

import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
}
SITEMAP = "https://www.bezrealitky.cz/sitemap/sitemap.xml"
PRODLEVA = 0.4  # slusnost k cizimu serveru

DISPOZICE = {
    "DISP_1_KK": "1+kk", "DISP_1_1": "1+1", "DISP_2_KK": "2+kk", "DISP_2_1": "2+1",
    "DISP_3_KK": "3+kk", "DISP_3_1": "3+1", "DISP_4_KK": "4+kk", "DISP_4_1": "4+1",
    "DISP_5_KK": "5+kk", "DISP_5_1": "5+1", "DISP_6_KK": "6+kk", "DISP_6_1": "6+1",
    "DISP_OSTATNI": "atypický",
}


def _sitemapa_odkazy():
    """Pražské byty na prodej ze sitemapy, filtrované podle tvaru adresy."""
    r = requests.get(SITEMAP, headers=HEADERS, timeout=30)
    r.raise_for_status()
    podmapy = [m for m in re.findall(r"<loc>(.*?)</loc>", r.text) if "detail" in m]

    odkazy = []
    for m in podmapy:
        r = requests.get(m, headers=HEADERS, timeout=60)
        r.raise_for_status()
        odkazy += re.findall(r"<loc>(.*?)</loc>", r.text)
        time.sleep(PRODLEVA)

    return [
        u for u in odkazy
        if "nabidka-prodej-bytu" in u and re.search(r"-praha(-|$)|praha-\d", u)
    ]


def _detail(url):
    """Datový blok inzerátu. Vrací None, když se nepodaří přečíst."""
    try:
        html = requests.get(url, headers=HEADERS, timeout=30).text
        m = re.search(r"__NEXT_DATA__[^>]*>(.*?)</script>", html, re.S)
        if not m:
            return None
        return json.loads(m.group(1))["props"]["pageProps"]["origAdvert"]
    except Exception:
        return None


def _ctvrt(adv):
    for r in adv.get("regionTree") or []:
        if r.get("subType") == "CITY_DISTRICT":
            return (r.get("name") or "").replace("Praha-", "").strip()
    return None


def nacti_prazske_byty(urci_mestskou_cast, log=print):
    """Vrátí pražské byty na prodej v osobním vlastnictví.

    `urci_mestskou_cast(ctvrt, lat, lon)` musí vrátit název městské části
    ("Praha 3"), nebo None. Řeší se tím, že název čtvrti sám o sobě nestačí —
    Vinohrady leží ve třech městských částech.
    """
    odkazy = _sitemapa_odkazy()
    log(f"  ze sitemapy: {len(odkazy)} pražských bytů na prodej")

    byty = []
    preskoceno = {"neaktivní": 0, "není osobní vlastnictví": 0, "podzemní podlaží": 0,
                  "chybí cena/plocha": 0, "neurčená část": 0, "nečitelné": 0}

    for url in odkazy:
        adv = _detail(url)
        time.sleep(PRODLEVA)
        if not adv:
            preskoceno["nečitelné"] += 1
            continue
        if not adv.get("active") or adv.get("estateType") != "BYT" or adv.get("offerType") != "PRODEJ":
            preskoceno["neaktivní"] += 1
            continue
        # Bezrealitky drží vlastnictví přímo v datech, takže na rozdíl od
        # sreality se kvůli němu nemusí stahovat nic navíc.
        if adv.get("ownership") != "OSOBNI":
            preskoceno["není osobní vlastnictví"] += 1
            continue

        # Nic pod přízemím. Bezrealitky číslují od 1, kde 1 je nejnižší
        # obytné podlaží — v žádném ze zkoumaných inzerátů se nižší hodnota
        # neobjevila. Filtr je tu jako pojistka pro případ, že se objeví;
        # neznámé patro se nezahazuje, aby se kvůli nevyplněnému údaji
        # neztrácely inzeráty.
        patro = adv.get("etage")
        if patro is not None and patro < 1:
            preskoceno["podzemní podlaží"] += 1
            continue

        cena, plocha = adv.get("price"), adv.get("surface")
        if not cena or not plocha or cena < 100000:
            preskoceno["chybí cena/plocha"] += 1
            continue

        ctvrt = _ctvrt(adv)
        gps = adv.get("gps") or {}
        cast = urci_mestskou_cast(ctvrt, gps.get("lat"), gps.get("lng"))
        if not cast or not ctvrt:
            preskoceno["neurčená část"] += 1
            continue

        byty.append({
            # Prefix, aby se identifikátory nesrazily se sreality.
            "hash_id": f"bz:{adv['id']}",
            "zdroj": "bezrealitky",
            "nazev": f"Prodej bytu {DISPOZICE.get(adv.get('disposition'), '')} {plocha} m²".replace("  ", " "),
            "lokalita": adv.get("address") or "Praha",
            "cast_prahy": cast,
            "ctvrt": ctvrt,
            "dispozice": DISPOZICE.get(adv.get("disposition"), ""),
            "cena": cena,
            "plocha": plocha,
            "cena_za_m2": cena / plocha,
            "patro": patro,
            "gps_lat": gps.get("lat"),
            "gps_lon": gps.get("lng"),
            "odkaz": url,
        })

    log(f"  použitelných: {len(byty)}")
    for duvod, kolik in preskoceno.items():
        if kolik:
            log(f"    vynecháno — {duvod}: {kolik}")
    return byty
