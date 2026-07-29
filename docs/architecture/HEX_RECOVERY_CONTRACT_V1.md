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
| `hierarchical_runtime_shadow` | Skeema ja validointisopimus ovat olemassa, mutta nykyiselle instanssille ei vielä ole auktoritatiivista tracked-lähdettä | Subdivisionin ja jälleenrakennuksen shadow-harjoittelu |

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

## Exact-Git current-topology -bundlegeneraattori

`tools/build_hex_recovery_bundle.py` tuottaa nykyisestä exact HEADista yhden
deterministisen source-only-bundlen:

```powershell
python tools\build_hex_recovery_bundle.py `
  --expected-head <40-lowercase-hex> `
  --out-dir <ennestään-puuttuva-paikallinen-hakemisto> `
  --json
```

Generaattori ei lue topology- tai state-inputteja working treestä, indexistä,
`data/`- tai `models/`-hakemistoista eikä live-ledgeristä. Se hyväksyy vain
annetun SHA:n kanssa yhtäpitävän `HEAD`in, poistaa perityt `GIT_*`-ohjaukset,
estää replace-object- ja lazy-fetch-käytön ja lukee sallitut topology-inputit
suoraan commitin tree- ja blob-objekteista. Jokainen lähde saa raw-byte
SHA-256:n; commit- ja blob-objektien Git-identiteetit lasketaan lisäksi
uudelleen. Historiallista topology-Python-moduulia ei importata eikä suoriteta.

CLI tarkistaa ennen buildiä ja uudelleen ennen kirjoitusta, että ajettava
generaattori sekä importoidut recovery-contract- ja canonical-digest-moduulit
tulevat odotetuista repo-poluista ja vastaavat exact HEADin Git-blob-tavuja
(CRLF/LF-tekstinormalisointi sallitaan). Näin dirty working tree ei voi
huomaamatta vaihtaa manifestin tai digestien rakentajaa, vaikka topology-data
itsessään luetaan jo commit-objekteista.

Sallitut nykyiset lähteet ovat:

- `configs/hex_cells.yaml`,
  `waggledance/core/domain/hex_mesh.py` ja
  `waggledance/application/services/hex_topology_registry.py` 7-solun
  aksiaaliverkolle;
- `waggledance/core/hex_cell_topology.py` ja
  `core/symbolic_solver.py` 8-solun logical overlaylle; sekä
- kaikki exact commitin `configs/axioms/**/*.yaml`-tiedostot.

YAML parsitaan ilman PyYAML:n Python-objektikonstruktoreita. Duplicate keyt,
alias-, anchor-, merge- ja explicit-tag-rakenteet, epäkanoniset scalarit sekä
ei-finiittiset luvut torjutaan. Python-topologioista poimitaan rajattu
deklaratiivinen AST-projektio: `AXIAL_DIRECTIONS` sekä
`CELL_*`/`ALL_CELLS`/`_ADJACENCY`. Näiden deklaratioiden arvoilta vaaditaan
literal-muoto, eikä parseri suorita historiallista Pythonia. Parserin
mutation- ja namespace-guardit ovat vain puolustava lisäraja: ympäröivän
yleisen Python-lähteen runtime-semanttista puhtautta ei päätellä
AST-heuristiikalla. V1 pinnaa siksi molempien suoritettavaa Pythonia
sisältävien topology-lähteiden tarkistetut raw SHA-256-digestit. Yhdenkin
tavun muutos tiedostoissa
`waggledance/core/domain/hex_mesh.py` tai
`waggledance/core/hex_cell_topology.py` pysäyttää buildin virheeseen
`topology_source_revision_unreviewed`, kunnes lähde, projektio ja digest-pin
on katselmoitu yhdessä.

Tämän checkpointin tulos sisältää:

- `agent.axial`: 7 genomia, aksiaalisuuntien mukainen naapurijärjestys ja
  laskettu halkaisija 2;
- `solver.logical`: 8 genomia, runtimea vastaava järjestetty
  naapurijärjestys ja laskettu halkaisija 3;
- kaikki 22 tracked aksiomia niiden eksplisiittisten ja yksikäsitteisten
  `model_id`- ja `cell_id`-kenttien perusteella; sekä
- 15 `verified_copy`-genome-artifactia ja nolla replikaa tai mutable-state-
  artifactia.

Jokaisella required capability -lähteellä on vastaava
`repo_artifact`/`git_checkout`-input. Agenttisolut sitovat config-, geometry-
ja registry-lähteet; solver-solut sitovat overlayn, symbolic solver -enginen ja
vain omalle `cell_id`:lleen nimetyt aksiomit. `repair_peer_cell_ids` sisältää
kaikki ring-1-naapurit. Se on korjausaikomus ja reitityssopimus, ei todiste
siitä, että peer todella säilyttää tavut eri hostilla tai levyllä.

Capability-rivit ovat digest-sidottuja source-inventory-deklaraatioita, eivät
todiste siitä, että jokainen solver antaa oikean funktionaalisen vastauksen
palautuksen jälkeen. Generaattori tarkistaa registry- ja solver-lähteiden
vaaditun class/method-pinnan sekä aksiomien vähimmäisrakenteen
(`formulas`, `variables`, `solver_output_schema`), mutta se ei suorita
aksiomeja. CLI lukitsee siksi
`functional_capability_recovery_verified: false`- ja
`mutable_state_coverage_complete: false` -kentät. Myös
`source_inventory_complete` pysyy epätotena writer-rajalla, koska writer
hyväksyy minkä tahansa contract-validin bundle-kuvan eikä voi yksin todistaa,
että sen kutsuja käytti exact-Git-lukijaa. Sen sijaan
`bundle_artifact_inventory_complete: true` tarkoittaa vain writerin todella
todistamaa manifestin ja genome-blobien täsmällistä artifact-joukkoa.
Funktionaaliset capability-kohtaiset probe-oracle-testit kuuluvat
neighbor-assisted shadow-rebuild -porttiin.

Commitin committer-epoch normalisoidaan sekuntitarkkaan UTC `Z` -muotoon.
Topology-epoch on kanonisen semanttisen graafiprojektion sisältöjohdettu
63-bittiseen rajaan mahtuva identiteetti; se ei väitä olevansa runtime-
kronologian laskuri. Samalla commitilla tuotetut manifesti- ja genome-tavut
ovat toistettavia käyttöjärjestelmän newline-asetuksista riippumatta.

Output kirjoitetaan vain paikallisen, ennestään puuttuvan kohteen
sibling-stagingiin exclusive/no-follow-kirjoituksilla. Source-repo,
sen `.git`-metadata ja kaikki muut source-repon alihakemistot ovat kiellettyjä
output-kohteita. Ennen atomista no-replace-promotionia generaattori varmistaa
tarkan inventaarion, tyypit, koot ja digestit. Bundleen ei lisätä
raporttitiedostoa:

```text
<out-dir>/
  hive_recovery_manifest.v1.json
  blobs/
    sha256/
      <15 raw-genome-digestiä>
```

Jos directory-durabilityä ei voida todistaa, CLI ei yritä destruktiivista
rollbackia eikä väitä jo promotoitua kohdetta puuttuvaksi. Se palauttaa
exit-koodin 3 sekä kentät `bundle_complete: true`,
`promotion_completed: true`, `directory_durability_verified: false` ja
virheen. POSIX-alustalla destination-parentin viimeisen `fsync`-kutsun
epäonnistuminen tuottaa
`error: parent_directory_fsync_failed_after_promotion`.

V1 ei toteuta Windowsille tuettua directory-`fsync`- tai muuta vastaavaa
crash-durability-barrieria. Siksi Windowsilla no-op ei saa koskaan muuttua
onnistumisväitteeksi: `staging_directory_fsync_completed: false`,
`parent_directory_fsync_completed: false`,
`directory_durability_verified: false` ja
`error: directory_fsync_unavailable_after_promotion`. Tiedostosisällöt on
silti `fsync`attu, bundle näkyy atomisen promotion jälkeen ja sen sisältö on
rehashattu, mutta crash-durability vaatii uuden varmennuksen tai myöhemmän
todistetun Windows-barrierin.

CLI tulostaa raw manifest -tiedoston
`candidate_manifest_file_digest`-arvon, mutta samalla eksplisiittisesti
`candidate_digest_is_independently_trusted: false`. Arvo pitää siirtää
riippumattomaan luotettuun kanavaan ennen kuin sitä käytetään myöhemmän
materialisoinnin `--expected-manifest-digest`-ankkurina.

Generaattori ei lisää `hierarchical_runtime_shadow`-topologiaa. Repolla on
hierarkian validointikoodi ja proof-fixtureitä, mutta ei yhtä tracked,
auktoritatiivista nykyisen runtime-hierarkian lähdettä. Fixturen nimeäminen
nykyiseksi topologiaksi keksisi arkkitehtuuria. Siksi CLI raportoi
`authoritative_hierarchical_runtime_shadow_available: false`,
`hierarchical_runtime_shadow_included: false` ja
`target_state_topology_coverage_complete: false`. Tämä ei estä 7- ja
8-solun exact-Git-genomien käyttämistä seuraavan portin inputtina, mutta se
estää kutsumasta bundlea koko Image #1 -tavoitetilan topologiaksi.

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

1. **Current-topology manifest generator:** exact-Git-generaattori tuottaa nyt
   nykyiset auktoritatiiviset 7- ja 8-solun genomit sekä hive-manifestin.
   Lisää hierarchical shadow vasta, kun sen konkreettinen tracked-lähde on
   määritetty; proof-fixtureä ei saa ylentää tuotantototuudeksi.
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
