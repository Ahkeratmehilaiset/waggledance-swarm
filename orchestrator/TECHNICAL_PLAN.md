# WaggleDance Orchestrator – tekninen suunnitelma (Vaihe 1)

Tämä dokumentti kuvaa paikallisen orkestrointijärjestelmän **ensimmäisen vaiheen**: Claude Code -ajon valvonta, tilantunnistus, lokin keräys ja checkpoint-tila. Selain-LLM-vaiheet (Claude/Gemini/Grok/GPT) tulevat vasta seuraavissa vaiheissa, kun tämä perusta on todettu robustiksi.

## 1. Suunnitteluperiaatteet

1. **Erotettu prosessi**: orkestraattori ei aja Claude Codea. Se vain *lukee* PowerShell-transcriptiä toisesta prosessista. Näin orkestraattori voidaan käynnistää, pysäyttää ja päivittää häiritsemättä Claude Code -sessiota.
2. **Tiedostot ovat sopimus**: tilamuutokset, raportit ja artefaktit kommunikoidaan tiedostojen kautta, ei näyttökuvilla, ikkunalukemisella tai signaaleilla. Tämä tekee järjestelmästä testattavan ja debugattavan.
3. **Puhdas detektoriydin**: tilantunnistus on puhdas funktio (input → tila), ei sivuvaikutuksia. Sitä voidaan testata kuvitelluilla syötteillä ilman oikeita tiedostoja tai aikoja.
4. **Idempotenssi**: jokainen vaihe voi suorittua uudestaan korruptoimatta aiempia tuloksia. Iteraatiokansiot ovat aikaleimattuja, eikä mikään vaihe kirjoita aiempien iteraatioiden päälle.
5. **Pysähdy mieluummin kuin arvaa**: epävarmoissa tiloissa orkestraattori jää odottamaan ja kirjoittaa selkeän diagnostiikan, ei jatka eteenpäin.

## 2. Arkkitehtuuri

```
+----------------------------+        +-------------------------------+
| WaggleDanceAi PS-ikkuna    |  log   | Watch-ClaudeCode.ps1          |
| - Start-Transcript -> .log | -----> | - lukee transcriptia          |
| - Claude Code (interaktiv.)|        | - kutsuu detectoria           |
+----------------------------+        | - kirjoittaa state.json       |
                                      | - kerää artefaktit lopuksi    |
                                      +---------------+---------------+
                                                      |
                              +-----------------------+--------------------+
                              v                       v                    v
                         iterations/<ID>/        state/current.json   transcripts/*.log
                         - state.json            (live-tilan kopio)   (kasvavat lokit)
                         - powershell_tail.txt
                         - raportti.md
                         - git_metadata.json
```

## 3. Kansiorakenne

Projektin juuressa (esim. `D:\WaggleDanceAi\`):

```
WaggleDanceAi/
├── orchestrator/             # Tämän vaiheen toimitus
│   ├── Watch-ClaudeCode.ps1
│   ├── Start-WaggleSession.ps1
│   ├── Test-Detector.ps1
│   ├── config.example.json
│   └── lib/
│       ├── State.ps1
│       ├── Checkpoint.ps1
│       ├── Detector.ps1
│       └── Collector.ps1
├── transcripts/              # PowerShell-transcriptit (yksi per sessio)
│   └── waggledance_2026-05-06_14-23-00.log
├── iterations/               # Yksi alikansio per ajo
│   └── 2026-05-06_14-23-00/
│       ├── state.json
│       ├── powershell_tail.txt
│       ├── raportti.md
│       └── git_metadata.json
├── state/
│   └── current.json          # Symlink/kopio uusimmasta state.json:ista
├── raportti.md               # Claude Coden tuottama raportti
└── orchestrator.config.json  # Käyttäjän oma kopio config.example.json:ista
```

Polut konfiguroidaan `orchestrator.config.json`-tiedostossa, jotta sama orkestraattori toimii myös ulkoisilta levyiltä (C:, D:).

## 4. Tilakone

```
                       +----------+
                       |   IDLE   |   (orkestraattori käynnistynyt, ei vielä iteraatiota)
                       +-----+----+
                             |
                     start watch
                             v
                       +----------+   transcript kasvaa, ei interaktiota
                       | RUNNING  | <-------------------+
                       +-----+----+                     |
              +--------------+--------------+           |
              |              |              |           |
   interaktiokysymys    PS-prompt &     ajoraja        kasvua
              |        stabiili         ylittyi        (käyttäjä vastasi)
              v              v              v           |
   +-----------------+ +-----------+ +----------+       |
   | WAITING_FOR     | | COMPLETED | | TIMEOUT  |       |
   | _USER           | +-----+-----+ +-----+----+       |
   +--------+--------+       |             |            |
            |          (terminaali)  (terminaali)       |
            +-------------------------------------------+
                             |
                             v
                       +----------+
                       |  FAILED  |   (transcript hävisi, parsetus rikki, jne.)
                       +----------+

   NEEDS_MANUAL_LOGIN_OR_CAPTCHA -- varattu vaiheelle 2 (selainautomaatio)
```

**Terminaalit**: COMPLETED, FAILED, TIMEOUT — orkestraattori kerää artefaktit ja sulkee iteraation.
**Ei-terminaali**: WAITING_FOR_USER — kirjataan, otetaan snapshot, jatketaan polling.

## 5. Tunnistusheuristiikka

Tilan tunnistus käyttää **viittä** signaalia. COMPLETED vaatii **kaksi** itsenäistä signaalia, mikä torjuu vääriä positiivisia.

| Signaali | Tarkoitus | Käyttötilanne |
|---|---|---|
| Transcript-koko muuttunut | Aktiivisuus | RUNNING |
| Stabiilius (N s ilman kasvua) | Mahdollinen valmistuminen | + alla oleva |
| Interaktioprompti tailissa | Käyttäjältä odotetaan vastausta | WAITING_FOR_USER |
| PS-prompti tailissa (regex) | Sessio palannut shelliin | + stabiilius = COMPLETED |
| Exit-marker (esim. `##WAGGLE_RUN_COMPLETE##`) | Vahvin signaali | COMPLETED yksinään |
| Aikaraja ylittynyt | Suoja jumeja vastaan | TIMEOUT |
| `raportti.md` muokattu ajon aikana | Vahvistus | Ei pakollinen, lisäsignaali |

### Suositeltu vahvistus: exit-marker

Ohjeista Claude Code aina päättämään ajot esim. komentoon
```
Write-Host "##WAGGLE_RUN_COMPLETE##"
```
tai siihen, että kirjoitat samaisen merkin `raportti.md`:n loppuun. Tämä on selvästi luotettavin signaali, koska se ei ole heuristiikkaa vaan eksplisiittistä viestintää. Detektori tukee sitä alusta asti.

### Mitä ei käytetä signaalina

- Pelkkä "tuloste hiljentyi" — Claude Code voi olla hetkellisesti vaiti analysoidessaan.
- Pelkkä exit code — emme ohjaa Claude Code -ajoa, joten sitä ei saatavilla.
- Ikkunaotsikko tai pikselihaut — hauraita.

## 6. state.json -skeema

```json
{
  "iteration_id": "2026-05-06_14-23-00",
  "started_at": "2026-05-06T11:23:00.000Z",
  "phase": "claude_code_run",
  "status": "RUNNING",
  "last_check_at": "2026-05-06T11:24:32.000Z",
  "transcript_file": "D:/WaggleDanceAi/transcripts/waggledance_2026-05-06_14-23-00.log",
  "transcript_size_bytes": 184738,
  "transcript_last_growth_at": "2026-05-06T11:24:00.000Z",
  "report_file": "D:/WaggleDanceAi/raportti.md",
  "report_last_modified": "2026-05-06T11:23:50.000Z",
  "git_branch": "main",
  "git_commit": "abc1234",
  "error": null,
  "history": [
    { "at": "...", "from": "RUNNING", "to": "WAITING_FOR_USER", "reason": "..." }
  ]
}
```

`history` säilyttää kaikki tilamuutokset, jotta voidaan jälkikäteen analysoida miksi iteraatio päättyi. `phase` on varattu tulevia vaiheita varten (selain-LLM:t, GPT-synteesi).

## 7. Konfiguraatio

`orchestrator.config.json`:

| Avain | Tarkoitus | Oletus |
|---|---|---|
| `projectRoot` | Projektin juuri (absoluuttinen polku) | (pakollinen) |
| `transcriptDir` | Transcriptien hakemisto suhteessa juureen | `transcripts` |
| `iterationsDir` | Iteraatiokansioiden hakemisto | `iterations` |
| `stateDir` | Live-state-hakemisto | `state` |
| `reportFile` | Polku raportti.md:hen suhteessa juureen | `raportti.md` |
| `tailLineCount` | Kuinka monta riviä iteraatioon talletetaan | `1000` |
| `pollIntervalSeconds` | Tarkistusväli | `5` |
| `stableThresholdSeconds` | Minimi stabiiliusaika ennen valmiiksi-luokitusta | `25` |
| `runTimeoutMinutes` | Kovan rajan timeout | `120` |
| `interactivePromptPatterns` | Regexit, jotka kertovat WAITING_FOR_USER | (lista) |
| `completedPromptPatterns` | Regexit, jotka kertovat shellin palanneen | (lista) |
| `exitMarker` | Tunnusmerkki (tyhjä = pois käytöstä) | `##WAGGLE_RUN_COMPLETE##` |

Kaikki regexit ovat .NET-yhteensopivia.

## 8. Virheiden hallinta

| Virhe | Tunnistus | Toiminta |
|---|---|---|
| Transcript-tiedostoa ei ole | `Test-Path` | `FAILED`, error-objekti, ei retryä |
| Tiedosto häviää kesken ajon | Sama kuin yllä | `FAILED` |
| state.json korruptoitunut | `ConvertFrom-Json` poikkeus | Logataan, käytetään `state.json.bak` jos olemassa |
| Aikaraja ylittyi | `runTimeoutMinutes` | `TIMEOUT`, kerätään silti artefaktit |
| Detektori ei pysty päättämään | Default-haara | Pysytään `RUNNING`, lisätään diagnostiikka signaaleihin |

Kaikki kirjoitukset state.json:iin tehdään **atomisesti**: kirjoitetaan `state.json.tmp` ja siirretään `Move-Item -Force` lopuksi. Näin keskeytys ei jätä puolikasta tilatiedostoa.

## 9. Testausstrategia

`Test-Detector.ps1` ajaa puhtaita yksikkötestejä `Get-DetectorVerdict`-funktiolle:

- Recent growth → RUNNING
- Interaktioprompti tailissa → WAITING_FOR_USER
- Stabiili + PS-prompt → COMPLETED
- PS-prompt mutta ei stabiili → RUNNING
- Aikaraja → TIMEOUT
- Exit-marker → COMPLETED
- Stabiili mutta ei prompttia → RUNNING

Testit eivät käytä tiedostojärjestelmää, kelloa tai prosesseja — ne ajetaan sekunneissa ja niitä on helppo lisätä, kun heuristiikkaa hienosäädetään.

Integraatiotestin (oikealla transcriptillä) voi tehdä manuaalisesti: aja Claude Code lyhyellä tehtävällä, käynnistä Watcher, varmista että iteraatiokansio syntyy ja `state.json` päättyy `COMPLETED`-tilaan.

## 10. Vaiheittainen käyttöönotto

1. **Tällä tasolla (Vaihe 1)**: ajojen valvonta, tilantunnistus, artefaktien keräys, state.json. Selain ei vielä mukana.
2. **Vaihe 2**: yhden LLM-palvelun (Claude Web) selainadapteri (`OpenConversation`/`SubmitPrompt`/`WaitForCompletion`/`ExtractAnswer`) → `iterations/<ID>/claude_response.md`.
3. **Vaihe 3**: Gemini-adapteri.
4. **Vaihe 4**: Grok-adapteri (yksitasoisena ensin, ei vielä syventäviä jatkoanalyysejä).
5. **Vaihe 5**: koonti + GPT-vaihe → `iterations/<ID>/next_claude_code_prompt.md`.
6. **Vaihe 6**: Grokin 3-tason syventävä silmukka.
7. **Vaihe 7**: ulompi automaatiosilmukka, joka käynnistää uuden iteraation kun edellinen on päättynyt.

Tämän dokumentin scope rajataan vaiheeseen 1.

## 11. Avoimet kysymykset / oletukset

- **Claude Code -prosessin tunnistus**: emme tunnista *mikä* CC-prosessi ajaa, vain transcriptin. Jos käyttäjä ajaa kahta CC-istuntoa rinnakkain, transcript saattaa sekoittua. Vaihe 1 olettaa yhden istunnon kerrallaan.
- **PS-prompti regex**: oletuksena `PS [A-Z]:[^>\r\n]*> *$`. Jos käyttäjällä on mukautettu prompti (esim. Oh-My-Posh), kuviota täytyy päivittää. Konfiguroitavissa.
- **Claude Coden oma sisäinen ready-prompt**: ei tällä hetkellä tunnistuksen kohteena. Suositus on käyttää exit-markeria, mutta käyttäjä voi lisätä regexin `completedPromptPatterns`-listalle.
- **Aikavyöhyke**: kaikki aikaleimat tallennetaan UTC-muodossa (ISO 8601 + 'Z'). Jos haluat paikallisajan myöhemmin näytöllä, se muunnetaan vasta esitysvaiheessa.
