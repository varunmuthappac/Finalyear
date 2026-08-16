# RenalCare AI - Backend

FastAPI backend for RenalCare AI: auth, water/meal tracking, scan classification, risk insights,
health goals, and appointments.

---

## Quick Start

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env              # review settings
python init_db.py                 # create DB + demo account (demo@renalcare.ai / demo1234)
python -m uvicorn main:app --port 8001
```

API docs (Swagger UI): `http://localhost:8001/docs`

---

## Dataset & Model Training (for scan analysis)

The `/api/analyze-scan` endpoint needs a trained classifier (`models/kidney_stone_cnn.pth`). If the
model is missing, the endpoint returns `503` and `/api/health` reports `"vision_model":
"not_available"`.

1. **Download the dataset** into `dataset/` (gitignored). Development used the public
   `CT-KIDNEY-DATASET-Normal-Cyst-Tumor-Stone` from Kaggle, using the **Normal** and **Stone**
   classes only.

2. **Build a deterministic, class-balanced split** (70/15/15, no overlap between splits):
   ```bash
   python prepare_dataset.py
   ```
   This writes `dataset/manifest.json`.

3. **Train the classifier** (MobileNetV2, ImageNet-pretrained):
   ```bash
   python train_vision_model.py
   ```
   Artifacts:
   - `models/kidney_stone_cnn.pth` — model checkpoint
   - `models/vision_metrics.json` — honest held-out test metrics + training hyperparameters

   **No GPU needed** — `get_device()` auto-selects CUDA, then Apple Silicon (MPS), then falls
   back to CPU with no config required. Expect roughly **2-3 minutes on a CUDA GPU** vs.
   **30-50 minutes CPU-only** for the default 10 epochs (~1900 training images); this is a
   one-time cost, not something you repeat per app run.

   For an NVIDIA GPU, install the CUDA build of torch/torchvision *before* `pip install -r
   requirements.txt` (adjust `cu126` to whatever your driver supports):
   ```bash
   pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
   ```

4. **Calibrate the stone-size estimate** against the trained model (one-time, after training):
   ```bash
   python calibrate_size_scale.py
   ```
   Writes `models/stone_scale.json` with a population-derived mm-per-pixel scale (median stone
   ≈6mm, matching typical clinical ranges). Without this step, size estimates fall back to an
   uncalibrated default pixel spacing. Also GPU/CPU-portable (~1 minute GPU, a few minutes CPU
   for the default 400-image sample).

The reported accuracy is computed on a test split that never overlaps training data, and the same
numbers are surfaced by `GET /api/vision/metrics` and shown in the frontend.

### What the model does — and doesn't

- **Does:** binary image-level classification into `normal` or `stone`, with a confidence score.
- **Does (approximate):** estimate stone size from the model's own Grad-CAM attention region.
  The size is the largest in-image dimension of the strongest-attention blob, converted to mm
  using the calibrated pixel spacing from `calibrate_size_scale.py` (uploaded images carry no
  DICOM metadata, so this population-derived spacing is a documented approximation).
  It is surfaced as an estimate (`size_estimated: true`), not a clinical measurement.
- **Does not:** estimate stone location or composition. The API returns
  `stone_location = "Not estimated (image-level analysis only)"`, and `severity` of `none`/`present`.
  Stone type is captured as patient/user input (e.g. calcium oxalate), not inferred from the image.

---

## Authentication

All `/api/...` data endpoints require a Bearer token:

```bash
curl -X POST http://localhost:8001/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"name":"Jane","email":"jane@x.com","password":"password123","age":35,"gender":"female"}'
# -> {"token":"...","patient":{...}}

curl -X POST http://localhost:8001/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"jane@x.com","password":"password123"}'
# -> {"token":"...","patient":{...}}

curl http://localhost:8001/api/auth/me -H "Authorization: Bearer <token>"
```

- Passwords are stored as salted hashes (never plaintext).
- Logout revokes the session token server-side.
- `require_patient_access` / `ensure_patient_access` block access to other users' data (403).

---

## Main Endpoints

| Method | Path | Auth |
| --- | --- | --- |
| POST | `/api/auth/register` | no |
| POST | `/api/auth/login` | no |
| POST | `/api/auth/logout` | token |
| GET | `/api/auth/me` | token |
| GET | `/api/health` | no |
| GET | `/api/vision/metrics` | no |
| POST | `/api/analyze-scan?patient_id=..&stone_type=..` | token |
| GET | `/api/scans/{patient_id}` | token |
| GET | `/api/scans/detail/{scan_id}` | token |
| POST | `/api/water-intake` | token |
| GET | `/api/water-intake/{patient_id}/daily` | token |
| GET | `/api/water-intake/{patient_id}/history` | token |
| POST | `/api/meals` | token |
| GET | `/api/meals/{patient_id}/daily` | token |
| GET | `/api/meals/{patient_id}/history` | token |
| GET | `/api/diet-recommendations/{stone_type}` | no |
| GET | `/api/risk-insights/{patient_id}` | token |
| GET | `/api/risk-insights/{patient_id}/history` | token |
| GET | `/api/patients/{patient_id}/health-summary` | token |
| POST | `/api/appointments` | token |
| GET | `/api/appointments/{patient_id}` | token |
| POST | `/api/recommendations` | token |
| GET | `/api/goals/{patient_id}` | token |

### Analyze a scan

```bash
curl -X POST "http://localhost:8001/api/analyze-scan?patient_id=patient_demo_001&stone_type=unknown" \
  -H "Authorization: Bearer <token>" \
  -F "file=@scan.png"
```

Returns `prediction` (`normal`/`stone`), `confidence`, `model_version`, and explicit
"not estimated" fields for size/location.

---

## Health Goals

`GET /api/goals/{patient_id}` returns personalized daily goals. By default they are generated
deterministically from the user's tracked data (hydration, compliance, latest scan). To enable LLM
generation via NVIDIA NIM, set `ENABLE_LLM_GOALS=true` and a real `NVIDIA_API_KEY` in `.env`; on any
LLM failure the API falls back to rule-based goals. Results are cached per user per day.

---

## Running Tests

```bash
venv/bin/python -m pytest tests -q
```

The suite uses an isolated temporary SQLite database (set via `DATABASE_URL` before import), so it
never touches real data. It covers auth (register/login/logout/password hashing), cross-account data
isolation (403s), water/meal round-trips, appointment booking, goals fallback/caching, vision
metrics, and the scan endpoint (synthetic image; skipped if the model isn't trained).

---

## Environment Variables

See `.env.example`:

```env
DATABASE_URL=sqlite:///./renal_care.db
SECRET_KEY=change-me-in-production
VISION_MODEL_PATH=./models/kidney_stone_cnn.pth
VISION_METRICS_PATH=./models/vision_metrics.json
ENABLE_LLM_GOALS=false
NVIDIA_API_KEY=
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
NVIDIA_GOALS_MODEL=
```

## Troubleshooting

- **Port 8001 in use:** `lsof -i :8001`, then kill the PID.
- **`vision_model: not_available`:** run `prepare_dataset.py` + `train_vision_model.py`.
- **DB errors:** `rm renal_care.db && python init_db.py`.
