# AI Agents: Intensive Vibe Coding — Capstone Project

Capstone projekt za **Google × Kaggle 5-Day AI Agents: Intensive Vibe Coding** tečaj (lipanj 2026).

## Zadatak

Izgraditi **AI agenta koji rješava stvarni problem** — pomaže ljudima ili poboljšava
svakodnevni život — primjenom onoga što je naučeno na tečaju.

Format je **HACKATHON** (ne klasični Kaggle leaderboard):
- nema zadanog dataseta (competition `NOTE.md`: *"This is a Hackathon with no provided dataset."*)
- ocjenjuje **Google po rubrici** (judged submission), ne automatski score
- **jedan (1) submission po timu/osobi**

Tečaj pokriva (Day 1–5): agente & vibe coding → tools & interoperabilnost →
context engineering (sessions, skills, memory) → quality & security →
prototype-to-production (cloud deploy, observability).

## Tračevi — biramo JEDAN

1. **Agents for Good** — društveni izazovi: edukacija, zdravstvo, poljoprivreda, umjetnost.
2. **Agents for Business** — enterprise problemi: expense management, pipeline optimizacija,
   generiranje uvida, product innovation.
3. **Concierge Agents** — sigurni osobni asistenti: planiranje, organizacija, osobni taskovi
   uz zaštitu korisničkih podataka.
4. **Freestyle** — bilo što kreativno/eksperimentalno, uz jak agent design i stvarnu korisnost.

> **Odabrani trač:** _(TBD — vidi `IDEAS.md` kad ga dodamo)_

## Submission (rok: 6.7.2026. 23:59 PT  ==  7.7.2026. 08:59 GMT+2)

Jedan submission po timu/osobi. Tim do **4** sudionika.

- [ ] Writeup na Kaggle-u (glavni artefakt: problem, rješenje, arhitektura)
- [ ] Video (demo + objašnjenje)
- [ ] Kratak rationale
- [ ] Link na kod (ovaj repo)

**Nagrade:** top 3 tima u svakom traču → Kaggle swag + feature na Kaggle social media.
Winners objavljeni do kraja srpnja 2026.

> **Licenca:** pobjednički kod ide pod **CC-BY 4.0** (open source). Repo smije ostati
> private dok radimo; ako pobijedimo, objavljujemo ga.

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
├── data/NOTE.md       # competition note (nema dataseta)
├── .env.example       # predložak env varijabli (bez tajni)
├── requirements.txt   # ovisnosti
├── .gitignore
└── README.md
```

## Korisni linkovi

- [Capstone competition (Kaggle)](https://www.kaggle.com/competitions/vibecoding-agents-capstone-project)
- [5-Day course (Kaggle)](https://www.kaggle.com/competitions/5-day-ai-agents-intensive-vibecoding-course-with-google)
