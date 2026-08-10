# ManyOS — Technische Architectuur & Roadmap

**Status:** Voorstel (v0.1 concept) — nog geen code
**Auteur:** Claude (in rol van CTO), voor ManyFast
**Datum:** 2026-08-03

## 0. Context

ManyFast is een videoproductiebureau met als kernbelofte **24H delivery**. Dat betekent dat
de tijd tussen "brief binnen" en "video geleverd" de belangrijkste metric van het bedrijf is.
ManyOS is het interne AI-besturingssysteem dat dit proces moet ondersteunen: niet één los
tool, maar de ruggengraat waarop alle productiestappen, mensen en AI-agents samenkomen.

Dit document beschrijft:
1. Welke onderdelen ManyOS nodig heeft
2. De technische architectuur
3. De roadmap van v0.1 → v1.0
4. Welke module we als eerste moeten bouwen, en waarom

Er wordt in dit document **geen functionaliteit gebouwd** — dit is het ontwerp waarop we
afstemmen voordat we beginnen.

---

## 1. Procesanalyse: wat moet ManyOS ondersteunen?

Een typisch traject bij ManyFast ziet er ruwweg zo uit:

```
Intake/Brief → Planning → Footage/Asset binnen → Editing → Review/Feedback → Delivery
```

Elke stap heeft vandaag waarschijnlijk een eigen los tool (mail, Slack, WeTransfer,
Drive, Premiere, WhatsApp, spreadsheets). Dat werkt tegen de 24H-belofte, omdat:
- **Overdrachten tussen stappen** (brief → editor, footage → editor, cut → client) tijd
  kosten en foutgevoelig zijn.
- **Wachttijd op mensen** (client feedback, interne review) niet zichtbaar of stuurbaar is.
- **Herhaalbare taken** (transcriberen, loggen van footage, formatteren voor export,
  statusupdates sturen) handmatig gebeuren terwijl ze uitstekend automatiseerbaar zijn.

Op basis hiervan onderscheid ik de onderdelen die ManyOS nodig heeft:

| # | Onderdeel | Functie |
|---|---|---|
| 1 | **Project & Taken Core** | Eén bron van waarheid: klanten, projecten, briefs, taken, status, deadlines |
| 2 | **Intake / Briefing** | Gestructureerd opnemen van een klantbrief (vorm, doel, deadline, stijl) → automatisch project + taken aanmaken |
| 3 | **Asset Management (DAM)** | Ontvangst, opslag en organisatie van ruwe footage en outputs |
| 4 | **Asset Intelligence** | Automatisch transcriberen, taggen en doorzoekbaar maken van ruwe footage |
| 5 | **Editing / Productie Orkestratie** | Taakverdeling naar editors, versiebeheer, AI-ondersteunde rough cuts |
| 6 | **Review & Approval Portal** | Klant bekijkt concept, laat tijdgestempelde feedback achter, revisieronde wordt getrackt |
| 7 | **Delivery Automation** | Automatisch exporteren in juiste formaten/platformen en leveren aan klant |
| 8 | **Scheduling & Capaciteit** | Wie heeft ruimte, wat is het risico op het missen van de 24H-deadline |
| 9 | **Communicatie & Notificaties** | Automatische statusupdates naar klant en team (Slack/e-mail/WhatsApp) |
| 10 | **Analytics & SLA-monitoring** | Doorlooptijd per stap, bottleneck-detectie, 24H-compliance rapportage |
| 11 | **AI Orchestratie-laag** | De "OS-kern": koppelt agents aan events, regelt welke AI-taak wanneer draait |
| 12 | **Identity & Toegang** | Rollen voor team, editors, freelancers en klanten |

Onderdeel 11 (AI Orchestratie-laag) is conceptueel het hart van "ManyOS": het is niet één
feature, maar de laag die events uit de andere onderdelen oppikt en er AI-agents op
loslaat (transcriberen zodra footage binnenkomt, brief parsen zodra intake binnenkomt,
klant notificeren zodra een revisie klaarstaat, enzovoort).

---

## 2. Technische Architectuur

### 2.1 Architectuurprincipes

- **Eén source of truth, event-driven.** Elke statusverandering (brief binnen, footage
  geüpload, cut klaar, feedback ontvangen) is een event. Modules reageren op events in
  plaats van dat ze hard aan elkaar gekoppeld zijn. Dit maakt het makkelijk om later
  AI-agents toe te voegen zonder bestaande modules aan te passen.
- **AI als laag, niet als losse features.** Elke AI-taak (transcriptie, tagging, rough
  cut voorstel, klantcommunicatie-concept) is een "agent" die luistert naar events en een
  resultaat terugschrijft naar de Core. Mensen blijven in de loop voor alles wat naar de
  klant gaat, tenzij expliciet geautomatiseerd.
- **Snelheid = zichtbaarheid.** Omdat 24H delivery de belofte is, moet elk moment in het
  proces een meetbare timestamp hebben. Zonder meten geen sturen.
- **Boring core, interessante randen.** De Project/Taken/Asset-datamodellen moeten saai en
  stabiel zijn (relationele database). De AI-orchestratie en agents mogen sneller
  itereren en experimenteler zijn.
- **Klein beginnen, geen premature schaalarchitectuur.** ManyFast is een bureau, geen
  platform met duizenden tenants. Kies pragmatische, bewezen technologie; optimaliseer
  voor snelheid van bouwen en aanpassen, niet voor hypothetische schaal.

### 2.2 Lagen

```
┌─────────────────────────────────────────────────────────────┐
│  Interfaces                                                  │
│  · Intern dashboard (team)  · Klantportaal  · Slack/e-mailbot│
├─────────────────────────────────────────────────────────────┤
│  AI Orchestratie-laag                                        │
│  · Event bus  · Agent runner  · Job queue  · Agent-registry  │
│    (transcriptie-agent, brief-parser-agent, rough-cut-agent, │
│     notificatie-agent, QC-agent, ...)                        │
├─────────────────────────────────────────────────────────────┤
│  Core Domain / Data-laag                                     │
│  · Klanten · Projecten · Briefs · Taken · Assets · Reviews    │
│  · Deliverables · Status & tijdlijn (source of truth)         │
├─────────────────────────────────────────────────────────────┤
│  Integraties                                                  │
│  · Object storage (video/audio) · Transcriptie-API            │
│  · Editing tools (Premiere/DaVinci export-hooks) · Frame.io   │
│  · Slack/e-mail/WhatsApp · CRM/facturatie (later)             │
└─────────────────────────────────────────────────────────────┘
```

### 2.3 Kern datamodel (conceptueel)

- **Client** — klantgegevens, huisstijl/brand-guidelines, voorkeuren
- **Project** — gekoppeld aan client, heeft deadline (24H-klok start hier), status
- **Brief** — de intake: doel, vorm, lengte, stijl, referenties
- **Asset** — ruwe footage/audio/beeldmateriaal, met metadata en (later, via Asset Intelligence) transcript/tags
- **Task** — werkeenheid binnen een project (bijv. "rough cut maken", "review verwerken")
- **Deliverable** — output-bestand(en) + formaat/platform-eisen
- **Review** — feedbackronde op een deliverable, met tijdgestempelde comments
- **AgentRun** — log van elke AI-taak: input, output, welk agent, welk event triggerde het
- **Event** — de ruggengraat: elke statusverandering in het systeem

### 2.4 Technologievoorstel (richting, geen keuze in beton)

- **Backend/API**: TypeScript (Node) of Python — afhankelijk van waar het team al sterk in
  is; Python ligt voor de hand gezien de AI/agent-laag.
- **Database**: PostgreSQL als source of truth.
- **Object storage**: S3-compatible (bijv. Cloudflare R2 of AWS S3) voor video/audio.
- **Event/Queue**: iets lichts om te beginnen (bijv. Postgres-based queue of Redis),
  geen zware message-broker nodig bij dit volume.
- **AI-laag**: Claude (Anthropic) voor taalgebonden agents (brief parsen, samenvatten,
  klantcommunicatie, QC tegen brand-guidelines); gespecialiseerde API's voor transcriptie
  (bijv. Whisper-achtige diensten) waar een taalmodel niet de juiste tool is.
- **Interfaces**: lichte webapp voor intern dashboard + klantportaal; notificaties via
  bestaande kanalen (Slack/e-mail) in plaats van een nieuw kanaal forceren.

Dit zijn richtingen, geen onomkeerbare keuzes — bij de eerste module bepalen we dit
concreet.

---

## 3. Roadmap v0.1 → v1.0

| Versie | Focus | Wat het oplevert |
|---|---|---|
| **v0.1** | Core datamodel + statusbord | Eén plek met alle projecten/taken/deadlines i.p.v. verspreide tools. Nog geen AI. Fundament waarop alles verder bouwt. |
| **v0.2** | Intake/briefing automatisering | Gestructureerd brief-formulier → AI-agent zet brief om in project + takenlijst. Eerste "AI parseert iets" moment. |
| **v0.3** | Asset ingestion + Asset Intelligence | Footage upload triggert automatische transcriptie + tagging. Editors starten met doorzoekbaar materiaal i.p.v. zelf uitloggen. |
| **v0.4** | Review & approval portaal | Klant bekijkt concept, laat tijdgestempelde feedback achter; revisierondes worden getrackt i.p.v. los in mail/WhatsApp. |
| **v0.5** | AI rough-cut assistent | Agent stelt op basis van transcript + brief een eerste selectie/rough cut voor; editor verfijnt i.p.v. van nul begint. |
| **v0.6** | Delivery automatisering | Automatische export per platform/formaat + automatische levering en notificatie aan klant. |
| **v0.7** | Scheduling & capaciteitsintelligentie | Zicht op wie ruimte heeft, vroegtijdige risicosignalering voor het missen van de 24H-deadline. |
| **v0.8** | Analytics & SLA-dashboard | Doorlooptijd per stap, bottleneck-rapportage, 24H-compliance zichtbaar per project/klant/periode. |
| **v0.9** | Uitbreiding integraties | Koppelingen met CRM/facturatie, Slack/WhatsApp-bot als volwaardig interface, externe tools (Frame.io e.d.). |
| **v1.0** | Volledige AI-orchestratie | Agents handelen routinetaken end-to-end af met human-in-the-loop waar nodig; scheduling optimaliseert zichzelf; gesloten keten van brief tot delivery. |

Let op: dit is een **volgorde van waarde**, geen vaste tijdlijn. Elke versie is pas
"klaar" als hij in de praktijk tijd bespaart op het 24H-traject — niet als de code af is.

---

## 4. Advies: welke module bouwen we als eerste?

### 4.1 Impact vs. complexiteit

| Module | Impact op 24H-belofte | Complexiteit | Risico |
|---|---|---|---|
| Core datamodel + statusbord (v0.1) | Middel (randvoorwaardelijk, geen directe tijdswinst) | Laag | Laag |
| Intake/briefing-automatisering (v0.2) | Middel-hoog | Middel | Laag — puur intern |
| **Asset ingestion + Asset Intelligence (v0.3)** | **Hoog** | **Laag-middel** | **Laag — puur intern, geen klant-facing risico** |
| Review & approval portaal (v0.4) | Hoog | Middel-hoog | Middel — klant-facing, UX moet goed zijn |
| AI rough-cut assistent (v0.5) | Hoog, maar pas nuttig als v0.3 al staat | Hoog | Middel — kwaliteit van output is kritisch |
| Delivery automatisering (v0.6) | Middel | Middel | Laag |

### 4.2 Aanbeveling

**Bouw eerst het fundament (v0.1: Core datamodel + statusbord)** — niet omdat het veel
impact heeft op zichzelf, maar omdat geen enkele andere module zonder een source of truth
kan bestaan. Dit is bewust klein en saai houden.

**Als eerste écht impactvolle AI-module: Asset Ingestion + Asset Intelligence (v0.3).**
Redenen:

1. **Grootste directe tijdswinst binnen het 24H-venster.** Het uitloggen/doorzoeken van
   ruwe footage is bij videoproductie doorgaans een van de grootste, meest onzichtbare
   tijdvreters vóór een editor daadwerkelijk kan beginnen te snijden. Automatisch
   transcriberen en taggen geeft editors direct een vliegende start.
2. **Technisch bewezen en beheersbaar.** Transcriptie is een opgelost probleem
   (spraak-naar-tekst API's zijn volwassen); dit vraagt geen complex multi-stap
   agent-gedrag of subjectieve kwaliteitsbeoordeling zoals een rough-cut-agent dat wel
   vraagt.
3. **Geen klant-facing risico.** Dit speelt zich volledig intern af — als de tagging een
   keer niet perfect is, kost dat geen klantvertrouwen, alleen een check door de editor.
   Dat maakt het een veilige eerste plek om AI daadwerkelijk in productie te zetten.
4. **Meetbaar en overtuigend.** Tijdswinst per project is direct te meten (tijd tot
   editor start snijden), wat een sterke, aantoonbare business case oplevert voor verdere
   investering in ManyOS.

De review/approval-module (v0.4) is de andere sterke kandidaat qua impact — feedbackloops
met klanten zijn vaak de andere grote tijdvreter binnen 24H — maar is klant-facing en dus
gevoeliger voor UX-fouten. Die bouwen we ná v0.3, zodra het interne fundament staat.

---

## 5. Vervolgstappen

1. Akkoord op deze architectuur en volgorde.
2. v0.1 scherp specificeren (welke velden, welke statussen, wie gebruikt het dagelijks).
3. Concrete technologiekeuzes vastleggen (backend-taal, hosting, storage-provider).
4. Pas dan: eerste code voor v0.1 (Core datamodel + statusbord).
# ManyOS Vision

## Why ManyOS exists

ManyOS exists to make creative production radically more efficient without sacrificing quality.

It is built by ManyFast, for ManyFast.

Every feature must help us deliver better work, faster, with less repetitive manual effort.

Our goal is not to replace creative people.

Our goal is to remove everything that slows creative people down.

---

# Mission

Build the most efficient AI-powered production operating system for creative teams.

Every workflow should become:

* faster
* smarter
* more consistent
* easier to scale

---

# Long-term Vision

ManyOS starts as the internal operating system of ManyFast.

When mature, it can become the operating system for creative production companies worldwide.

We build for ourselves first.

If it solves our own problems exceptionally well, it may become a product for others.

---

# Core Principles

## 1. ManyFast First

Every feature must solve a real problem inside ManyFast before it is generalized.

No speculative features.

---

## 2. Time is the Primary Metric

The purpose of ManyOS is to save time.

Every module should reduce manual work or decision-making.

If a feature saves no measurable time, it should not be built.

---

## 3. Simplicity Wins

Prefer simple systems over clever systems.

Complexity is only acceptable when it creates significant value.

---

## 4. Modular by Design

Every capability should exist as its own module.

Modules should be replaceable, extendable and independently testable.

---

## 5. AI as an Assistant

AI should support people, not replace them.

Humans always make the final creative decisions.

---

## 6. Local First

Develop locally.

Design so migration to cloud infrastructure is possible without rebuilding the system.

---

## 7. Documentation is Product

Good documentation is part of the product.

Every architectural decision should be documented.

---

## 8. Build Once, Reuse Forever

Avoid solving the same problem twice.

Every reusable workflow should become a reusable module.

---

# Success Metrics

ManyOS is successful when it:

* reduces production time
* reduces repetitive manual work
* improves consistency
* reduces errors
* makes onboarding easier
* allows ManyFast to scale without proportional growth in workload

---

# What ManyOS is NOT

ManyOS is not:

* generic project management software
* generic cloud storage
* generic video editing software

ManyOS is the intelligence layer that connects creative production workflows.

---

# Decision Filter

Before building any feature, ask:

1. Does this solve a real ManyFast problem?
2. Does it save measurable time?
3. Can it be reused?
4. Is this the simplest solution?
5. Will this still make sense in three years?

If the answer is "no" to multiple questions, do not build it.

---

# Motto

**Create more. Wait less.**

ManyOS exists so that creative professionals spend their time creating, not managing files, searching footage, or repeating manual work.
