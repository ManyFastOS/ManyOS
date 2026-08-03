# Many Ingest v0.1 — CTO-beoordeling: klaar voor dagelijks gebruik door een niet-technische editor?

**Status:** Analyse — geen code aangepast
**Rol:** CTO ManyFast
**Datum:** 2026-08-03
**Aanleiding:** de eerste echte ingest is succesvol geweest en camera-herkenning is
inmiddels bruikbaar op echte footage (FX6/FX3 via bevestigde signalen). Dat is een
andere vraag dan: kan een editor zonder technische kennis dit morgen zelfstandig
en veilig gebruiken?

**Kort antwoord: nee, nog niet.** Niet omdat er iets kapot is — v0.1 doet precies
wat het bouwplan beloofde. Maar "veilig door een niet-technische editor" was
expliciet **geen doel van v0.1** (zie `CLAUDE.md`: "Maak nog geen UI"). Dit document
zet op een rij wat daar wél voor nodig is, gerangschikt naar risico, niet naar
toevallige volgorde van ontdekking.

---

## 0. Het fundamentele gat: er is geen interface voor niet-technische gebruikers

Dit is niet één punt in een lijstje — dit overkoepelt alles hieronder. Many Ingest
is vandaag:

```
cd modules/many_ingest
.venv/bin/many-ingest run --source "/Volumes/..." --client "..." --project "..." --dry-run --config ... --camera-profiles ...
```

Dat vereist: Terminal kunnen openen, weten wat een pad is, een virtualenv-pad
onthouden, flags correct typen, en zelf onthouden om eerst `--dry-run` te draaien.
Geen enkele van de punten hieronder maakt dit alleen op zichzelf "klaar" — zonder
een simpelere interface (zelfs een dubbelklikbaar script met een paar prompts is al
genoeg) blijft dit een tool voor iemand die er nu al mee werkt, niet voor "een
editor zonder technische kennis". Dat hoort een bewuste volgende stap te zijn, geen
bijvangst.

---

## 1. Blokkerend voor niet-technisch dagelijks gebruik

Deze moeten opgelost zijn vóórdat iemand zonder technische achtergrond dit
zelfstandig gebruikt — los van de interface-vraag hierboven:

- ~~Geen bescherming tegen bestandsnaam-botsingen op de bestemming.~~ **Opgelost
  (2026-08-03).** Een bestaand doelbestand wordt nooit meer overschreven: zelfde
  checksum → behandeld als duplicaat, andere checksum → automatische `_001`/`_002`-
  suffix. Zie `MANY_INGEST_BUILD_PLAN.md`, sectie 5, stap 8.
- ~~Stilzwijgend falende afhankelijkheid (ffmpeg).~~ **Opgelost (2026-08-03).**
  `IngestService.run()` stopt nu onmiddellijk met een duidelijke foutmelding
  (`brew install ffmpeg`) als ffprobe ontbreekt — vóór er iets anders gebeurt.
- ~~Geen voortgangsindicatie.~~ **Opgelost (2026-08-03).** Elke verwerkte bestand
  toont voortgang (verwerkt/totaal, percentage, huidig bestand, cumulatieve
  grootte) via een herbruikbare `progress_callback`.
- ~~Geen duidelijk "klaar, veilig om te ontkoppelen"-signaal.~~ **Grotendeels
  opgelost (2026-08-03).** Er verschijnt nu een leesbaar eindrapport met expliciet
  "Veilig om bronmedia te verwijderen: JA/NEE" (nooit "JA" bij een dry-run of als
  er fouten waren), ook weggeschreven als tekstbestand naast het logbestand. Nog
  niet opgelost: dit staat in de terminal/een bestand, niet op een plek die een
  niet-technische gebruiker vanzelf ziet vóórdat hij de schijf loskoppelt — dat
  wacht nog op de interface uit sectie 0.
- **Geen vangnet tegen per ongeluk een echte run i.p.v. dry-run.** Nog steeds
  open — dit was expliciet niet in scope van de huidige verbeteringsronde. Het
  enige verschil is nog altijd een vlag die de gebruiker moet onthouden.

---

## 2. Risico's die sowieso eerst opgelost moeten worden, ook voor technisch gebruik

Deze zijn niet uniek voor "niet-technische gebruiker" — ze zijn nu al een risico,
en worden erger naarmate meer mensen dit gebruiken:

- **Geen locking op de JSON-based ManyFast Asset Schema.** Al benoemd in
  `MANY_INGEST_STORAGE_LAYOUT.md` als bekend risico, maar nog steeds niet
  opgelost: twee gelijktijdige runs (twee mensen, of twee ingest-stations op
  dezelfde externe schijf) kunnen elkaars schema-schrijfactie overschrijven.
  Zonder een `--dry-run`-vangnet en zonder locking is dit met meerdere gebruikers
  een kwestie van tijd, niet van "als".
- **Geen afhandeling van een onderbroken run** (schijf losgekoppeld, proces
  gekilld, stroomstoring midden in een kopieeractie). Er is geen opruiming van een
  half-gekopieerd bestand op de bestemming, en geen duidelijke manier om te zien
  welke bestanden wél/niet succesvol zijn afgerond zonder de JSON-lines-log met de
  hand te lezen.
- **Geen bescherming tegen tikfouten in klant/project.** "ManyFast" vs "Manyfast"
  vs "Many Fast" worden drie verschillende mappen — geen validatie tegen een lijst
  bekende klanten/projecten, geen suggestie/autocomplete. Dit fragmenteert
  footage stilletjes over meerdere mappen zonder dat iemand het meteen merkt.

---

## 3. Vertrouwens-/nauwkeurigheidsgaten in de camera-herkenning

**~~Generieke MP4-bestanden classificeren als FX3~~ — OPGELOST (2026-08-03).**
`container_contains: ["mp4"]` op het Sony FX3-profiel matchte vrijwel elk generiek
MP4-bestand, niet alleen Sony XAVC-MP4 — bevestigd met een puur ffmpeg-gegenereerd
testbestand zonder enige Sony-tag (`major_brand: isom`) dat desondanks als "Sony
FX3" met **hoge** confidence classificeerde. Dit was geen "Onbekend"-geval zoals de
rest van deze sectie, maar een **actieve misclassificatie met hoge confidence** —
het risicovolste punt in deze sectie.

**Fix:** MP4 mag nooit op zichzelf een HIGH-confidence signaal zijn — het is een
vrijwel universeel containerformaat, geen Sony-specifiek kenmerk. Het nieuwe,
generieke, opt-in profielveld `container_requires_brand: true` voorkomt dit:
wanneer gezet, telt `container_contains` alleen mee als `brand_contains` ook
matcht. `sony_fx3` wordt nu dus alleen herkend via de **combinatie** XAVC-brand +
MP4-container, nooit via MP4 alleen. `sony_fx6` blijft ongewijzigd zelfstandig
werken op `container_contains: ["mxf"]`, omdat MXF — in tegenstelling tot MP4 —
binnen ManyFast's eigen apparatuur smal en specifiek genoeg is (bevestigd over 6
crew-gelabelde mappen, geen enkele uitzondering) om geen aanvullend brand-signaal
nodig te hebben. Geen nieuwe hardgecodeerde camera-uitzondering in code — het
mechanisme is generiek, elk toekomstig profiel kan hetzelfde opt-in gebruiken. Zie
`docs/MANY_INGEST_CAMERA_PROFILES_V2_PROPOSAL.md` voor de volledige analyse en
`CLAUDE.md` voor de vastgelegde beslissing.

De rest van deze sectie is geen veiligheidsrisico (het systeem gokt niet, valt
terecht terug op "Onbekend"), maar wel relevant voor "dagelijks gebruik": hoe vaker
"Onbekend" verschijnt, hoe minder een editor het systeem vertrouwt.

- **DJI en GoPro zijn nog steeds volledig ongevalideerd** — nul echte bestanden
  van die apparaten gezien tot nu toe. De patronen zijn illustratief giswerk.
- **Openstaand: Sony A7IV gebruikt mogelijk dezelfde XAVC-MP4-workflow en kan
  verdere validatie vereisen.** Als de A7IV ook XAVC-brand + MP4-container met een
  vergelijkbaar `C####`-patroon blijkt te gebruiken, zou een A7IV-bestand nog
  steeds als FX3 geclassificeerd kunnen worden — de zojuist doorgevoerde fix lost
  specifiek "matcht willekeurig alles" op, niet deze kleinere, resterende
  onzekerheid tussen twee specifieke Sony-modellen. Nog steeds bewust geaccepteerd,
  niet weggenomen (zie `MANY_INGEST_CAMERA_PROFILES_V2_PROPOSAL.md`, sectie 7).
- **Het FX3/FX6-containersignaal is een workflow-conventie, geen hardware-feit.**
  Betrouwbaar zolang de crew consistent blijft — een aannemelijke aanname, geen
  garantie. Als iemand ooit een camera-instelling wijzigt, misclassificeert dit
  signaal stilletjes in plaats van "Onbekend" te tonen.
- **Hernoemde/geëxporteerde bestanden blijven per definitie onherkenbaar.** Geen
  fix lost dit op — dit is een proces-vraag (niet hernoemen vóór ingest), geen
  technische.

---

## 4. Operationele volwassenheid

Dingen die "het werkt bij mij" scheiden van "het werkt betrouwbaar voor het hele
team, structureel":

- **Geen distributie-/installatiemechanisme.** Vandaag is er een handmatig
  opgezette virtualenv op één Mac. Geen manier om dit naar een andere Mac of een
  nieuwe collega te krijgen zonder dat iemand technisch de installatiestappen
  herhaalt.
- **Geen update-mechanisme voor `camera_profiles.yaml`.** Elke verbetering
  (zoals deze week s FX3-fix) moet nu handmatig gekopieerd worden naar elke plek
  waar Many Ingest draait.
- **Geen centraal overzicht over meerdere operators/machines heen.** De
  ManyFast Asset Schema en actielogs zijn lokaal (of op de gedeelde externe
  schijf) per run — er is geen plek waar iemand in één oogopslag ziet "wat is
  deze week binnengekomen, door wie, met welke classificatie-issues".
- **Geen geautomatiseerde tests tegen productie-achtige edge cases**: extreem
  lange bestandspaden, ongebruikelijke tekens in bestandsnamen, zeer grote
  bestanden (19GB+), een schijf die tijdens een run volloopt. De huidige 40 tests
  dekken de logica goed, maar niet deze operationele randgevallen.

---

## 5. Wat ik als eerste zou oppakken (v0.2-prioriteit)

Getoetst aan VISION.md's Decision Filter — niet alles hierboven is nu de moeite
waard, sommige dingen wel. **Bijgewerkt na de veiligheidsronde van 2026-08-03:**

1. ~~Botsingsbescherming op de bestemming~~, ~~ffmpeg-afhankelijkheidscheck~~,
   ~~voortgangsindicatie~~ — **alle drie opgelost**, zie sectie 1.
2. ~~De `container_contains`-misclassificatiebug in sectie 3 fixen.~~ **Opgelost
   (2026-08-03)** via `container_requires_brand`. Zie sectie 3.
3. **Vangnet tegen per-ongeluk-echte-run** (sectie 1) — nog steeds open, nog
   steeds klein.
4. Dán pas, en apart getraceerd: **een simpele interface** (sectie 0) — dat is het
   grootste stuk werk en verdient een eigen implementatieplan, niet iets wat
   "erbij" gebeurt.

Wat ik bewust **niet** als eerste zou doen: DJI/GoPro/A7IV verder valideren zonder
echte bestanden (giswerk blijft giswerk, zie eerdere proposal-documenten), en geen
multi-user/locking-oplossing bouwen vóórdat er daadwerkelijk meerdere gelijktijdige
gebruikers zijn — dat is nu nog premature complexiteit.

---

## Openstaand

1. Akkoord op de prioritering in sectie 5, of een andere volgorde.
2. Bevestigen: is "een editor zonder technische kennis" al voor v0.2 het doel, of
   pas later — dat bepaalt of sectie 0 nu al meegepland moet worden.
3. ~~Akkoord om de `container_contains`-bug te fixen~~ — **opgelost (2026-08-03)**.
4. **Sony A7IV blijft openstaand:** gebruikt mogelijk dezelfde XAVC-MP4-workflow als
   FX3 en kan verdere validatie vereisen zodra er echte A7IV-bestanden beschikbaar
   zijn (zie sectie 3 en 5 van `MANY_INGEST_CAMERA_PROFILES_V2_PROPOSAL.md`).
