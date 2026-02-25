import re

src = open('hivemind.py', encoding='utf-8').read()

# 1. Poista vanha hook (vaarin sijoitettu)
old_hook = """
        # YAML-sielujen FI-EN kaannos
        if self._use_en_prompts and self.translation_proxy:
            if hasattr(self.spawner, "yaml_bridge") and self.spawner.yaml_bridge:
                self.spawner.yaml_bridge.set_translation_proxy(
                    self.translation_proxy, "en")"""

if old_hook in src:
    src = src.replace(old_hook, "", 1)
    print("1. Vanha hook poistettu")
else:
    print("1. Vanhaa hookia ei loydy - ehka jo poistettu")

# 2. Lisaa hook Translation Proxyn jalkeen
# Etsitaan kohta jossa proxy on alustettu
target = '            self.translation_proxy = None\n'

# Etsi VIIMEINEN esiintyma (se on else-haaran jalkeen)
idx = src.rfind(target)
if idx > 0:
    insert_pos = idx + len(target)
    hook = """
        # YAML-sielujen FI-EN kaannos (Translation Proxyn jalkeen)
        if getattr(self, '_use_en_prompts', False) and self.translation_proxy:
            if hasattr(self, 'spawner') and self.spawner and hasattr(self.spawner, 'yaml_bridge'):
                self.spawner.yaml_bridge.set_translation_proxy(
                    self.translation_proxy, "en")
"""
    src = src[:insert_pos] + hook + src[insert_pos:]
    print("2. Uusi hook lisatty Translation Proxyn jalkeen")
else:
    print("2. EI LOYDY: translation_proxy = None")

import ast
ast.parse(src)
print("3. Syntax: OK")

open('hivemind.py', 'w', encoding='utf-8').write(src)
print("4. Tallennettu")
