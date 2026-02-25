import ast

src = open('hivemind.py', encoding='utf-8').read()
changes = 0

# 1. FI→EN käyttäjän viesti (rivi ~482)
old1 = 'self._fi_en_result = self.translation_proxy.fi_to_en(message)'
new1 = 'self._fi_en_result = self.translation_proxy.fi_to_en(message, force_opus=True)'
if old1 in src:
    src = src.replace(old1, new1, 1)
    changes += 1
    print(f"  ✅ FI→EN chat: force_opus=True")
else:
    print(f"  ❌ FI→EN chat: ei löydy")

# 2. EN→FI master-vastaus (rivi ~568)
old2 = '''            if self._translation_used and self.translation_proxy:
                _en_fi = self.translation_proxy.en_to_fi(response)'''
new2 = '''            if self._translation_used and self.translation_proxy:
                _en_fi = self.translation_proxy.en_to_fi(response, force_opus=True)'''
if old2 in src:
    src = src.replace(old2, new2, 1)
    changes += 1
    print(f"  ✅ EN→FI master: force_opus=True")
else:
    print(f"  ❌ EN→FI master: ei löydy")

# 3. EN→FI delegoitu vastaus (rivi ~743)
old3 = '''        if getattr(self, '_translation_used', False) and self.translation_proxy:
            _en_fi = self.translation_proxy.en_to_fi(response)'''
new3 = '''        if getattr(self, '_translation_used', False) and self.translation_proxy:
            _en_fi = self.translation_proxy.en_to_fi(response, force_opus=True)'''
if old3 in src:
    src = src.replace(old3, new3, 1)
    changes += 1
    print(f"  ✅ EN→FI delegated: force_opus=True")
else:
    print(f"  ❌ EN→FI delegated: ei löydy")

print(f"\n  Muutoksia: {changes}/3")

if changes > 0:
    ast.parse(src)
    open('hivemind.py', 'w', encoding='utf-8').write(src)
    print("  ✅ Tallennettu, syntax OK")
else:
    print("  ⚠️  Ei muutoksia!")
