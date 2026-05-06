# WaggleDance Orchestrator – Phase 1.6 (verified automation hardening)

Paikallinen PowerShell-orkestraattori, joka ajaa Claude Coden hallittuna lapsiprosessina, tunnistaa interaktiopromptit reaaliajassa, sanitoi ympäristön, redaktoi salaisuudet ja **ei merkitse iteraatiota `COMPLETED`-tilaan ennen kuin completion-sopimus on todella täyttynyt.**

> **Pääperiaate Phase 1.6:ssä:** väärän positiivisen `COMPLETED`-tilan estäminen ennen kaikkea muuta. Mieluummin liian usein `NEEDS_REVIEW` kuin yksikin valheellinen "valmis".

Lue `CHANGELOG.md` Phase 1.6:n koko muutoslista. `TECHNICAL_PLAN.md` kuvaa alkuperäisen Phase 1 -arkkitehtuurin.

## Ensisijainen käyttötapa (print-moodi)

```powershell
pwsh .\orchestrator\Invoke-WaggleIteration.ps1 `
    -ConfigPath .\orchestrator.config.json `
    -PromptFile .\prompts\next.md
```

Tämä on ainoa moodi, josta Phase 2 (selain-LLM-arviot) saa automaattisesti edetä — ja silloinkin vain jos lopputila on `COMPLETED`.

## Fallback-moodi (transcript-watcher)

Käytä vain jos print-mode ei sovi (esim. ajat Claude Codea käsin interaktiivisessa istunnossa). Aseta configissa `executionMode: "interactiveTranscriptFallback"`, käynnistä bootstrap-skripti Claude Code -ikkunassa, ja watcher toisessa:

```powershell
. .\orchestrator\Start-WaggleSession.ps1 -ConfigPath .\orchestrator.config.json
pwsh .\orchestrator\Watch-ClaudeCode.ps1   -ConfigPath .\orchestrator.config.json
```

## Tilamalli (Phase 1.6)

| Status | Merkitys | Auto-proceed Phase 2:een? |
|---|---|---|
| `RUNNING` | Ajo käynnissä | – |
| `COMPLETED` | exit 0 + valid signal + iteration_id-osuma + timestamp ikkunassa + exit marker + ArtifactValidator OK | **Kyllä** |
| `COMPLETED_UNVERIFIED` | exit 0, mutta completion contract ei täyty (esim. signal puuttuu, marker puuttuu, artefakti puuttuu) | Ei — tarkista käsin |
| `NEEDS_REVIEW_CONFLICT` | Ristiriita: completed+failed yhtaikaa, tai signal vs exit code, tai iteration_id mismatch | Ei |
| `NEEDS_MANUAL_ACTION` | Interaktioprompti tai vastaava — runner pysäytti ennen timeoutia | Ei |
| `FAILED` | Selvä epäonnistuminen (exit ≠ 0 tai eksplisiittinen failure signal) | Ei |
| `TIMEOUT` | Kovan rajan ylitys, prosessi tapettu | Ei |

Tila ratkaistaan `CompletionVerifier`-moduulissa, joka yhdistää runnerin tuloksen + signal-tiedostot + ArtifactValidatorin tarkistukset. Detektorin ennakkotila ei yksin riitä `COMPLETED`-tilan myöntämiseen.

## Turvallisuus oletuksilla

- **Bash on opt-in.** `safeMode: true`, `allowBash: false`, `allowedTools: ["Read","Write","Edit","Glob","Grep"]`, `disallowedTools: ["Bash"]`.
- **Ympäristön sanitointi päällä oletuksena.** Lapsi käynnistetään `System.Diagnostics.ProcessStartInfo`-pohjaisesti. `EnvironmentVariables`-collection nollataan ja siihen kopioidaan vain ne emoympäristön muuttujat, jotka eivät täsmää denylistiin (`*_TOKEN`, `*_SECRET`, `*_PASSWORD`, `*_API_KEY`, `*_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GITHUB_TOKEN`, `GH_TOKEN`, `AWS_*`, `GOOGLE_APPLICATION_CREDENTIALS` jne).
- **`--dangerously-skip-permissions`** on `false` oletuksena. ConfigValidator antaa varoituksen joka ajolla, jos asetettu `true`.
- **Reaaliaikainen interaktioprompti-tunnistus.** Runner pollaa stdoutia/stderriä prosessin ollessa elossa. Jos prompti havaitaan, prosessi tapetaan `KillOnInteractivePrompt: true`-asetuksella heti, ei vasta timeoutilla.
- **Lockfile on aidosti atominen.** `FileMode.CreateNew`-pohjainen luonti, GUID-`lock_id`, release vain jos lock_id täsmää. Erilliset liput `-ForceStaleLock` (kuolleen pid:n vapautukseen) ja `-DangerouslyOverrideLiveLock` (hätätapaus).
- **IterationId-validointi** estää path traversalin: regex `^[A-Za-z0-9._-]{1,80}$`, ei `..`/`/`/`\\`/`:`, ei reservoituja Windows-nimiä, ei absoluuttisia polkuja, polun containment-tarkistus.
- **Salaisuuksien redaktointi pakollinen ennen LLM-paketointia.** Anthropic/OpenAI/sk-proj/GitHub/Slack/Stripe/Google/AWS-avaimet, JWT, Bearer/Basic, password=/api_key=/.env-kuviot, Cookie-headerit, BEGIN PRIVATE KEY -lohkot. Redaction report sisältää vain laskurit.
- **Markdown-paketointi prompt-injection-tiukennettu.** Dynaaminen code-fence-pituus (pidempi kuin sisällön pisin backtick-jono), eksplisiittinen `UNTRUSTED DATA` -merkintä joka osiossa, turvallisuuspreambula paketin alussa.
- **Promptiin liitetään completion contract** joka kieltää epäluotettavan datan ohjeiden seuraamisen ja salaisuuksien siirtämisen.

## Konfiguraatio

Ks. `config.example.json`. Tärkeimmät kentät:

| Kenttä | Tarkoitus | Suositus |
|---|---|---|
| `executionMode` | `"print"` tai `"interactiveTranscriptFallback"` | `"print"` |
| `claudeCommand` | Polku/komento `claude`-binääriin | `"claude"` |
| `model` | `"opus"`, `"sonnet"` | `"opus"` |
| `outputFormat` | `"text"`/`"json"`/`"stream-json"` | `"text"` |
| `maxTurns` | `--max-turns` | `30` |
| `permissionMode` | `default`/`acceptEdits`/`plan`/`bypassPermissions` | `"default"` |
| `safeMode` | Estää Bashin oletusarvoisesti | `true` |
| `allowBash` | Eksplisiittinen Bash-opt-in | `false` |
| `allowedTools` / `disallowedTools` | Työkalu-listat | `["Read","Write","Edit","Glob","Grep"]` / `["Bash"]` |
| `dangerouslySkipPermissions` | Vaarallinen | `false` |
| `sanitizeEnvironment` | Strippaa salaisuusmuuttujat lapselta | `true` |
| `envDenylist` | Lisätyt regexit denylistiin (`null` = oletus) | `null` |
| `envAllowList` | Lisättyjä variableja, jotka aina läpä