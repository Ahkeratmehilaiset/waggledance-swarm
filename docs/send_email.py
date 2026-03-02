"""
WaggleDance demo-email sender
Käyttää U:/project/gmail-automation/ Gmail-clientia
"""
import sys
import os

# Fix Windows console encoding
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Add gmail-automation to path
sys.path.insert(0, r"U:\project\gmail-automation")

from gmail_client import GmailClient

# === VASTAANOTTAJAT ===
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
1. Tuote-esite — yleiskatsaus järjestelmästä
2. Latenssivertailu — mitatut ja lasketut suorituskykyluvut

Tämä on puhtaasti havainnollistava esimerkki siitä, mikä on mahdollista paikallisella tekoälyllä. Vapaa-aikani kuluu joka tapauksessa koodaamisessa ja kiinnostavien aiheiden tutkimisessa — tämä on yksi niistä projekteista.

Ystävällisin terveisin,
Jani
"""

ATTACHMENTS = [
    r"U:\project2\docs\waggledance_tuote_esite.html",
    r"U:\project2\docs\latency_comparison.html",
]

def main():
    # Verify attachments exist
    for f in ATTACHMENTS:
        if not os.path.exists(f):
            print(f"VIRHE: Liite ei löydy: {f}")
            sys.exit(1)
        size_kb = os.path.getsize(f) / 1024
        print(f"  Liite: {os.path.basename(f)} ({size_kb:.0f} KB)")

    print(f"\nTo:      {TO}")
    print(f"CC:      {CC}")
    print(f"Aihe:    {SUBJECT}")
    print(f"Liitteet: {len(ATTACHMENTS)} kpl")
    print()

    confirm = input("Lähetetäänkö? (k/e): ").strip().lower()
    if confirm != "k":
        print("Peruttu.")
        return

    print("\nYhdistetään Gmailiin...")
    gc = GmailClient()

    print("Lähetetään...")
    result = gc.send_email(
        to=TO,
        subject=SUBJECT,
        body=BODY,
        attachments=ATTACHMENTS,
        cc=CC,
    )
    print(f"\nLähetetty! Message ID: {result.get('id', 'N/A')}")

if __name__ == "__main__":
    main()
