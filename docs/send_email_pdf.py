"""
WaggleDance demo-email sender — PDF-liitteet
"""
import sys, os
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"U:\project\gmail-automation")

from gmail_client import GmailClient

TO = "ari.ostman@murata.com, christian.lauren@murata.com"
CC = "jarkko.lauraeus@murata.com, niko.remo@murata.com, olli.nurmiainen@murata.com, jani.korpi@gmail.com"
SUBJECT = "Havainnollistus: paikallinen AI-järjestelmä — WaggleDance"

BODY = """Hei,

Olen vapaa-ajallani rakentanut havainnollistavan demon paikallisesta AI-järjestelmästä. Ajattelin näyttää teille, koska konsepti voisi olla kiinnostava myös teollisuusympäristössä.

Kyseessä on WaggleDance Swarm AI — paikallinen tekoäly, joka pyörii kokonaan yhdellä tietokoneella ilman pilvipalveluja tai internet-yhteyttä. Demo on käynnissä HP ZBook -kannettavallani.

Mitä demo tekee:
- 23 erikoistunutta AI-agenttia, jotka kommunikoivat keskenään
- Yli 3 100 faktaa vektorimuistissa (kasvaa autonomisesti)
- Vastausaika 5–55 ms (pilvi-AI tyypillisesti 1 200–4 000 ms)
- Oppii itsenäisesti 24/7 — noin 3 000 uutta faktaa per yö

Miksi tämä on mielestäni kiinnostavaa:
- Täysin paikallinen — data ei poistu koneelta, ei pilvikuluja
- Huomattavasti nopeampi kuin pilvipohjaiset AI-ratkaisut
- Skaalautuu: sama ohjelmisto toimii kannettavasta DGX B200 -palvelimeen asti
- Tehokkaammalla laitteistolla (esim. DGX B200) laskennallisesti 100–1 200x nopeampi kuin pilvi-AI

Liitteenä tarkemmat tiedot, jos kiinnostaa vilkaista:
1. Tuote-esite (PDF) — yleiskatsaus järjestelmästä
2. Latenssivertailu (PDF) — mitatut ja lasketut suorituskykyluvut

Tämä on puhtaasti havainnollistava esimerkki siitä, mikä on mahdollista paikallisella tekoälyllä. Vapaa-aikani kuluu joka tapauksessa koodaamisessa ja kiinnostavien aiheiden tutkimisessa — tämä on yksi niistä projekteista.

Ystävällisin terveisin,
Jani

P.S. Mikäli saitte aiemmin HTML-version liitteistä, nämä PDF:t ovat samat dokumentit helpommin avattavassa muodossa.
"""

ATTACHMENTS = [
    r"U:\project2\docs\waggledance_tuote_esite.pdf",
    r"U:\project2\docs\waggledance_latenssivertailu.pdf",
]

def main():
    for f in ATTACHMENTS:
        if not os.path.exists(f):
            print(f"VIRHE: {f} ei löydy"); sys.exit(1)
        print(f"  Liite: {os.path.basename(f)} ({os.path.getsize(f)/1024:.0f} KB)")

    print(f"\nTo:  {TO}")
    print(f"CC:  {CC}")
    print(f"Aihe: {SUBJECT}")
    print(f"Liitteet: {len(ATTACHMENTS)} PDF")
    print("\nLähetetään...")

    gc = GmailClient()
    result = gc.send_email(to=TO, subject=SUBJECT, body=BODY, attachments=ATTACHMENTS, cc=CC)
    print(f"\nLähetetty! Message ID: {result.get('id', 'N/A')}")

if __name__ == "__main__":
    main()
