# RenalCare AI

A kidney stone **prevention & management** demo application: track hydration and meals, run an
**image-level normal/stone classifier** on kidney CT scans, and get personalized daily goals and
appointment recommendations.

> **Important disclaimer:** this is a research/demo system, **not a medical device**. Scan analysis
> is a binary image-level classifier (normal vs. stone) and does **not** estimate stone size,
> location, severity, or composition. Always consult a physician.

---

## What's real here

| Feature | Status |
| --- | --- |
| Normal vs. stone scan classification (MobileNetV2) | Real model, trained on public data, honest held-out metrics shown in the UI |
| Multi-user accounts (signup/login, hashed passwords, session tokens) | Real, with per-account data isolation |
| Water intake & meal logging | Real, persisted in SQLite, live in the dashboard |
| Risk insights / recovery roadmap | Rule-based, computed from *your* tracked data (not fabricated charts) |
| Risk trend history | Real monthly snapshots, recorded the first time you check risk insights each month — starts accumulating from your first month, not backfilled |
| Daily health goals | Rule-based by default; optional NVIDIA NIM LLM generation |
| Appointments | Real booking persistence against the backend |

**What is NOT real:** size/location/severity/composition of stones (the model is image-level only),
anything presented as a diagnosis.

---

## Tech stack

- **Frontend:** React 19 + Vite + Tailwind CSS
- **Backend:** FastAPI + SQLAlchemy + SQLite
- **ML:** PyTorch (torchvision MobileNetV2)

---

## Quick start

### Prerequisites

- **Python 3.9+** with `venv`
- **Node.js 18+** (npm)

### 1. Backend

```bash
cd backend
python3 -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                # review the settings

python init_db.py                   # creates the DB + demo account
python -m uvicorn main:app --port 8001
```

The API runs at `http://localhost:8001` (Swagger docs at `/docs`).

### 2. Train the scan model (required for scan analysis)

```bash
cd backend
python prepare_dataset.py            # builds a deterministic, class-balanced 70/15/15 split
python train_vision_model.py         # trains MobileNetV2 -> models/kidney_stone_cnn.pth + metrics
```

Both scripts read from `backend/dataset/` (gitignored). See
[`backend/README.md`](backend/README.md) for how to download the public `CT-KIDNEY` dataset used
during development.

### 3. Frontend

```bash
npm install
npm run dev
```

Open `http://localhost:5173`. Log in with the demo account `demo@renalcare.ai / demo1234` or create
your own account.

---

## Tests

```bash
# Backend (isolated test DB, 41 tests)
cd backend
venv/bin/python -m pytest tests -q

# Frontend lint + build
npm run lint
npm run build
```

---

## Optional: AI-generated health goals (NVIDIA NIM)

Goals are generated from your tracked data by default (no external calls). To enable LLM generation:

1. Create a free account at [build.nvidia.com](https://build.nvidia.com) and get an API key.
2. In `backend/.env`:
   ```env
   ENABLE_LLM_GOALS=true
   NVIDIA_API_KEY=nvapi-...
   NVIDIA_GOALS_MODEL=meta/llama-3.1-8b-instruct
   ```
3. Restart the backend. If the LLM call fails for any reason, the API degrades gracefully to the
   rule-based goals.

---

## Project structure

```
renalcare/
├── src/                     # React frontend
│   ├── App.jsx              # Dashboard, auth gate, tab navigation
│   ├── api.js               # Backend client (auth token attached automatically)
│   └── components/          # Auth, Scan, Hydration, Risk, Appointments, Goals
├── backend/
│   ├── main.py              # FastAPI app + all endpoints
│   ├── auth.py              # Password hashing + session tokens + access control
│   ├── goals.py             # Health goals (NVIDIA NIM + rule-based fallback)
│   ├── vision_utils.py      # Model loading + honest metric reporting
│   ├── prepare_dataset.py   # Deterministic dataset split
│   ├── train_vision_model.py# MobileNetV2 training
│   ├── tests/               # pytest suite (isolated DB)
│   ├── models/              # Trained artifacts (gitignored)
│   └── dataset/             # Raw CT-KIDNEY data (gitignored)
└── eslint.config.js         # Lint ignores backend runtime dirs
```

---

## Known limitations

- The scan model is trained on a **small public dataset** (the CT-KIDNEY Normal vs. Stone classes)
  and is a research prototype. It is not validated for clinical use and can misclassify real scans.
- Stone size/location/severity/composition are **not** estimated; the UI labels them clearly.
- Risk scores and recovery roadmaps are simple rule-based estimates.
- Data is stored in SQLite — fine for a demo, not for production.
