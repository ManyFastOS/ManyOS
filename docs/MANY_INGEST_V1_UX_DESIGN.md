# Many Ingest Desktop v1.0 — UX-blauwdruk

**Status:** Definitief ontwerp — nog geen code, geen GUI, geen wijziging aan de engine
**Rol:** Head of Product / Senior UX Designer / CTO, ManyFast
**Datum:** 2026-08-04
**Sluit aan op:** `CLAUDE.md`, `docs/VISION.md`, `docs/MANY_INGEST_BUILD_PLAN.md`,
`docs/MANY_INGEST_CLOUD_READY_ARCHITECTURE.md`, `docs/MANY_INGEST_V0.1_READINESS_ASSESSMENT.md`.

Dit document is de blauwdruk voor **Many Ingest Desktop v1.0** — de laag die boven op
de bestaande, ongewijzigde ingest-engine komt (`IngestService`, classificatie,
`Storage`/`Manifest`-adapters, `report.py`). Geen backend-herontwerp, geen nieuwe
bedrijfslogica — alleen de applicatie die een editor zonder technische kennis
zelfstandig kan gebruiken.

**Ontwerpdoel, in de woorden van de editor die hem gebruikt:**

> "Ik stop een SSD erin. Ik klik één keer. Klaar."

---

## 1. UX-review van het vorige ontwerp

Kritische audit van het eerder besproken ontwerp, vóór de verbeteringen. Dit is geen
correctie van fouten — het vorige ontwerp werkte — maar een aanscherping vanuit een
hogere lat: Blackmagic/Apple/Arc/Linear-niveau, niet "beter dan een CLI".

### 1.1 Overbodige stappen

- **Handmatig een schijf kiezen** terwijl er meestal maar één geschikte schijf is
  aangesloten. Een aparte "Welkom, kies een schijf"-stap is dan pure frictie.
- **Een aparte klik nodig om te zien welke camera's herkend zijn.** Dit stond
  weggestopt achter "Bekijk wat er gevonden is →" — maar dit is precies het soort
  informatie dat vertrouwen geeft (zie 1.4), dus hoort niet verstopt te zijn.
- **Klant én project altijd opnieuw kiezen**, ook als het overduidelijk dezelfde
  shoot is als gisteren (zelfde klant, vervolgdag van hetzelfde project). Geen
  standaardkeuze aanbieden op basis van "meest recent gebruikt" is een gemiste
  stap-besparing.

### 1.2 Keuzes die de software zelf kan maken

- **Welke schijf**, als er precies één geschikte is aangesloten.
- **Welke klant/project als startpunt**, als de vorige ingest recent was — voorstel,
  niet dwingend (de editor kan het altijd wijzigen, maar hoeft het niet altijd te
  bevestigen met een klik).
- **Of iets veilig te verwijderen is.** Dit bepaalt de engine al (`safe_to_delete_source`)
  — de UI moet dit tonen als gedrag van een knop (wel/niet aanwezig, wel/niet actief),
  niet als een JA/NEE-tekst die de editor zelf moet interpreteren.
- **Bestandsnaamconflicten oplossen** — gebeurt al automatisch in de engine, blijft zo.

### 1.3 Schermen die samengevoegd kunnen worden

- **Welkom-scherm + Klaar-om-te-starten-scherm** vallen samen tot één scherm zodra er
  precies één geschikte schijf is: geen "kies eerst, bevestig dan" — direct het
  Ready-scherm, met de gekozen schijf zichtbaar en wijzigbaar.
- **Een bevestigingsdialoog vóór Start** blijft, zoals eerder besloten, weg — maar nu
  met een steviger onderbouwing (zie hoofdstuk 5), niet alleen "gemak".

### 1.4 Informatie die vertrouwen geeft — moet blijven, en prominenter

- **Herkende camera-namen** (Sony FX6, Sony FX3, ...) — bewijst dat de app echt naar
  het materiaal heeft gekeken. Dit hoort op het hoofdscherm, niet achter een klik.
- **Exacte aantallen bestanden en GB** — concreet, verifieerbaar, geen vage taal.
- **Waar het naartoe gaat, in mensentaal** ("Nike → Zomer Campagne"), nooit een pad.
- **Een permanente, duidelijke "niet loskoppelen"-melding** tijdens het kopiëren.
- **Een proactieve melding als een kaart al eerder verwerkt is** (nieuw — zie 1.6 en
  hoofdstuk 5): dit is het soort informatie dat een editor behoedt voor een
  concrete fout, en versterkt tegelijk het vertrouwen dat de app meedenkt.

### 1.5 Informatie die afleidt — moet weg of verborgen

- **Bestandsnamen die voorbijflitsen** tijdens de voortgang — geen extra betekenis
  voor de editor, wel visuele ruis.
- **Confidence-scores/percentages** ("hoog"/"middel"/"laag") als letterlijke tekst.
- **Technische paden**, en elk woord als *dry-run*, *manifest*, *checksum*, *adapter*.
- **Te veel losse instellingen-schermen** in het hoofdpad — instellingen zijn per
  definitie iets dat niet dagelijks nodig is (zie hoofdstuk 9).

### 1.6 Waar een editor nog fouten kan maken — en hoe dit ontwerp dat dichttimmert

| Foutmogelijkheid | Oplossing in dit ontwerp |
|---|---|
| Verkeerde of dubbel gespelde klantnaam typen | Klant is een **keuze uit een lijst**, geen vrij tekstveld; fuzzy-matching waarschuwt vóór een nieuwe, bijna-identieke klant ontstaat (hoofdstuk 2) |
| Verkeerde schijf kiezen bij meerdere aangesloten schijven | Duidelijke kaarten met naam + grootte; ook bij auto-select blijft een "andere schijf?"-optie zichtbaar (hoofdstuk 3) |
| Een kaart die al eerder verwerkt is, nogmaals aanbieden | Proactieve waarschuwing vóór Start, gebaseerd op bestaande duplicaatdetectie (hoofdstuk 5) |
| Schijf loskoppelen tijdens het kopiëren | Permanente waarschuwing tijdens voortgang + nette foutafhandeling i.p.v. een crash (hoofdstuk 6) |
| Op Start klikken zonder goed te zien waar het naartoe gaat | Klant/project staat visueel onlosmakelijk vast aan de Start-knop — je kunt niet op Start klikken zonder de bestemming te zien (hoofdstuk 5) |

---

## 2. Klanten & projecten — verbeterde workflow

**Uitgangspunt blijft staan (locked decision, `CLAUDE.md`/bouwplan sectie 7): geen
automatische klant/project-herkenning uit bestandsinhoud.** Wat hieronder verbetert,
is niet "de app raadt het voor je", maar "de app maakt het bijna onmogelijk om per
ongeluk twee keer dezelfde klant aan te maken of de verkeerde te kiezen."

**Ontwerp:**
- **Klant is een keuze, geen tekstveld.** De editor tikt in een zoekveld, de app
  filtert een lijst van bekende klanten (gesorteerd op meest recent gebruikt boven-
  aan). Er is geen manier om per ongeluk een nieuwe klant aan te maken door gewoon
  te typen en op Enter te drukken.
- **Nieuwe klant is een bewuste, aparte actie.** Alleen via een expliciete
  "+ Nieuwe klant toevoegen"-knop onderaan de lijst. Als de getypte naam sterk lijkt
  op een bestaande (kleine verschillen in hoofdletters, spaties, spelling), toont de
  app eerst: *"Bedoel je 'Nike'?"* — vóórdat een nieuwe klant daadwerkelijk ontstaat.
- **Projecten zijn genest onder de klant.** Zodra een klant gekozen is, toont de
  projectenlijst alleen projecten van díe klant, ook weer meest-recent-eerst. Zelfde
  patroon: kiezen uit een lijst, nieuw project is een bewuste stap.
- **Standaardvoorstel.** Bij het openen van het Ready-scherm staat de laatst
  gebruikte klant/project-combinatie al ingevuld (niet afgedwongen — één klik om te
  wijzigen). Voor een shoot die meerdere dagen duurt, is dit vaak nul clicks.
- **Normalisatie, niet handmatige nette invoer.** De weergavenaam van een klant wordt
  één keer vastgelegd (bij aanmaken) en daarna altijd hergebruikt — een editor kan
  "nike" typen en toch "Nike" gekozen krijgen; er ontstaan geen twee records voor
  dezelfde klant door hoofdletter- of spatieverschillen.

**Wat dit onder water vereist (geen nieuwe bedrijfslogica, wel genoemd voor de
volledigheid — zie hoofdstuk 12):** een leesmethode op de bestaande `Manifest`-
interface die alle al bekende `client_id`/`project_id`-combinaties teruggeeft. Dit is
een kleine, additieve uitbreiding op een interface — exact het patroon dat
`ports/storage.py` zelf al documenteert ("toegevoegd zodra het nodig was, niet
vooraf") — geen nieuwe opslag, geen schema-wijziging: de data staat al in de
ManyFast Asset Schema.

---

## 3. SSD-detectie — automatisch, met escape hatch

**Doel:** de editor hoeft nooit een schijf te kiezen als er maar één geschikte is.

**"Geschikte schijf"-heuristiek (beschrijvend, geen code):** een aangesloten,
externe (niet-systeem-)schijf die niet leeg is en niet al herkend is als iets anders
(bijv. een Time Machine-back-updisk) — en nooit de schijf die zelf de bestemming
(`storage_root`) is, want die kan per ongeluk ook aangesloten zijn.

**Gedrag:**
- **Eén geschikte schijf gevonden** → de app opent direct op het Ready-scherm, met
  die schijf al gekozen. Een kleine, rustige regel toont welke schijf het is, met een
  link *"Andere schijf gebruiken"* — de automatische keuze is een slim standaard, geen
  onomkeerbare beslissing.
- **Meerdere geschikte schijven gevonden** → een korte kiezer met kaarten (naam +
  grootte), verder niets — dit is de enige situatie waarin een aparte "kies een
  schijf"-stap nog verschijnt.
- **Geen geschikte schijf gevonden** → een rustig wachtscherm: "Sluit een SD-kaart of
  SSD aan." Geen foutmelding, gewoon een uitnodigende lege staat.
- **Schijf wordt tijdens het gebruik van de app aangesloten** (app stond al open) →
  zelfde logica herhaalt zich; het wachtscherm gaat vanzelf over in het Ready-scherm
  zodra een geschikte schijf verschijnt.

Dit vereist alleen het uitlezen van aangesloten volumes op het moment dat de app
ernaar kijkt (zie ook het vorige ontwerp) — geen achtergronddienst, geen
volume-watcher-daemon, in lijn met de vastgelegde "geen achtergrond-daemon"-regel.

---

## 4. Preview — het "Klaar om te starten"-scherm

In één oogopslag, zonder technisch detail, zonder extra klik:

- **Hoeveel bestanden en hoeveel GB** — groot, direct zichtbaar.
- **Welke typen footage** — gegroepeerd op herkende camera (bijv. "🎥 214 Sony FX6 ·
  98 Sony FX3 · 🎙️ 8 geluidsopnames · 12 niet herkend"). Geen confidence-percentages,
  wél de naam als die bekend is — dit is vertrouwenwekkende info (zie 1.4), dus
  standaard zichtbaar, niet weggestopt.
  **Iconografie-kanttekening:** 🎥/🎙️/📦 hierboven zijn illustratieve placeholders
  voor dit document, geen definitieve ontwerpkeuze — toegestaan als categorie-
  aanduiding conform `docs/MANYOS_DESIGN_LANGUAGE.md` (hoofdstuk 7), maar bedoeld om
  vervangen te worden door het officiële ManyOS-iconenstelsel zodra dat bestaat. Alle
  overige emoji die eerder in dit document als navigatie-icoon, statusindicator of
  beheerder-badge stonden, zijn in deze versie vervangen door neutrale
  `[icoon: ...]`-verwijzingen (zie hoofdstuk 10) — die zijn geen categorie-aanduiding
  en vielen dus niet onder deze uitzondering.
- **Waar het terechtkomt** — in mensentaal: klant → project, nooit een bestandspad.
- **Bijzonderheden, alleen als relevant, neutraal geformuleerd:** "3 bestanden staan
  er al — die slaan we automatisch over" (duplicaten), "12 bestanden herkenden we
  niet automatisch — die worden gewoon meegenomen" (onbekende types). Geen van
  beide is een foutmelding; beide worden gewoon getoond als feit.
- **Eén detail-link** (niet verplicht) voor wie de volledige bestandslijst wil zien —
  voor de dagelijkse flow niet nodig, wel beschikbaar.

---

## 5. Startmoment — mag het nog eenvoudiger, en moet er nog bevestigd worden?

**Antwoord: geen bevestigingsdialoog, en het ontwerp hieronder maakt dat sterker
onderbouwd dan in de vorige versie.**

**Het tegenargument dat ik hier zelf tegenover zet, en waarom het niet wint:**
kopiëren is inderdaad niet-destructief, maar Many Ingest v0.1 heeft **geen
move/delete-functionaliteit** — een verkeerd gekozen klant/project betekent dat er
handmatig buiten de app om opgeruimd moet worden. Dat is een echt, niet triviaal
risico, dus "het is toch niet destructief" is op zichzelf niet genoeg onderbouwing
voor "dus geen bevestiging nodig."

**De reden dat een modal-dialoog tóch niet de juiste oplossing is:** bevestigings-
dialogen die bij elke actie verschijnen, trainen mensen om ze weg te klikken zonder
te lezen ("confirmation blindness") — precies het probleem dat ze zouden moeten
voorkomen. Apple/Blackmagic-software bewaart modals voor uitzonderingen, niet voor
de standaardhandeling.

**De daadwerkelijke oplossing: de bevestiging zit al onvermijdelijk in het scherm
zelf.**
- Klant/project staan **visueel vast aan de Start-knop** — niet ergens hogerop het
  scherm, maar er direct naast/boven, zodat je niet op Start kunt klikken zonder de
  bestemming in je blikveld te hebben.
- **Eén concrete, proactieve uitzondering krijgt wél een waarschuwing, omdat die een
  reëel scenario dekt in plaats van elk scenario preventief te onderbreken:** als de
  meeste bestanden op de gekozen schijf al eerder zijn gekopieerd (herkenbaar via de
  bestaande duplicaatdetectie — geen nieuwe logica, alleen slimmer geïnterpreteerd),
  toont het Ready-scherm een duidelijke banner: *"Deze kaart lijkt al eerder verwerkt
  — 61 van de 64 bestanden staan al in het systeem."* Dat is precies het scenario
  waarin een editor de verkeerde/oude kaart pakt — en het enige scenario waarin een
  waarschuwing echt iets voorkomt in plaats van alleen frictie toe te voegen.

---

## 6. Voortgang — een voortgangsscherm dat vertrouwen uitstraalt

```
Percentage         Groot, centraal, rustig — geen felle kleuren, geen knipperen.
Bestanden          "128 van 340 bestanden"
Snelheid            Afgeleid uit voortgangsdata (GB verwerkt / verstreken tijd) —
                     geen nieuwe engine-data nodig, puur berekend in de app-laag.
Resterende tijd      Afgeleid uit snelheid × resterende hoeveelheid — zelfde principe.
Waarschuwingen        Alleen als aantal > 0, klein en rustig: "2 bestanden hadden
                     een probleem — details volgen in het rapport." Nooit een
                     onderbrekende popup per bestand.
```

**Wat hier bewust niet staat:** bestandsnamen die voorbijkomen (afleidend, geen
waarde), technische tussenstappen (kopiëren/verifiëren apart benoemen — voor de
editor is het één doorlopende handeling).

**Betrouwbaarheid bij herhaalde fouten:** als er meerdere bestanden ná elkaar
mislukken (bijv. omdat de bestemmingsschijf is weggevallen), stopt de app proactief
met een duidelijke melding in plaats van door te ploegen door honderden bestanden die
toch allemaal gaan mislukken — dit is puur app-laag-gedrag (tellen van uitkomsten),
geen wijziging aan de engine.

**Annuleren:** klein, ondergeschikt, met één korte bevestiging bij klikken — dit is
wél een moment met gevolgen (het laatst verwerkte bestand is al veilig afgerond, de
rest niet), dus hier is een bevestiging wél op zijn plaats, in tegenstelling tot bij
Start.

---

## 7. Klaar-scherm — het eindscherm

**Alles gelukt:**
```
[icoon: geslaagd] Klaar
312 bestanden veilig gekopieerd naar Nike – Zomer Campagne.

[ Schijf veilig verwijderen ]
Bekijk rapport
```

**Gedeeltelijk gelukt:** zelfde opbouw, andere toon — geen alarmerend rood, wel
duidelijk en direct bruikbaar:
```
[icoon: waarschuwing] Bijna klaar
308 van 312 bestanden gekopieerd. 4 bestanden hadden een probleem.

[ Bekijk wat er misging ]
```
De "veilig verwijderen"-knop verschijnt hier bewust **niet** — de app biedt hem
letterlijk niet aan als het niet veilig is, in plaats van een JA/NEE-tekst te tonen
die de editor zelf moet interpreteren. Dit volgt rechtstreeks uit de bestaande
`safe_to_delete_source`-logica in de engine.

**Wat direct duidelijk moet zijn, zonder scrollen of klikken:** dat het gelukt is (of
niet), hoeveel er verwerkt is, en of de schijf weg mag. Alle overige cijfers
(duplicaten, naamconflicten, herkenning per camera, duur) staan achter *"Bekijk
rapport"* — nuttig, niet opgedrongen.

---

## 8. Home / Historie — nodig, maar klein

**Conclusie: ja, een lichte vorm is nodig — geen volwaardig historie-dashboard.**

**Waarom niet groot:** getoetst aan VISION.md's Decision Filter — een uitgebreide,
doorzoekbare geschiedenis bespaart geen meetbare tijd in de dagelijkse flow ("kaart
erin, één klik, klaar"). Op de meeste dagen kijkt een editor nooit terug. Een zware
historie-functie nu bouwen is investeren in iets dat zelden gebruikt wordt, ten koste
van de eenvoud van het hoofdpad.

**Waarom wel iets:** het wachtscherm (hoofdstuk 3, "geen schijf aangesloten") is
anders een lege staat zonder enige waarde. En af en toe is er een echte, concrete
behoefte: "hebben we deze kaart al gedaan?", "hoe heette dat project ook alweer?".

**Ontwerp — het wachtscherm draagt de historie, geen apart scherm:**
```
┌─────────────────────────────────┐
│      Many Ingest                 │
│                                   │
│      Sluit een SD-kaart of       │
│      SSD aan om te beginnen.     │
│                                   │
│  ─────────────────────────────   │
│  Laatste ingest                  │
│  [icoon: geslaagd] Nike – Zomer Campagne│
│     vandaag, 312 bestanden        │
│     Bekijk rapport →              │
│                                   │
│  Alle ingests bekijken →          │
│                  [icoon: instellingen]│
└─────────────────────────────────┘
```
(Notatie zoals toegelicht in hoofdstuk 10.)
- **Standaard zichtbaar:** alleen de állerlaatste ingest, als rustige, niet-opdringende
  strook — puur ter geruststelling, niet als functie die "gebruikt" moet worden.
- **Op aanvraag:** *"Alle ingests bekijken"* opent een simpele lijst (datum, klant/
  project, status) — voor het uitzonderlijke moment dat iemand echt wil terugzoeken.
  Geen zoekfunctie, geen filters in v1 — als blijkt dat dit veel gebruikt wordt, is
  dat het signaal om het uit te breiden, niet iets om nu al te bouwen.

Dit hergebruikt bestaande data (de JSON-lines-logs en de ManyFast Asset Schema) —
geen nieuwe opslag.

---

## 9. Instellingen — drie niveaus

| Niveau | Wat | Waarom |
|---|---|---|
| **Zichtbaar voor de editor** (tandwiel-icoon) | Bekende klanten/projecten opschonen (een fout aangemaakte naam uit de lijst verwijderen — raakt nooit al gekopieerde bestanden); eigen recente rapporten/logboek bekijken | Lage impact, dagelijks nuttig, geen risico op dataverlies |
| **Alleen voor een beheerder** ("Geavanceerd", apart afgeschermd) | Opslaglocatie inzien (waar materiaal/systeembestand staan — **alleen-lezen in v1**, zie 10.10); camera-herkenning bekijken/beheren (`camera_profiles.yaml`); volledige geschiedenis/export over alle editors heen | Een verkeerde opslaglocatie-wijziging heeft hoge impact (footage op de verkeerde plek) — daarom is dit in v1 bewust alleen-lezen in de app, en gebeurt een echte wijziging (nog) buiten de app om, door wie ManyOS onderhoudt |
| **Nooit zichtbaar, voor niemand in de app** | Ruwe YAML/JSON, bestandspaden, checksums, interne velden (`ingest_run_id`, `operator`/`source_machine` als ruwe waarden — wél getoond in vriendelijke vorm, bijv. "door: Christian"), elke verwijzing naar ffprobe/Python/venv | Puur technisch, voegt niets toe, ondermijnt het "geen technische kennis nodig"-uitgangspunt |

---

## 10. Complete wireframes — volledige gebruikersflow

**Notatie:** `[icoon: naam]` staat voor een plek waar een echt pictogram uit het
toekomstige ManyOS-iconenstelsel komt (conform `docs/MANYOS_DESIGN_LANGUAGE.md`,
hoofdstuk 7) — geen emoji, geen definitieve vorm, puur een leesbare aanduiding van
de functie op die plek. De enige emoji die in deze wireframes overblijven, zijn
🎥/🎙️/📦 als categorie-aanduiding voor video/audio/onbekend (toegestane uitzondering,
zie de kanttekening in hoofdstuk 4) — nergens anders.

### 10.1 Geen schijf aangesloten (wachtscherm / thuisbasis)
```
┌─────────────────────────────────────┐
│        Many Ingest                    │
│                                        │
│   [icoon: schijf]  Sluit een SD-kaart   │
│             of SSD aan.                 │
│                                        │
│   ───────────────────────────────    │
│   Laatste ingest                       │
│   [icoon: geslaagd] Nike – Zomer Campagne│
│      vandaag, 312 bestanden             │
│      Bekijk rapport →                   │
│                                        │
│   Alle ingests bekijken →               │
│                          [icoon: instellingen] │
└─────────────────────────────────────┘
```

### 10.2 Eén geschikte schijf gevonden → direct door naar Ready
```
┌─────────────────────────────────────┐
│  [icoon: schijf] SD_CARD_1  Andere schijf →│
│                                        │
│  Klant      [ Nike              ▾]    │
│  Project    [ Zomer Campagne    ▾]    │
│                                        │
│  312 bestanden · 214 GB                │
│  🎥 214 Sony FX6 · 98 Sony FX3          │
│  🎙️ 8 geluidsopnames                    │
│  📦 12 niet herkend                     │
│                                        │
│  Komt terecht bij:                     │
│  Nike → Zomer Campagne                 │
│                                        │
│  Bekijk alle bestanden →                │
│                                        │
│          [        Start        ]      │
└─────────────────────────────────────┘
```

### 10.3 Meerdere schijven aangesloten → kiezer (uitzondering, niet de norm)
```
┌─────────────────────────────────────┐
│  Welke schijf wil je gebruiken?        │
│                                        │
│  ┌───────────────────┐ ┌───────────────────┐│
│  │ [icoon: schijf]     │ │ [icoon: schijf]     ││
│  │ SD_CARD_1            │ │ EXT_DRIVE_2          ││
│  │ 64 bestanden          │ │ 340 bestanden         ││
│  └───────────────────┘ └───────────────────┘│
└─────────────────────────────────────┘
```

### 10.4 Nieuwe klant — bewuste bevestiging (voorkomt duplicaten)
```
┌─────────────────────────────────────┐
│  Klant   [ Nike Benelux          ]   │
│                                        │
│  Bedoel je "Nike"? Die kennen we al.   │
│                                        │
│   [    Ja, "Nike" gebruiken    ]  ← Primary│
│      Toch "Nike Benelux" aanmaken  ← Secondary│
└─────────────────────────────────────┘
```
**Knophiërarchie, bewust:** "Ja, gebruik bestaande klant" is de Primary-knop, de
optisch nadrukkelijke, standaard-gefocuste keuze. "Toch nieuw aanmaken" is Secondary
— zichtbaar en volwaardig klikbaar, maar niet de visuele standaard. Dit is geen
willekeurige styling-keuze: in de overgrote meerderheid van de gevallen waarin deze
melding verschijnt, is de bijna-identieke naam een tikfout of een lichte
schrijfwijzevariant van een bestaande klant, geen daadwerkelijk nieuwe klant. De
Primary-knop leidt de editor dus naar de uitkomst die het vaakst de juiste is en die
tegelijk voorkomt dat er dubbele klanten ontstaan — de editor kan nog steeds bewust
voor nieuw kiezen, maar moet daar niet per ongeluk in terechtkomen.

### 10.5 Ready-scherm — waarschuwing bij (waarschijnlijk) al verwerkte kaart
```
┌─────────────────────────────────────┐
│  [icoon: schijf] SD_CARD_1  Andere schijf →│
│                                        │
│  [icoon: waarschuwing]  Deze kaart lijkt al eerder │
│      verwerkt — 61 van de 64            │
│      bestanden staan al in het systeem. │
│                                        │
│  Klant      [ Nike              ▾]    │
│  Project    [ Zomer Campagne    ▾]    │
│                                        │
│          [        Start        ]      │
└─────────────────────────────────────┘
```

### 10.6 Voortgang
```
┌─────────────────────────────────────┐
│                                        │
│            ▓▓▓▓▓▓▓▓▓▓░░░░  66%          │
│                                        │
│         128 van 340 bestanden           │
│         1,4 GB/s · nog ca. 3 min        │
│                                        │
│    [icoon: waarschuwing]  Niet loskoppelen│
│                                        │
│                          Annuleren     │
└─────────────────────────────────────┘
```

### 10.7 Klaar — alles gelukt
```
┌─────────────────────────────────────┐
│                                        │
│         [icoon: geslaagd]             │
│             Klaar                     │
│                                        │
│   312 bestanden veilig gekopieerd      │
│   naar Nike – Zomer Campagne.          │
│                                        │
│   [  Schijf veilig verwijderen  ]      │
│                                        │
│   Bekijk rapport · Nieuwe ingest        │
└─────────────────────────────────────┘
```

### 10.8 Klaar — met aandachtspunten
```
┌─────────────────────────────────────┐
│                                        │
│        [icoon: waarschuwing]          │
│           Bijna klaar                 │
│                                        │
│   308 van 312 bestanden gekopieerd.    │
│   4 bestanden hadden een probleem.     │
│                                        │
│   [   Bekijk wat er misging    ]       │
│                                        │
│              Nieuwe ingest              │
└─────────────────────────────────────┘
```

### 10.9 Instellingen — editorniveau
```
┌─────────────────────────────────────┐
│  ← Terug                               │
│                                        │
│  Bekende klanten & projecten           │
│   Nike               [ Verwijder ]     │
│   ManyFast            [ Verwijder ]     │
│                                        │
│  Mijn rapporten bekijken →              │
│                                        │
│  Geavanceerd (alleen beheerder) →       │
└─────────────────────────────────────┘
```

### 10.10 Instellingen — geavanceerd/beheerder
```
┌─────────────────────────────────────┐
│  ← Terug         [icoon: beheerder]     │
│                                        │
│  Opslaglocatie (alleen-lezen)          │
│   /Volumes/Extreme SSD/ManyFast/...    │
│                                        │
│  Camera-herkenning                     │
│   6 profielen geladen [ Bekijken ]     │
│                                        │
│  Volledige geschiedenis exporteren →    │
└─────────────────────────────────────┘
```
**Opslaglocatie is in v1 bewust alleen-lezen**, geen "Wijzigen"-knop. De engine
heeft vandaag alleen een functie om configuratie te *lezen*, geen functie om hem
*weg te schrijven* (zie hoofdstuk 12) — dat in-app aanbieden zou een nieuwe
schrijffunctie vereisen die nog niet bestaat. Voor v1 wordt de opslaglocatie, net als
vandaag, buiten de app om aangepast (het configuratiebestand direct bewerken door wie
ManyOS onderhoudt) — een in-app bewerkbare versie is een expliciete, latere fase, met
een eigen afweging over hoe dat veilig moet (zie hoofdstuk 11).

---

## 11. Implementatieplan — bouwfasen

**Fase 0 — Fundament**
Nieuwe composition root voor de Desktop-app (het equivalent van `cli.py`), rechtstreeks
gekoppeld aan de bestaande `IngestService`. Geen zichtbare UI nog — dit bewijst alleen
dat de app-schil de engine ongewijzigd kan aanroepen.

**Fase 1 — Hoofdvenster & schijf-detectie**
Wachtscherm (10.1), automatische detectie van geschikte schijven, auto-select bij één
schijf, kiezer bij meerdere (10.2/10.3).

**Fase 2 — Klaar-om-te-starten**
Ready-scherm (10.2) met de automatische achtergrondcontrole, klant/project-picker met
autocomplete en dedupe-bevestiging (10.4), laatst-gebruikt-voorstel.

**Fase 3 — Ingest & voortgang**
Start-knop (geen modal, zie hoofdstuk 5), voortgangsscherm met snelheid/resterende
tijd (10.6), annuleren met bevestiging, de "veel fouten op rij"-veiligheidsstop.

**Fase 4 — Rapportage**
Klaar-scherm in beide varianten (10.7/10.8), veilig-verwijderen-knop gekoppeld aan
`safe_to_delete_source`, rapport-detailweergave.

**Fase 5 — Vertrouwens- en foutverfijning**
"Al eerder verwerkt"-waarschuwing (10.5), nette foutafhandeling bij een losgekoppelde
schijf tijdens het kopiëren, alle overige foutmeldingen uit hoofdstuk 1.6 in hun
uiteindelijke, vriendelijke bewoording.

**Fase 6 — Comfortlaag**
Mini-historie op het wachtscherm (10.1), "alle ingests bekijken", instellingen
editorniveau (10.9) en geavanceerd/beheerderniveau (10.10).

**Bewust niet gepland, apart te beslissen:**
- Automatische app-launch bij het aansluiten van een schijf (vereist een
  achtergronddienst — botst met de vastgelegde "geen daemon"-regel).
- **In-app bewerkbare opslaglocatie.** Voor v1 is dit veld in de app altijd
  alleen-lezen (zie 10.10) — er is vandaag geen schrijffunctie voor configuratie,
  alleen een leesfunctie (hoofdstuk 12). Een editeerbare versie is een bewuste,
  latere fase, met een eigen afweging over hoe dat veilig moet, niet iets dat er
  terloops bij komt.
- Alle AI-uitbreidingen (clip-thumbnails, tagging, transcriptie) — horen bij een
  latere Asset Intelligence-module, niet bij Many Ingest Desktop v1.0.

---

## 12. Wat aan de engine ongewijzigd blijft

Ter bevestiging, niet ter herhaling van eerdere analyse: `IngestService.run()`,
classificatie, `Storage`/`Manifest`-adapters, `IngestSummary`/`render_report()`,
`ProgressUpdate`/`progress_callback` en de JSON-lines-log blijven exact zoals ze zijn.
De enige additieve uitbreiding die dit ontwerp vereist, is een leesmethode op de
`Manifest`-interface om bekende klant/project-combinaties op te vragen (hoofdstuk 2)
— een kleine, additieve interface-uitbreiding, geen herontwerp, in lijn met hoe
`Storage`/`Manifest` in dit project al eerder zijn uitgebreid.

Ook `config.py` blijft ongewijzigd: het bevat vandaag alleen functies om configuratie
te *lezen* (`load_ingest_config`, `load_camera_profiles`), geen functie om
configuratie te *schrijven*. Dat is de reden dat de opslaglocatie in Instellingen
(10.10) in v1 alleen-lezen is — een in-app "Wijzigen"-knop zou een nieuwe
schrijffunctie vereisen die nu nog niet bestaat, en die bewust geen deel uitmaakt van
dit ontwerp (zie hoofdstuk 11).
