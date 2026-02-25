import re

src = open('hivemind.py', encoding='utf-8').read()

# Etsi rivi jossa "Spawner valmis"
for i, line in enumerate(src.splitlines()):
    if "Spawner valmis" in line:
        print(f"Rivi {i+1}: {repr(line)}")

# Lisaa YAML EN hook rivin jalkeen
pattern = r'(        print\("  . Spawner valmis"\))'
hook = r'''\1

        # YAML-sielujen FI-EN kaannos
        if self._use_en_prompts and self.translation_proxy:
            if hasattr(self.spawner, "yaml_bridge") and self.spawner.yaml_bridge:
                self.spawner.yaml_bridge.set_translation_proxy(
                    self.translation_proxy, "en")'''

new_src, count = re.subn(pattern, hook, src, count=1)

if count > 0:
    open('hivemind.py', 'w', encoding='utf-8').write(new_src)
    print("OK: YAML EN hook lisatty")
else:
    print("EI LOYDY - kokeile manuaalisesti")
