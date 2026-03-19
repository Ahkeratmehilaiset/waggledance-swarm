"""Math solver — safe expression evaluation with Finnish support.

Extracted from memory_engine.py (v1.17.0).
"""

import math
import re


class MathSolver:
    SAFE_NAMES = {
        "sqrt": math.sqrt, "abs": abs, "round": round,
        "sin": math.sin, "cos": math.cos, "tan": math.tan,
        "log": math.log, "log10": math.log10, "log2": math.log2,
        "pi": math.pi, "e": math.e,
        "pow": pow, "min": min, "max": max,
        "ceil": math.ceil, "floor": math.floor,
    }
    MATH_TRIGGERS = [
        "calculate", "laske", "paljonko on", "paljonko",
        "compute", "what is", "mikä on", "kuinka paljon",
        "montako", "how much", "eval",
    ]
    # Suomenkieliset operaattorit
    FI_MATH_REPLACEMENTS = [
        (r'neliojuuri\s*(\d+)', r'sqrt(\1)'),
        (r'neliöjuuri\s*(\d+)', r'sqrt(\1)'),
        (r'(\d+)\s*potenssiin\s*(\d+)', r'\1**\2'),
        (r'(\d+)\s*kertaa\s*(\d+)', r'\1*\2'),
        (r'(\d+)\s*jaettuna\s*(\d+)', r'\1/\2'),
        (r'(\d+)\s*plus\s*(\d+)', r'\1+\2'),
        (r'(\d+)\s*miinus\s*(\d+)', r'\1-\2'),
    ]
    UNIT_CONVERSIONS = {
        r'(\d+\.?\d*)\s*°?[cC]\s*(fahrenheit|fahrenheitiksi|to\s*f)':
            lambda m: f"{float(m.group(1)) * 9/5 + 32:.1f}°F",
        r'(\d+\.?\d*)\s*°?[fF]\s*(celsius|celsiukseksi|to\s*c)':
            lambda m: f"{(float(m.group(1)) - 32) * 5/9:.1f}°C",
        r'(\d+\.?\d*)\s*kg\s*(lbs?|paunoiksi|to\s*lbs?)':
            lambda m: f"{float(m.group(1)) * 2.20462:.1f} lbs",
    }

    @classmethod
    def is_math(cls, text):
        clean = text.strip().lower()
        for pattern in cls.UNIT_CONVERSIONS:
            if re.search(pattern, clean):
                return True
        # Suomenkielinen matikka?
        if hasattr(cls, 'FI_MATH_REPLACEMENTS'):
            for pattern, _ in cls.FI_MATH_REPLACEMENTS:
                if re.search(pattern, clean):
                    return True
        for w in sorted(cls.MATH_TRIGGERS, key=len, reverse=True):
            clean = clean.replace(w, "")
        clean = clean.strip().rstrip("?=")
        if not clean or len(clean) < 2:
            return False
        has_digit = bool(re.search(r'\d', clean))
        has_operator = bool(re.search(r'[+\-*/^%×÷()]', clean))
        has_func = any(fn in clean for fn in
                       ["sqrt", "sin", "cos", "log", "pow", "abs",
                        "squared", "cubed"])
        return (has_digit and has_operator) or (has_digit and has_func)

    # Natural-language math patterns → computed results
    NL_MATH_PATTERNS = [
        # "15% of 300" → 45
        (r'(\d+\.?\d*)\s*%\s*(?:of|kertaa)\s*(\d+\.?\d*)',
         lambda m: f"{float(m.group(1))*float(m.group(2))/100:.6g}"),
        # "15% sadasta" → 15 (sadasta = of 100 in Finnish)
        (r'(\d+\.?\d*)\s*%\s*sadasta',
         lambda m: f"{float(m.group(1)):.6g}"),
        # "12 squared" → 144
        (r'(\d+\.?\d*)\s*squared', lambda m: str(float(m.group(1)) ** 2)),
        # "5 cubed" → 125
        (r'(\d+\.?\d*)\s*cubed', lambda m: str(float(m.group(1)) ** 3)),
    ]

    @classmethod
    def solve(cls, text):
        clean = text.strip().lower()
        for pattern, converter in cls.UNIT_CONVERSIONS.items():
            m = re.search(pattern, clean)
            if m:
                return converter(m)
        # Natural-language math (percentage, squared, cubed)
        for pattern, converter in cls.NL_MATH_PATTERNS:
            m = re.search(pattern, clean)
            if m:
                result = converter(m)
                if float(result) == int(float(result)):
                    return str(int(float(result)))
                return result
        for w in sorted(cls.MATH_TRIGGERS, key=len, reverse=True):
            clean = clean.replace(w, "")
        clean = clean.strip().rstrip("?=")
        # Suomenkieliset operaattorit
        if hasattr(cls, 'FI_MATH_REPLACEMENTS'):
            for pattern, repl in cls.FI_MATH_REPLACEMENTS:
                clean = re.sub(pattern, repl, clean)
        clean = clean.replace("^", "**").replace("×", "*")
        clean = clean.replace("÷", "/").replace(",", ".")
        clean = re.sub(r'\s*(kg|g|ml|l|€|eur|kpl|pcs)\s*$', '', clean)
        try:
            result = eval(clean, {"__builtins__": {}}, cls.SAFE_NAMES)
            if isinstance(result, float):
                if result == int(result):
                    return str(int(result))
                return f"{result:.6g}"
            return str(result)
        except Exception:
            return None
