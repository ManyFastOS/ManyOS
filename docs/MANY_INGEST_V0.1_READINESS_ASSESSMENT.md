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

- **Geen vangnet tegen per ongeluk een echte run i.p.v. dry-run.** Het enige verschil
  is een vlag die de gebruiker moet onthouden. Eén verkeerd getypt commando en er
  wordt echt gekopieerd. Nodig: een expliciete bevestigingsstap bij een eerste
  (niet-dry-run) run, of dry-run als onveranderlijke standaard die je bewust moet
  uitzetten.
- **Stilzwijgend falende afhankelijkheid (ffmpeg).** Als ffprobe ontbreekt, wordt
  élk bestand gewoon "Onbekend" — geen foutmelding, geen waarschuwing. Een
  niet-technische gebruiker ziet dan alleen "het herkent niks" en heeft geen idee
  waarom. Nodig: een duidelijke, vroege check ("ffmpeg ontbreekt, installeer via
  ...") vóór een run begint, niet een stille kwaliteitsdegradatie.
- **Geen bescherming tegen bestandsnaam-botsingen op de bestemming.** Als er al een
  bestand met dezelfde naam op de doelplek staat (maar een andere checksum — dus
  geen duplicaat), wordt dat vandaag stilzwijgend overschreven door `shutil.copy2`.
  Dat is een echt dataverlies-risico, niet hypothetisch: twee cameraverkopen die
  toevallig `C0001.MP4` heten, twee shoots op dezelfde dag.
- **Geen voortgangsindicatie.** We hebben zelf bestanden van tot 19GB op de externe
  SSD gezien. Een kopieeractie van zulke bestanden duurt merkbaar lang met nul
  feedback — een niet-technische gebruiker weet niet of het "hangt" of gewoon
  bezig is, en zal geneigd zijn het proces af te breken (wat op zichzelf weer een
  onvolledige kopie kan achterlaten, zie punt 2).
- **Geen duidelijk "klaar, veilig om de schijf te ontkoppelen"-signaal.** De huidige
  samenvatting print in de terminal en verdwijnt; er is geen persistente,
  makkelijk te vinden bevestiging dat een run echt voltooid is voordat iemand de
  SSD losklikt.

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

Geen veiligheidsrisico (het systeem gokt niet, valt terecht terug op "Onbekend"),
maar wel relevant voor "dagelijks gebruik": hoe vaker "Onbekend" verschijnt, hoe
minder een editor het systeem vertrouwt.

- **DJI en GoPro zijn nog steeds volledig ongevalideerd** — nul echte bestanden
  van die apparaten gezien tot nu toe. De patronen zijn illustratief giswerk.
- **Sony A7IV is ongevalideerd**, en specifiek relevant: als de A7IV ook
  XAVC-MP4 met een vergelijkbaar `C####`-patroon blijkt te gebruiken, kan het
  nieuwe containerformaat-signaal voor FX3 dan verkeerd gaan — dat risico is
  bewust geaccepteerd, niet weggenomen (zie `MANY_INGEST_CAMERA_PROFILES_V2_PROPOSAL.md`,
  sectie 7).
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
waard, sommige dingen wel:

1. **Vangnet tegen per-ongeluk-echte-run + botsingsbescherming op de bestemming**
   (sectie 1) — kleine wijziging, voorkomt het enige échte dataverlies-scenario
   dat vandaag bestaat.
2. **Duidelijke ffmpeg-afhankelijkheidscheck vooraf** (sectie 1) — klein, voorkomt
   een verwarrende, stille kwaliteitsdegradatie.
3. **Voortgangsindicatie bij kopiëren** (sectie 1) — nodig zodra dit niet meer
   alleen door mij getest wordt.
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
