import ast

src = open('translation_proxy.py', encoding='utf-8').read()

# FI-EN GPU
old1 = '            self._fi_en = {\n                "tokenizer": MarianTokenizer.from_pretrained(model_name),\n                "model": MarianMTModel.from_pretrained(model_name),\n            }\n            logger.info("FI\u2192EN malli ladattu")'

new1 = '            import torch\n            _dev = "cuda" if torch.cuda.is_available() else "cpu"\n            self._fi_en = {\n                "tokenizer": MarianTokenizer.from_pretrained(model_name),\n                "model": MarianMTModel.from_pretrained(model_name).half().to(_dev),\n                "device": _dev,\n            }\n            logger.info(f"FI\u2192EN malli ladattu ({_dev})")'

# EN-FI GPU
old2 = '            self._en_fi = {\n                "tokenizer": MarianTokenizer.from_pretrained(model_name),\n                "model": MarianMTModel.from_pretrained(model_name),\n            }\n            logger.info("EN\u2192FI malli ladattu")'

new2 = '            import torch\n            _dev = "cuda" if torch.cuda.is_available() else "cpu"\n            self._en_fi = {\n                "tokenizer": MarianTokenizer.from_pretrained(model_name),\n                "model": MarianMTModel.from_pretrained(model_name).half().to(_dev),\n                "device": _dev,\n            }\n            logger.info(f"EN\u2192FI malli ladattu ({_dev})")'

c1 = src.count(old1)
c2 = src.count(old2)
print(f"FI-EN match: {c1}")
print(f"EN-FI match: {c2}")

if c1 == 1 and c2 == 1:
    src = src.replace(old1, new1, 1)
    src = src.replace(old2, new2, 1)
    ast.parse(src)
    open('translation_proxy.py', 'w', encoding='utf-8').write(src)
    print("OK: Opus-MT GPU patch asennettu")
else:
    print("EI LOYDY - nayta rivit 908-928 tarkistusta varten")

# Nayta tulos
src2 = open('translation_proxy.py', encoding='utf-8').read()
lines = src2.splitlines()
for i in range(907, 935):
    if i < len(lines):
        print(f"{i+1}: {lines[i]}")
