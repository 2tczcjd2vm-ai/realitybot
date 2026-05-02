import requests

url = "https://www.sreality.cz/api/cs/v2/estates"
params = {
    "category_main_cb": 1,
    "category_type_cb": 2,
    "locality_district_id": 25,
    "per_page": 60,
}

headers = {
    "User-Agent": "Mozilla/5.0"
}

response = requests.get(url, params=params, headers=headers)
data = response.json()

najmy = {}

for inzerat in data["_embedded"]["estates"]:
    nazev = inzerat.get("name", "")
    cena = inzerat.get("price", 0)
    
    if cena and cena < 30000:  # filtr nesmyslných cen
        print(f"{nazev} – {cena} Kč/měsíc")
        
        # rozlišení dispozice
        for disp in ["1+kk", "1+1", "2+kk", "2+1", "3+kk", "3+1"]:
            if disp in nazev:
                if disp not in najmy:
                    najmy[disp] = []
                najmy[disp].append(cena)

print("\n--- PRŮMĚRNÉ NÁJMY PODLE DISPOZICE ---")
for disp, ceny in sorted(najmy.items()):
    prumer = sum(ceny) / len(ceny)
    print(f"{disp}: {prumer:.0f} Kč/měsíc (z {len(ceny)} inzerátů)")