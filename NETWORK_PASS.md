# NETWORK_PASS — koraci na mašini s mrežom

Sandbox u kojem je LaunchGuard izgrađen **nema mrežu za `pip install google-adk`**, pa je
deterministički core napravljen bez runtime ovisnosti (247 testova prolazi offline). Ovaj
runbook su koraci koje radiš na **svojoj mašini** (mreža + GCP + GitHub) da uključiš live dio
i snimiš demo. Ništa od ovoga nije refactor — sve je već wireano preko import-safe seam-ova.

> Preduvjeti: `gcloud` autentificiran, pristup `worknote-ai` GCP projektu (read-only je dovoljno),
> GitHub token (samo ako želiš pravi PR u demou), Gemini API ključ.

## 1. Instaliraj agent stack u venv

```bash
source venv/bin/activate
pip install -r requirements.txt        # ili: make install  (60s timeout)
python -c "import google.adk, google.genai; print('ADK OK')"
```

## 2. Postavi tajne (samo u venv/.env — gitignoran)

```bash
# venv/.env
GOOGLE_API_KEY=...            # https://aistudio.google.com/app/apikey
GITHUB_TOKEN=ghp_...          # opcionalno, samo za pravi PR
DATABASE_URL=postgresql://launchguard:launchguard@localhost:5432/launchguard_memory
```

## 3. (Opc.) Podigni pgvector za memoriju

```bash
make db-up        # docker-compose.dev.yml → postgres:16 + pgvector
# bez ovoga memorija radi na in-memory fallbacku (testovi svejedno prolaze)
```

## 4. Record mode — osvježi golden fixture pravim (redaktiranim) podacima

Snima stvarno stanje `worknote-ai` (read-only) u golden-JSON. Secret VRIJEDNOSTI se redaktiraju
pri snimanju — u fixture idu samo imena/postojanje grantova.

```bash
gcloud auth application-default login           # ADC za read-only describe/list
export LAUNCHGUARD_FIXTURE_MODE=record
python -m launchguard.tools.fixture_replay --target ~/repos/github/private/worknote-ai \
    --project <worknote-ai-project-id> --out fixtures/gcp/worknote-ai.json
# provjeri da NEMA vrijednosti tajni prije commita:
grep -RInE "AIza|ghp_|BEGIN .*PRIVATE KEY|secret.*=.*[A-Za-z0-9]{20}" fixtures/gcp/worknote-ai.json || echo "clean"
```

## 5. Wire pravi Gemini + embedder (umjesto deterministic fallbacka)

```python
from launchguard.ambiguity import set_gemini_classifier
from launchguard.memory import set_embedder
# set_gemini_classifier(<gemini callable>)   # ambiguity → pravi model
# set_embedder(<gemini embedder>)            # memory semantic recall
```
(Pozivi su već pripremljeni; samo injektiraš callable. Bez njih → deterministički fallback.)

## 6. adk web — hero trace za video

```bash
adk web launchguard/agent.py
# pokreni reconciliation nad worknote-ai (ili hero fixtureom)
# snimi trace: SECRET_FOO will-fail → fix prijedlog → guardrail blocked-write trip → scorecard
```

## 7. (Opc.) Pravi GitHub PR u demou

```bash
export LAUNCHGUARD_PR_MODE=real          # default je mock (renderira u eval/pr_preview/)
# FixWriter otvori PR na throwaway repou; human-in-the-loop ostaje (ne mergea sam)
```

## 8. Snimi video (hero demo, ~2-3 min)

Okosnica: **bug u service.yaml/IAM → LaunchGuard ga uhvati prije deploya → PR s fixom → scorecard.**
1. Pokaži `worknote-ai` (stvaran servis) kao metu
2. Dodaj `secretKeyRef` bez grant-a (ili pokaži postojeći gap)
3. LaunchGuard: will-fail delta + IAM diff + PR preview
4. Guardrail trip: pokušaj mutacije → blocked
5. Eval scorecard (precision/recall 1.00 preko 9 fixtura)
6. Framing rečenica: *"tri-source contract reconciliation — delta koju linter, security scan i deploy svaki zasebno ne vide"*

## 9. Kaggle submission

Vidi `WRITEUP.md` (draft) → finaliziraj → predaj writeup + video + link na repo prije
**2026-07-06 23:59 PT**.

---

**Sigurnosni podsjetnik:** nikad ne commitaj `venv/.env` ni record-mode output s vrijednostima
tajni. Prije svakog commita: `git diff --cached | grep -iE "AIza|ghp_|PRIVATE KEY"`.
