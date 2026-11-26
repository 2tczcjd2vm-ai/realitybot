# -*- coding: utf-8 -*-
"""Domy na sreality podle poptávky konkrétního klienta.

Zadání (listopad 2026):
    rodinný dům 120-180 m², ideálně 5+kk, pozemek od 600 m²,
    bez nutnosti velké rekonstrukce, Turnov / Liberec, do 14 mil. Kč

Dva režimy:
    bez přepínače  denní e-mail s tím, co od minule přibylo
    --vse          jednorázový sken celého trhu do HTML a CSV ve vystup/
    --nahled       místo odeslání uloží e-mail do souboru (na ladění vzhledu)

Jak se pozná, co je nové
------------------------
NE časovým oknem. Přesně na tom se 17. 11. 2026 našla chyba u chaty.py:
`watchdog_last_changed_from` se počítá z datetime.now(), což je na GitHub
Actions UTC, ale sreality to čtou jinak — okno bylo ve skutečnosti 26 hodin
a každý den se o dvě hodiny překrylo s předchozím. Tři chaty tak přišly
dvakrát.

Tady se proto nové pozná evidencí odeslaných inzerátů (/api/nove-inzeraty,
zdroj "poptavka-dum-turnov"). Je to tatáž tabulka, jakou používají
Bezrealitky i placený report, a je na zdroji nezávislá. Výhoda proti oknu:
nezáleží na tom, kdy běh proběhne ani jestli se jeden den vynechá.

Eviduje se až to, co opravdu odejde v mailu. Dům vyřazený kvůli stavu se
tedy přezkoumá znovu každý den — kdyby makléř stav opravil nebo dům zlevnil
pod strop, pořád se k nám dostane.

Proč to není jen jeden dotaz na API
-----------------------------------
Výpis ze sreality neumí ani dispozici, ani stav objektu — v `advert_name` je
jen "Prodej rodinného domu 120 m², pozemek 1179 m²". Obojí je až v detailu
inzerátu, takže skript nejdřív serverovými filtry zúží kraj na desítky
kandidátů a teprve u nich stahuje detail. V listopadu 2026 to bylo
1 003 domů v kraji -> 140 po filtrech -> 65 do 15 km od měst.

Číselníky ověřené proti API 2026-11-25 (meta_title / search_title odpovědi
říká, co filtr doopravdy znamená — nehádat je):
    category_main_cb = 2            domy
    category_sub_cb  = 37           rodinný dům (39 vila, 43 chalupa, 33 chata)
    locality_region_id = 5          Liberecký kraj
    locality_district_id = 22       okres Liberec, 34 okres Semily (Turnov)
    building_condition: 1 velmi dobrý · 2 dobrý · 3 špatný · 4 ve výstavbě
                        5 developerský projekt · 6 novostavba · 7 k demolici
                        8 před rekonstrukcí · 9 po rekonstrukci
    room_count_cb                   počet pokojů (u domů není dispozice 5+kk)

Souřadnice měst nehádat ani nevypisovat ručně — bere je našeptávač
/api/v1/localities/suggest, který vrací i okres a kraj.
"""

import argparse
import csv
import math
import os
import smtplib
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

HEADERS = {"User-Agent": "Mozilla/5.0"}
API = "https://www.sreality.cz/api/v1/estates/search"
DETAIL = "https://www.sreality.cz/api/v1/estates/{}"
SUGGEST = "https://www.sreality.cz/api/v1/localities/suggest"

WEB = os.environ.get("PB_API_BASE", "https://podhodnocenebyty.cz")
ZDROJ_EVIDENCE = "poptavka-dum-turnov"

# Adresy se nepisou do kodu — workflow je dodava z promennych prostredi.
ODESILATEL = os.environ.get("REPORT_FROM", "")
PRIJEMCE = os.environ.get("REPORT_TO", "")

# --- zadání klienta --------------------------------------------------------
MESTA = ["Turnov", "Liberec"]
KRAJ_ID = 5                 # Liberecký; obě města v něm leží
POLOMER_KM = 15             # kolem každého z měst
CENA_MAX = 14_000_000
PLOCHA_OD, PLOCHA_DO = 120, 180
POZEMEK_OD = 600
POKOJU_IDEALNE = 5          # "ideálně 5kk" — měkké kritérium, nevyřazuje

# Stavy, které znamenají "velká rekonstrukce nebo ještě nestojí".
# Klient chce "po rekonstrukci nebo novější", tedy tyhle ven.
STAVY_VEN = {
    3: "ve špatném stavu",
    4: "ve výstavbě",
    5: "developerský projekt",
    7: "k demolici",
    8: "před rekonstrukcí",
}

VYSTUP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vystup")


def fmt(n):
    return f"{int(n):,}".replace(",", " ") if n else "—"


def sklonuj(pocet, jedna, dve_ctyri, pet_vic):
    """1 nový dům / 3 nové domy / 7 nových domů."""
    if pocet == 1:
        return f"{pocet} {jedna}"
    if 2 <= pocet <= 4:
        return f"{pocet} {dve_ctyri}"
    return f"{pocet} {pet_vic}"


def km(a, b):
    """Vzdušná vzdálenost dvou bodů v km."""
    dla, dlo = math.radians(b[0] - a[0]), math.radians(b[1] - a[1])
    x = (math.sin(dla / 2) ** 2
         + math.cos(math.radians(a[0])) * math.cos(math.radians(b[0])) * math.sin(dlo / 2) ** 2)
    return 6371 * 2 * math.asin(math.sqrt(x))


def souradnice(nazev):
    """Souřadnice obce z našeptávače sreality. Vrací (lat, lon)."""
    r = requests.get(SUGGEST, params={"phrase": nazev}, headers=HEADERS, timeout=30)
    r.raise_for_status()
    for v in r.json().get("results", []):
        u = v.get("userData", {})
        if u.get("entityType") == "municipality" and u.get("municipality") == nazev:
            return (u["latitude"], u["longitude"])
    raise SystemExit(f"Našeptávač nezná obec {nazev!r} — zkontroluj název.")


def stahni_vypis():
    """Kandidáti z kraje. Serverové filtry udělají hrubou práci."""
    params = {
        "category_main_cb": 2,
        "category_sub_cb": 37,
        "category_type_cb": 1,
        "locality_country_id": 112,
        "locality_region_id": KRAJ_ID,
        "price_to": CENA_MAX,
        "usable_area_from": PLOCHA_OD,
        "usable_area_to": PLOCHA_DO,
        "estate_area_from": POZEMEK_OD,
        "limit": 500,
        "offset": 0,
        "lang": "cs",
    }
    ven = []
    while True:
        davka = requests.get(API, params=params, headers=HEADERS, timeout=60).json().get("results", [])
        ven.extend(davka)
        if len(davka) < 500:
            return ven
        params["offset"] += 500
        time.sleep(0.5)


def detail(hash_id):
    r = requests.get(DETAIL.format(hash_id), headers=HEADERS, timeout=30)
    r.raise_for_status()
    d = r.json()
    return d.get("result", d)


def odkaz(inzerat):
    lok = inzerat.get("locality", {})
    slug = "-".join(x for x in (lok.get("city_seo_name"), lok.get("citypart_seo_name"),
                                lok.get("street_seo_name")) if x) or "x"
    return f"https://www.sreality.cz/detail/prodej/dum/rodinny/{slug}/{inzerat['hash_id']}"


def evidence(hash_ids, seed=False):
    """Vrátí ty, co jsme ještě neposlali, a ZÁROVEŇ si je zapíše.

    POZOR na to zapsání: endpoint dotaz a zápis nedělí, takže se na něj nedá
    zeptat "nanečisto". Volá se proto až nad tím, co se opravdu chystáme
    poslat — ne nad všemi kandidáty, jinak by si zapsal i domy, které
    vypadly kvůli stavu, a ty by se po opravě inzerátu už nikdy neukázaly.

    seed=True jen zapíše a nevrátí nic.

    Při chybě vrátí všechny jako nové — radši poslat něco podruhé než
    kvůli výpadku evidence neposlat nic.
    """
    tajemstvi = os.environ.get("BROADCAST_SECRET")
    if not tajemstvi:
        print("BROADCAST_SECRET chybí — evidence se přeskakuje, hlásím všechno")
        return set(hash_ids)
    try:
        r = requests.post(
            f"{WEB}/api/nove-inzeraty",
            json={"zdroj": ZDROJ_EVIDENCE, "hash_ids": list(hash_ids), "seed": seed},
            headers={"Authorization": f"Bearer {tajemstvi}"},
            timeout=60,
        )
        r.raise_for_status()
        return set(r.json().get("nove", []))
    except Exception as e:
        print("CHYBA evidence: " + str(e) + " — pokračuji bez ní")
        return set(hash_ids)


def blizka_mesta(vypis, mesta_gps):
    """Jen to, co je do POLOMER_KM od některého z měst."""
    ven = []
    for i in vypis:
        lok = i.get("locality") or {}
        lat, lon = lok.get("gps_lat"), lok.get("gps_lon")
        if lat is None or lon is None:
            continue
        vzdalenosti = {m: km(g, (lat, lon)) for m, g in mesta_gps.items()}
        nejbliz = min(vzdalenosti, key=vzdalenosti.get)
        if vzdalenosti[nejbliz] <= POLOMER_KM:
            i["_mesto"] = nejbliz
            i["_km"] = vzdalenosti[nejbliz]
            ven.append(i)
    return ven


def posbirej(mesta_gps, verbose=True):
    vypis = stahni_vypis()
    if verbose:
        print(f"V kraji prošlo filtry {len(vypis)} domů.")

    blizko = blizka_mesta(vypis, mesta_gps)
    if verbose:
        print(f"Do {POLOMER_KM} km od {' nebo '.join(mesta_gps)}: {len(blizko)}")
        print("Stahuji detaily (dispozice a stav objektu ve výpisu nejsou)...")

    nalezy, vyrazene = [], []
    for poradi, i in enumerate(blizko, 1):
        try:
            d = detail(i["hash_id"])
        except Exception as e:
            print(f"  detail {i['hash_id']} nenačten ({e}), vynechávám")
            continue
        time.sleep(0.35)

        stav = (d.get("building_condition") or {}).get("value")
        stav_nazev = (d.get("building_condition") or {}).get("name") or "neuvedeno"
        pokoju = (d.get("room_count_cb") or {}).get("value")

        zaznam = {
            "hash_id": str(i["hash_id"]),
            "obec": (d.get("locality") or {}).get("city", ""),
            "cast": (d.get("locality") or {}).get("citypart", "") or "",
            "mesto": i["_mesto"],
            "km": round(i["_km"], 1),
            "cena": d.get("price_czk") or d.get("price") or 0,
            "plocha": d.get("usable_area"),
            "pozemek": d.get("estate_area"),
            "pokoju": pokoju,
            "stav": stav_nazev,
            "rok_kolaudace": d.get("acceptance_year"),
            "rok_rekonstrukce": d.get("reconstruction_year"),
            "garaz": bool(d.get("garage")),
            "prodejce": (d.get("premise") or {}).get("name") or "soukromá osoba",
            "odkaz": odkaz(i),
        }

        # Tvrdé vyřazení jen u stavu, který klient výslovně nechce.
        # Neuvedený stav se NEVYHAZUJE — makléři ho často nevyplní a tiše
        # zahozený dům by nikdo nedohledal.
        if stav in STAVY_VEN:
            zaznam["duvod"] = STAVY_VEN[stav]
            vyrazene.append(zaznam)
        else:
            nalezy.append(zaznam)

        if verbose and poradi % 10 == 0:
            print(f"  {poradi}/{len(blizko)}")

    # Nejdřív to, co sedí i dispozicí, pak podle vzdálenosti a ceny.
    nalezy.sort(key=lambda z: (0 if (z["pokoju"] or 0) >= POKOJU_IDEALNE else 1,
                               z["km"], z["cena"]))
    return nalezy, vyrazene


# Druhý pád pro větu "12 km od ...". Jen pro města ze zadání, žádné
# obecné skloňování — to by byla věda na jeden řádek textu.
OD_MESTA = {"Turnov": "od Turnova", "Liberec": "od Liberce"}


def karta(z):
    sedi = (z["pokoju"] or 0) >= POKOJU_IDEALNE
    barva = "#15803d" if sedi else "#b45309"
    # Část obce se vypisuje, jen když se liší od obce — u měst se jinak
    # opakuje ("Turnov · Turnov").
    kde = z["obec"] + (f' · {z["cast"]}' if z["cast"] and z["cast"] != z["obec"] else "")
    odkud = OD_MESTA.get(z["mesto"], f'od města {z["mesto"]}')
    radky = [
        f'📍 {kde} — {z["km"]} km {odkud}',
        f'📐 {fmt(z["plocha"])} m² dům · {fmt(z["pozemek"])} m² pozemek'
        + (f' · {sklonuj(int(z["pokoju"]), "pokoj", "pokoje", "pokojů")}'
           if z["pokoju"] else " · počet pokojů neuveden"),
        f'🔨 {z["stav"]}'
        + (f' · rekonstrukce {z["rok_rekonstrukce"]}' if z["rok_rekonstrukce"] else "")
        + (f' · kolaudace {z["rok_kolaudace"]}' if z["rok_kolaudace"] else ""),
        f'🏢 {z["prodejce"]}',
    ]
    return (
        f'<a href="{z["odkaz"]}" style="text-decoration:none;color:inherit" target="_blank">'
        f'<div style="background:white;border-radius:10px;padding:16px;margin-bottom:12px;'
        f'box-shadow:0 1px 4px rgba(0,0,0,.08);border-left:4px solid {barva}">'
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start">'
        f'<div style="flex:1;padding-right:12px">'
        + "".join(f'<div style="color:#6b7280;font-size:13px;margin-bottom:3px">{r}</div>'
                  for r in radky)
        + '</div>'
        f'<div style="background:{barva};color:white;padding:8px 12px;border-radius:8px;'
        f'font-weight:800;font-size:16px;white-space:nowrap;text-align:center">'
        f'{fmt(z["cena"])} Kč</div>'
        f'</div></div></a>'
    )


def html(nalezy, vyrazene, denni=False):
    sedi = sum(1 for z in nalezy if (z["pokoju"] or 0) >= POKOJU_IDEALNE)
    nadpis = "Nové domy Turnov / Liberec" if denni else "Domy Turnov / Liberec"
    podnadpis = (sklonuj(len(nalezy), "nový dům", "nové domy", "nových domů") if denni
                 else sklonuj(len(nalezy), "nabídka", "nabídky", "nabídek"))
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1.0'></head>"
        "<body style='margin:0;padding:0;background:#f8fafc;font-family:Arial,sans-serif'>"
        "<div style='max-width:640px;margin:0 auto;padding:16px'>"
        "<div style='background:linear-gradient(135deg,#0f766e,#2dd4bf);padding:26px;"
        "text-align:center;border-radius:12px;margin-bottom:16px'>"
        f"<h1 style='margin:0;color:white;font-size:21px'>🏠 {nadpis}</h1>"
        f"<p style='margin:8px 0 0;color:#ccfbf1;font-size:14px'>"
        f"{datetime.now().strftime('%d. %m. %Y')} · {podnadpis}, "
        f"z toho {sedi} s 5 a více pokoji</p></div>"
        f"<div style='margin:0 0 10px;padding:0 4px;font-size:13px;color:#6b7280'>"
        f"{fmt(PLOCHA_OD)}–{fmt(PLOCHA_DO)} m², pozemek od {fmt(POZEMEK_OD)} m², "
        f"do {fmt(CENA_MAX)} Kč, do {POLOMER_KM} km od Turnova nebo Liberce. "
        f"Zeleně ty s 5 a více pokoji.</div>"
        + "".join(karta(z) for z in nalezy)
        + (f"<div style='margin:18px 0 8px;padding:0 4px;font-size:13px;color:#9ca3af'>"
           f"Vyřazeno kvůli stavu objektu: {len(vyrazene)}</div>"
           + "".join(f"<div style='font-size:12px;color:#9ca3af;padding:2px 4px'>"
                     f"{z['obec']} · {fmt(z['cena'])} Kč · {z['duvod']} · "
                     f"<a href='{z['odkaz']}' style='color:#9ca3af'>odkaz</a></div>"
                     for z in vyrazene) if vyrazene else "")
        + "</div></body></html>"
    )


def posli_mail(predmet, telo):
    if not ODESILATEL or not PRIJEMCE:
        print("REPORT_FROM nebo REPORT_TO chybí — e-mail se neodesílá.")
        return False
    zprava = MIMEMultipart("alternative")
    zprava["Subject"] = predmet
    zprava["From"] = ODESILATEL
    zprava["To"] = PRIJEMCE
    zprava.attach(MIMEText(telo, "html"))
    try:
        with smtplib.SMTP_SSL("smtp.email.cz", 465) as server:
            server.login(ODESILATEL, os.environ.get("SMTP_PASS", ""))
            server.sendmail(ODESILATEL, PRIJEMCE, zprava.as_string())
        return True
    except Exception as e:
        print("CHYBA odeslání: " + str(e))
        return False


def main():
    global POLOMER_KM, CENA_MAX

    ap = argparse.ArgumentParser(description="Domy podle poptávky klienta (Turnov/Liberec).")
    ap.add_argument("--vse", action="store_true",
                    help="jednorázový sken celého trhu do HTML a CSV, nic se neodesílá")
    ap.add_argument("--nahled", action="store_true",
                    help="e-mail uložit do souboru místo odeslání")
    ap.add_argument("--zalozit-evidenci", action="store_true",
                    help="jen zaevidovat současnou nabídku, nic neposlat (první běh)")
    ap.add_argument("--polomer", type=int, help=f"km od měst (výchozí {POLOMER_KM})")
    ap.add_argument("--cena-max", type=int, help=f"strop v Kč (výchozí {CENA_MAX})")
    a = ap.parse_args()

    if a.polomer:
        POLOMER_KM = a.polomer
    if a.cena_max:
        CENA_MAX = a.cena_max

    mesta_gps = {m: souradnice(m) for m in MESTA}
    for m, g in mesta_gps.items():
        print(f"{m}: {g[0]:.4f}, {g[1]:.4f}")

    # --- jednorázový sken celého trhu -------------------------------------
    if a.vse:
        nalezy, vyrazene = posbirej(mesta_gps)
        os.makedirs(VYSTUP, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d")
        cesta_html = os.path.join(VYSTUP, f"domy-turnov-liberec-{stamp}.html")
        cesta_csv = os.path.join(VYSTUP, f"domy-turnov-liberec-{stamp}.csv")
        with open(cesta_html, "w", encoding="utf-8") as f:
            f.write(html(nalezy, vyrazene))
        sloupce = ["obec", "cast", "mesto", "km", "cena", "plocha", "pozemek", "pokoju",
                   "stav", "rok_kolaudace", "rok_rekonstrukce", "garaz", "prodejce", "odkaz"]
        with open(cesta_csv, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=sloupce, delimiter=";", extrasaction="ignore")
            w.writeheader()
            w.writerows(nalezy)
        sedi = sum(1 for z in nalezy if (z["pokoju"] or 0) >= POKOJU_IDEALNE)
        print(f"\nNalezeno {len(nalezy)} domů, z toho {sedi} s 5 a více pokoji.")
        print(f"Vyřazeno kvůli stavu objektu: {len(vyrazene)}")
        print(f"  {cesta_html}\n  {cesta_csv}")
        return

    # --- založení evidence: současná nabídka se jen zapíše ----------------
    if a.zalozit_evidenci:
        nalezy, vyrazene = posbirej(mesta_gps)
        evidence([z["hash_id"] for z in nalezy], seed=True)
        print(f"\nZaevidováno {len(nalezy)} domů jako již poslané. "
              f"Od příště se hlásí jen nové přírůstky.")
        return

    # --- denní režim -------------------------------------------------------
    # Posuzuje se celá nabídka každý den, ne jen přírůstek za 24 hodin.
    # Je to pár desítek dotazů navíc, ale odpadá tím celá třída chyb kolem
    # časového okna — a dům, který dnes vypadl kvůli stavu, dostane zítra
    # další šanci, kdyby makléř inzerát opravil.
    nalezy, vyrazene = posbirej(mesta_gps)
    if not nalezy:
        print("Nabídce neodpovídá nic, e-mail se neposílá.")
        return

    nove_id = evidence([z["hash_id"] for z in nalezy])
    nove = [z for z in nalezy if z["hash_id"] in nove_id]
    print(f"\nOdpovídá {len(nalezy)} domů, z toho nových od minule: {len(nove)}")

    if not nove:
        print("Nic nového, e-mail se neposílá.")
        return

    sedi = sum(1 for z in nove if (z["pokoju"] or 0) >= POKOJU_IDEALNE)
    souhrn = sklonuj(len(nove), "nový dům", "nové domy", "nových domů")
    # Vyřazené do denního mailu nepatří — zajímají jen při skenu celého trhu.
    telo = html(nove, [], denni=True)

    if a.nahled:
        cesta = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "nahled-poptavka-dum.html")
        with open(cesta, "w", encoding="utf-8") as f:
            f.write(telo)
        print(f"Náhled uložen do {cesta} — e-mail se neposílá.")
        print("POZOR: evidence si je i tak zapsala, takže v ostrém běhu už nové nebudou.")
        return

    if posli_mail(f"🏠 {souhrn} · Turnov / Liberec", telo):
        print(f"Odesláno. {souhrn}, z toho {sedi} s 5 a více pokoji.")
    else:
        # Evidence si je zapsala při dotazu, takže zítra už nové nebudou.
        # Vypsat je, ať se dají dohledat.
        print("Mail neodešel, tyhle domy se už znovu nenahlásí:")
        for z in nove:
            print(f"  {z['obec']} · {fmt(z['cena'])} Kč · {z['odkaz']}")


if __name__ == "__main__":
    main()
