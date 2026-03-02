# Sähköposti

**Aihe:** Havainnollistus: paikallinen AI-järjestelmä — WaggleDance

**Vastaanottajat:** Ari Östman, Christian Lauren
**CC:** Jarkko Lauraeus, Nico Remo, Olli Nurmiainen

---

Hei,

Olen vapaa-ajallani rakentanut havainnollistavan demon paikallisesta AI-järjestelmästä. Ajattelin näyttää teille, koska konsepti voisi olla kiinnostava myös teollisuusympäristössä.

Kyseessä on WaggleDance Swarm AI — paikallinen tekoäly, joka pyörii kokonaan yhdellä tietokoneella ilman pilvipalveluja tai internet-yhteyttä. Demo on käynnissä HP ZBook -kannettavallani.

**Mitä demo tekee:**
- 23 erikoistunutta AI-agenttia, jotka kommunikoivat keskenään
- Yli 3 100 faktaa vektorimuistissa (kasvaa autonomisesti)
- Vastausaika 5–55 ms (pilvi-AI tyypillisesti 1 200–4 000 ms)
- Oppii itsenäisesti 24/7 — noin 3 000 uutta faktaa per yö

**Miksi tämä on mielestäni kiinnostavaa:**
- Täysin paikallinen — data ei poistu koneelta, ei pilvikuluja
- Huomattavasti nopeampi kuin pilvipohjaiset AI-ratkaisut
- Skaalautuu: sama ohjelmisto toimii kannettavasta DGX B200 -palvelimeen asti
- Tehokkaammalla laitteistolla (esim. DGX B200) laskennallisesti 100–1 200× nopeampi kuin pilvi-AI

Liitteenä tarkemmat tiedot, jos kiinnostaa vilkaista:
1. **Tuote-esite** — yleiskatsaus järjestelmästä
2. **Latenssivertailu** — mitatut ja lasketut suorituskykyluvut

Tämä on puhtaasti havainnollistava esimerkki siitä, mikä on mahdollista paikallisella tekoälyllä. Vapaa-aikani kuluu joka tapauksessa koodaamisessa ja kiinnostavien aiheiden tutkimisessa — tämä on yksi niistä projekteista.

Ystävällisin terveisin,
Jani

---

**Liitteet:**
1. waggledance_tuote_esite.pdf
2. waggledance_latenssivertailu.pdf
