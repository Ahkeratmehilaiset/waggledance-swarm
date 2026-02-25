"""
Korjaa 2 ongelmaa:
1. Poista vanha test-DB (v1-embeddingt ilman prefixiä)
2. Säädä hallusinaatiokynnys (AND → painotettu yhdistelmä)

Aja: python fix_consciousness_retest.py
"""
import shutil, os

# ═══ 1. Poista vanha testi-DB ═══
db_path = "data/test_consciousness_v2"
if os.path.exists(db_path):
    shutil.rmtree(db_path)
    print(f"  ✅ Poistettu vanha DB: {db_path}")
else:
    print(f"  ⏭️  DB ei ollut olemassa: {db_path}")

# ═══ 2. Säädä hallusinaatiokynnys consciousness.py:ssä ═══
src = open("consciousness.py", encoding="utf-8").read()

# Vanha: AND-logiikka, liian tiukka
old_hall = '        is_suspicious = (similarity < 0.40 and overlap < 0.25)'

# Uusi: painotettu yhdistelmä — toimii paremmin
new_hall = '''        # Painotettu yhdistelmäscore
        combined = 0.6 * similarity + 0.4 * overlap
        is_suspicious = combined < 0.30'''

if old_hall in src:
    src = src.replace(old_hall, new_hall, 1)
    
    # Päivitä myös reason-teksti
    old_reason = '            reason = f"embed={similarity:.0%}, keyword={overlap:.0%}"'
    new_reason = '            reason = f"embed={similarity:.0%}, keyword={overlap:.0%}, combined={combined:.0%}"'
    src = src.replace(old_reason, new_reason, 1)
    
    open("consciousness.py", "w", encoding="utf-8").write(src)
    print("  ✅ Hallusinaatiokynnys korjattu (AND → combined)")
else:
    print("  ⏭️  Hallusinaatiokynnys jo korjattu")

print("\n  Aja nyt: python consciousness.py")
