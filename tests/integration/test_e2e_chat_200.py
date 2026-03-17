"""200 end-to-end chat tests — Finnish, English, special chars, formulas, typos.

Categories (200 total):
  1. Finnish correct queries          (30)
  2. Finnish with spelling errors     (25)
  3. English correct queries          (25)
  4. English with spelling errors     (20)
  5. Special characters & unicode     (25)
  6. Math, formulas, units            (20)
  7. Mixed language (FI+EN)           (15)
  8. Edge cases & adversarial         (20)
  9. Domain-specific with errors      (20)

All tests run in stub mode (no Ollama required).
"""

import pytest
from starlette.testclient import TestClient

from waggledance.adapters.config.settings_loader import WaggleSettings
from waggledance.bootstrap.container import Container

# ── Module-scoped test client (shared across all 200 tests) ───────


_client = None
_api_key = None
_app = None


def _get_client():
    global _client, _api_key, _app
    if _client is None:
        settings = WaggleSettings.from_env()
        container = Container(settings=settings, stub=True)
        _app = container.build_app()
        _client = TestClient(_app, raise_server_exceptions=False)
        _api_key = settings.api_key
    return _client, _api_key


def _reset_rate_limit():
    """Clear rate-limiter buckets so 200+ tests don't hit 429."""
    if _app is None:
        return
    from waggledance.adapters.http.middleware.rate_limit import RateLimitMiddleware
    # middleware_stack is built lazily on first request
    obj = getattr(_app, "middleware_stack", None)
    if obj is None:
        return
    for _ in range(30):
        if isinstance(obj, RateLimitMiddleware):
            obj._buckets.clear()
            return
        obj = getattr(obj, "app", None)
        if obj is None:
            break


def _chat(query: str, lang: str = "auto") -> "Response":
    """Send a chat query and return the HTTP response."""
    _reset_rate_limit()
    client, api_key = _get_client()
    resp = client.post(
        "/api/chat",
        json={"query": query, "language": lang},
        headers={"Authorization": f"Bearer {api_key}"},
    )
    return resp


# ══════════════════════════════════════════════════════════════════
# 1. FINNISH CORRECT (30 tests)
# ══════════════════════════════════════════════════════════════════

FINNISH_CORRECT = [
    ("fi01", "Miten mehiläispesä talvehditaan?"),
    ("fi02", "Kuinka paljon hunajaa yksi pesä tuottaa vuodessa?"),
    ("fi03", "Mikä on varroa-punkin torjuntakalenteri?"),
    ("fi04", "Milloin mehiläiset alkavat parveilemaan?"),
    ("fi05", "Paljonko kello on?"),
    ("fi06", "Mikä on huoneen lämpötila tällä hetkellä?"),
    ("fi07", "Kerro sähkönkulutuksesta viime viikolla"),
    ("fi08", "Kuinka monta agenttia on aktiivisena?"),
    ("fi09", "Mitä mehiläiset tekevät talvella?"),
    ("fi10", "Selitä pesän tarkastuksen vaiheet"),
    ("fi11", "Kuinka usein pesä pitää tarkistaa kesällä?"),
    ("fi12", "Mikä on paras hunajan varastointilämpötila?"),
    ("fi13", "Miten tunnistaa sairaan mehiläispesän?"),
    ("fi14", "Kerro Finnish NLP -putkilinjan toiminnasta"),
    ("fi15", "Mitä tarkoittaa round table -keskustelu?"),
    ("fi16", "Kuinka monta muistia järjestelmässä on?"),
    ("fi17", "Miten MQTT-anturi toimii mehiläispesässä?"),
    ("fi18", "Mitkä ovat hyvän pesäpaikan valintakriteerit?"),
    ("fi19", "Kerro varroapunkin elinkaaresta yksityiskohtaisesti"),
    ("fi20", "Miten mehiläisvaha tuotetaan ja käsitellään?"),
    ("fi21", "Mikä lämpötila on normaalisti pesässä talvella?"),
    ("fi22", "Kuinka paljon vettä mehiläiset tarvitsevat päivässä?"),
    ("fi23", "Mitä tehdä jos pesä on jäänyt ilman kuningatarta?"),
    ("fi24", "Selitä kuningattaren merkitseminen värikoodeilla"),
    ("fi25", "Miten hunajaa lypsetään kennoista lingon avulla?"),
    ("fi26", "Mikä on nosema-tauti ja miten sitä hoidetaan?"),
    ("fi27", "Kerro pölytyspalveluiden hinnoittelusta Suomessa"),
    ("fi28", "Mitä agentteja COTTAGE-profiilissa on käytettävissä?"),
    ("fi29", "Kuinka säätila vaikuttaa mehiläisten käyttäytymiseen?"),
    ("fi30", "Miten järjestelmä oppii autonomisesti yön aikana?"),
]


@pytest.mark.parametrize(
    "test_id,query", FINNISH_CORRECT,
    ids=[t[0] for t in FINNISH_CORRECT],
)
def test_finnish_correct(test_id, query):
    resp = _chat(query, lang="fi")
    assert resp.status_code == 200
    data = resp.json()
    assert "response" in data
    assert len(data["response"]) > 0


# ══════════════════════════════════════════════════════════════════
# 2. FINNISH WITH SPELLING ERRORS (25 tests)
# ══════════════════════════════════════════════════════════════════

FINNISH_TYPOS = [
    ("fi_t01", "Miten mehillaispesä talvehditaan?"),           # ä→a: mehiläis→mehillais
    ("fi_t02", "Kuinka paljn hunajaa tuotetaan?"),              # missing 'o': paljon→paljn
    ("fi_t03", "Mika on varroa-punkin torjunta?"),              # ä→a: Mikä→Mika
    ("fi_t04", "Milloin mehiläiset alkavt parveilemaan?"),      # missing 'a': alkavat→alkavt
    ("fi_t05", "Paljonko kellp on nyt?"),                       # o→p: kello→kellp
    ("fi_t06", "Kuinka monta aggenttiä on aktiivisena?"),       # agenttia→aggenttiä
    ("fi_t07", "Mitä mehilaiset tekevat talvella?"),            # ä→a twice
    ("fi_t08", "Selita pesän tarkastuksen vaiheet"),            # ä→a: Selitä→Selita
    ("fi_t09", "Kuinka usien pesä pitää tarkistaa?"),           # usein→usien
    ("fi_t10", "Miten tunnistaa sairan pesän?"),                # sairaan→sairan
    ("fi_t11", "Kerro finnin NLP-putkilinjasta"),               # Finnish→finnin
    ("fi_t12", "Mita tarkoittaa round table?"),                 # ä→a: Mitä→Mita
    ("fi_t13", "Kuinka monta muistja järjestelmässä on?"),      # muistia→muistja
    ("fi_t14", "Miten MQQT-anturi toimii?"),                   # MQTT→MQQT
    ("fi_t15", "Mikä on parhaan pesapäikan valinta?"),          # pesäpaikan→pesapäikan swap
    ("fi_t16", "Kerro varroapunkin elinkareesta"),              # elinkaaresta→elinkareesta
    ("fi_t17", "Miten mehiläsivaha tuotetaan?"),               # mehiläis→mehiläsi
    ("fi_t18", "Mika lämpotila on pesässä normaalisti?"),       # Mikä→Mika, ö→o
    ("fi_t19", "Kuinka palijon vettä mehiläiset tarvitsevat?"), # paljon→palijon
    ("fi_t20", "Mitä tehdä jos pesä on orvoksi jaanyt?"),      # jäänyt→jaanyt
    ("fi_t21", "Selita kuninkuudenpesän merkitseminen"),        # Selitä→Selita, kuningatar→kuninkuuden
    ("fi_t22", "Miten hunajjaa lypsetään kennoista?"),          # hunajaa→hunajjaa
    ("fi_t23", "Mikä on noesma-tauti?"),                       # nosema→noesma
    ("fi_t24", "Kerro pöllytyspalveluista Suomesa"),           # pölytys→pöllytys, Suomessa→Suomesa
    ("fi_t25", "Miten järjestemä oppii yöllä?"),               # järjestelmä→järjestemä
]


@pytest.mark.parametrize(
    "test_id,query", FINNISH_TYPOS,
    ids=[t[0] for t in FINNISH_TYPOS],
)
def test_finnish_with_typos(test_id, query):
    resp = _chat(query, lang="fi")
    assert resp.status_code == 200
    data = resp.json()
    assert "response" in data
    assert len(data["response"]) > 0


# ══════════════════════════════════════════════════════════════════
# 3. ENGLISH CORRECT (25 tests)
# ══════════════════════════════════════════════════════════════════

ENGLISH_CORRECT = [
    ("en01", "How do you winterize a beehive?"),
    ("en02", "How much honey does one hive produce per year?"),
    ("en03", "What is the varroa mite treatment calendar?"),
    ("en04", "When do bees start swarming?"),
    ("en05", "What time is it right now?"),
    ("en06", "What is the current room temperature?"),
    ("en07", "Tell me about electricity consumption last week"),
    ("en08", "How many agents are currently active?"),
    ("en09", "What do bees do during the winter months?"),
    ("en10", "Explain the steps of a full hive inspection"),
    ("en11", "How often should I check the hive in summer?"),
    ("en12", "What is the best storage temperature for honey?"),
    ("en13", "How can I identify a sick bee colony?"),
    ("en14", "Tell me about the Finnish NLP pipeline"),
    ("en15", "What is a round table discussion in WaggleDance?"),
    ("en16", "How many memories does the system have?"),
    ("en17", "How does the MQTT sensor bridge work?"),
    ("en18", "What are the criteria for choosing a hive location?"),
    ("en19", "Tell me about the varroa mite lifecycle"),
    ("en20", "How is beeswax produced by worker bees?"),
    ("en21", "What is the normal temperature inside a beehive?"),
    ("en22", "How much water do honey bees need daily?"),
    ("en23", "What should I do if the hive is queenless?"),
    ("en24", "Explain the international queen marking color system"),
    ("en25", "How is honey extracted from the combs?"),
]


@pytest.mark.parametrize(
    "test_id,query", ENGLISH_CORRECT,
    ids=[t[0] for t in ENGLISH_CORRECT],
)
def test_english_correct(test_id, query):
    resp = _chat(query, lang="en")
    assert resp.status_code == 200
    data = resp.json()
    assert "response" in data
    assert len(data["response"]) > 0


# ══════════════════════════════════════════════════════════════════
# 4. ENGLISH WITH SPELLING ERRORS (20 tests)
# ══════════════════════════════════════════════════════════════════

ENGLISH_TYPOS = [
    ("en_t01", "How do you winterise a beehiv?"),               # beehive→beehiv
    ("en_t02", "How mutch honey does one hive produse?"),       # much→mutch, produce→produse
    ("en_t03", "What is teh varroa mite treatmnet?"),           # the→teh, treatment→treatmnet
    ("en_t04", "Wen do bees start swarmin?"),                   # When→Wen, swarming→swarmin
    ("en_t05", "Waht time is it rite now?"),                    # What→Waht, right→rite
    ("en_t06", "Tell me abut electricity consumtion"),          # about→abut, consumption→consumtion
    ("en_t07", "How meny agents are actve?"),                   # many→meny, active→actve
    ("en_t08", "Waht do bees do in wintr?"),                    # What→Waht, winter→wintr
    ("en_t09", "Explian the hive inpsection steps"),            # Explain→Explian, inspection→inpsection
    ("en_t10", "How oftn should I check the hive?"),            # often→oftn
    ("en_t11", "How to idetify a sick colny?"),                 # identify→idetify, colony→colny
    ("en_t12", "Tell me about the NLP pipline"),                # pipeline→pipline
    ("en_t13", "What is a roud table discusion?"),              # round→roud, discussion→discusion
    ("en_t14", "How manny memories does the systm have?"),      # many→manny, system→systm
    ("en_t15", "How dose the MQTT sensr work?"),                # does→dose, sensor→sensr
    ("en_t16", "What are the critria for hive locaion?"),       # criteria→critria, location→locaion
    ("en_t17", "Tell me abouut the varroa lifecycle"),          # about→abouut
    ("en_t18", "How is beswax prodced?"),                       # beeswax→beswax, produced→prodced
    ("en_t19", "Whats the normall temperature inside hive?"),   # normal→normall
    ("en_t20", "How much watter do bees nee?"),                 # water→watter, need→nee
]


@pytest.mark.parametrize(
    "test_id,query", ENGLISH_TYPOS,
    ids=[t[0] for t in ENGLISH_TYPOS],
)
def test_english_with_typos(test_id, query):
    resp = _chat(query, lang="en")
    assert resp.status_code == 200
    data = resp.json()
    assert "response" in data
    assert len(data["response"]) > 0


# ══════════════════════════════════════════════════════════════════
# 5. SPECIAL CHARACTERS & UNICODE (25 tests)
# ══════════════════════════════════════════════════════════════════

SPECIAL_CHARS = [
    ("sc01", "Kerro mehiläisistä \U0001f41d\U0001f36f"),           # 🐝🍯
    ("sc02", "What's the temperature? \U0001f321\ufe0f"),          # 🌡️
    ("sc03", "Lämpötila on 35\u00b0C \u2014 onko se normaali?"),   # 35°C — em dash
    ("sc04", "Price is 0.15 \u20ac/kWh, is that good?"),           # €
    ("sc05", "CO\u2082 level is 400ppm in the hive"),              # CO₂
    ("sc06", "H\u2082O consumption per hive per day?"),            # H₂O
    ("sc07", "\u0394x/\u0394t, what about bee flight speed?"),     # Δx/Δt
    ("sc08", "\u03c3 = F/A, stress in honeycomb structure?"),      # σ = F/A
    ("sc09", "\u03c0 \u2248 3.14159 \u2014 how precise is the routing?"),  # π ≈ 3.14159 —
    ("sc10", "\u6e29\u5ea6\u306f\u4f55\u5ea6\u3067\u3059\u304b\uff1f"),  # 温度は何度ですか？ (Japanese)
    ("sc11", "\u0645\u0627 \u0647\u064a \u062f\u0631\u062c\u0629 \u0627\u0644\u062d\u0631\u0627\u0631\u0629\u061f"),  # Arabic RTL
    ("sc12", "\u041a\u0430\u043a\u0430\u044f \u0442\u0435\u043c\u043f\u0435\u0440\u0430\u0442\u0443\u0440\u0430 \u0432 \u0443\u043b\u044c\u0435?"),  # Russian
    ("sc13", "Quelle est la temp\u00e9rature de la ruche?"),       # French: é
    ("sc14", "Was ist die Temperatur im Bienenstock? Bl\u00fchende Wiese"),  # German: ü
    ("sc15", "Hello <script>alert('xss')</script> world"),         # XSS attempt
    ("sc16", "Query with\nnewlines\nin\nit"),                      # embedded newlines
    ("sc17", "Query with\ttabs\there\tand\tthere"),                # embedded tabs
    ("sc18", "Backslash \\\\ and quotes \\\" and 'single'"),      # escaped chars
    ("sc19", "Curly {braces} and [brackets] and (parens)"),        # brackets
    ("sc20", "Ampersand & pipe | tilde ~ caret ^"),                # operators
    ("sc21", "Hash # at @ dollar $ percent 50%"),                  # misc symbols
    ("sc22", "Stars *** underscores ___ dashes ---"),              # markdown-like
    ("sc23", "Emoji chain: \U0001f41d\U0001f338\U0001f36f\U0001f3e1\u2744\ufe0f\u2600\ufe0f\U0001f327\ufe0f\U0001f4a8"),  # 🐝🌸🍯🏡❄️☀️🌧️💨
    ("sc24", "Zero\u200bwidth\u200bspace\u200btest"),              # zero-width spaces
    ("sc25", "\u00a9 2026 WaggleDance\u2122 \u2013 All rights reserved\u2026"),  # © ™ – …
]


@pytest.mark.parametrize(
    "test_id,query", SPECIAL_CHARS,
    ids=[t[0] for t in SPECIAL_CHARS],
)
def test_special_characters(test_id, query):
    resp = _chat(query)
    assert resp.status_code == 200
    data = resp.json()
    assert "response" in data
    assert len(data["response"]) > 0


# ══════════════════════════════════════════════════════════════════
# 6. MATH, FORMULAS, UNITS (20 tests)
# ══════════════════════════════════════════════════════════════════

MATH_FORMULAS = [
    ("math01", "Laske 3 pes\u00e4\u00e4 \u00d7 25kg hunajaa = ?"),  # 3 pesää × 25kg
    ("math02", "Paljonko on 150 kWh \u00d7 0.15 \u20ac/kWh?"),      # 150 kWh × 0.15 €
    ("math03", "Calculate 35\u00b0C to Fahrenheit"),                  # 35°C
    ("math04", "What is 12.5% of 400 kg?"),
    ("math05", "Kuinka paljon on \u221a(144)?"),                      # √(144)
    ("math06", "E = mc\u00b2, selitä kaava lyhyesti"),               # E = mc²
    ("math07", "2\u2078 = 256, onko oikein?"),                       # 2⁸
    ("math08", "log\u2081\u2080(1000) = ?"),                         # log₁₀
    ("math09", "sin(90\u00b0) + cos(0\u00b0) = ?"),                 # sin(90°) + cos(0°)
    ("math10", "\u2211(n=1 to 10) n = ?"),                           # ∑
    ("math11", "Convert 20 liters to US gallons"),
    ("math12", "15mm rainfall per day for 30 days = total mm?"),
    ("math13", "3.5 kg \u00d7 9.81 m/s\u00b2 = ? N"),              # 3.5 kg × 9.81 m/s²
    ("math14", "C\u2086H\u2081\u2082O\u2086 \u2014 what molecule is this?"),  # C₆H₁₂O₆
    ("math15", "CH\u2083COOH \u2014 what is this acid?"),            # CH₃COOH
    ("math16", "Pes\u00e4n paino: 45.3kg \u2192 42.1kg, erotus?"),  # Pesän paino: →
    ("math17", "ROI = (25\u20ac - 15\u20ac) / 15\u20ac \u00d7 100%"),  # ROI formula
    ("math18", "Hunajasato: mean(20, 25, 18, 30, 22) kg = ?"),
    ("math19", "\u03c3 = \u221a(\u2211(x-\u03bc)\u00b2/N), selitä"),  # σ = √(Σ(x-μ)²/N)
    ("math20", "1 acre = ? m\u00b2, kuinka monta pes\u00e4\u00e4 per hehtaari?"),  # m², pesää
]


@pytest.mark.parametrize(
    "test_id,query", MATH_FORMULAS,
    ids=[t[0] for t in MATH_FORMULAS],
)
def test_math_formulas(test_id, query):
    resp = _chat(query)
    assert resp.status_code == 200
    data = resp.json()
    assert "response" in data
    assert len(data["response"]) > 0


# ══════════════════════════════════════════════════════════════════
# 7. MIXED LANGUAGE FI+EN (15 tests)
# ══════════════════════════════════════════════════════════════════

MIXED_LANGUAGE = [
    ("mix01", "Miten varroa treatment tehdään oikein keväällä?"),
    ("mix02", "The hive lämpötila is dropping, mitä pitäisi tehdä?"),
    ("mix03", "Round table -keskustelu mehiläisten health-aiheesta"),
    ("mix04", "Explain pesän tarkastus in English please"),
    ("mix05", "Kuinka SmartRouter works sisäisesti?"),
    ("mix06", "MQTT sensor data mehiläispesästä, show JSON"),
    ("mix07", "Night mode yöoppiminen, how does it work?"),
    ("mix08", "ChromaDB embeddings ja bilingual vector index"),
    ("mix09", "Parveilun prevention, miten estää swarming keväällä?"),
    ("mix10", "Temperature alert: hive #3 on liian kuuma, > 40\u00b0C"),
    ("mix11", "Agent trust level viisi = MASTER, kerro lisää siitä"),
    ("mix12", "Cognitive graph nodes ja causal edges pesätiedoissa"),
    ("mix13", "Hunajan viscosity at different temperatures, selitä"),
    ("mix14", "Nosema-tauti treatment with oxalic acid syksyllä"),
    ("mix15", "Dashboard UI näyttää CPU load is 85%, onko normaali?"),
]


@pytest.mark.parametrize(
    "test_id,query", MIXED_LANGUAGE,
    ids=[t[0] for t in MIXED_LANGUAGE],
)
def test_mixed_language(test_id, query):
    resp = _chat(query)
    assert resp.status_code == 200
    data = resp.json()
    assert "response" in data
    assert len(data["response"]) > 0


# ══════════════════════════════════════════════════════════════════
# 8. EDGE CASES & ADVERSARIAL (20 tests)
# ══════════════════════════════════════════════════════════════════

EDGE_CASES = [
    ("edge01", "x"),                                              # single char
    ("edge02", "???"),                                            # only punctuation
    ("edge03", "12345"),                                          # only numbers
    ("edge04", "a" * 500),                                        # repeated char ×500
    ("edge05", " ".join(["mehiläinen"] * 50)),                    # same word ×50
    ("edge06", "SELECT * FROM users WHERE 1=1; --"),              # SQL injection
    ("edge07", "Robert'); DROP TABLE facts;--"),                   # Bobby Tables
    ("edge08", '<img src=x onerror="alert(1)">'),                 # XSS img tag
    ("edge09", "../../etc/passwd"),                                # path traversal
    ("edge10", '{"key": "value", "nested": {"a": 1}}'),          # JSON blob
    ("edge11", "# Heading\n\n**Bold** and *italic* text"),        # Markdown
    ("edge12", "https://example.com/page?q=test&lang=fi#anchor"), # full URL
    ("edge13", "."),                                              # single dot
    ("edge14", "\U0001f41d"),                                     # single emoji 🐝
    ("edge15", "mehiläinen " * 100 + "kerro lisää"),              # long query
    ("edge16", "CAPS LOCK KOKO VIESTI ISOILLA KIRJAIMILLA"),      # all caps FI
    ("edge17", "aaaaabbbbbcccccdddddeeeee" * 10),                 # gibberish
    ("edge18", "1+1=2, 2+2=4, 4+4=8, 8+8=16, 16+16=32"),        # number sequence
    ("edge19", "     whitespace     around     words     "),      # extra whitespace
    ("edge20", "Hello\r\nWorld\r\nCRLF\r\nLine\r\nEndings"),     # CRLF line endings
]


@pytest.mark.parametrize(
    "test_id,query", EDGE_CASES,
    ids=[t[0] for t in EDGE_CASES],
)
def test_edge_cases(test_id, query):
    resp = _chat(query)
    assert resp.status_code == 200
    data = resp.json()
    assert "response" in data


# ══════════════════════════════════════════════════════════════════
# 9. DOMAIN-SPECIFIC WITH ERRORS (20 tests)
# ══════════════════════════════════════════════════════════════════

DOMAIN_ERRORS = [
    ("dom01", "varroapunkki torjuntakaleneri kevällä"),           # -kalenteri→-kaleneri, keväällä→kevällä
    ("dom02", "mehiläistarhaaus aloittelijalle opas"),            # tarhaus→tarhaaus
    ("dom03", "hunajan kristallisoitumisen estäminen purkisa"),   # purkissa→purkisa
    ("dom04", "kunigatarpesän käsittley varovasti"),              # kuningatar→kunigatar, käsittely→käsittley
    ("dom05", "parveilunesto tekniikat keäkuussa"),               # kesäkuussa→keäkuussa
    ("dom06", "propolis kerays ja käyttö lääkkeenä"),             # keräys→kerays
    ("dom07", "oksaalihappo käsittely talvela joulukussa"),       # talvella→talvela
    ("dom08", "apimelifera vs mellifera carnica erot"),           # Apis→api (uncapitalized)
    ("dom09", "langstorth pesämalli mitat senttimetreinä"),       # Langstroth→langstorth
    ("dom10", "warre pesä vs dadant pesä, kumpi parempi?"),       # Warré→warre (no accent)
    ("dom11", "nektarivirtaus ja satokaussi heinäkuusa"),         # satokausi→satokaussi, heinäkuussa→heinäkuusa
    ("dom12", "robbing behavio ja sen estaminen syksylä"),        # behaviour→behavio, estäminen→estaminen, syksyllä→syksylä
    ("dom13", "bee space 9mm, onko oikeein mittaus?"),            # oikein→oikeein
    ("dom14", "vahaliuskojen asentminen kehykseen"),              # asentaminen→asentminen
    ("dom15", "smoker käytö ja turvalisuus ohjeet"),              # käyttö→käytö, turvallisuus→turvalisuus
    ("dom16", "supersedure vs emergency qween cells erkot"),      # queen→qween, erot→erkot
    ("dom17", "brood frme inspection checklist for bginners"),    # frame→frme, beginners→bginners
    ("dom18", "honey moistre content max 18% standrd"),           # moisture→moistre, standard→standrd
    ("dom19", "fondant feedng in late autum and erly wintr"),     # feeding→feedng, autumn→autum, early→erly, winter→wintr
    ("dom20", "emomuuttos ja uuden emlon hyväksyminen pesäsä"),  # emomuutos→emomuuttos, emon→emlon, pesässä→pesäsä
]


@pytest.mark.parametrize(
    "test_id,query", DOMAIN_ERRORS,
    ids=[t[0] for t in DOMAIN_ERRORS],
)
def test_domain_specific_with_errors(test_id, query):
    resp = _chat(query)
    assert resp.status_code == 200
    data = resp.json()
    assert "response" in data
    assert len(data["response"]) > 0


# ══════════════════════════════════════════════════════════════════
# Structural validation tests (bonus — use remaining test IDs)
# ══════════════════════════════════════════════════════════════════


def test_response_has_required_fields():
    """Every chat response must include source, confidence, latency_ms, cached."""
    resp = _chat("Hello WaggleDance", lang="en")
    assert resp.status_code == 200
    data = resp.json()
    for field in ("response", "source", "confidence", "latency_ms", "cached"):
        assert field in data, f"Missing field: {field}"


def test_finnish_detected_from_umlauts():
    """Query with ä/ö → language=fi in response."""
    resp = _chat("Mikä on mehiläisten pääravinto?")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("language") == "fi"


def test_english_detected_from_ascii():
    """Plain ASCII query → language=en in response."""
    resp = _chat("What do bees eat?")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("language") == "en"


def test_time_query_returns_time():
    """Time keyword → response contains time-related content."""
    resp = _chat("What time is it?", lang="en")
    assert resp.status_code == 200
    data = resp.json()
    assert "time" in data["response"].lower()


def test_confidence_is_numeric():
    """Confidence field must be a float 0.0–1.0."""
    resp = _chat("Tell me about varroa mites", lang="en")
    assert resp.status_code == 200
    data = resp.json()
    conf = data.get("confidence", -1)
    assert isinstance(conf, (int, float))
    assert 0.0 <= conf <= 1.0
