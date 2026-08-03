# ManyOS v0.1 — Voorstel vanuit eigenaarsperspectief (rendement)

**Status:** Voorstel — herziening van [MANYOS_ARCHITECTURE.md](./MANYOS_ARCHITECTURE.md)
**Rol:** Eigenaar ManyFast, kritisch op rendement
**Datum:** 2026-08-03

## 0. Waarom dit een herziening is

Het vorige document (de architectuur) is correct, maar is geschreven vanuit "wat heeft
ManyOS uiteindelijk allemaal nodig". Als eigenaar stel ik een andere vraag: **wat van dit
alles verdient zich terug voordat we er verder geld en tijd in steken?** Niet alles wat
architectonisch logisch is, is nu de moeite waard om te bouwen. Sommige onderdelen lossen
een probleem op dat we vandaag al goed genoeg oplossen met bestaande tools (Slack, Drive,
een spreadsheet) — daar geen engineering-tijd aan besteden is zelf ook een keuze met
rendement.

---

## 1. Wat bespaart ons binnen 30 dagen daadwerkelijk tijd?

| Onderdeel | Bespaart tijd binnen 30 dagen? | Waarom |
|---|---|---|
| **AI transcriptie & logging van ruwe footage** | **Ja, direct en meetbaar** | Editors wachten nu op handmatig uitloggen/doorzoeken van footage voor ze kunnen beginnen. Automatische transcriptie is technisch simpel te bouwen (dagen, geen weken) en scheelt direct wachttijd in het kritieke 24H-pad. |
| **Gestructureerd intake-formulier (zonder AI)** | Ja, bescheiden | Een vast format voor een brief voorkomt heen-en-weer mailen over ontbrekende info. Dit hoeft geen AI te zijn — een goed formulier is al winst. |
| Core datamodel + custom statusbord | **Nee, niet op zichzelf** | Een spreadsheet of Slack-kanaal doet dit vandaag al "goed genoeg". Dit zelf bouwen kost meer tijd dan het de eerste maand oplevert — dit is infrastructuur, geen tijdsbesparing. |
| Review & approval portaal | Nee, te groot voor 30 dagen | Waardevol, maar client-facing UX kost weken, niet dagen, om goed te doen. Fout hierin kost ons klantvertrouwen. |
| AI rough-cut assistent | Nee | Kwaliteit is onzeker, risico op ruis die editor-tijd juist kóst in plaats van bespaart. Pas zinvol als transcriptie/tagging al staat. |
| Delivery-automatisering | Nee, niet urgent | Export/levering is vandaag geen aantoonbare bottleneck vergeleken met footage-prep en klant-feedback. |
| Scheduling & capaciteit | Nee | Heeft historische data nodig om nuttig te zijn — die hebben we na 30 dagen nog niet. |
| Analytics/SLA-dashboard | Nee | Kip-en-ei: eerst moet er iets zijn om te meten. Nu bouwen is gokken op aannames. |
| AI-orchestratielaag (generieke infra) | Nee | Premature abstractie. Bouw dit pas als er twee of meer agents zijn die het daadwerkelijk nodig hebben. |

**Conclusie:** er is precies één onderdeel met een directe, meetbare terugverdientijd
binnen 30 dagen: **AI transcriptie & logging.** Het intake-formulier is een goedkope
bijkomstige winst, geen los project.

---

## 2. Wat is "nice to have" en moet wachten?

- **Review & approval portaal** — wachten tot na v0.1. Blijf voorlopig werken met wat we
  nu gebruiken (Frame.io/mail/WhatsApp) voor klantfeedback.
- **AI rough-cut assistent** — wachten tot de transcriptie-pijplijn bewezen heeft dat
  hij betrouwbaar en snel is. Zonder goede transcripten heeft een rough-cut-agent niets
  om op te bouwen.
- **Scheduling & capaciteitsintelligentie** — wachten tot we data hebben. Nu bouwen is
  koffiedik kijken.
- **Analytics/SLA-dashboard** — wachten tot er meetbare gebeurtenissen zijn om te tonen.
  Voor nu: een simpele handmatige check (voldeden we aan 24H, ja/nee) is genoeg.
- **Custom Core datamodel/statusbord als eigen software** — wachten. Gebruik voorlopig
  een spreadsheet of Notion/Airtable als project-overzicht. Bouw dit pas zodra het
  probleem "we lopen tools voorbij" zich echt voordoet, niet omdat het architectonisch
  netjes is.
- **Generieke AI-orchestratielaag, CRM/facturatie-koppelingen** — duidelijk later,
  geen discussie.

De vuistregel: als een bestaand, goedkoop tool het probleem vandaag al "goed genoeg"
oplost, bouwen we het niet zelf — engineering-tijd gaat naar wat *niemand anders* voor
ons oplost.

---

## 3. Met 2 weken: wat zou ik bouwen?

**Eén ding: een automatische pijplijn van ruwe footage naar doorzoekbaar, getagd
transcript, geleverd op de plek waar het team al werkt.**

Niet meer dan dat. Concreet:

1. Footage komt binnen op de plek waar het nu ook al binnenkomt (bijv. een aangewezen
   Drive-map of upload-locatie).
2. Een automatisering pikt dit op, stuurt het naar een transcriptie-API, en genereert:
   - een doorzoekbaar transcript
   - basis-tags (sprekers, ruwe scène-indeling waar mogelijk)
3. Het resultaat wordt gepost/gedeeld waar de editor het al zoekt (Slack-kanaal of
   bestand naast de footage) — **geen nieuwe interface, geen nieuw dashboard.**

**Expliciet niet in scope binnen deze 2 weken:**
- Geen eigen project/klanten-datamodel.
- Geen klantportaal.
- Geen dashboard of nieuwe UI — we voegen automatisering toe aan bestaande gewoontes,
  we vervangen ze niet.
- Geen AI-rough-cut, geen scheduling, geen analytics.

**Succesmeting:** tijd tussen "footage binnen" en "editor kan beginnen met snijden",
gemeten op een handvol echte projecten vóór en na. Als dat aantoonbaar korter wordt,
is het bewezen. Zo niet, dan hebben we in 2 weken geleerd zonder een groot systeem te
hebben gebouwd.

---

## 4. De kleinste versie van ManyOS die al waarde levert (voorstel v0.1)

**ManyOS v0.1 = de transcriptie/logging-automatisering, los toegevoegd aan het bestaande
proces. Geen platform, geen "OS" in de volledige zin — één bewezen automatisering.**

| Wat wél | Wat niet (nog) |
|---|---|
| Automatische transcriptie + tagging van binnenkomende footage | Eigen Project/Klant/Taken-datamodel |
| Output op de plek waar editors nu al werken (Slack/bestand) | Nieuw dashboard of klantportaal |
| Simpele log van welke bestanden verwerkt zijn (voor controle, geen UI) | Analytics, scheduling, AI-rough-cut |
| Meting: tijd tot editor start, vóór/na | Client-facing functionaliteit |

**Waarom dit de juiste "kleinste versie" is:**
- Het levert vanaf dag één meetbare tijdswinst op het enige dat telt: het 24H-venster.
- Het risico is laag: puur intern, klant merkt er niets van als het een keer misgaat.
- Het is een investeringsbeslissing in stukjes: pas als dit bewezen werkt, is er een
  onderbouwde reden om te investeren in een eigen datamodel, dashboard of verdere
  automatisering (zoals in het architectuurdocument beschreven). Tot die tijd is verdere
  bouw giswerk op onze kosten.

**Wat de architectuur-versie (v0.1 in het vorige document: "Core datamodel +
statusbord") hierin verandert:** die schuift op naar **v0.2 of later**, en alleen als
blijkt dat spreadsheets/Notion het écht niet meer trekken. Rendement gaat voor
architecturale netheid.
