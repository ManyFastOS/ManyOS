# ManyOS Design Language

**Status:** Officiële designstandaard — nog geen code
**Rol:** Apple Human Interface Designer / Senior Product Designer (Linear) / Design
Lead (Blackmagic Design) / CTO, ManyFast
**Datum:** 2026-08-04
**Sluit aan op:** `docs/VISION.md` (Core Principles, Decision Filter), `CLAUDE.md`
(terminologie, vastgelegde architectuurkeuzes), `docs/MANY_INGEST_V1_UX_DESIGN.md`
(de eerste toepassing van deze taal, op Many Ingest Desktop v1.0).

Dit document is het ManyOS-equivalent van Apple's Human Interface Guidelines of
Google's Material Design — maar specifiek voor ManyOS. **Elke toekomstige module**
(Many Ingest, Many Select, Many Review, Many Deliver, en wat daarna komt) volgt deze
principes. Een module die er anders uitziet of anders aanvoelt dan de rest, is per
definitie niet af — ongeacht hoe goed de functionaliteit werkt.

---

# 1. Filosofie

ManyOS ondersteunt mensen die onder tijdsdruk creatief werk leveren. De belofte is
**24H delivery** — dat betekent dat de mensen die deze software gebruiken al onder
druk staan vóórdat ze de app openen. ManyOS mag daar nooit iets aan toevoegen.

**ManyOS voelt:**
- **Rustig.** Geen felle kleuren, geen knipperende meldingen, geen schermen die om
  aandacht schreeuwen. Rust is niet de afwezigheid van informatie — het is informatie
  die zo geordend is dat ze geen inspanning kost.
- **Betrouwbaar vóór snel.** Snelheid die twijfel oproept ("is dit wel goed gegaan?")
  is geen winst. Elke handeling laat zien dát hij goed is gegaan, niet alleen dat hij
  klaar is.
- **Zonder stress.** De software neemt de operationele last over, zodat de mens zich
  op het creatieve werk kan richten (VISION.md: *"Onze doel is niet om creatieve
  mensen te vervangen. Ons doel is om alles weg te halen wat ze vertraagt."*). Stress
  in de software is een ontwerpfout, geen onvermijdelijk gevolg van complexe techniek
  eronder.
- **Geen overbodige keuzes.** Elke keuze die aan de gebruiker wordt voorgelegd, is een
  keuze die de software niet zelf kon maken. Dat moet de uitzondering zijn, niet de
  regel.
- **Professioneel, niet consumentistisch.** ManyOS is gereedschap voor mensen die het
  de hele dag gebruiken, geen app die geïnstalleerd, geopend en weer vergeten wordt.
  Vergelijk het gevoel met Blackmagic Resolve of Linear, niet met een consumenten-app
  die aandacht probeert vast te houden.

**De test voor elk scherm, elke knop, elke melding:** zou dit iemand die net een hele
dag heeft gedraaid, gehaast is en niet technisch is, kalmer of gestrester maken?
Als het antwoord niet overduidelijk "kalmer" is, is het ontwerp niet af.

---

# 2. Visuele identiteit

- **Donker als standaard.** Video- en post-productieomgevingen zijn van oudsher
  donker (grading suites, edit bays) — donker voelt hier niet als een stijlkeuze maar
  als het native register van het vakgebied, en is rustiger voor beeldschermgebruik
  gedurende lange sessies. Licht wordt ondersteund (volgt het systeemthema), maar
  donker is het gezicht van ManyOS.
- **Kleur is betekenis, geen decoratie.** De basis is neutraal (grijstinten, weinig
  verzadiging). Kleur wordt alleen ingezet om iets te communiceren: status, actie,
  aandacht. Een scherm vol kleur draagt geen informatie meer over — als alles opvalt,
  valt niets op.
- **Eén accentkleur**, consistent gebruikt voor de belangrijkste actie op een scherm
  en voor merkherkenning. Niet meerdere concurrerende accentkleuren door elkaar.
- **Foutmeldingen zijn nooit paniekerig.** Geen volle rode vlakken, geen schreeuwende
  iconen. Een gedempte, herkenbare kleur, gecombineerd met tekst en een icoon (nooit
  kleur als enig signaal — zie punt 7 en toegankelijkheid). De toon is: "dit moet
  opgelost worden," nooit "er is iets verschrikkelijks gebeurd."
- **Succesmeldingen zijn subtiel, niet feestelijk.** Eén rustig groen accent, geen
  confetti, geen grote animatie. Succes is de verwachte uitkomst, geen bijzondere
  gebeurtenis om te vieren — vergelijk het gevoel met een klok die op tijd afloopt,
  niet met een spel dat gewonnen is.
- **Waarschuwingen zijn informatief, niet alarmerend.** Een gedempte geel/amber-toon,
  gebruikt voor "dit is het weten waard", nooit voor "dit is fout." Het onderscheid
  tussen waarschuwing en fout moet ook zonder kleur (via woordkeuze) duidelijk zijn.

---

# 3. Typography

Een vaste hiërarchie, overal in ManyOS hetzelfde:

| Niveau | Gebruik |
|---|---|
| **Grote titel** | Vertelt waar je bent. Maximaal één per scherm. Nooit gebruikt voor nadruk binnen een scherm — daar is de kernwaarde-stijl voor (zie hieronder). |
| **Sectietitel** | Groepeert inhoud binnen een scherm. Alleen als een scherm daadwerkelijk meerdere secties heeft — geen sectietitel boven één blok inhoud. |
| **Kernwaarde** | Eén expliciet, apart type-niveau voor het ene getal of woord dat er op dit scherm het meest toe doet — een percentage, een bestandsaantal, "Klaar". Groot, rustig, geen versiering. Dit is wat een gebruiker in een oogopslag moet kunnen lezen zonder de rest van het scherm te lezen. |
| **Body** | De standaardtekst waarmee de gebruiker daadwerkelijk leest en beslist. Kort, direct, geen technisch jargon (zie hoofdstuk 15). |
| **Caption** | Ondergeschikte/context-informatie (tijdstempels, metadata, kleine labels). Nooit de enige drager van iets dat de gebruiker moet weten om een beslissing te nemen — als het belangrijk genoeg is om te weten, is het body-tekst. |

**Vuistregel:** als een scherm twee grote titels nodig lijkt te hebben, zijn het twee
schermen. Als body-tekst een technische term nodig heeft om precies te zijn, is de
tekst fout, niet het concept.

---

# 4. Buttons

| Type | Gebruik | Niet gebruiken voor |
|---|---|---|
| **Primary** | De ene, voor de hand liggende volgende stap op dit scherm (Start, Bevestigen). **Maximaal één per scherm, altijd.** | Een tweede gelijkwaardige actie — die is per definitie Secondary, ook al voelt hij belangrijk. |
| **Secondary** | Een geldig alternatief naast de primaire actie (bijv. "Andere schijf gebruiken"). Visueel duidelijk onderschikt aan Primary. | Iets dat eigenlijk de hoofdactie is — dan is de indeling van het scherm fout, niet de knop. |
| **Danger** | Uitsluitend voor acties met een echt, onomkeerbaar gevolg (verwijderen, overschrijven — komt in Many Ingest v1 vrijwel niet voor omdat de engine copy-only is, maar wordt relevant in latere modules zoals Many Deliver). | Alles wat feitelijk omkeerbaar of onschadelijk is — een Danger-knop op een veilige actie ondermijnt het signaal voor de keer dat het er echt toe doet. |
| **Ghost** | Lage-nadruk acties binnen een dichter scherm (bijv. een actie binnen een kaart). | De hoofdactie van een scherm — Ghost mag nooit de enige manier zijn om verder te komen. |
| **Link** | Inline, tekstuele, tertiaire acties ("Bekijk rapport", "Alle ingests bekijken"). Voelt aan als "meer informatie", niet als "beslissing nemen". | Een actie met gevolgen — als een klik iets verandert (niet alleen toont), hoort het geen Link te zijn. |

**Kernregel, geldig voor elke module:** een scherm zonder duidelijke Primary-knop is
een scherm dat de gebruiker in de steek laat. Een scherm met twee Primary-knoppen is
een scherm dat een beslissing aan de gebruiker doorschuift die het ontwerp had moeten
nemen.

---

# 5. Cards

Cards groeperen **losse, aftastbare eenheden** — een gedetecteerde schijf, een
project, een regel in een geschiedenislijst. Geen algemeen lay-outmiddel.

- **Padding:** ruim. Ademruimte is onderdeel van "rustig" (hoofdstuk 1), geen
  esthetische bijzaak — een krappe kaart voelt gehaast, ook als de inhoud identiek is.
- **Radius:** zacht afgerond, consistent dezelfde afronding overal in ManyOS — dit is
  een van de dingen die een module onmiddellijk herkenbaar maakt als "ManyOS", zie
  hoofdstuk 13.
- **Schaduw:** minimaal — een subtiele rand of een heel lichte schaduw om diepte aan
  te geven, nooit een zware drop-shadow. ManyOS is plat en kalm, geen skeuomorfisme.
- **Spacing:** een vaste, herhaalde afstand tussen kaarten — nooit variabel per
  scherm, dat is wat een layout "onrustig" laat aanvoelen zonder dat iemand precies
  kan zeggen waarom.

**Wanneer wél een kaart:** een keuze uit meerdere gelijkwaardige, aftastbare opties
(schijven, projecten, geschiedenis-items).
**Wanneer geen kaart:** een enkele, op-zichzelf-staande actie (dat is een knop), de
hoofdinhoud van het hele scherm (dat heeft geen kader nodig), of een kaart in een
kaart (nooit nesten — als het zo complex wordt, is het een apart scherm).

---

# 6. Formulieren

**De belangrijkste regel, geldig voor élke module, niet alleen Many Ingest:** alles
wat een herhaalde entiteit is met een geschiedenis (klanten, projecten, camera-
profielen, later leveringsplatforms, reviewers, wat dan ook) is **een keuze uit een
lijst, nooit een vrij tekstveld.** Vrije tekstinvoer is de bron van tikfouten,
duplicaten en inconsistentie — een lijst met zoeken/filteren lost dat structureel op,
niet door de gebruiker te vertrouwen op zorgvuldigheid.

- **Dropdown** — voor een kleine, vaste verzameling opties die zelden verandert.
- **Autocomplete** — voor lijsten die na verloop van tijd groeien (klanten,
  projecten). Altijd gesorteerd op meest-recent-gebruikt bovenaan — de meest
  waarschijnlijke keuze staat het dichtst bij de vinger.
- **Zoekveld** — verschijnt pas zodra een lijst daadwerkelijk lang genoeg is om te
  rechtvaardigen dat er gezocht moet worden. Een zoekveld boven drie opties is
  ruis, geen hulp.
- **Nieuwe klant/project aanmaken** — altijd een aparte, bewuste, expliciet
  gelabelde actie ("+ Nieuwe klant toevoegen"), nooit een impliciet gevolg van typen
  + Enter. Bij een naam die sterk lijkt op een bestaande, vraagt de app eerst:
  *"Bedoel je [bestaande naam]?"* — vóórdat er iets nieuws ontstaat.
- **Validatie** is altijd direct en inline, nooit pas na een volledige inzending —
  een fout wordt getoond op het moment dat hij ontstaat, niet als straf achteraf.
- **Labels blijven altijd zichtbaar**, ook als een veld al een waarde bevat (nooit
  een placeholder als enige label — context mag nooit verdwijnen zodra iemand iets
  heeft ingevuld).

Dit patroon (kiezen > typen, expliciet aanmaken, direct valideren) is niet uniek voor
Many Ingest — het is het formulierpatroon voor heel ManyOS.

---

# 7. Iconografie

- **Eén consistente iconenstijl** door heel ManyOS: eenvoudige lijniconen, gelijke
  lijndikte, geen mengeling van stijlen tussen modules. De stijl volgt platform-
  conventies (zie hoofdstuk 14) eerder dan een eigen, herkenbaar illustratie-systeem
  — ManyOS moet native aanvoelen, niet als een opvallend eigen ontwerptaaltje.
- **Emoji: spaarzaam, functioneel, nooit decoratief.** Toegestaan als snel te
  scannen categorie-aanduiding waar herkenning belangrijker is dan polish (bijv.
  🎥/🎙️/📦 om video/audio/onbekend te onderscheiden in een bestandenoverzicht) — dit
  is een pragmatische, tijdelijke keuze voor snelle herkenbaarheid, geen permanent
  merkelement. Naarmate modules volwassener worden, verhuizen deze naar het echte
  iconenstelsel.
- **Emoji horen nooit in UI-chrome.** Nooit als icoon op een knop, nooit als
  statusindicator, nooit als vervanging van een systeempictogram. Dat soort plekken
  gebruikt altijd het consistente iconenstelsel — anders verschilt de "toon" van de
  interface per schermdeel.
- **Alleen-icoon (zonder label)** is uitsluitend toegestaan voor universeel
  begrepen, veelgebruikte acties in krappe ruimtes (terug-pijl, instellingen-tandwiel).
  Elke minder voor de hand liggende of zeldzame actie krijgt altijd een tekstlabel —
  een icoon dat geraden moet worden, is geen duidelijkheid, het is een raadsel.

---

# 8. Loading states

- **Geen spinners voor iets met een meetbare duur.** Een spinner communiceert niets
  ("er gebeurt iets, geen idee hoelang") en ondermijnt het vertrouwensprincipe uit
  hoofdstuk 1. Alles wat een aantal/hoeveelheid heeft (bestanden, GB, stappen) krijgt
  een voortgangsbalk met een concreet getal, geen ronddraaiend icoon.
- **Spinners zijn alleen acceptabel voor bijna-instante handelingen** (ruwweg onder
  een seconde of twee), waar een volledige voortgangsbalk overkill zou zijn — puur
  als korte, bevestigende overgang, niet als hoofdmiddel om wachttijd te overbruggen.
- **Skeletons** voor schermen die gestructureerde data laden vóórdat de inhoud bekend
  is (bijv. een geschiedenislijst) — ze tonen de vorm van wat komt, wat rustiger
  aanvoelt dan een lege pagina of een spinner.
- **Voortgangsbalken** voor elk proces met een meetbare hoeveelheid werk — dit is het
  standaardmiddel in ManyOS voor "we zijn ergens mee bezig", niet de uitzondering.
- **Nooit een leeg scherm zonder enige terugkoppeling**, ook niet voor een fractie
  van een seconde die "waarschijnlijk niet gemerkt wordt" — als er twijfel is of iets
  merkbaar is, behandel het alsof het merkbaar is.

---

# 9. Animaties

**Toegestaan:**
- **Fade** — voor het verschijnen/verdwijnen van inhoud en statuswisselingen.
- **Subtiele slide** — voor navigatie vooruit/terug, voor het openen van een paneel.
- **Zeer subtiele scale** — uitsluitend als bevestiging van een voltooiing (bijv. het
  vinkje op het Klaar-scherm), nooit als speels effect.

**Niet toegestaan:**
- Stuiterende/veerkrachtige ("bouncy") beweging, speelse fysica.
- Draaiende logo's of merkanimaties als wachtmiddel.
- Elke animatie die puur decoratief is en geen statusverandering communiceert.
- Elke animatie die de gebruiker vertraagt in het uitvoeren van de volgende stap.

**Timing:** snel en nauwelijks merkbaar — animatie in ManyOS bevestigt een
statusverandering, het vermaakt niet. Als een animatie zou opvallen als "leuk", is
hij te langzaam of te uitbundig. **Vuistregel:** als het verwijderen van een animatie
geen informatie kost, verwijder hem.

---

# 10. Empty states

Nooit een leeg, dood scherm. Een lege staat in ManyOS bestaat altijd uit: een rustig
icoon, één zin die de situatie beschrijft, en — indien van toepassing — precies één
voor de hand liggende vervolgactie.

- **Nooit negatief geformuleerd.** "Geen projecten gevonden" leest als een probleem.
  "Sluit een SD-kaart aan om te beginnen" leest als een uitnodiging. Elke lege staat
  in ManyOS is een uitnodiging tot de volgende stap, nooit een melding van afwezigheid.
- **Consistent patroon in élke module:** icoon + één zin + (optioneel) één knop.
  Geen lange, wervende tekst, geen opsomming van wat er allemaal wél zou kunnen.
- Een lege staat mag, waar zinvol, een klein stukje context tonen (zoals de laatste
  activiteit op het Many Ingest-wachtscherm) — maar dat blijft ondergeschikt aan de
  hoofdboodschap, nooit een concurrerend blok informatie.

---

# 11. Meldingen

| Type | Vorm | Onderbreekt de flow? |
|---|---|---|
| **Succes** | Subtiel, inline of als korte banner | Nee — succes bevestigt, het houdt nooit tegen |
| **Waarschuwing** | Banner, in context bij wat hij betreft | Nee, tenzij de gebruiker anders een fout zou maken die niet meer te herstellen is |
| **Fout** | Inline waar mogelijk (bij het veld/de actie die het betreft); een blokkerend scherm alleen als de hele flow écht niet verder kan (bijv. bestemming onbereikbaar) | Alleen wanneer verdergaan feitelijk onmogelijk of zinloos is |
| **Informatie** | Kleinste vorm — captionniveau, geen aparte melding | Nooit |

**Wanneer een popup (blokkerend):** alleen als de eerstvolgende actie van de
gebruiker zonder deze informatie onveilig of betekenisloos zou zijn.
**Wanneer een banner:** voor alles wat de moeite waard is om te weten, maar waar de
gebruiker prima omheen of voorbij kan blijven werken.
**Wanneer helemaal niets:** informatie die het systeem intern nodig heeft (een
achterliggende herstelpoging, een technische terugvaloptie) en die geen enkele
invloed heeft op wat de gebruiker moet weten of beslissen. Stilte is hier een
bewuste ontwerpkeuze, geen omissie.

---

# 12. Confirmations

**Bevestig alleen wanneer een actie tegelijk (a) werkelijk onomkeerbaar is, en (b)
het gevolg niet al vanzelfsprekend zichtbaar was vóór de klik.** Beide voorwaarden
moeten gelden — niet één.

- **Nooit bevestigen bij omkeerbare of onschadelijke acties.** Een ingest starten is
  bijvoorbeeld niet destructief (er wordt nooit iets overschreven of van de bron
  verwijderd) — dus geen bevestigingsdialoog, ook al voelt de actie "belangrijk".
- **Bevestigingsdialogen die bij te veel acties verschijnen, trainen mensen om ze
  weg te klikken zonder te lezen** — en ondermijnen zichzelf zo precies op het
  moment dat het er echt toe doet. Een dialoog die zelden verschijnt, wordt gelezen.
  Een dialoog die vaak verschijnt, wordt genegeerd.
- **De eerste vraag bij twijfel is nooit "voeg ik een bevestiging toe?", maar "kan ik
  het gevolg zichtbaar maken in het scherm zelf?"** Bijna altijd is het antwoord ja
  — en is dat de betere oplossing. (Zie `MANY_INGEST_V1_UX_DESIGN.md`, hoofdstuk 5,
  voor de uitgewerkte toepassing: klant/project blijven zichtbaar naast de Start-
  knop in plaats van een los bevestigingsscherm.)
- **Bevestig wél** bij een handeling die middenin al bezig verwerkte data achterlaat
  (bijv. annuleren tijdens een lopende actie) of die daadwerkelijk data zou
  verwijderen/overschrijven — dat zijn de uitzonderingen die de regel waard maken.

---

# 13. Consistentie

Dit blijft **identiek** in élke huidige en toekomstige ManyOS-module — dit is wat
"ManyOS" tot één besturingssysteem maakt in plaats van een verzameling losse apps:

- **Navigatiepatroon** — dezelfde plek voor instellingen (tandwiel), hetzelfde
  terug-gedrag, dezelfde manier waarop een scherm "de vorige stap" toont.
- **Knopprioriteit** — de regels uit hoofdstuk 4, zonder uitzondering: één Primary
  per scherm, Danger alleen bij echte onomkeerbaarheid.
- **Kaartontwerp** — padding, radius, schaduw en spacing uit hoofdstuk 5, exact
  hetzelfde in elke module.
- **Animatietiming en -stijl** — hoofdstuk 9, geen module met een "eigen" snellere
  of speelsere variant.
- **Kleurbetekenis** — groen betekent overal "gelukt", amber betekent overal
  "let op", nooit per module een andere invulling van dezelfde kleur.
- **Terminologie** — de ManyFast-specifieke woorden uit `CLAUDE.md` (ManyFast Asset
  Schema, Project Workspace, Asset Intelligence) gelden voor heel ManyOS, niet
  alleen voor Many Ingest. Een toekomstige module verzint geen eigen synoniemen.
- **Het "één primaire actie per scherm"-principe** en het bevestigingsprincipe uit
  hoofdstuk 12 — dit zijn geen Many Ingest-specifieke keuzes, het zijn ManyOS-regels.
- **Lege-staat- en laadstaat-patronen** uit hoofdstuk 8 en 10.

**Wat wél per module mag verschillen:** de specifieke inhoud, de specifieke
werkstroom, de specifieke iconen voor domeinspecifieke concepten. De *taal* waarin
dat gezegd wordt, verschilt nooit.

---

# 14. Toekomst — macOS, Windows, web

ManyOS begint op macOS, maar het ontwerp mag zich daar niet aan vastklinken.

- **Platform-native interacties, ManyOS-identiteit erboven.** Native bestands-/
  volumekiezers, native venstergedrag, platform-eigen toetsencombinaties — ManyOS
  vecht nooit tegen de conventies van het besturingssysteem waarop het draait. Wat
  constant blijft, is de laag daarboven: typografische hiërarchie, kleurbetekenis,
  spacing, iconenstijl, terminologie (hoofdstuk 13).
- **Vermijd een volledig zelfgebouwde "eigen chrome"** die op elk platform hetzelfde
  probeert te zijn — dat voelt nergens native en overal een beetje vreemd. Het doel
  is: op macOS aanvoelen als een macOS-app, op Windows als een Windows-app, en toch
  in beide gevallen onmiskenbaar ManyOS.
- **Een toekomstige webversie is dezelfde ManyOS, niet een afgeslankt product.**
  Zelfde terminologie, zelfde informatiehiërarchie, aangepast aan wat een browser
  wél en niet kan (bijv. geen directe schijf-detectie zoals hoofdstuk 3 van de
  Many Ingest UX-blauwdruk beschrijft — dat is een platformbeperking, geen reden om
  de rest van de taal te laten verwateren).

---

# 15. Dingen die ManyOS NOOIT doet

- Nooit technische termen tonen aan de gebruiker (checksum, manifest, dry-run, JSON,
  bestandspaden, ruwe foutmeldingen/stack traces).
- Nooit meer dan één primaire knop op een scherm.
- Nooit een volledig rood scherm — fouten zijn altijd rustig, herkenbaar en
  oplosbaar, nooit paniekerig.
- Nooit schreeuwerige, speelse of vertragende animaties.
- Nooit een instellingenscherm vol keuzes die de meeste gebruikers niet nodig hebben
  of begrijpen — geavanceerde opties zijn altijd apart afgeschermd (zie
  `MANY_INGEST_V1_UX_DESIGN.md`, hoofdstuk 9, voor het niveaumodel).
- Nooit een modaal bevestigingsscherm voor een omkeerbare of onschadelijke actie.
- Nooit de gebruiker een technische keuze laten maken die het systeem zelf
  betrouwbaar kan bepalen (welke schijf, welk apparaat, welk bestandsformaat).
- Nooit stilzwijgend data overschrijven of verwijderen — als iets kan botsen met wat
  er al is, wordt dat zichtbaar afgehandeld, nooit stil weggeschreven.
- Nooit een spinner tonen voor iets met een meetbare duur.
- Nooit jargon uit één vakgebied (engineering) doorzetten naar een ander
  (creatieve/editing-taal) — de taal van de interface is altijd de taal van de
  gebruiker, nooit die van de techniek eronder.
- Nooit de indruk wekken dat AI iets definitief besliste zonder dat een mens het kon
  zien of corrigeren (VISION.md, principe 5: *AI as an Assistant*) — een suggestie
  wordt altijd als suggestie getoond, nooit als voldongen feit.

---

# 16. Samenvatting — 10 kernprincipes

Deze tien regels zijn het fundament voor iedere huidige en toekomstige ManyOS-module:

1. **De gebruiker denkt nooit aan bestandspaden, techniek of instellingen die er
   niet toe doen.**
2. **De software neemt zoveel mogelijk beslissingen zelf** — een keuze aan de
   gebruiker voorleggen is de uitzondering, niet de standaard.
3. **Vertrouwen is belangrijker dan snelheid — en rust is hoe vertrouwen voelt.**
4. **Minder schermen is beter.** Elk scherm moet zijn eigen bestaan verdienen.
5. **Nooit meer dan één primaire actie per scherm.**
6. **Bevestig alleen wat écht onomkeerbaar is — nooit uit gewoonte.**
7. **Kleur en beweging hebben altijd betekenis, nooit decoratie.**
8. **Elke module voelt aan als hetzelfde besturingssysteem**, nooit als een losse,
   eigen app.
9. **Platform-native waar het kan, ManyOS-identiteit waar het telt.**
10. **AI ondersteunt, de mens beslist — zichtbaar, nooit stiekem.**

Elke nieuwe ManyOS-module — Many Select, Many Review, Many Deliver, en alles daarna
— wordt tegen deze tien regels getoetst vóórdat de eerste schets gemaakt wordt.
