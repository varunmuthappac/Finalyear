# RenalCare AI — Completion Roadmap

**Purpose**: capture what needs to be fixed, what the current repo actually contains, and what should be deferred until the API/LLM decision is made.

**Environment assumption**: macOS with zsh. The instructions below use macOS-compatible shell commands.

---

## Table of contents

- [Current repository status](#current-repository-status)
- [Budget allocation](#budget-allocation-target)
- [Phase 0 — Environment setup](#phase-0--environment-setup)
- [Phase 1 — Hygiene and cleanup](#phase-1--hygiene-and-cleanup)
- [Phase 2 — Core feature fixes](#phase-2--core-feature-fixes)
- [Phase 3 — Scan analysis model](#phase-3--scan-analysis-model)
- [Phase 4 — Multi-patient auth](#phase-4--multi-patient-auth)
- [Phase 5 — Health Goals / LLM](#phase-5--health-goals--llm)
- [Sequencing & effort estimate](#sequencing--effort-estimate)
- [Definition of done](#definition-of-done)
- [Rollback / fallback plan](#rollback--fallback-plan)

---

## Current repository status

- Backend: FastAPI with patient management, scan upload/analysis, hydration tracking, meals, risk insights, risk trend history, appointments, and doctor recommendations.
- Multi-user auth: signup/login/logout, hashed passwords, server-revoked session tokens, per-account data isolation on all patient endpoints.
- Vision model: MobileNetV2 normal/stone classifier, **trained and calibrated** on the public CT-KIDNEY dataset (1926 train / 412 val / 416 test images); held-out test accuracy/precision/recall/F1 all 1.0, served honestly by `/api/vision/metrics` and shown in the UI. Stone-size estimation is calibrated via `calibrate_size_scale.py` (median ~6mm, matching typical clinical ranges).
- Risk trend history: monthly risk-score snapshots recorded the first time `/api/risk-insights/{id}` is hit each calendar month, surfaced via `/api/risk-insights/{id}/history` and charted in the UI — accumulates real history rather than fabricating one.
- Health Goals: rule-based by default with optional NVIDIA NIM LLM generation and graceful fallback + daily cache.
- Frontend: auth-gated dashboard with live backend data (hydration, risk, latest scan), scan upload, hydration tracker, risk insights with trend chart, appointment booking (persisted), and health goals.
- Tests: 41-pass pytest suite (isolated DB), 0 skipped — covering auth, isolation, water/meal round-trips, appointments, goals, risk history, vision metrics, and the scan endpoint (the vision-model-dependent tests run for real now that a model is trained).

---

## Budget allocation (target)

| Item | Cost | Notes |
|---|---|---|
| Data / training environment | ₹0 | Free Kaggle dataset, trained locally (GPU or CPU). |
| Model training / integration | ₹0 | Done — MobileNetV2, trained and calibrated locally, no paid service involved. |
| Health Goals / LLM | ~₹150–250 | Code path is ready; stays ₹0 until `ENABLE_LLM_GOALS=true` with a real NVIDIA API key. |
| Buffer / contingency | ~₹150–250 | Keep budget headroom for any paid service. |
| **Total** | **≤ ₹500** | |

> Actual spend so far: ₹0. The LLM spend only starts if/when `ENABLE_LLM_GOALS` is turned on.

---

## Phase 0 — Environment setup

1. Confirm tools:
   - `python3 --version` (3.10+)
   - `node --version` (18+)
   - `npm --version`
   - `git --version`

2. Install dependencies:
   - `cd backend && python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt`
   - `cd ../ && npm install`

3. Sanity check:
   - Run `python init_db.py` in `backend` to seed the demo patient.
   - Run `python main.py` in `backend` and `npm run dev` in `renalcare`.
   - Confirm backend docs at `http://localhost:8001/docs` and frontend at the Vite URL.

---

## Phase 1 — Hygiene and cleanup

This is the first priority.

### 1.1 Fix repository hygiene

- Add backend artifacts to `.gitignore`:
  - `backend/__pycache__/`
  - `backend/*.pyc`
  - `backend/venv/`
  - `backend/*.db`
  - `backend/uploads/*`
  - `.env`
  - `backend/.env`
- Keep a placeholder `backend/uploads/.gitkeep` if needed.

### 1.2 Remove stale assets and docs

- Remove duplicate or stale root docs that are no longer accurate.
- Remove the top-level `api.js` file if it is not used.
- Keep only the accurate `README.md` and `backend/README.md`.

### 1.3 Make `.env` local-only

- Keep only `.env.example` in git with placeholders.
- Do not commit `backend/.env`.

---

## Phase 2 — Core feature fixes

This phase is the current implementation priority.

### 2.1 Appointment persistence

What needs to be done:
- Wire the `Book Now` button to call the backend appointment creation endpoint.
- Ensure the backend returns a real appointment id.
- Use the real id when saving recommendations, not a synthetic local id.
- Verify the appointment is persisted and visible via `/api/appointments/{patient_id}`.

### 2.2 Dashboard should be live data

What needs to be done:
- Replace static dashboard cards with real values from backend APIs.
- Use existing API helpers like `getHealthSummary()` and `getRiskInsights()`.
- Replace the fake drag-and-drop upload area with the real scan upload flow or a navigation path to the scan tab.
- Avoid showing placeholder metrics as if they are real.

### 2.3 Clean stale frontend/backend imports

What needs to be done:
- Remove unused imports such as `pickle` from `backend/main.py`.
- Remove unused frontend imports like `getPatient` from `ImageUploadComponent.jsx` if the function is not used.

---

## Phase 3 — Scan analysis model

**Done.** A MobileNetV2 binary classifier (normal vs. stone) is trained on the public CT-KIDNEY
dataset and wired into `/api/analyze-scan`. Stone-size estimation is calibrated against the
training population via `calibrate_size_scale.py`.

### 3.1 Current status

- `models/kidney_stone_cnn.pth` and `models/vision_metrics.json` are produced locally by
  `prepare_dataset.py` → `train_vision_model.py` (both gitignored — regenerate after a fresh
  clone, see `backend/README.md`).
- Held-out test metrics (416 test images, zero overlap with train/val): accuracy, precision,
  recall, and F1 all 1.0. This is consistent with CT-KIDNEY Normal-vs-Stone being a commonly
  reported "easy" benchmark for transfer-learning setups, not a leakage artifact — verified no
  path overlap across splits.
- GPU training (CUDA) is ~15-20x faster per epoch than CPU; see `backend/requirements.txt` for
  the install command.

### 3.2 Possible future work

- Multi-class (Normal/Cyst/Tumor/Stone) instead of binary, if the product ever needs it.
- Location/severity estimation beyond the current binary presence + Grad-CAM size approximation.

---

## Phase 4 — Multi-patient auth

**Done.** Signup/login/logout with hashed passwords and server-revoked session tokens.
`require_patient_access` / `ensure_patient_access` enforce per-account data isolation on every
patient-scoped endpoint (verified: cross-account access attempts return `403`, covered by the
`TestIsolation` test class). A seeded demo account (`demo@renalcare.ai` / `demo1234`) remains
available alongside real signup.

---

## Phase 5 — Health Goals / LLM

**Mostly done.** The Goals tab is real, not a placeholder: `GET /api/goals/{patient_id}` generates
4 personalized goals from the patient's own tracked data, cached once per day. NVIDIA NIM
(Llama 3.1) LLM generation is wired in and gated behind `ENABLE_LLM_GOALS` — off by default, so
the shipped behavior has no paid dependency.

### 5.1 Current state

- Rule-based generation is the default and requires no API key or cost.
- LLM generation (NVIDIA NIM) is implemented and tested but disabled until a real
  `NVIDIA_API_KEY` is provided and `ENABLE_LLM_GOALS=true` is set — see root `README.md`.
- If the LLM call fails for any reason (bad key, rate limit, network), the endpoint degrades
  gracefully to the rule-based goals rather than erroring.

### 5.2 Remaining decision

- Whether to ever turn `ENABLE_LLM_GOALS` on for real usage is a budget/product call, not an
  engineering one — the code path is ready either way.

---

## Sequencing & effort estimate

- Phase 0: environment setup — ~30 min
- Phase 1: hygiene and cleanup — ~15 min
- Phase 2: core fixes — ~1 day
- Phase 3: scan model — done (dataset download + GPU training + calibration: ~15 min hands-on, most of it unattended)
- Phase 4: auth — done
- Phase 5: Goals / LLM — deferred until decision

---

## Definition of done

- [x] Repo runs end to end after fresh clone and setup
- [x] No stale docs or duplicate top-level files remain
- [x] Dashboard uses live backend data, not placeholders
- [x] Appointment booking persists to the backend
- [x] `backend/.env` is local-only and gitignored
- [x] Goals / LLM work ships with a rule-based default and optional NVIDIA NIM path
- [x] Vision model trained on real data with honest held-out test metrics, wired into `/api/analyze-scan`
- [x] Risk trend history accumulates real monthly snapshots, no fabricated chart data

---

## Rollback / fallback plan

| Phase | If not finished in time | Fallback |
|---|---|---|
| Phase 3 | *(Done — trained and calibrated)* | If retraining is ever needed and doesn't converge well in time, `/api/analyze-scan` returns a clean `503`/error rather than a fabricated result — never silently falls back to a fake heuristic |
| Phase 4 | *(Done — full multi-user auth shipped)* | — |
| Phase 5 | LLM decision delayed | Keep Goals as a placeholder and postpone paid integration |

---
