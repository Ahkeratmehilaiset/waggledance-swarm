import ast

src = open('translation_proxy.py', encoding='utf-8').read()

# FI-EN: lisaa device inputteihin
old1 = '''            inputs = tok(text, return_tensors="pt", padding=True, truncation=True)
            outputs = mdl.generate(**inputs)
            return tok.decode(outputs[0], skip_special_tokens=True)
        except Exception as e:
            logger.error(f"Opus-MT FI\u2192EN virhe: {e}")'''

new1 = '''            _dev = self._fi_en.get("device", "cpu")
            inputs = tok(text, return_tensors="pt", padding=True, truncation=True)
            inputs = {k: v.to(_dev) for k, v in inputs.items()}
            outputs = mdl.generate(**inputs)
            return tok.decode(outputs[0], skip_special_tokens=True)
        except Exception as e:
            logger.error(f"Opus-MT FI\u2192EN virhe: {e}")'''

# EN-FI: lisaa device inputteihin
old2 = '''            inputs = tok(text, return_tensors="pt", padding=True, truncation=True)
            outputs = mdl.generate(**inputs)
            return tok.decode(outputs[0], skip_special_tokens=True)
        except Exception as e:
            logger.error(f"Opus-MT EN\u2192FI virhe: {e}")'''

new2 = '''            _dev = self._en_fi.get("device", "cpu")
            inputs = tok(text, return_tensors="pt", padding=True, truncation=True)
            inputs = {k: v.to(_dev) for k, v in inputs.items()}
            outputs = mdl.generate(**inputs)
            return tok.decode(outputs[0], skip_special_tokens=True)
        except Exception as e:
            logger.error(f"Opus-MT EN\u2192FI virhe: {e}")'''

c1 = src.count(old1)
c2 = src.count(old2)
print(f"FI-EN func match: {c1}")
print(f"EN-FI func match: {c2}")

if c1 == 1 and c2 == 1:
    src = src.replace(old1, new1, 1)
    src = src.replace(old2, new2, 1)
    ast.parse(src)
    open('translation_proxy.py', 'w', encoding='utf-8').write(src)
    print("OK: Opus-MT inputs -> GPU patch asennettu")
else:
    print("EI LOYDY - tarkista rivit 936-960")
    if c1 != 1:
        print(f"  FI-EN: loytyi {c1} kertaa")
    if c2 != 1:
        print(f"  EN-FI: loytyi {c2} kertaa")
