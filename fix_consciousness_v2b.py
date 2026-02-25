"""
Kaksi korjausta consciousness.py:hin:
1. TranslationResult.text purkaminen
2. Suomenkieliset matikkafraasit
"""
import re

src = open('consciousness.py', encoding='utf-8').read()
fixes = 0

# ═══ FIX 1: TranslationResult.text ═══

old1 = '''                result = self._proxy.fi_to_en(text, force_opus=True)
                if isinstance(result, tuple):
                    return result[0]
                return result'''
new1 = '''                result = self._proxy.fi_to_en(text, force_opus=True)
                if hasattr(result, 'text'):
                    return result.text
                if isinstance(result, tuple):
                    return result[0]
                return str(result)'''

if old1 in src:
    src = src.replace(old1, new1, 1)
    fixes += 1
    print("  OK [1a] fi_to_en TranslationResult fix")

old2 = '''                result = self._proxy.en_to_fi(text, force_opus=True)
                if isinstance(result, tuple):
                    return result[0]
                return result'''
new2 = '''                result = self._proxy.en_to_fi(text, force_opus=True)
                if hasattr(result, 'text'):
                    return result.text
                if isinstance(result, tuple):
                    return result[0]
                return str(result)'''

if old2 in src:
    src = src.replace(old2, new2, 1)
    fixes += 1
    print("  OK [1b] en_to_fi TranslationResult fix")

# ═══ FIX 2: Suomenkieliset matikkaoperaattorit ═══

# Lisää FI_MATH_REPLACEMENTS MATH_TRIGGERS:in jälkeen
if 'FI_MATH_REPLACEMENTS' not in src:
    old_triggers = '    ]\n    UNIT_CONVERSIONS'
    new_triggers = """    ]
    # Suomenkieliset operaattorit
    FI_MATH_REPLACEMENTS = [
        (r'neliojuuri\\s*(\\d+)', r'sqrt(\\1)'),
        (r'neliöjuuri\\s*(\\d+)', r'sqrt(\\1)'),
        (r'(\\d+)\\s*potenssiin\\s*(\\d+)', r'\\1**\\2'),
        (r'(\\d+)\\s*kertaa\\s*(\\d+)', r'\\1*\\2'),
        (r'(\\d+)\\s*jaettuna\\s*(\\d+)', r'\\1/\\2'),
        (r'(\\d+)\\s*plus\\s*(\\d+)', r'\\1+\\2'),
        (r'(\\d+)\\s*miinus\\s*(\\d+)', r'\\1-\\2'),
    ]
    UNIT_CONVERSIONS"""

    if old_triggers in src:
        src = src.replace(old_triggers, new_triggers, 1)
        fixes += 1
        print("  OK [2a] FI_MATH_REPLACEMENTS lisatty")
    else:
        print("  FAIL [2a] UNIT_CONVERSIONS markeria ei loydy")

    # Lisää FI-tarkistus is_math:iin
    old_ismath = '        for w in sorted(cls.MATH_TRIGGERS, key=len, reverse=True):'
    new_ismath = '''        # Suomenkielinen matikka?
        if hasattr(cls, 'FI_MATH_REPLACEMENTS'):
            for pattern, _ in cls.FI_MATH_REPLACEMENTS:
                if re.search(pattern, clean):
                    return True
        for w in sorted(cls.MATH_TRIGGERS, key=len, reverse=True):'''

    if old_ismath in src:
        src = src.replace(old_ismath, new_ismath, 1)
        fixes += 1
        print("  OK [2b] is_math FI-tunnistus")

    # Lisää FI-korvaukset solve:en (toinen esiintyma)
    solve_marker = '        clean = clean.strip().rstrip("?=")'
    idx1 = src.find(solve_marker)
    idx2 = src.find(solve_marker, idx1 + 1) if idx1 >= 0 else -1
    if idx2 > 0:
        replacement = solve_marker + '''
        # Suomenkieliset operaattorit
        if hasattr(cls, 'FI_MATH_REPLACEMENTS'):
            for pattern, repl in cls.FI_MATH_REPLACEMENTS:
                clean = re.sub(pattern, repl, clean)'''
        src = src[:idx2] + replacement + src[idx2 + len(solve_marker):]
        fixes += 1
        print("  OK [2c] solve FI-korvaukset")
else:
    print("  SKIP [2] FI math jo olemassa")

# ═══ TALLENNUS ═══
print(f"\n  Muutoksia: {fixes}")
if fixes > 0:
    open('consciousness.py', 'w', encoding='utf-8').write(src)
    print("  SAVED")
