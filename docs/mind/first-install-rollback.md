# Mindin ensimmäisen sisältöhaun asennus ja palautus

Operaattori tekee asennuksen ja palautuspäätöksen. Lead harjoittelee lopullisen paketin kopiossa, Tools tarkistaa resurssit ja fable-5 mittaa asennetun lukijan. Palautettava bundle on `29a356a99b79ca00a10b69edd080f2de4532693d` ja sen manifestin SHA-256 on `5A54839D79FDFBF0125281C35431C1A45F7EC1F3474054ABA4AE67F013DA942C`.

### Julkaisukuitti

Kokoa kuitti, kun asennusavut ja lähteen kokorajan muutos ovat molemmat mainissa. Käytä tarkasta asennuscommitista tuotetun Git-arkiston apureita. Niiden hakemisto on `ops/windows/mind-operator`. Säilytä täsmäcommit, apurien tarkisteet sekä asennuksen ja palautuksen kuitit operaattorin hallussa. Aja komennot Windows PowerShell 5.1:ssä ja pysäytä menettely heti, jos odotettu kuitti puuttuu.

- Vertaa mallihakemiston ja Python-hakemiston nimet, tiedostokoot ja tiivisteet hyväksyttyihin luetteloihin. Sido hakemistojen identiteetit asennuscommitin kuvaimiin. Laske työntekijäohjelman tiiviste saman commitin arkistosta.
- Kirjaa `descriptor-set.json`-tiedoston täydellinen polku ja SHA-256 sekä sen viiden sisartiedoston tarkisteet: `wd-mind-runtime-manifest.json`, `wd-mind-interpreter-identity.json`, `wd-mind-e5-encoder-binding.json`, `wd-mind-runtime-location.json` ja `wd-mind-message-policy.json`. Johda viestipolitiikka uudelleen lopullisesta lukijasta. Hyväksytyn politiikan SHA-256 on `2550bc158309b7b732eb8a19129d67d0f352e0b2c4f0d53fc7901ccd135be5e3`. Yksityisen tunnistepolitiikan sisältö pysyy operaattorilla.
- Kirjaa lopullisella paketilla tehdyn kopioharjoituksen kuitti. Siihen kuuluvat asennus molemmilla sovituilla ohitusvalitsimilla, yhden kaistan onnistunut lähdetarkistettu haku, keskeytystapaukset ja kaikkien 25 palautuskohteen tavujen tarkistus. Säilytä molemmat bundlet ja varmista vapaa tila.
- Käytä hyväksyttyä manuaalisen suorituksen aktivointitietuetta ja tunnistepolitiikan erillistä hyväksyntäviitettä. Julkaisuun sidottu manifestitiiviste muodostuu vasta StageOnly-ajossa.

Vie apurit arkistoon seuraavalla komennolla. Aseta `$installCommit` hyväksytyksi täydelliseksi asennuscommitiksi ja `$archive` uuden zip-tiedoston poluksi. Valitsin säilyttää Gitissä olevat rivinvaihdot, joista alla olevat tarkisteet on laskettu.

```powershell
git -c core.autocrlf=false archive --format=zip --output=$archive $installCommit -- ops/windows/mind-operator
if ($LASTEXITCODE -ne 0) { throw 'Apurien vienti epäonnistui' }
```

### Lähtötila

Varmista Supervisor OFF, lopeta aktiiviset kirjoitukset hallitusti ja käytä samaa Windows-tiliä, jolla Mindin omistajaprosessi ajetaan. Aseta `$helpers` tarkistetun Git-arkiston apurihakemistoksi ja `$privateBaseline` uudeksi tiedostoksi olemassa olevaan, käyttöoikeuksiltaan rajattuun hakemistoon **C:\Python-hakemiston ulkopuolella**. Luettelo sisältää paikallisia suojaustietoja. Julkaise kuitista vain tarkisteet ja tulos.

```powershell
$ErrorActionPreference = 'Stop'
$ps51 = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$before = 'C:\Python\WD_REBOOT_STATE_CURRENT.json'
$inventory = Join-Path $helpers 'Get-WdMachineRootInventory.ps1'
$rollback = Join-Path $helpers 'Restore-WdMindOperatorSnapshot.ps1'
$cache = Join-Path $helpers 'New-WdMindCacheFolders.ps1'
$pins = @{
    $inventory = 'EB3FE1078004CF2E68C4CD9C1799D1A6871416D9410DC48855A6369F23F729C8'
    $rollback = '625238A34BF4768B8E756A42B8CA842C32A555E99D47D94C0060605EA03C9328'
    $cache = 'D009DA4A9FCC21679AB60832EEDEE0341CCB6C1302C326C7F22E6EF9E89A55BD'
}
foreach ($path in $pins.Keys) {
    if ((Get-FileHash -LiteralPath $path).Hash -cne $pins[$path]) { throw 'Apurin tarkiste poikkeaa' }
}
& $ps51 -NoProfile -NonInteractive -File $inventory -Mode Save -MachineRoot 'C:\Python' -Out $privateBaseline
if ($LASTEXITCODE -ne 0) { throw 'Tarkista lähtötilan 25 tiedostoa' }
$oldPointerSha = (Get-FileHash -LiteralPath $before).Hash
$snapshotRoot = Join-Path 'C:\Python\wd-reboot-backups' ('mind-pr2179-' + [guid]::NewGuid().ToString('N'))
$out = @(& $ps51 -NoProfile -NonInteractive -File $rollback -Phase Snapshot -Mode Production -SnapshotRoot $snapshotRoot)
if ($LASTEXITCODE -ne 0 -or $out.Count -ne 1 -or $out[0] -cnotmatch '^SNAPSHOT_SHA256=([A-F0-9]{64})$') {
    throw 'Palautuskopion kuitti puuttuu'
}
$snapshotSha = $Matches[1]
if ((Get-FileHash (Join-Path $snapshotRoot 'snapshot.json')).Hash -cne $snapshotSha -or
    (Get-FileHash (Join-Path $snapshotRoot 'WD_REBOOT_STATE_CURRENT.json')).Hash -cne $oldPointerSha -or
    (Get-FileHash $before).Hash -cne $oldPointerSha) { throw 'Lähtötilan ankkurit poikkeavat' }
```

Kirjaa snapshotin polku ja SHA-256, vanhan osoittimen SHA-256 ja yksityisen luettelon SHA-256 erilliseen kuittiin ennen aktivointia. Luettelon on tässä ensiasennuksessa sisällettävä kaikki 25 olemassa olevaa tiedostoa. Selvitä puuttuva, varattu tai odottamaton kohde ennen jatkamista.

### Asennus ja koeajo

Aja hyväksytty välimuistiapurikomento kerran, kun hakemistot puuttuvat.

```powershell
& $ps51 -NoProfile -NonInteractive -File $cache -Mode Production
if ($LASTEXITCODE -ne 0) { throw 'Tarkista välimuistiapurin tilarivi ja luodut hakemistot' }
```

Onnistumisen tunnus on `both_created`. Käytä uudelleenkäynnistyksessä jo tarkistettua välimuistia. Jos apuri raportoi osittain luodut hakemistot, selvitä niiden tila ja oikeudet ennen jatkamista.

Aseta seuraavat muuttujat julkaisukuitista. `$installer` on puhtaan, mainiin yhdistetyn Git-työpuun `ops/windows/reboot/Deploy-WdRebootBundle.ps1`. Pidä molemmat ohitusvalitsimet käytössä myös kopioharjoituksessa. Kopiossa anna lisäksi `-MachineRoot` ja `-BundleStore` kopion polkuina kummassakin vaiheessa.

```powershell
$params = @{
    SkipTaskRegistration = $true
    SkipGrokResolve = $true
    MindRuntimeDescriptorSetPath = $descriptorSetPath
    MindIdentifierPolicyPath = $identifierPolicyPath
    MindApprovalReferenceSha256 = $policyApprovalSha
    ManualExecutionApprovalRecordPath = $manualExecutionRecord
}
& $installer @params -StageOnly
```

Vertaa tulosteen `final pair to pin` -rivin committia hyväksyttyyn main-committiin ja tarkista staged-manifestin tavut. Kirjaa pari operaattorin kuittiin. Käytä kuitin arvoja asennukseen sen hyväksymisen jälkeen.

```powershell
& $installer @params -ExpectedFinalCommit $installedCommit -ExpectedFinalManifestHash $installedManifestSha
```

Tee hallittu uudelleenkäynnistys ennen ensimmäistä indeksirakennusta. Käynnistä yksi Codex-kaista ja omistajaprosessi hyväksytyillä manifesti-, Python- ja politiikkapinneillä. Säilytä omistajan istunto. Ensimmäisen koehaun kuitissa on oltava onnistunut paluukoodi, vähintään yksi lähderivistään tarkistettu osuma, sen tapahtumaviite, ikä ja mitattu kokonaisaika. `STARTING` voi näkyä myös palvelun vastatessa. Jos hyväksyttyä hakukuittia ei saada neljässä tunnissa, pysäytä laajennus ja säilytä käynnissä oleva rakennus operaattorin tarkistettavaksi. Mittaa toisen käynnistyksen kesto ja varmista, että muuttumattomia rivejä upotettiin uudelleen nolla. Fable-5 kirjaa todellisen kontekstitoimituksen jokaiselta viideltä kaistalta erikseen. Tarkista PR-vetot bridgen porttityökaluilla, sillä katselmoijien rivit jäävät sisältöhaun ulkopuolelle.

Kirjaa omistajan ja upotustyöntekijöiden muistihuiput sekä välimuistin koko. Tunnista mitattava prosessi PID:n ja käynnistysajan perusteella. Jos vain käynnistävän PowerShell-prosessin tunniste on tiedossa, merkitse Python-omistajan prosessimittaus tilaan `UNKNOWN`.

Toimita julkaisukuitin mukana tarkistetut omistajan käynnistys- ja sulkemiskomennot, nimetyn kaistan mittauskomento sekä mittausapurien tarkisteet. Anna niille asennetun bundlen ja sen manifestin tarkiste, yksityisen politiikan polku ja tarkiste, hyväksyntäviitteen tarkiste sekä varmennetun bridge-Pythonin polku ja tarkiste. Kirjaa hyväksytystä käsikyselystä paluukoodi, osuman bridge-viite ja ikä sekä kulunut kokonaisaika. Säilytä kunkin kaistan omassa istunnossa havaittu tulos erillisenä todisteena.

### Palautus

Palautus on käsitoimi. Varmista, että kirjoittajat on hallitusti pysäytetty. Lue `$snapshotRoot`, `$snapshotSha`, `$oldPointerSha`, `$installedCommit` ja `$installedManifestSha` erillisistä ennen tapahtumaa säilytetyistä kuiteista ja tarkista apurin SHA-256 kuten edellä. Valitse täsmälleen yksi tapaus:

1. **Osoitin vastaa hyväksyttyä uutta paria.** Aja tavallinen palautus. Sama reitti koskee keskeytystä, jos uusi osoitin on edelleen ehjä.
   ```powershell
   $result = @(& $ps51 -NoProfile -NonInteractive -File $rollback -Phase Restore -Mode Production -SnapshotRoot $snapshotRoot -ExpectedSnapshotSha256 $snapshotSha -ExpectedCurrentCommit $installedCommit -ExpectedCurrentManifestSha256 $installedManifestSha)
   ```
2. **Osoitin vastaa täsmälleen vanhan osoittimen ennen asennusta kirjattua tavutiivistettä.** Käytä vanhan osoittimen reittiä operaattorin palautuspäätöksellä.
   ```powershell
   $result = @(& $ps51 -NoProfile -NonInteractive -File $rollback -Phase Restore -Mode Production -SnapshotRoot $snapshotRoot -ExpectedSnapshotSha256 $snapshotSha -RecoverOldPointer -ExpectedOldPointerSha256 $oldPointerSha)
   ```
3. **Osoitin puuttuu, on vajaa tai poikkeaa molemmista.** Pysäytä palautus ja pidä käynnistimet suljettuina. Säilytä molemmat bundlet, snapshot ja loki operaattorin tilaselvitykseen.

Tarkista palautus heti samassa istunnossa:

```powershell
if ($LASTEXITCODE -ne 0 -or $result[-1] -cne 'RESTORE_CANDIDATE_COMPLETE') { throw 'Palautus kesken' }
$rows = @($result | Where-Object { $_ -clike 'PROCESSED *' })
if ($rows.Count -ne 25 -or $rows[-1] -cne 'PROCESSED WD_REBOOT_STATE_CURRENT.json') { throw 'Palautusjärjestys poikkeaa' }
$snapFile = Join-Path $snapshotRoot 'snapshot.json'
if ((Get-FileHash $snapFile).Hash -ine $snapshotSha) { throw 'Snapshotin ankkuri poikkeaa' }
foreach ($entry in (Get-Content $snapFile -Raw -Encoding UTF8 | ConvertFrom-Json).files) {
    $name = Join-Path 'C:\Python' $entry.name
    if (-not $entry.present -or (Get-Item $name).Length -ne $entry.length -or
        (Get-FileHash $name).Hash -ine $entry.sha256) { throw 'Palautetun tiedoston tarkistus epäonnistui' }
}
if ((Get-FileHash $before).Hash -ine $oldPointerSha) { throw 'Palautetun osoittimen tarkiste poikkeaa' }
```

Varmista palautetun bundlen eheys ja Supervisor OFF ennen hallittua käynnistystä. Tiedostojen palautus voi keskeytyä kirjoitusten välissä. Jos jälkitarkistus epäonnistuu, jätä käynnistimet suljetuiksi ja ilmoita operaattorille viimeinen kuitti sekä virhe. Hakemistojen käyttöoikeudet, tiedostoidentiteetit ja kirjoitusajat tarkistetaan erikseen. Inventaarion vertailukoodi 5 tarkoittaa, että sisältö täsmää mutta identiteetti tai aika muuttui.
