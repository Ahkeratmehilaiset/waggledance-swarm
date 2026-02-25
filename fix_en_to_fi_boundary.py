import re

src = open('translation_proxy.py', encoding='utf-8').read()

# Bugi: substring-korvaus ilman word boundarya
old = '''            if en_term in result.lower():
                # Case-insensitive korvaus
                pattern = re.compile(re.escape(en_term), re.IGNORECASE)
                result = pattern.sub(fi_term, result)'''

# Fix: lisaa word boundary \b
new = r'''            if en_term in result.lower():
                # Case-insensitive korvaus KOKONAISIIN SANOIHIN
                pattern = re.compile(r'\b' + re.escape(en_term) + r'\b', re.IGNORECASE)
                if pattern.search(result):
                    result = pattern.sub(fi_term, result)
                else:
                    continue  # Oli vain substring, ei kokonainen sana'''

if old in src:
    src = src.replace(old, new, 1)
    import ast
    ast.parse(src)
    open('translation_proxy.py', 'w', encoding='utf-8').write(src)
    print("OK: Word boundary fix asennettu")
    print("  ENNEN: 'compound' -> 'compaallaeund' (substring match)")
    print("  JALKEEN: 'compound' -> 'compound' (ei muutu)")
else:
    print("EI LOYDY - tarkista translation_proxy.py")
