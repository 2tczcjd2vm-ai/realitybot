"""Zjisteni poctu bytovych jednotek z textu inzeratu.

Sreality pocet jednotek nikde nedrzi - neni to filtr ani pole v datech,
takze jedina cesta je text inzeratu. Cislo je proto vzdycky odhad a do
reportu se vedle nej dava i veta, ze ktere se cetlo, aby slo overit okem.
"""
import re

CISLA_SLOVY = {
    "jeden": 1, "jedna": 1, "jednu": 1, "jedním": 1, "jednim": 1,
    "dva": 2, "dvě": 2, "dve": 2, "dvou": 2, "dvěma": 2, "dvema": 2,
    "tři": 3, "tri": 3, "třemi": 3, "tremi": 3, "třech": 3, "trech": 3,
    "čtyři": 4, "ctyri": 4, "čtyřmi": 4, "ctyrmi": 4, "čtyřech": 4, "čtyř": 4,
    "pět": 5, "pet": 5, "pěti": 5, "peti": 5,
    "šest": 6, "sest": 6, "šesti": 6, "sesti": 6,
    "sedm": 7, "sedmi": 7,
    "osm": 8, "osmi": 8,
    "devět": 9, "devet": 9, "devíti": 9, "deviti": 9,
    "deset": 10, "desíti": 10, "deseti": 10,
    "jedenáct": 11, "jedenácti": 11,
    "dvanáct": 12, "dvanácti": 12,
}

# Podstatne jmeno oznacujici jednotku. "bytovy dum" ani "bytova vystavba"
# sem nepatri - "V lokalite je celkem 6 bytovych domu" nejsou ctyri jednotky.
JEDNOTKA = (
    r"(?:bytov\w*\s+(?:jednotk\w*|část\w*|cast\w*)"
    r"|nájemn\w*\s+jednotk\w*|najemn\w*\s+jednotk\w*"
    r"|jednotk\w*"
    r"|byt(?:y|ů|u|ech|ům|em)?)\b"
)
NENI_JEDNOTKA = re.compile(r"\b(?:dom\w*|dům|výstavb\w*|vystavb\w*|fond\w*)", re.IGNORECASE)

# Mezi cislem a podstatnym jmenem byva pridavne jmeno, casto i dve:
# "3 radne vymezene bytove jednotky". Vzorec, ktery cekal cislo tesne
# u slova, takove vety propoustel - stejna chyba jako kdysi u stoplistu.
VYPLN = r"(?:\s+[a-záčďéěíňóřšťúůýžA-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ]+){0,2}?"
CISLO = r"(\d{1,2}|[a-záčďéěíňóřšťúůýž]+)"

# Cislo ve vete o zameru nebo o minulosti neni pocet stavajicich jednotek.
# Hlida se jen NEDOKONAVY tvar: "vybudovat"/"vybudovani" je zamer, ale
# "nove vybudovanym bytovym jednotkam" uz je hotovy stav.
ZAMER = re.compile(
    r"\blze\b|možnost|moznost|možno\b|mozno\b|"
    r"vybudovat|vybudování|vybudovani|přestavb|prestavb|přestavět|prestavet|"
    r"potenciál|potencial|vznikn|"
    r"stavební\s+povolení|stavebni\s+povoleni|pro\s+výstavbu|pro\s+vystavbu|"
    r"projekt\w*\s+(?:počítá|pocita|na\b)|projektov\w*\s+dokumentac|"
    r"plánuje|planuje|záměr|zamer|studie|"
    r"v\s+minulosti|původně|puvodne|dříve\s+zde|drive\s+zde|"
    r"by\s+(?:bylo|šlo|slo|mohl)",
    re.IGNORECASE,
)

# Veta se casto sklada z casti, kde jedna popisuje stav a druha zamer:
# "V soucasne dobe se nachazi 16 jednotek, pricemz je potencial vybudovat 15 dalsich."
# Proto se posuzuje kazda cast zvlast, ne cela veta.
CASTI = re.compile(r"[,;:–—]|\bpřičemž\b|\bpricemz\b|\bpřípadně\b|\bpripadne\b|\bzatímco\b|\bnebo\b")


def _cislo(token):
    token = token.lower()
    if token.isdigit():
        return int(token)
    return CISLA_SLOVY.get(token)


def _vety(text):
    return re.split(r"(?<=[.!?])\s+|\n+", text)


def pocet_jednotek(text):
    """Vraci (pocet, veta_ze_ktere_se_cetlo) nebo (None, None).

    Bere nejvyssi verohodny udaj - inzerat casto popisuje nejdriv jedno patro
    a az pak cely dum.
    """
    if not text:
        return None, None

    kandidati = []
    for veta in _vety(re.sub(r"[ \t]+", " ", text)):
        for cast in CASTI.split(veta):
            if ZAMER.search(cast):
                continue

            for m in re.finditer(CISLO + VYPLN + r"\s+" + JEDNOTKA, cast, re.IGNORECASE):
                n = _cislo(m.group(1))
                if not n or not (1 <= n <= 40):
                    continue
                if NENI_JEDNOTKA.match(cast[m.end(): m.end() + 14].strip()):
                    continue
                kandidati.append((n, veta.strip()))

            # "3x byt 2+1, 2x byt 1+1" - takove vycty se scitaji
            nx = [int(x) for x in re.findall(r"(\d{1,2})\s*x\s*(?=" + JEDNOTKA + ")", cast, re.IGNORECASE)]
            if nx and 1 <= sum(nx) <= 40:
                kandidati.append((sum(nx), veta.strip()))

    if not kandidati:
        return None, None
    n, veta = max(kandidati, key=lambda x: x[0])
    return n, re.sub(r"\s+", " ", veta)[:200]
