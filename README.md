# AI Agents: Intensive Vibe Coding — Capstone (Kaggriculture)

Capstone projekt za **Google × Kaggle 5-Day AI Agents: Intensive Vibe Coding** tečaj (lipanj 2026).

## Zadatak

**Kaggriculture** — farming simulacija u kojoj gradimo i deployamo **autonomnog agenta**
koji upravlja resursima i pokušava nadmašiti druge u dinamičnom okruženju.

Tečaj pokriva (Day 1–5): agente & vibe coding → tools & interoperabilnost →
context engineering (sessions, skills, memory) → quality & security →
prototype-to-production (cloud deploy, observability).

> **Format: HACKATHON, bez dataseta.** Competition `NOTE.md` izričito kaže
> *"This is a Hackathon with no provided dataset."* → nema ML pipeline-a
> (treniranje/EDA); vrijednost je u agentu koji se gradi i deploya + writeup/video.

## Submission (rok: 2026-07-07 06:59 UTC)

- [ ] Writeup na Kaggle-u
- [ ] Video objašnjenje
- [ ] Kratak rationale
- [ ] Link na kod (ovaj repo)

> Detaljne tehničke specifikacije agenta/simulacije objavljuju se tijekom tečaja.
> Skeleton se nadograđuje kad dobijemo točan API Kaggriculture okruženja.

## Setup

```bash
# 1. Aktiviraj venv (izolirano okruženje)
source venv/bin/activate

# 2. Instaliraj ovisnosti
pip install -r requirements.txt

# 3. Učitaj env varijable (tajni .env je u venv/.env, gitignoran)
set -a; source venv/.env; set +a
```

## Struktura

```
.
├── venv/              # izolirani Python env (gitignoran); sadrži i .env s API ključem
│   └── .env           # KAGGLE_KEY (tajna — NIKAD se ne commita)
├── .env.example       # predložak env varijabli (bez tajni)
├── requirements.txt   # ovisnosti
├── .gitignore
└── README.md
```

## Korisni linkovi

- [Capstone competition (Kaggle)](https://www.kaggle.com/competitions/vibecoding-agents-capstone-project)
- [5-Day course (Kaggle)](https://www.kaggle.com/competitions/5-day-ai-agents-intensive-vibecoding-course-with-google)
