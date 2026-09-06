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
# Odkaz do aplikace.
#
# Sestimistny kod je vazany na prohlizec, ktery si o nej rekl — kdo si ho
# vyzada na pocitaci, v telefonu uz ho nepouzije. Report se ale cte hlavne
# na telefonu, takze odkaz vede na prihlaseni s uz vyplnenym e-mailem:
# clovek jen klepne na "poslat kod", ten mu prijde do teze schranky a je
# uvnitr — bez opisovani adresy na male klavesnici.
#
# Podepsany odkaz, ktery prihlasuje rovnou (jako v placenem reportu), tu
# udelat nejde: bezplatny report jde jednim HTML pres Resend broadcast,
# takze pro kazdeho prijemce zvlast se podepsat necim neda. {{{EMAIL}}}
# doplnuje Resend az pri odesilani.
APLIKACE_BLOK = (
    '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
    'style="border-collapse:collapse;margin:20px 0 0"><tr><td align="center" '
    'style="font-family:Arial,Helvetica,sans-serif">'
    '<a href="https://podhodnocenebyty.cz/app/prihlaseni?email={{{EMAIL}}}" '
    'style="background:#0f172a;color:#ffffff;text-decoration:none;padding:12px 26px;'
    'border-radius:999px;font-weight:700;font-size:14px;display:inline-block;'
    'font-family:Arial,Helvetica,sans-serif">Otevřít v aplikaci</a>'
    '<p style="margin:8px 0 0;color:#9ca3af;font-size:11px;'
    'font-family:Arial,Helvetica,sans-serif">Všechny byty přehledně a s historií — '
    'e-mail už máte předvyplněný.</p>'
    '</td></tr></table>'
)

def prehled_vcera():
    """Kolik bytu vcera nasla placena verze a kde.

    Slouzi k jedine vete v upgrade bloku, ale je to nejsilnejsi veta v celem
    e-mailu: rika konkretni cislo z vcerejska misto obecneho slibu. Za 31. 8.
    az 5. 9. pripadlo na Prahu 32 nalezu a mimo ni 57 — bezplatny odberatel
    o vetsine ani nevi.

    Kdyz se to nepovede nacist, vrati None a blok se vykresli bez teto vety.
    Report kvuli marketingove vsuvce padat nesmi.
    """
    try:
        r = requests.get(
            f"{WEB}/api/prehled-vcera",
            headers={"Authorization": f"Bearer {os.environ['BROADCAST_SECRET']}"},
            timeout=20,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print("Prehled vcerejska se nenacetl: " + str(e))
        return None


def projekt_banner():
    """Prouzek s nabidkou developerskeho projektu, ze ktereho bereme provizi.

    HTML se nesklada tady, ale stahuje se z webu (lib/projekt.ts). Duvod je
    prozaicky: pri vymene projektu se meni texty i cisla a duplikat v Pythonu
    by se driv nebo pozdeji rozesel s tim, co vidi platici clenove.

    Kdyz se nenacte nebo zadny projekt neni aktivni, vrati prazdny retezec —
    report kvuli nabidce padat nesmi.
    """
    try:
        r = requests.get(
            f"{WEB}/api/projekt-banner",
            headers={"Authorization": f"Bearer {os.environ['BROADCAST_SECRET']}"},
            timeout=20,
        )
        r.raise_for_status()
        return r.json().get("html") or ""
    except Exception as e:
        print("Banner projektu se nenacetl: " + str(e))
        return ""


# Blok pod nalezenymi byty, ktery zve k placenemu clenstvi.
#
# Je to funkce, ne konstanta, protoze nejsilnejsi veta pracuje s poctem bytu
# v prave odesilanem reportu: cteнar vidi konkretni cislo, ne obecny slib.
#
# Argument je zamerne cas, ne kvalita analyzy. Bezplatna verze ukazuje byty
# o 24 hodin pozdeji a u bytu pod cenou rozhoduje, kdo zavola prvni — to je
# rozdil, ktery je citit hned, na rozdil od presnosti prumeru.
_VYHODY = [
    ("Byty ihned", "bez denního zpoždění. Tenhle jeden den je u bytu pod cenou celý rozdíl."),
    ("Přesnější srovnání", "byt ve Střešovicích se poměřuje jen se Střešovicemi, ne s celou Prahou 6."),
    ("Proč je byt levný", "stav, panelový dům, patro. A když důvod nenajdeme, řekneme vám to."),
    ("Šest měst", "Praha, Brno, Ostrava, Plzeň, Liberec, Olomouc — vyberete si, která chcete."),
    ("Upozornění na zlevnění", "u bytů, které jsme vám poslali, plus nedělní souhrn."),
]

_RADKY = "".join(
    '<tr>'
    '<td style="padding:0 0 9px;width:22px;color:#0d9488;font-weight:700;vertical-align:top">✓</td>'
    '<td style="padding:0 0 9px;font-size:13px;line-height:1.55;color:#475569">'
    f'<strong style="color:#0f172a">{nadpis}</strong> — {text}</td>'
    '</tr>'
    for nadpis, text in _VYHODY
)


def _tvar(n, jeden, dva, pet):
    return jeden if n == 1 else (dva if n < 5 else pet)


# Sesty pad nazvu mest. Seznam je uzavreny (mesta v MESTA), takze staci tabulka
# — sklonovat cesky programove by bylo delsi i krehci nez sest radku.
_KDE = {
    "Praha": "Praze",
    "Brno": "Brně",
    "Ostrava": "Ostravě",
    "Plzeň": "Plzni",
    "Liberec": "Liberci",
    "Olomouc": "Olomouci",
}


def pobidka_nahore(vcera):
    """Uzky pruh hned pod hlavickou.

    Cely upgrade blok je az pod vypisem bytu a vetsina lidi tam nedoscrolluje.
    Tenhle pruh nese dve veci, ktere rozhoduji nejvic — cas a presnost vyberu —
    a odkaz. Je nahore, takze ho uvidi i ten, kdo e-mail jen prolitne.

    Vedle vypisu to dat nejde: e-mailovi klienti sloupce na mobilu skladaji
    pod sebe, takze "bocni panel" by stejne skoncil az za byty.

    Nemluvi o mestech zamerne. Lokality resi blok dole; tady jde o to, ze
    placena verze hleda jinak a driv, coz plati i pro cistě prazskeho ctenare.
    """
    if not vcera or not vcera.get("celkem"):
        return ""

    n = vcera["celkem"]
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="border-collapse:collapse;margin:0 0 16px"><tr>'
        '<td style="background:#0f172a;border-left:4px solid #22d3ee;border-radius:10px;'
        'padding:15px 18px;font-family:Arial,sans-serif">'
        f'<p style="margin:0 0 5px;font-size:15px;font-weight:700;color:#ffffff;line-height:1.45">'
        f'Platící členové včera dostali {n} {_tvar(n, "byt", "byty", "bytů")}. '
        f'O 24 hodin dřív než vy.</p>'
        '<p style="margin:0;font-size:13px;line-height:1.55;color:#94a3b8">'
        'A vybrané přesnější analýzou — podle konkrétní čtvrti, ne podle celé městské části. '
        '<a href="https://podhodnocenebyty.cz/clenstvi?utm_source=email&utm_medium=email&utm_campaign=pruh-nahore" '
        'style="color:#22d3ee;font-weight:700;text-decoration:none">Odemknout od 149 Kč &rarr;</a></p>'
        '</td></tr></table>'
    )


def upgrade_blok(pocet, vcera=None):
    kolik = (
        "Tenhle byt viděli" if pocet == 1
        else f"Těchto {pocet} bytů viděli"
    )

    # Nejsilnejsi cast: kolik bytu ctenar vcera vubec nedostal, protoze byly
    # mimo Prahu. Ukazuje se jen kdyz takove byty opravdu byly.
    navic = ""
    if vcera and vcera.get("mimoPrahu"):
        n = vcera["mimoPrahu"]
        jmena = [_KDE.get(m["nazev"], m["nazev"]) for m in (vcera.get("mesta") or [])][:3]
        kde = ", ".join(jmena[:-1]) + " a " + jmena[-1] if len(jmena) > 1 else (jmena[0] if jmena else "jiných městech")
        sleva = vcera.get("nejlepsiOdchylka")
        sleva_text = (
            f" {'Byl' if n == 1 else 'Nejlevnější z nich byl'} "
            f"<strong>{abs(sleva):.0f} % pod cenou</strong>."
            if isinstance(sleva, (int, float)) else ""
        )
        navic = (
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            'style="border-collapse:collapse;margin:0 0 16px"><tr>'
            '<td style="background:#fff7ed;border:1px solid #fed7aa;border-radius:10px;padding:14px 16px">'
            f'<p style="margin:0;font-size:13px;line-height:1.6;color:#7c2d12">'
            f'A k tomu <strong>{n} {_tvar(n, "byt", "byty", "bytů")} '
            f'{_tvar(n, "byl", "byly", "bylo")} v {kde}</strong> — '
            f'{_tvar(n, "ten se", "ty se", "ty se")} do bezplatné verze '
            f'{_tvar(n, "nedostane", "nedostanou", "nedostanou")} vůbec.{sleva_text}</p>'
            '</td></tr></table>'
        )

    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:24px 0 0;border-collapse:collapse"><tr>'
        '<td style="background:linear-gradient(135deg,#0d9488,#22d3ee);border-radius:14px;padding:2px">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse"><tr>'
        '<td style="background:#ffffff;border-radius:12px;padding:22px;font-family:Arial,sans-serif">'
        '<p style="margin:0 0 6px;font-size:11px;font-weight:800;color:#0d9488;letter-spacing:1px;text-transform:uppercase">O den napřed</p>'
        f'<p style="margin:0 0 12px;font-size:17px;font-weight:700;color:#0f172a;line-height:1.4">{kolik} platící členové už včera.</p>'
        '<p style="margin:0 0 16px;font-size:13px;line-height:1.6;color:#475569">'
        'Bezplatná verze ukazuje byty s denním zpožděním a to jen v Praze.</p>'
        + navic +
        '<table role="presentation" cellpadding="0" cellspacing="0" style="border-collapse:collapse;margin:0 0 18px">'
        + _RADKY +
        '</table>'
        '<a href="https://podhodnocenebyty.cz/clenstvi?utm_source=email&utm_medium=email&utm_campaign=denni-report" '
        'style="background:#0d9488;color:#ffffff;text-decoration:none;padding:13px 28px;border-radius:999px;'
        'font-weight:700;font-size:14px;display:inline-block">Chci byty ihned</a>'
        '<p style="margin:14px 0 0;font-size:11px;color:#94a3b8">'
        'Od 149 Kč měsíčně za jedno město, 249 Kč za všech šest — necelých 5 Kč denně. '
        'Zrušit můžete kdykoliv.</p>'
        '</td></tr></table></td></tr></table>'
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
# Bezplatna verze ukazuje byty se zpozdenim jednoho dne.
#
# Duvod je obchodni, ne technicky: bezplatna verze delala tutez praci jako
# placena, jen o neco hrubeji, takze nebyl duvod platit. U podhodnoceneho bytu
# je pritom hodnota v tom byt prvni — za den je po nem cast zajemcu.
# Zpozdeni tedy dela z placene verze jinou sluzbu, ne jen lepsi.
#
# Okno je proto 24 az 48 hodin zpetne. Overeno, ze sreality horni mez umi:
# vysledek presne odpovida rozdilu obou dotazu, bez prekryvu s poslednim dnem.
print("Stahuji byty za obdobi 24-48 hodin zpet (bezplatna verze ma denni zpozdeni)...")

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
        "watchdog_last_changed_from": (datetime.now() - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%S"),
        "watchdog_last_changed_to": (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S"),
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

    # Kolik bytu vcera nasla placena verze mimo Prahu. Pouziva se v upgrade
    # bloku; kdyz se nenacte, blok se vykresli bez teto vety.
    vcerejsek = prehled_vcera()
    nabidka = projekt_banner()

    html_report = (
        "<!DOCTYPE html><html>"
        '<head><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>'
        '<body style="margin:0;padding:0;background:#f8fafc;font-family:Arial,sans-serif">'
        '<div style="max-width:600px;margin:0 auto;padding:16px">'
        '<div style="background:linear-gradient(135deg,#7c3aed,#a78bfa);padding:28px;text-align:center;border-radius:12px;margin-bottom:16px">'
        f'<h1 style="margin:0;color:white;font-size:22px">🏠 Nové podhodnocené byty Praha</h1>'
        f'<p style="margin:8px 0 0;color:#ede9fe;font-size:14px">{datum} · {len(pod_cenou)} bytů pod cenou · seřazeno podle odchylky od průměru</p>'
        '<p style="margin:10px 0 0;color:#ede9fe;font-size:12px;opacity:.85">Bezplatná verze ukazuje byty s denním zpožděním — platící členové je dostali včera.</p>' 
        "</div>"
        f"{pobidka_nahore(vcerejsek)}"
        f"{karty}"
        f"{APLIKACE_BLOK}"
        f"{upgrade_blok(len(pod_cenou), vcerejsek)}"
        f"{nabidka}"
        f"{PATICKA_PARTNERI}"
        '<div style="text-align:center;padding:16px">'
        '<p style="margin:0;color:#9ca3af;font-size:11px">Odchylka = (cena/m² − průměr části Prahy) / průměr · Data ze Sreality</p>'
        "</div></div></body></html>"
    )

    zprava = MIMEMultipart("alternative")
    zprava["Subject"] = f"🏠 Nové byty Praha · {datum}"
    zprava["From"] = "realitybot@seznam.cz"
    zprava["To"] = "tomas.tuzar@seznam.cz"
    # {{{EMAIL}}} doplnuje az Resend pri rozesilce. Tahle kopie jde primo
    # pres SMTP, takze by se zastupny text ukazal tak, jak je — dosadime ho.
    zprava.attach(MIMEText(html_report.replace("{{{EMAIL}}}", "tomas.tuzar@seznam.cz"), "html"))

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