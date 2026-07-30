# HEX Recovery Contract V1

## Tila ja tarkoitus

Tämä dokumentti määrittää WaggleDancen HEX-palautuksen V1-sopimuksen.
V1 on sisältöosoitteinen, deterministinen ja `shadow_only`-tilaan päättyvä
palautussopimus. Se ei ole tuotantovalmiusväite eikä runtime-aktivointilupa.

Käyttäjän arkkitehtuuri-invariantti on:

1. HEX säilyttää solver-kyvykkyyden, kun yksittäinen solu poistuu käytöstä.
2. Solujen välinen reititys on tiheä mutta rajattu; se ei perustu
   rajattomaan all-to-all-broadcastiin.
3. Solu ja koko pesä voidaan rakentaa deterministisesti uudelleen uudelle
   levylle kanonisesta genomista ja siitä riippumattomista kestävistä
   tilalähteistä.

Kolmas kohta toteutuu vasta, kun jokainen palautuksessa tarvittava mutable
state -lähde on oikeasti saatavilla alkuperäisen koneen ja levyn ulkopuolella.
Samalla levyllä oleva kopio ei ole riippumaton palautuslähde.

## Kolme erillistä HEX-topologiaa

V1 ei yhdistä seuraavia topologioita yhdeksi nimeämättömäksi verkoksi:

| Topologia | Nykyinen rakenne | Tarkoitus |
|---|---:|---|
| `axial_agent_mesh` | 7 solua: keskus ja kuusi aksiaalista naapuria | Agenttien paikallinen, geometrisesti rajattu reititys |
| `logical_solver_overlay` | 8 loogista solver-solua | Solverien haku, naapuriapu ja kyvykkyyden eristäminen |
| `hierarchical_runtime_shadow` | Yhden juuren parent-child-hierarkia ja yleinen vikasietoinen recovery-ring | Subdivisionin ja jälleenrakennuksen shadow-harjoittelu |

Auditissa 7-solun verkon halkaisija oli 2 ja 8-solun verkon halkaisija 3.
Molemmat nykyiset graafit säilyivät yhteydessä minkä tahansa yhden tai kahden
loogisen solun poiston jälkeen. Tämä on graafitason ominaisuus, ei vielä
todiste siitä, että solver-prosessit, mallit tai mutable state säilyvät
fyysisessä häiriössä.

Nykyiset solut jakavat saman host-, prosessi- ja levy-failure-domainin.
Siksi koneen tai levyn rikkoutuminen voi poistaa useita tai kaikki solut
yhdellä kertaa, vaikka itse graafi kestää solujen poistoja.

## Lähteiden auktoriteettijärjestys

Tracked genomi ja palautuskoodi tulevat exact-commit Git-checkoutista.
GitHub-historia on tracked tiedostojen ensisijainen lähde. Branchin nimi tai
paikallinen working tree ei korvaa manifestiin sidottua 40-merkkistä commit
SHA:ta.

Zip- ja paikalliset levybackupit ovat toissijaisia palautusapuvälineitä.
Niitä ei saa käyttää Git-historian tai exact-commit-genomin korvikkeena.

MAGMA, tietokannat, ledgerit ja muu Gitistä sivuutettu mutable state tarvitsevat
vähintään yhden sisällöltään varmennetun off-host-replikan. Replikan
`failure_domain` ei saa olla sama kone tai sama fyysinen levy kuin aktiivinen
tila. Ulkoisen mallin tai palvelun palautus vaatii vastaavasti pinnatun,
varmennettavan artifact- tai reprovision-sopimuksen.

## V1-skeemat

### `hex_cell_genome.v1`

`schemas/hex_cell_genome.v1.json` kuvaa yhden solun jälleenrakennettavan
genomin. Se sitoo vähintään:

- solun ja mesh-nimiavaruuden sekä `mesh_kind`-tyypin;
- topology-epochin, koordinaatin, parent-child-linkit ja naapurit;
- vähintään kaksi nimettyä repair-peer-solua;
- solver-, agentti- ja router-kyvykkyydet niiden lähdepolkuihin ja SHA-256-
  digesteihin;
- tarvittavat Git-, MAGMA-, snapshot-, model- ja external-export-inputit;
- replay-vesileiman silloin, kun input rakennetaan replaylla;
- odotetun cell state rootin;
- palautustilan `shadow_only`;
- eksplisiittisen `runtime_activation_authority_granted: false` -rajan; sekä
- itse genomin kanonisen digestin.

Skeema tarkistaa rakenteen, rajat, enumit, digestien muodon ja turvallisen
POSIX-suhteellisen polkumuodon. Python-validatorin on lisäksi tarkistettava
ristiviittaukset, digestit, linkkien vastavuoroisuus, repair-peerien
olemassaolo ja mesh-kohtaiset topologiainvariantit.

### `hive_recovery_manifest.v1`

`schemas/hive_recovery_manifest.v1.json` sitoo koko pesän:

- exact Git commitin ja Git-primary-säännön;
- erilliset topology-epochit ja niiden cell-genome-viitteet;
- yhteyden, kaksisuuntaisten naapureiden, yhden solun sietokyvyn ja
  enimmäisreittisyvyyden vaatimukset (vähintään kolme ja enintään 256 solua
  topologiaa kohti);
- jokaisen palautusartifactin suhteellisen polun, raw-byte-digestin, koon,
  luokituksen ja palautusstrategian;
- nimetyt replika- ja failure-domain-viitteet;
- genome-, memory- ja hive-state-rootit;
- V1:ssä aina epätosiksi lukitut off-host- ja blank-disk-varmennusliput;
- edellisen manifestin digestin, mikäli sellainen on olemassa; sekä
- `production_ready_claim: false` -vakion.

Manifesti sitoo exact commit -ankkurin ja kaikki digestit, mutta se ei tarkista,
että kyseinen commit on todella checkoutattu kohdekoneelle. Manifestiin
kirjoitettu replika ei myöskään yksin todista, että replika on saatavilla.
Siksi `external_replication_verified` ja `blank_disk_dry_run_verified` ovat
V1:ssä vakioita `false`, eikä V1:n validator tarjoa recovery-ready-tulosta.
Saatavuus, failure-domainin riippumattomuus, salaus ja clean-checkout on
myöhemmin sidottava erilliseen varmennettuun raporttiin.

## Digest- ja state-root-säännöt

Kaikki sopimusdigestit käyttävät muotoa `sha256:<64 lowercase hex>` ja
kanonisointitunnusta `magma-jcs-subset-v1`.

- `genome_digest` lasketaan genome-objektin kanonisesta projektiosta ilman
  sen omaa `genome_digest`-kenttää.
- `topology_digest` sitoo mesh-ID:n, mesh-tyypin, epochin ja deterministisesti
  järjestetyt cell/genome/state-root-viitteet.
- `genome_root` sitoo kaikkien meshien deterministisesti järjestetyt
  cell-genome-digestit.
- `memory_root` sitoo jokaisen vaaditun non-genome-artifactin ID:n,
  turvallisen suhteellisen palautuspolun, sisältödigestin, koon, luokituksen,
  `required: true` -arvon ja restore-strategian. Konekohtaiset absoluuttiset
  polut eivät kuulu juuriin.
- Replay-checkpoint sitoutuu solun durable-inputtiin ja siten
  `expected_cell_state_root` → `genome_digest` → `genome_root` -ketjuun, ei
  `memory_root`-kenttään.
- `hive_state_root` sitoo exact commit -SHA:n, Git-primary-tunnisteen,
  `genome_root`- ja `memory_root`-arvot. Genome-JSON:n semanttinen sisältö
  sitoutuu genome-ketjun kautta; sen raw-byte-digest ja kaikki muut manifestin
  artifact-kentät sitoutuvat lisäksi `manifest_digest`-kenttään.
- `manifest_digest` lasketaan koko manifestin kanonisesta projektiosta ilman
  sen omaa `manifest_digest`-kenttää.

Manifestin settiluonteiset topology-, cell-ref-, artifact- ja replica-listat
järjestetään builderissä vakailla identiteettiavaimilla. Solugenomin
`neighbor_cell_ids`, `repair_peer_cell_ids`, `capabilities`,
`durable_inputs` ja `child_cell_ids` ovat sen sijaan tarkoituksella
järjestettyjä sekvenssejä: niiden järjestys kuvaa reititys-, korjaus-,
kyvykkyys- tai rebuild-prioriteettia ja muuttaa cell rootia. Luontiaika,
paikallinen checkout-polku ja replikan mount-polku eivät muuta
`hive_state_root`ia. Vaaditun suhteellisen sijoituspolun, lähteen,
checkpointin, capabilityn, topologiaepochin tai commitin muutos muuttaa sitä
vastaavaa digestiketjua.

JSON-skeema ei voi todistaa näitä yhtäläisyyksiä. Python-validatorin on
laskettava digestit uudelleen ja hylättävä puuttuva, ristiriitainen tai
väärään exact commitiin kuuluva aineisto.

## Sisältöosoitteinen palautusbundle

Mutable ja Gitin ulkopuolinen aineisto paketoidaan sisältöosoitteisesti:

```text
recovery-bundle/
  hive_recovery_manifest.v1.json
  blobs/
    sha256/
      <64-lowercase-hex>
```

Blobin nimi on sen raw-byte SHA-256 ilman `sha256:`-etuliitettä. Verifier
lukee blobin, laskee digestin uudelleen ja vertaa sitä sekä polkuun että
manifestin `content_digest`-kenttään. Pelkkä tiedoston nimi, koko, mtime tai
arkiston eheä avautuminen ei riitä.

Manifestin `relative_path` ei saa olla absoluuttinen, sisältää backslashia,
tyhjää segmenttiä tai `.`/`..`-segmenttiä. Python-loaderin on lisäksi
ratkaistava lopullinen polku ja estettävä rootista poistuminen sekä
symlink-escape.

CLI:n `--expected-manifest-digest` on raw manifest -tiedoston trust anchor.
Sen täytyy tulla ehdokasbundlesta riippumattomasta luotetusta kanavasta tai
checkpointista. Ehdokasbundlesta juuri ennen ajoa laskettu arvo todistaa vain
bundlen sisäisen konsistenssin, ei sen alkuperää.

## Nykyinen local shadow-bundle -materialisointi

`tools/run_hex_blank_disk_recovery_dry_run.py` tekee yhden rajatun V1-vaiheen:

1. se hyväksyy vain paikallisen filesystem-bundlen ja torjuu UNC/device- sekä
   mapped-remote-polut;
2. se sitoo samasta vakaasta lukukerrasta raw manifest -digestin ja strict-JSON-
   sisällön, tarkistaa manifestin commit-ankkurin ja kaikki canonical digestit;
3. se validoi kaikki genome-ristiviittaukset, required capability →
   durable-input → non-Git artifact -sidonnat sekä topologiainvariantit;
4. se tarkistaa täydellisen content-addressed blob -inventaarion, koot,
   SHA-256:t, levytilan ja vaaralliset Windows-polut;
5. se kirjoittaa vain ennestään puuttuvaan kohteeseen sibling-stagingin kautta,
   rehashaa tarkan staging-inventaarion ja tekee no-overwrite-directory-
   promotionin; ja
6. se jättää stagingiin epätoden provisional-raportin sekä kirjoittaa vasta
   onnistuneen directory-promotionin jälkeen erillisen completion-markerin.

Completion-raportti kertoo
`source_commit_anchor_matched: true`,
`exact_commit_checkout_verified: false`,
`shadow_tree_materialized: true`,
`shadow_rebuild_completed: false`,
`runtime_started: false` ja `production_ready_claim: false`.
Näin onnistunut bundle-materialisointi ei esiinny end-to-end blank-disk-
palautuksena.

V1 rajaa yhden topologian 256 soluun, yhden artifactin 16 GiB:iin,
materialisoitavan kokonaisuuden 64 GiB:iin ja vaatii lisäksi 512 MiB vapaata
turvamarginaalia. Destinationin on oltava paikallinen, turvallisesti nimetty ja
ennestään puuttuva; olemassa oleva tyhjäkään hakemisto ei kelpaa
no-overwrite-porttiin.

Työkalu olettaa, ettei sama käyttöjärjestelmäkäyttäjä tarkoituksella muokkaa
bundlea tai staging-puuta tarkistuksen ja rename-operaation välisessä hyvin
lyhyessä ikkunassa. Pythonin polkupohjaisilla Windows-rajapinnoilla kaikkia
samanaikaisia NTFS race-/ADS-tapauksia ei voi poistaa. V1 kaventaa ikkunaa
same-read digest+parse -lukemisella, no-follow/exclusive-kopioinnilla,
reparse/hardlink-tarkistuksilla, lopullisella exact-inventaario+rehash-
skannauksella ja sillä, ettei tulos saa runtime- tai aktivointivaltaa.

## Täyden shadow-only-palautuksen tuleva portti

Täyden palautusketjun hyväksytty tuleva päätepiste on eristetty
`shadow_only`-pesä:

1. exact commit on checkoutattu puhtaalle persistentille C:-levylle;
2. schema- ja semanttinen validointi sekä nykyisen työkalun bundle-
   materialisointi onnistuvat;
3. kaikki vaaditut raw-byte- ja canonical digestit täsmäävät;
4. solut on rakennettu ilman live-liikennettä;
5. generic recovery-ring-, yhden juuren parent-child-, capability- ja
   state-root-invariantit täsmäävät; ja
6. palautusraportti kertoo poikkeamat ilman production-valmiusväitettä.

V1 ei vielä vaadi erillistä per-parent sibling-ring-geometriaa; se vaatii
koko recovery-graafin kaksisuuntaisuuden, yhteyden ja yhden solun menetyksen
siedon sekä hierarkialle yhden juuren, vastavuoroiset parent-child-linkit ja
syklittömyyden.

V1 säilyttää manifestoidun naapuri- ja prioriteettijärjestyksen sekä validoi
nykyiset 7- ja 8-solun graafit. Se ei vielä optimoi uuden topologian tiheyttä
eikä määrää logical overlaylle biologisesta HEX-geometriasta johdettua
max-degree/completeness-funktiota; tämä kuuluu erilliseen vertailevaan
topologiageneraattori- ja benchmark-porttiin.

Manifesti, schema, onnistunut bundle-materialisointi tai täsmäävä state root eivät anna
merge-, deploy-, transport-, runtime activation-, `claim_safe`- tai
operator-allekirjoitusvaltaa. Live-aktivointi on erillinen operator-portti.

## Mitä V1 ei vielä tee

V1 ei vielä:

- konfiguroi, salaa tai siirrä off-host-replikaatiota;
- todista, että nimetty replika on fyysisesti toisessa failure-domainissa;
- käynnistä WaggleDance-runtimea tai ohjaa tuotantoliikennettä;
- todista RPO- tai RTO-tavoitteita;
- todista production readinessia;
- tee automaattista bootstrapia fyysisen koneen tai järjestelmälevyn vaihdon
  jälkeen;
- palauta puuttuvia salaisuuksia, tunnuksia tai lisensoituja ulkoisia
  malleja; eikä
- muuta backupia ensisijaiseksi lähteeksi.

Näistä syistä väite “kone hajoaa ja pesä rakentuu varmasti automaattisesti
uudelleen” ei ole vielä V1:n todistama ominaisuus. V1 luo koneellisesti
tarkistettavan sopimuksen, jonka päälle ominaisuus voidaan toteuttaa ja
falsifioida.

## Seuraavan vaiheen porttijärjestys

1. **Current-topology manifest generator:** rakenna genomit ja hive-manifesti
   suoraan nykyisestä 7-, 8- ja shadow-topologiasta exact HEADissa.
2. **Encrypted off-host replica:** toteuta salattu, sisältöosoitteinen kopio
   kaikelle vaaditulle ei-Git-mutable statelle ja varmista riippumaton
   failure-domain.
3. **Clean-C-drive drill:** tyhjään persistenttiin C:-hakemistoon tehtävä
   clone, blobien haku, digest-varmennus ja deterministinen rebuild ilman
   alkuperäisen checkoutin apua.
4. **Neighbor-assisted rebuild:** terveet repair-peer-solut tuottavat
   puuttuvan solun input-inventaarion ja vertaavat rakennettua cell state
   rootia genomiin.
5. **Shadow verification ja operator activation:** ring- ja
   solver-kyvykkyystestit, poikkeamaraportti, rollback-valmius ja vasta sen
   jälkeen erillinen operatorin live-aktivointipäätös.

Jokainen portti on fail-closed: seuraavaan vaiheeseen ei siirrytä, jos
vaadittu artifact, digest, replika, invariantti tai exact-commit-todiste
puuttuu.
