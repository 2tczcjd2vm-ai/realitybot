import requests

headers = {"User-Agent": "Mozilla/5.0"}

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

print("Průměrné ceny bytů podle části Prahy:")
print("=" * 50)

for nazev, district_id in prahy.items():
    url = "https://www.sreality.cz/api/cs/v2/estates"
    params = {
        "category_main_cb": 1,
        "category_type_cb": 1,
        "locality_district_id": district_id,
        "per_page": 60,
    }
    
    response = requests.get(url, params=params, headers=headers)
    data = response.json()
    
    ceny_za_m2 = []
    for inzerat in data["_embedded"]["estates"]:
        cena = inzerat.get("price", 0)
        name = inzerat.get("name", "")
        # Vytáhni m² z názvu
        import re
        m = re.search(r"(\d+)\s*m²", name)
        if m and cena and cena > 100000:
            plocha = int(m.group(1))
            if plocha > 0:
                ceny_za_m2.append(cena / plocha)
    
    if ceny_za_m2:
        prumer = sum(ceny_za_m2) / len(ceny_za_m2)
        print(f"{nazev}: {prumer:,.0f} Kč/m² (z {len(ceny_za_m2)} inzerátů)")
    else:
        print(f"{nazev}: žádná data")