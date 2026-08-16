"""
RenalCare AI - FastAPI Backend
Complete backend for kidney stone analysis, tracking, and recommendations.
"""

import json
import uuid
import os
from datetime import datetime, timedelta

from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func

# Load .env (never commit real keys)
load_dotenv()

from database import (
    init_db, get_db, seed_diet_recommendations,
    Patient, KidneyScan, WaterIntake, MealLog, DietRecommendation,
    Appointment, DoctorRecommendation, HealthGoal, Medicine, Doctor, RiskSnapshot,
)
from auth import (
    hash_password, verify_password, create_session, delete_session,
    get_current_patient, require_patient_access, ensure_patient_access,
)
from image_utils import save_upload_file, analyze_image_file
from vision_utils import metrics_summary, model_available, model_version
from goals import get_health_goals
from schemas import (
    PatientCreate, PatientResponse, ScanResponse, ScanDetailedResponse,
    WaterIntakeCreate, WaterIntakeResponse, DailyWaterSummary,
    MealLogCreate, MealLogResponse, DailyMealSummary, MealItemCreate,
    DietRecommendationRequest, DietRecommendationResponse,
    RiskPredictionRequest, RiskPredictionResponse,
    SuccessResponse, ErrorResponse, HealthSummary,
    RegisterRequest, LoginRequest, AuthResponse,
    AppointmentCreate, RecommendationCreate, HealthGoalResponse,
    MedicineCreate, MedicineResponse, PrescriptionCreate, PrescriptionItem,
    DoctorCreate, DoctorResponse,
)

# ============= FastAPI App Setup =============

app = FastAPI(
    title="RenalCare AI",
    description="Advanced Kidney Stone Detection and Management System",
    version="2.0.0"
)

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174", "http://localhost:5175", "http://localhost:5176", "http://localhost:5177", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DISCLAIMER = (
    "This app is a research/demo tool and not a diagnostic device. "
    "Scan analysis is image-level normal/stone classification only; it does not "
    "estimate stone size, location, severity, or composition. Always consult a physician."
)


# ============= Initialization =============

@app.on_event("startup")
def startup_event():
    """Initialize database on startup"""
    init_db()
    print("✓ Database initialized")

    # Seed diet recommendations
    db = next(get_db())
    try:
        seed_diet_recommendations(db)
    finally:
        db.close()


# ============= Health Check =============

@app.get("/")
def root():
    """Health check endpoint"""
    return {
        "message": "RenalCare AI API is running",
        "version": "2.0.0",
        "status": "operational"
    }


@app.get("/api/health")
def health_check():
    """API health status"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow(),
        "services": {
            "database": "connected",
            "vision_model": "loaded" if model_available() else "not_available",
            "vision_model_version": model_version() if model_available() else None,
        }
    }


@app.get("/api/vision/metrics")
def get_vision_metrics():
    """Real held-out test metrics from the trained model (no fabrication)."""
    return {
        "available": model_available(),
        "metrics": metrics_summary(),
        "disclaimer": DISCLAIMER,
    }


# ============= Auth Endpoints =============

@app.post("/api/auth/register", response_model=AuthResponse)
def register(request: RegisterRequest, db: Session = Depends(get_db)):
    """Register a new account. Creates a patient and returns a session token."""
    existing = db.query(Patient).filter(func.lower(Patient.email) == request.email.lower()).first()
    if existing:
        raise HTTPException(status_code=409, detail="An account with this email already exists")

    patient = Patient(
        id=f"patient_{uuid.uuid4().hex[:12]}",
        name=request.name,
        email=request.email,
        password_hash=hash_password(request.password),
        age=request.age,
        gender=request.gender,
        bmi=request.bmi,
        family_history=request.family_history,
        created_at=datetime.utcnow(),
    )
    db.add(patient)
    db.commit()
    db.refresh(patient)

    session = create_session(db, patient)
    return AuthResponse(token=session.token, patient=patient)


@app.post("/api/auth/login", response_model=AuthResponse)
def login(request: LoginRequest, db: Session = Depends(get_db)):
    """Log in with email + password. Returns a session token."""
    patient = db.query(Patient).filter(func.lower(Patient.email) == request.email.lower()).first()
    if not patient or not verify_password(request.password, patient.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    session = create_session(db, patient)
    return AuthResponse(token=session.token, patient=patient)


@app.post("/api/auth/logout", response_model=SuccessResponse)
def logout(
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
):
    """Log out by revoking the current session token."""
    revoked = delete_session(authorization, db)
    return {"success": True, "message": "Logged out" if revoked else "No active session"}


@app.get("/api/auth/me", response_model=PatientResponse)
def me(current_patient: Patient = Depends(get_current_patient)):
    """Get the current authenticated patient."""
    return current_patient


# ============= Patient Management Endpoints =============

@app.post("/api/patients", response_model=PatientResponse)
def create_patient(patient: PatientCreate, db: Session = Depends(get_db)):
    """Create a new patient"""
    existing = db.query(Patient).filter(Patient.id == patient.id).first()
    if existing:
        raise HTTPException(status_code=400, detail="Patient already exists")

    db_patient = Patient(**patient.dict())
    db.add(db_patient)
    db.commit()
    db.refresh(db_patient)
    return db_patient


@app.get("/api/patients/{patient_id}", response_model=PatientResponse)
def get_patient(
    patient_id: str,
    current_patient: Patient = Depends(require_patient_access),
    db: Session = Depends(get_db),
):
    """Get patient details"""
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return patient


@app.get("/api/patients/{patient_id}/health-summary", response_model=HealthSummary)
def get_health_summary(
    patient_id: str,
    current_patient: Patient = Depends(require_patient_access),
    db: Session = Depends(get_db),
):
    """Get patient's comprehensive health summary"""
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    # Get latest scan
    latest_scan = db.query(KidneyScan).filter(
        KidneyScan.patient_id == patient_id
    ).order_by(KidneyScan.created_at.desc()).first()

    # Get today's water intake
    today = datetime.utcnow().date()
    today_intakes = db.query(WaterIntake).filter(
        WaterIntake.patient_id == patient_id,
        func.date(WaterIntake.date) == today
    ).all()
    total_water = sum(intake.amount_ml for intake in today_intakes)

    # Get today's meals
    today_meals = db.query(MealLog).filter(
        MealLog.patient_id == patient_id,
        func.date(MealLog.date) == today
    ).all()

    risk_score = calculate_patient_risk(patient, db)

    return HealthSummary(
        patient_id=patient.id,
        name=patient.name,
        latest_scan=latest_scan,
        today_water_intake_ml=total_water,
        water_goal_ml=3000,
        today_meals_count=len(today_meals),
        risk_score=risk_score,
        risk_level=get_risk_level(risk_score),
        recommendations=get_health_recommendations(patient, latest_scan, total_water)
    )


# ============= Image Upload & Analysis Endpoints =============

@app.post("/api/analyze-scan", response_model=ScanResponse)
async def analyze_scan(
    patient_id: str,
    stone_type: str = "calcium_oxalate",
    file: UploadFile = File(...),
    current_patient: Patient = Depends(require_patient_access),
    db: Session = Depends(get_db),
):
    """
    Upload and analyze a kidney CT scan with the trained normal/stone model.
    Returns: prediction (normal/stone), confidence, model version, and an
    approximate stone size estimated from the model's Grad-CAM attention region.
    Does NOT claim location or composition.
    """
    try:
        if not model_available():
            raise HTTPException(
                status_code=503,
                detail="Vision model not available. Train it first (see README).",
            )

        file_content = await file.read()
        filename = f"{patient_id}_{uuid.uuid4()}.png"
        file_path = save_upload_file(file_content, filename)

        analysis = analyze_image_file(file_path)

        if not analysis["success"]:
            raise HTTPException(status_code=400, detail=f"Analysis failed: {analysis['error']}")

        stone_data = analysis["analysis"]

        scan_id = f"scan_{uuid.uuid4()}"
        db_scan = KidneyScan(
            id=scan_id,
            patient_id=patient_id,
            image_path=file_path,
            stone_size_mm=stone_data.get("stone_size_mm") or 0.0,
            stone_location="Not estimated (image-level analysis only)",
            severity=stone_data["severity"],
            confidence=stone_data["confidence"],
            prediction=stone_data["prediction"],
            model_version=stone_data["model_version"],
            stone_type=stone_type if stone_type != "unknown" else None,
            analysis_results=json.dumps(stone_data),
            size_estimated=stone_data.get("size_estimated", False),
            size_estimation_note=stone_data.get("size_estimation_note"),
        )

        db.add(db_scan)
        db.commit()
        db.refresh(db_scan)

        return db_scan

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing scan: {str(e)}")


@app.get("/api/scans/{patient_id}", response_model=list[ScanResponse])
def get_patient_scans(
    patient_id: str,
    current_patient: Patient = Depends(require_patient_access),
    db: Session = Depends(get_db),
):
    """Get all scans for a patient"""
    scans = db.query(KidneyScan).filter(
        KidneyScan.patient_id == patient_id
    ).order_by(KidneyScan.created_at.desc()).all()

    return scans


@app.get("/api/scans/detail/{scan_id}", response_model=ScanDetailedResponse)
def get_scan_detail(
    scan_id: str,
    current_patient: Patient = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """Get detailed scan information"""
    scan = db.query(KidneyScan).filter(KidneyScan.id == scan_id).first()
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")
    if scan.patient_id != current_patient.id:
        raise HTTPException(status_code=403, detail="You do not have access to this scan")
    return scan


@app.get("/api/risk-insights/{patient_id}")
def get_risk_insights(
    patient_id: str,
    days: int = 30,
    current_patient: Patient = Depends(require_patient_access),
    db: Session = Depends(get_db),
):
    """Combine latest scan data with hydration history to create a recovery roadmap."""
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    insights = _compute_risk_insights(patient, db, days)

    # Record a monthly snapshot the first time this endpoint is hit in a given
    # calendar month, so the trend chart accumulates real history instead of
    # fabricating one.
    current_month = datetime.utcnow().strftime("%Y-%m")
    existing_snapshot = db.query(RiskSnapshot).filter(
        RiskSnapshot.patient_id == patient_id,
        RiskSnapshot.month == current_month,
    ).first()
    if not existing_snapshot:
        db.add(RiskSnapshot(
            id=f"snap_{uuid.uuid4().hex[:8]}",
            patient_id=patient_id,
            month=current_month,
            risk_percentage=insights["risk_percentage"],
        ))
        db.commit()

    return insights


@app.get("/api/risk-insights/{patient_id}/history")
def get_risk_history(
    patient_id: str,
    months: int = 6,
    current_patient: Patient = Depends(require_patient_access),
    db: Session = Depends(get_db),
):
    """Monthly risk score snapshots for trend charting — starts accumulating from
    whenever this feature first ran for a patient, rather than fabricating history."""
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    snapshots = db.query(RiskSnapshot).filter(
        RiskSnapshot.patient_id == patient_id
    ).order_by(RiskSnapshot.month.asc()).limit(months).all()

    return {
        "patient_id": patient_id,
        "history": [
            {"month": s.month, "risk_percentage": s.risk_percentage}
            for s in snapshots
        ],
    }


def _compute_risk_insights(patient: Patient, db: Session, days: int = 30) -> dict:
    """Shared risk computation used by /api/risk-insights and the dashboard summary."""
    patient_id = patient.id
    latest_scan = db.query(KidneyScan).filter(
        KidneyScan.patient_id == patient_id
    ).order_by(KidneyScan.created_at.desc()).first()

    scan_count = db.query(func.count(KidneyScan.id)).filter(
        KidneyScan.patient_id == patient_id
    ).scalar() or 0

    start_date = datetime.utcnow() - timedelta(days=days)
    hydration_entries = db.query(WaterIntake).filter(
        WaterIntake.patient_id == patient_id,
        WaterIntake.date >= start_date
    ).order_by(WaterIntake.date.asc()).all()

    daily_totals = {}
    for entry in hydration_entries:
        day_key = entry.date.date().isoformat()
        daily_totals[day_key] = daily_totals.get(day_key, 0) + float(entry.amount_ml)

    daily_percentages = []
    for day_key in sorted(daily_totals):
        percentage = min(100.0, round((daily_totals[day_key] / 2500.0) * 100, 1))
        daily_percentages.append(percentage)

    average_compliance = round(sum(daily_percentages) / len(daily_percentages), 1) if daily_percentages else 0.0
    consistency = round(sum(1 for p in daily_percentages if p >= 80) / len(daily_percentages) * 100, 1) if daily_percentages else 0.0

    severity_weight = {
        "none": 8,
        "present": 35,
        "mild": 25,
        "moderate": 48,
        "severe": 72,
    }.get((latest_scan.severity if latest_scan else "none").lower(), 20)

    size_penalty = 0.0  # size is not estimated by the model; no fabricated penalty
    hydration_penalty = max(0.0, (100.0 - average_compliance) / 100.0 * 35.0)
    recurrence_penalty = min(10.0, scan_count * 3.0)

    risk_percentage = round(min(100.0, severity_weight + size_penalty + hydration_penalty + recurrence_penalty), 1)
    recovery_percentage = round(max(0.0, 100.0 - risk_percentage), 1)
    risk_level = "High" if risk_percentage >= 70 else "Moderate" if risk_percentage >= 40 else "Low"

    warnings = []
    if average_compliance < 70:
        warnings.append("Hydration is below the recommended target and is increasing recurrence risk.")
    if latest_scan and latest_scan.prediction == "stone":
        warnings.append("The latest scan shows a stone pattern that needs close follow-up.")
    if latest_scan and latest_scan.severity == "present":
        warnings.append("The latest scan detected a stone. The model does not estimate size, location, or severity - consult a physician for a full diagnosis.")
    if scan_count > 1:
        warnings.append("Repeated stone findings suggest a higher recurrence risk.")
    if not latest_scan:
        warnings.append("No recent scan data is available yet. Upload a fresh scan to improve the insights.")

    roadmap = []
    month_1_status = "On track" if average_compliance >= 80 else "Needs focus"
    month_2_status = month_1_status
    month_3_status = month_1_status

    roadmap.append({
        "month": 1,
        "title": "Month 1 – Build the hydration baseline",
        "goal": "Reach at least 80% of your daily hydration goal for most days",
        "focus": "Drink water every 2–3 hours and log each intake immediately",
        "status": month_1_status,
        "actions": [
            "Aim for 2500–3000 ml daily depending on your stone type",
            "Carry a bottle and set reminders every 2 hours",
            "Log hydration after every main meal"
        ]
    })
    roadmap.append({
        "month": 2,
        "title": "Month 2 – Stabilize diet and reduce recurrence triggers",
        "goal": "Stay consistent with hydration and lower high-oxalate triggers",
        "focus": "Follow the stone-specific nutrition guidance and limit sodium",
        "status": month_2_status,
        "actions": [
            "Avoid processed foods and excess salt",
            "Use the stone-specific diet list from the scan insights",
            "Review your hydration log weekly and correct low days"
        ]
    })
    roadmap.append({
        "month": 3,
        "title": "Month 3 – Consolidate recovery and monitor progress",
        "goal": "Maintain compliance and improve recovery outlook",
        "focus": "Repeat the scan and review the trend with your care team",
        "status": month_3_status,
        "actions": [
            "Continue daily hydration tracking",
            "Recheck symptoms and stone indicators every 2 weeks",
            "Schedule a follow-up scan if symptoms return"
        ]
    })

    guidelines = []
    if latest_scan and latest_scan.stone_type == "calcium_oxalate":
        guidelines.extend([
            "Limit oxalate-rich foods like spinach, beets, nuts, and chocolate",
            "Keep sodium intake low and maintain regular hydration"
        ])
    elif latest_scan and latest_scan.stone_type == "uric_acid":
        guidelines.extend([
            "Reduce purine-rich foods such as red meat and seafood",
            "Avoid alcohol and sugary drinks"
        ])
    else:
        guidelines.extend([
            "Follow the recommended diet guidance for your stone type",
            "Stay hydrated and keep a daily log"
        ])

    guidelines.append("Seek urgent medical help if you develop severe pain, fever, or vomiting")

    danger_if_ignored = []
    if average_compliance < 80:
        danger_if_ignored.append({
            "period": "Within 1 week",
            "impact": "Hydration shortfall may push recurrence risk higher",
            "message": "Missing daily hydration targets can quickly increase stone recurrence risk."
        })
    if latest_scan and latest_scan.prediction == "stone":
        danger_if_ignored.append({
            "period": "Within 2 weeks",
            "impact": "Stone pattern needs medical follow-up",
            "message": "A stone detected on your scan needs professional evaluation and follow-up."
        })
    if recovery_percentage < 50:
        danger_if_ignored.append({
            "period": "Within 1 month",
            "impact": "Recovery may slow and recurrence risk may stay high",
            "message": "Ignoring the recovery plan can keep the risk percentage high for several weeks."
        })
    if not danger_if_ignored:
        danger_if_ignored.append({
            "period": "Stay consistent",
            "impact": "Progress is steady but still needs maintenance",
            "message": "Keep the current hydration and diet routine to preserve recovery progress."
        })

    return {
        "patient_id": patient_id,
        "patient_name": patient.name,
        "risk_percentage": risk_percentage,
        "risk_level": risk_level,
        "recovery_percentage": recovery_percentage,
        "hydration_compliance": average_compliance,
        "hydration_consistency": consistency,
        "latest_scan": {
            "prediction": latest_scan.prediction if latest_scan else None,
            "confidence": latest_scan.confidence if latest_scan else None,
            "model_version": latest_scan.model_version if latest_scan else None,
            "stone_size_mm": latest_scan.stone_size_mm if latest_scan else 0,
            "severity": latest_scan.severity if latest_scan else "none",
            "location": latest_scan.stone_location if latest_scan else "Not available",
            "stone_type": latest_scan.stone_type if latest_scan else "unknown"
        },
        "warnings": warnings,
        "roadmap": roadmap,
        "guidelines": guidelines,
        "danger_if_ignored": danger_if_ignored,
        "analysis_window_days": days
    }


# ============= Dashboard Summary (per-section outcomes + overall) =============

def _conclude(score: float) -> str:
    """Map a 0-100 section score to a status label."""
    if score >= 75:
        return "good"
    if score >= 50:
        return "attention"
    if score is None:
        return "info"
    return "critical"


def _safe_json(raw):
    """Parse a JSON column defensively (returns [] on any failure)."""
    if raw is None:
        return []
    try:
        value = json.loads(raw)
        return value if isinstance(value, list) else []
    except Exception:
        return []


@app.get("/api/dashboard/summary/{patient_id}")
def get_dashboard_summary(
    patient_id: str,
    current_patient: Patient = Depends(require_patient_access),
    db: Session = Depends(get_db),
):
    """Per-section outcomes (hydration, scan, diet & medicine, risk, appointments,
    goals) + a single overall conclusion. All values are computed from real
    tracked data; conclusions are deterministic summaries of that data."""
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    now = datetime.utcnow()
    today = now.date()
    sections = {}

    # ---------- Hydration ----------
    today_intakes = db.query(WaterIntake).filter(
        WaterIntake.patient_id == patient_id,
        func.date(WaterIntake.date) == today,
    ).all()
    today_ml = sum(i.amount_ml for i in today_intakes)
    water_goal = 3000.0
    today_pct = min(100.0, round(today_ml / water_goal * 100, 1))

    week_start = now - timedelta(days=6)
    week_entries = db.query(WaterIntake).filter(
        WaterIntake.patient_id == patient_id,
        WaterIntake.date >= week_start,
    ).all()
    weekly = {}
    for e in week_entries:
        weekly[e.date.date().isoformat()] = weekly.get(e.date.date().isoformat(), 0) + float(e.amount_ml)
    week_days = [(now - timedelta(days=i)).date().isoformat() for i in range(6, -1, -1)]
    weekly_ml = [round(weekly.get(d, 0.0)) for d in week_days]
    active_days = len([v for v in weekly_ml if v > 0])
    week_avg_pct = round(
        sum(min(100.0, weekly.get(d, 0.0) / water_goal * 100) for d in week_days) / 7.0, 1
    ) if week_days else 0.0

    hyd_score = (today_pct + week_avg_pct) / 2.0 if (today_pct + week_avg_pct) else 0.0
    if today_pct >= 100:
        hyd_conclusion = (f"Hydration goal met for today: {round(today_ml)} ml of {round(water_goal)} ml. "
                          f"Keep the same rhythm to guard against stone recurrence.")
    elif today_pct >= 60:
        hyd_conclusion = (f"Hydration is progressing at {today_pct}% of today's goal "
                          f"({round(today_ml)} ml of {round(water_goal)} ml). "
                          f"Still {max(0, round(water_goal - today_ml))} ml to go — keep logging.")
    else:
        hyd_conclusion = (f"Hydration is low today ({round(today_ml)} ml of {round(water_goal)} ml). "
                          f"Low urine volume increases the risk of new stone formation — "
                          f"prioritise drinking water.")
    hyd_status = _conclude(hyd_score)
    sections["hydration"] = {
        "key": "hydration",
        "title": "Hydration Tracking",
        "score": round(hyd_score, 1),
        "status": hyd_status,
        "metrics": [
            {"label": "Today", "value": f"{round(today_ml)} / {round(water_goal)} ml"},
            {"label": "Today target", "value": f"{today_pct}%"},
            {"label": "7-day average", "value": f"{week_avg_pct}%"},
            {"label": "Active days (7d)", "value": f"{active_days}/7"},
        ],
        "conclusion": hyd_conclusion,
        "series": [{"label": d[5:], "value": v} for d, v in zip(week_days, weekly_ml)],
    }

    # ---------- Scan ----------
    scans = db.query(KidneyScan).filter(
        KidneyScan.patient_id == patient_id
    ).order_by(KidneyScan.created_at.desc()).all()
    latest = scans[0] if scans else None
    if latest is None:
        scan_score, scan_status, scan_conclusion = None, "info", (
            "No AI scan has been uploaded yet. Uploading a kidney CT image lets the model "
            "check for a stone pattern and estimate a stone size."
        )
        scan_metrics = [{"label": "Scans on file", "value": "0"}]
        latest_scan_summary = None
    else:
        stone = latest.prediction == "stone"
        base = 40 if stone else 85
        if stone and latest.severity == "present":
            base = 35
        if stone and latest.stone_size_mm and latest.stone_size_mm > 10:
            base = 25
        scan_score = round(max(0.0, base), 1)
        if stone:
            size_txt = f"{latest.stone_size_mm} mm" if latest.stone_size_mm else "not measurable"
            scan_conclusion = (
                f"The latest AI scan detected a stone pattern (est. {size_txt}, "
                f"{round((latest.confidence or 0) * 100)}% confidence). This is an automated "
                f"screen, not a diagnosis — a urologist follow-up is recommended."
            )
        else:
            scan_conclusion = (
                f"The latest AI scan shows a normal pattern ({round((latest.confidence or 0) * 100)}% "
                f"confidence). No stone pattern was flagged in this image."
            )
        scan_status = _conclude(scan_score)
        scan_metrics = [
            {"label": "Result", "value": latest.prediction},
            {"label": "Confidence", "value": f"{round((latest.confidence or 0) * 100)}%"},
            {"label": "Est. size", "value": f"{latest.stone_size_mm} mm" if latest.stone_size_mm else "Not measurable"},
            {"label": "Scans on file", "value": str(len(scans))},
        ]
        latest_scan_summary = {
            "prediction": latest.prediction,
            "confidence": round(latest.confidence or 0, 4),
            "stone_size_mm": latest.stone_size_mm,
            "severity": latest.severity,
            "stone_type": latest.stone_type,
            "created_at": latest.created_at.isoformat(),
            "model_version": latest.model_version,
        }
    sections["scan"] = {
        "key": "scan",
        "title": "AI Scan Analysis",
        "score": scan_score,
        "status": scan_status,
        "metrics": scan_metrics,
        "conclusion": scan_conclusion,
        "latest_scan": latest_scan_summary,
    }

    # ---------- Diet & Medicine ----------
    today_meals = db.query(MealLog).filter(
        MealLog.patient_id == patient_id,
        func.date(MealLog.date) == today,
    ).all()
    total_sodium = round(sum(m.sodium_mg or 0 for m in today_meals), 1)
    high_oxalate = []
    for m in today_meals:
        if m.oxalate_level == "high":
            try:
                items = json.loads(m.food_items)
                high_oxalate.extend(i.get("name") for i in items if i.get("oxalate_level") == "high")
            except Exception:
                pass
    high_oxalate = list(dict.fromkeys(high_oxalate))

    medicines = db.query(Medicine).filter(
        Medicine.patient_id == patient_id,
        Medicine.active == True,  # noqa: E712
    ).order_by(Medicine.created_at.desc()).all()
    med_list = [{"name": m.name, "dose": m.dose, "frequency": m.frequency} for m in medicines]

    sodium_score = max(0.0, 100.0 - (min(total_sodium, 4000) / 2000.0) * 70.0)
    oxalate_penalty = min(40.0, len(high_oxalate) * 15.0)
    diet_score = round(max(0.0, sodium_score - oxalate_penalty), 1)
    if not today_meals:
        diet_conclusion = ("No meals logged today, so diet risk cannot be assessed yet. "
                           "Log your meals to check sodium and oxalate intake.")
        diet_status = "info"
        diet_score = None
    elif len(high_oxalate) > 0:
        diet_conclusion = (f"Today you logged {len(today_meals)} meal(s), {round(total_sodium)} mg sodium "
                           f"and high-oxalate foods: {', '.join(high_oxalate)}. High oxalate/sodium "
                           f"increases stone risk — prefer low-oxalate, low-salt options.")
        diet_status = "critical"
    elif total_sodium <= 2000:
        diet_conclusion = (f"Today: {len(today_meals)} meal(s), {round(total_sodium)} mg sodium "
                           f"(within the 2000 mg target), no high-oxalate foods flagged. "
                           f"Diet risk is low — maintain this pattern.")
        diet_status = _conclude(diet_score)
    else:
        diet_conclusion = (f"Today: {len(today_meals)} meal(s), {round(total_sodium)} mg sodium "
                           f"which exceeds the 2000 mg target. Try to cut salt for the rest of the day.")
        diet_status = "attention"
    diet_metrics = [
        {"label": "Meals today", "value": str(len(today_meals))},
        {"label": "Sodium today", "value": f"{total_sodium} mg"},
        {"label": "High-oxalate foods", "value": ", ".join(high_oxalate) if high_oxalate else "None"},
        {"label": "Active medicines", "value": str(len(med_list))},
    ]
    sections["diet"] = {
        "key": "diet",
        "title": "Food & Medicine",
        "score": diet_score,
        "status": diet_status,
        "metrics": diet_metrics,
        "conclusion": diet_conclusion,
        "medicines": med_list,
        "meals_today": [
            {
                "meal_type": m.meal_type,
                "food_items": _safe_json(m.food_items),
                "oxalate_level": m.oxalate_level,
                "sodium_mg": m.sodium_mg,
            }
            for m in today_meals
        ],
    }

    # ---------- Risk Insights ----------
    risk = _compute_risk_insights(patient, db, days=30)
    risk_score = round(max(0.0, 100.0 - risk["risk_percentage"]), 1)
    sections["risk"] = {
        "key": "risk",
        "title": "Risk Insights",
        "score": risk_score,
        "status": _conclude(risk_score),
        "metrics": [
            {"label": "Risk score", "value": f"{risk['risk_percentage']}%"},
            {"label": "Risk level", "value": risk["risk_level"]},
            {"label": "Recovery outlook", "value": f"{risk['recovery_percentage']}%"},
            {"label": "Hydration compliance", "value": f"{risk['hydration_compliance']}%"},
        ],
        "conclusion": " ".join(risk["warnings"]) if risk["warnings"] else (
            "No active risk warnings right now — your tracked data is within safe ranges."
        ),
        "details": {
            "risk_percentage": risk["risk_percentage"],
            "risk_level": risk["risk_level"],
            "recovery_percentage": risk["recovery_percentage"],
            "guidelines": risk["guidelines"],
            "danger_if_ignored": risk["danger_if_ignored"],
        },
    }

    # ---------- Appointments ----------
    appointments = db.query(Appointment).filter(
        Appointment.patient_id == patient_id,
        Appointment.status != "cancelled",
        Appointment.appointment_date >= now - timedelta(minutes=30),
    ).order_by(Appointment.appointment_date.asc()).all()
    upcoming = [
        {
            "id": a.id,
            "appointment_date": a.appointment_date.isoformat(),
            "appointment_type": a.appointment_type,
            "doctor_type": a.doctor_type,
            "title": a.title,
        }
        for a in appointments
    ]
    if upcoming:
        nxt = upcoming[0]
        appt_score, appt_status = 80.0, "good"
        if nxt["appointment_type"] in ("URGENT", "HIGH_RISK"):
            appt_score, appt_status = 65.0, "attention"
        appt_conclusion = (
            f"{len(upcoming)} upcoming appointment(s); the next is '{nxt['title']}' on "
            f"{nxt['appointment_date'][:10]} ({nxt['doctor_type'] or nxt['appointment_type']}). "
            f"Keep this booked so follow-up and prescriptions stay on schedule."
        )
    else:
        appt_score, appt_status = None, "info"
        appt_conclusion = ("No upcoming appointments booked. If a recent scan flagged a stone or "
                           "risk is high, booking a urologist visit is strongly advised.")
    sections["appointments"] = {
        "key": "appointments",
        "title": "Appointments",
        "score": appt_score,
        "status": appt_status,
        "metrics": [
            {"label": "Upcoming", "value": str(len(upcoming))},
            {"label": "Next", "value": (upcoming[0]["appointment_date"][:10] if upcoming else "None")},
            {"label": "Next type", "value": (upcoming[0]["appointment_type"] if upcoming else "—")},
        ],
        "conclusion": appt_conclusion,
        "upcoming": upcoming,
    }

    # ---------- Health Goals ----------
    goals = get_health_goals(patient, db)
    goal_items = goals.get("goals", [])
    goals_categories = list(dict.fromkeys(g.get("category") for g in goal_items if g.get("category")))
    goals_score = max(0.0, hyd_score + (90.0 if goal_items else 0.0)) if goal_items else hyd_score
    goals_score = round(min(100.0, goals_score), 1)
    if goal_items:
        goals_conclusion = (
            f"{len(goal_items)} personalized goals generated (today): "
            f"{', '.join(goals_categories)}. Follow-up targets include: "
            + "; ".join(g.get("goal", "") for g in goal_items[:3])
            + "."
        )
        goals_status = _conclude(goals_score)
    else:
        goals_conclusion = "No health goals available for today yet — open the Health Goals section to generate them."
        goals_status = "info"
    sections["goals"] = {
        "key": "goals",
        "title": "Health Goals",
        "score": goals_score,
        "status": goals_status,
        "metrics": [
            {"label": "Goals today", "value": str(len(goal_items))},
            {"label": "Categories", "value": ", ".join(goals_categories) if goals_categories else "—"},
            {"label": "Source", "value": goals.get("source", "rule_based")},
        ],
        "conclusion": goals_conclusion,
        "goals": goal_items,
    }

    # ---------- Overall conclusion ----------
    weights = {"hydration": 0.25, "scan": 0.20, "diet": 0.15, "risk": 0.25, "appointments": 0.10, "goals": 0.05}
    scored = [(k, s["score"], weights[k]) for k, s in sections.items() if s["score"] is not None]
    if scored:
        overall_score = round(sum(s * w for _, s, w in scored) / sum(w for _, _, w in scored), 1)
    else:
        overall_score = None
    overall_status = _conclude(overall_score) if overall_score is not None else "info"

    note_parts = [
        ("Scan", "critical", "A stone pattern was detected on your latest scan — schedule a urologist follow-up."),
        ("Hydration", "attention", "Daily hydration is below target — increase water intake to reach 3 L."),
        ("Diet", "attention", "Sodium and/or high-oxalate intake is above the recommended limits today."),
        ("Risk", "attention", "Your computed risk level is Moderate or High — follow the recovery roadmap."),
        ("Appointments", "info", "No upcoming appointment — book a follow-up to keep monitoring on track."),
        ("Goals", "attention", "Today's health goals are not yet met — stay consistent with the plan."),
    ]
    highlights = []
    for name, status_key, text in note_parts:
        sec = sections.get({"Scan": "scan"}.get(name, name.lower()), {})
        if sec.get("status") == "critical":
            highlights.append({"level": "critical", "text": text})
        elif sec.get("status") == "attention":
            highlights.append({"level": "attention", "text": text})
    if not highlights:
        highlights.append({"level": "good", "text": "All tracked sections are within healthy ranges — keep up the current routine."})
    for w in risk.get("warnings", []):
        if not any(h["text"] == w for h in highlights):
            highlights.append({"level": "attention", "text": w})

    if overall_score is None:
        overall_conclusion = (
            "Not enough tracked data to compute an overall outcome yet. Log water, meals and a "
            "scan, then return here to see the combined conclusion."
        )
    elif overall_score >= 75:
        overall_conclusion = (
            f"Your overall status is GOOD (score {overall_score}/100). Hydration, diet, scan and "
            f"follow-up are all on track. Maintain the current routine and continue tracking daily."
        )
    elif overall_score >= 50:
        overall_conclusion = (
            f"Your overall status is MODERATE (score {overall_score}/100). Some areas need focus "
            f"before they raise recurrence risk — see the per-section outcomes below and follow "
            f"the highlighted actions."
        )
    else:
        overall_conclusion = (
            f"Your overall status is CONCERNING (score {overall_score}/100). One or more areas "
            f"need immediate attention (see per-section outcomes). Consider booking a urologist "
            f"follow-up with this report."
        )

    return {
        "patient": {"id": patient.id, "name": patient.name},
        "generated_at": now.isoformat(),
        "sections": [sections[k] for k in ("hydration", "scan", "diet", "risk", "appointments", "goals")],
        "overall": {
            "score": overall_score,
            "status": overall_status,
            "conclusion": overall_conclusion,
            "highlights": highlights,
        },
        "latest_scan": latest_scan_summary,
        "water_history": {"days": week_days, "ml": weekly_ml, "goal_ml": water_goal},
        "disclaimer": "Research/demo system — conclusions are computed from your tracked data and are not a medical diagnosis. Always consult a urologist.",
    }


# ============= Water Intake Tracking Endpoints =============

@app.post("/api/water-intake", response_model=WaterIntakeResponse)
def log_water_intake(
    intake: WaterIntakeCreate,
    current_patient: Patient = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """Log daily water intake"""
    ensure_patient_access(current_patient, intake.patient_id)
    patient = db.query(Patient).filter(Patient.id == intake.patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    intake_id = f"water_{uuid.uuid4()}"
    current_time = intake.time or datetime.utcnow().strftime("%H:%M")

    db_intake = WaterIntake(
        id=intake_id,
        patient_id=intake.patient_id,
        amount_ml=intake.amount_ml,
        time=current_time,
        notes=intake.notes,
        date=datetime.utcnow()
    )

    db.add(db_intake)
    db.commit()
    db.refresh(db_intake)

    return db_intake


@app.get("/api/water-intake/{patient_id}/daily", response_model=DailyWaterSummary)
def get_daily_water_summary(
    patient_id: str,
    date: str = None,
    current_patient: Patient = Depends(require_patient_access),
    db: Session = Depends(get_db),
):
    """Get daily water intake summary"""
    if date:
        target_date = datetime.strptime(date, "%Y-%m-%d").date()
    else:
        target_date = datetime.utcnow().date()

    intakes = db.query(WaterIntake).filter(
        WaterIntake.patient_id == patient_id,
        func.date(WaterIntake.date) == target_date
    ).all()

    total_intake = sum(intake.amount_ml for intake in intakes)
    goal = 3000  # Default 3L per day
    percentage = (total_intake / goal) * 100 if goal > 0 else 0

    return DailyWaterSummary(
        date=target_date.isoformat(),
        total_intake_ml=total_intake,
        goal_ml=goal,
        percentage=min(percentage, 100),
        intakes=intakes
    )


@app.get("/api/water-intake/{patient_id}/history")
def get_water_history(
    patient_id: str,
    days: int = 7,
    current_patient: Patient = Depends(require_patient_access),
    db: Session = Depends(get_db),
):
    """Get water intake history (last N days)"""
    start_date = datetime.utcnow() - timedelta(days=days)

    intakes = db.query(WaterIntake).filter(
        WaterIntake.patient_id == patient_id,
        WaterIntake.date >= start_date
    ).order_by(WaterIntake.date.desc()).all()

    history = {}
    for intake in intakes:
        date_key = intake.date.date().isoformat()
        if date_key not in history:
            history[date_key] = {"total_ml": 0, "intakes": []}
        history[date_key]["total_ml"] += intake.amount_ml
        history[date_key]["intakes"].append({
            "id": intake.id,
            "amount_ml": intake.amount_ml,
            "time": intake.time,
            "notes": intake.notes,
        })

    return {
        "patient_id": patient_id,
        "days": days,
        "data": history
    }


@app.delete("/api/water-intake/{patient_id}/reset")
def reset_water_intake(
    patient_id: str,
    date: str = None,
    current_patient: Patient = Depends(require_patient_access),
    db: Session = Depends(get_db),
):
    """Reset/delete all water intake entries for a specific date"""
    if date:
        target_date = datetime.strptime(date, "%Y-%m-%d").date()
    else:
        target_date = datetime.utcnow().date()

    deleted_count = db.query(WaterIntake).filter(
        WaterIntake.patient_id == patient_id,
        func.date(WaterIntake.date) == target_date
    ).delete()

    db.commit()

    return {
        "success": True,
        "message": f"Deleted {deleted_count} water intake entries for {target_date.isoformat()}",
        "deleted_count": deleted_count
    }


# ============= Meal Logging Endpoints =============

@app.post("/api/meals", response_model=MealLogResponse)
def log_meal(
    meal: MealLogCreate,
    current_patient: Patient = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """Log a meal"""
    ensure_patient_access(current_patient, meal.patient_id)
    patient = db.query(Patient).filter(Patient.id == meal.patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    oxalate_levels = [item.oxalate_level for item in meal.food_items]
    if "high" in oxalate_levels:
        final_oxalate = "high"
    elif "medium" in oxalate_levels:
        final_oxalate = "medium"
    else:
        final_oxalate = "low"

    base_sodium = {"breakfast": 800, "lunch": 1200, "dinner": 1200, "snack": 400}
    sodium_mg = base_sodium.get(meal.meal_type, 500)

    meal_id = f"meal_{uuid.uuid4()}"

    db_meal = MealLog(
        id=meal_id,
        patient_id=meal.patient_id,
        date=datetime.utcnow(),
        meal_type=meal.meal_type,
        food_items=json.dumps([item.dict() for item in meal.food_items]),
        oxalate_level=final_oxalate,
        sodium_mg=sodium_mg,
        notes=meal.notes
    )

    db.add(db_meal)
    db.commit()
    db.refresh(db_meal)

    return db_meal


@app.get("/api/meals/{patient_id}/daily", response_model=DailyMealSummary)
def get_daily_meals(
    patient_id: str,
    date: str = None,
    current_patient: Patient = Depends(require_patient_access),
    db: Session = Depends(get_db),
):
    """Get daily meal summary"""
    if date:
        target_date = datetime.strptime(date, "%Y-%m-%d").date()
    else:
        target_date = datetime.utcnow().date()

    meals = db.query(MealLog).filter(
        MealLog.patient_id == patient_id,
        func.date(MealLog.date) == target_date
    ).all()

    total_sodium = sum(meal.sodium_mg for meal in meals)
    high_oxalate_items = []

    for meal in meals:
        if meal.oxalate_level == "high":
            food_items = json.loads(meal.food_items)
            high_oxalate_items.extend([item["name"] for item in food_items if item.get("oxalate_level") == "high"])

    recommendations = get_meal_recommendations(patient_id, db)

    return DailyMealSummary(
        date=target_date.isoformat(),
        meals=meals,
        total_sodium_mg=total_sodium,
        high_oxalate_items=list(set(high_oxalate_items)),
        recommendations=recommendations
    )


@app.get("/api/meals/{patient_id}/history")
def get_meal_history(
    patient_id: str,
    days: int = 7,
    current_patient: Patient = Depends(require_patient_access),
    db: Session = Depends(get_db),
):
    """Get meal history (last N days)"""
    start_date = datetime.utcnow() - timedelta(days=days)

    meals = db.query(MealLog).filter(
        MealLog.patient_id == patient_id,
        MealLog.date >= start_date
    ).order_by(MealLog.date.desc()).all()

    return {
        "patient_id": patient_id,
        "days": days,
        "total_meals": len(meals),
        "meals": meals
    }


# ============= Medicine & Prescription Endpoints =============

@app.get("/api/medicines/{patient_id}", response_model=list[MedicineResponse])
def get_active_medicines(
    patient_id: str,
    current_patient: Patient = Depends(require_patient_access),
    db: Session = Depends(get_db),
):
    """Get the patient's current (active) medicines, newest first."""
    medicines = db.query(Medicine).filter(
        Medicine.patient_id == patient_id,
        Medicine.active == True  # noqa: E712
    ).order_by(Medicine.created_at.desc()).all()
    return medicines


@app.post("/api/medicines", response_model=MedicineResponse)
def add_medicine(
    medicine: MedicineCreate,
    current_patient: Patient = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """Patient adds a medicine they take daily (self-tracked)."""
    ensure_patient_access(current_patient, medicine.patient_id)
    patient = db.query(Patient).filter(Patient.id == medicine.patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    db_medicine = Medicine(
        id=f"med_{uuid.uuid4()}",
        patient_id=medicine.patient_id,
        name=medicine.name.strip(),
        dose=medicine.dose,
        frequency=medicine.frequency,
        notes=medicine.notes,
        prescribed_by="patient",
        active=True,
    )
    db.add(db_medicine)
    db.commit()
    db.refresh(db_medicine)
    return db_medicine


@app.delete("/api/medicines/{medicine_id}", response_model=SuccessResponse)
def remove_medicine(
    medicine_id: str,
    current_patient: Patient = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """Patient marks a medicine as no longer taken (soft-deactivate)."""
    db_medicine = db.query(Medicine).filter(Medicine.id == medicine_id).first()
    if not db_medicine:
        raise HTTPException(status_code=404, detail="Medicine not found")
    ensure_patient_access(current_patient, db_medicine.patient_id)

    db_medicine.active = False
    db.commit()
    return SuccessResponse(success=True, message=f"{db_medicine.name} removed from your daily list.")


@app.post("/api/prescriptions", response_model=list[MedicineResponse])
def doctor_prescribe(
    prescription: PrescriptionCreate,
    current_patient: Patient = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """
    Doctor updates the patient's prescription during an appointment.
    Replaces the entire active medicine list: previous entries are deactivated
    and a new doctor-authored set becomes active.
    """
    ensure_patient_access(current_patient, prescription.patient_id)
    patient = db.query(Patient).filter(Patient.id == prescription.patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    db.query(Medicine).filter(
        Medicine.patient_id == prescription.patient_id,
        Medicine.active == True  # noqa: E712
    ).update({Medicine.active: False})

    created = []
    for item in prescription.medicines:
        db_medicine = Medicine(
            id=f"med_{uuid.uuid4()}",
            patient_id=prescription.patient_id,
            name=item.name.strip(),
            dose=item.dose,
            frequency=item.frequency,
            notes=item.notes,
            prescribed_by="doctor",
            active=True,
        )
        db.add(db_medicine)
        created.append(db_medicine)

    db.commit()
    for med in created:
        db.refresh(med)
    return created


# ============= Diet Recommendations Endpoints =============

@app.get("/api/diet-recommendations/{stone_type}", response_model=DietRecommendationResponse)
def get_diet_recommendations(stone_type: str, db: Session = Depends(get_db)):
    """Get diet recommendations based on stone type"""
    rec = db.query(DietRecommendation).filter(
        DietRecommendation.stone_type == stone_type
    ).first()

    if not rec:
        raise HTTPException(status_code=404, detail="Stone type not found")

    return DietRecommendationResponse(
        stone_type=rec.stone_type,
        restricted_foods=json.loads(rec.restricted_foods),
        recommended_foods=json.loads(rec.recommended_foods),
        daily_fluid_intake_ml=rec.daily_fluid_intake_ml,
        daily_sodium_limit_mg=rec.daily_sodium_limit_mg,
        tips=json.loads(rec.tips)
    )


@app.get("/api/diet-recommendations")
def get_all_diet_recommendations(db: Session = Depends(get_db)):
    """Get all available diet recommendations"""
    recs = db.query(DietRecommendation).all()

    result = []
    for rec in recs:
        result.append({
            "stone_type": rec.stone_type,
            "restricted_foods": json.loads(rec.restricted_foods),
            "recommended_foods": json.loads(rec.recommended_foods),
            "daily_fluid_intake_ml": rec.daily_fluid_intake_ml,
            "daily_sodium_limit_mg": rec.daily_sodium_limit_mg,
            "tips": json.loads(rec.tips)
        })

    return result


@app.post("/api/diet-recommendations/{patient_id}")
def update_patient_diet(
    patient_id: str,
    stone_type: str,
    current_patient: Patient = Depends(require_patient_access),
    db: Session = Depends(get_db),
):
    """Update patient's diet recommendations based on latest scan"""
    rec = db.query(DietRecommendation).filter(
        DietRecommendation.stone_type == stone_type
    ).first()

    if not rec:
        raise HTTPException(status_code=404, detail="Stone type not found")

    return {
        "patient_id": patient_id,
        "stone_type": stone_type,
        "diet_recommendations": {
            "restricted_foods": json.loads(rec.restricted_foods),
            "recommended_foods": json.loads(rec.recommended_foods),
            "daily_fluid_intake_ml": rec.daily_fluid_intake_ml,
            "daily_sodium_limit_mg": rec.daily_sodium_limit_mg,
            "tips": json.loads(rec.tips)
        }
    }


# ============= Risk Prediction Endpoints =============

@app.post("/api/predict-risk", response_model=RiskPredictionResponse)
def predict_risk(
    request: RiskPredictionRequest,
    current_patient: Patient = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """Predict kidney stone recurrence risk"""
    ensure_patient_access(current_patient, request.patient_id)
    patient = db.query(Patient).filter(Patient.id == request.patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    risk_score = calculate_risk_score(
        age=request.age,
        family_history=request.family_history,
        previous_stones=request.previous_stones,
        compliance=request.treatment_compliance
    )

    risk_level = get_risk_level(risk_score)

    return RiskPredictionResponse(
        patient_id=request.patient_id,
        risk_score=risk_score,
        risk_level=risk_level,
        recommendations=get_risk_recommendations(risk_score),
        last_updated=datetime.utcnow()
    )


@app.get("/api/patients/{patient_id}/risk-score")
def get_patient_risk_score(
    patient_id: str,
    current_patient: Patient = Depends(require_patient_access),
    db: Session = Depends(get_db),
):
    """Get current risk score for a patient"""
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    risk_score = calculate_patient_risk(patient, db)

    return {
        "patient_id": patient_id,
        "risk_score": risk_score,
        "risk_level": get_risk_level(risk_score),
        "timestamp": datetime.utcnow()
    }


# ============= Health Goals Endpoints =============

@app.get("/api/goals/{patient_id}", response_model=HealthGoalResponse)
def get_goals(
    patient_id: str,
    current_patient: Patient = Depends(require_patient_access),
    db: Session = Depends(get_db),
):
    """Get daily personalized health goals (LLM-backed when enabled, rule-based otherwise)."""
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    return get_health_goals(patient, db)


# ============= Helper Functions =============

def calculate_risk_score(age: int, family_history: bool, previous_stones: int, compliance: float) -> float:
    """Calculate risk score based on patient factors"""
    score = 0.0

    if age > 60:
        score += 0.15
    elif age > 40:
        score += 0.10

    if family_history:
        score += 0.20

    score += (previous_stones / 10) * 0.30

    score += (1 - compliance / 100) * 0.25

    score += 0.10

    return min(score, 1.0)


def calculate_patient_risk(patient: Patient, db: Session) -> float:
    """Calculate overall risk for a patient"""
    latest_scan = db.query(KidneyScan).filter(
        KidneyScan.patient_id == patient.id
    ).order_by(KidneyScan.created_at.desc()).first()

    stone_count = db.query(func.count(KidneyScan.id)).filter(
        KidneyScan.patient_id == patient.id
    ).scalar()

    risk = calculate_risk_score(
        age=patient.age,
        family_history=patient.family_history,
        previous_stones=int(stone_count or 0),
        compliance=75.0  # Default compliance
    )

    return risk


def get_risk_level(risk_score: float) -> str:
    """Get risk level label"""
    if risk_score < 0.33:
        return "Low"
    elif risk_score < 0.66:
        return "Moderate"
    else:
        return "High"


def get_risk_recommendations(risk_score: float) -> list:
    """Get recommendations based on risk score"""
    recommendations = [
        "Maintain daily hydration of 2.5-3 liters",
        "Follow recommended diet for your stone type",
        "Reduce sodium intake below 2000mg per day",
    ]

    if risk_score > 0.66:
        recommendations.extend([
            "Schedule monthly urologist follow-ups",
            "Consider preventive medication consultation",
            "Monitor for stone symptoms closely"
        ])
    elif risk_score > 0.33:
        recommendations.extend([
            "Schedule quarterly urologist check-ups",
            "Maintain detailed meal and water intake logs"
        ])
    else:
        recommendations.extend([
            "Continue regular annual check-ups",
            "Maintain current healthy lifestyle"
        ])

    return recommendations


def get_health_recommendations(patient: Patient, latest_scan, total_water: float) -> list:
    """Get personalized health recommendations"""
    recommendations = []

    if latest_scan:
        if latest_scan.prediction == "stone":
            recommendations.append("⚠️ Stone pattern detected in your latest scan - consult a urologist for a full diagnosis")
        elif latest_scan.severity == "severe":
            recommendations.append("⚠️ Severe stone detected - consult urologist immediately")
        elif latest_scan.severity == "moderate":
            recommendations.append("Consider medical intervention for stone management")

    if total_water < 2500:
        recommendations.append("💧 Increase water intake - aim for 3 liters per day")

    recommendations.append("🥗 Follow your personalized diet recommendations")
    recommendations.append("📊 Track your meals and water intake daily")

    return recommendations


def get_meal_recommendations(patient_id: str, db: Session) -> list:
    """Get meal recommendations based on patient's stone type"""
    latest_scan = db.query(KidneyScan).filter(
        KidneyScan.patient_id == patient_id
    ).order_by(KidneyScan.created_at.desc()).first()

    recommendations = [
        "Stay hydrated with 3+ liters of water daily",
        "Monitor sodium intake - keep below 2000mg",
        "Avoid processed foods"
    ]

    if latest_scan:
        if latest_scan.stone_type == "calcium_oxalate":
            recommendations.extend([
                "Limit oxalate-rich foods like spinach and nuts",
                "Maintain adequate calcium intake"
            ])
        elif latest_scan.stone_type == "uric_acid":
            recommendations.extend([
                "Reduce purine-rich foods (red meat, seafood)",
                "Avoid alcohol and sugary drinks"
            ])

    return recommendations


# ============= Doctor Profile Endpoints =============

@app.post("/api/doctors", response_model=DoctorResponse)
def add_doctor(
    doctor: DoctorCreate,
    current_patient: Patient = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """Add a doctor/clinic profile (name, hospital, phones, working hours)."""
    ensure_patient_access(current_patient, doctor.patient_id)
    patient = db.query(Patient).filter(Patient.id == doctor.patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    db_doctor = Doctor(
        id=f"doc_{uuid.uuid4().hex[:8]}",
        patient_id=doctor.patient_id,
        name=doctor.name.strip(),
        hospital=doctor.hospital,
        phone=doctor.phone,
        phone_additional=doctor.phone_additional,
        open_time=doctor.open_time,
        close_time=doctor.close_time,
        active=True,
    )
    db.add(db_doctor)
    db.commit()
    db.refresh(db_doctor)
    return db_doctor


@app.get("/api/doctors/{patient_id}", response_model=list[DoctorResponse])
def get_doctors(
    patient_id: str,
    current_patient: Patient = Depends(require_patient_access),
    db: Session = Depends(get_db),
):
    """List all active doctor profiles for the patient."""
    doctors = db.query(Doctor).filter(
        Doctor.patient_id == patient_id,
        Doctor.active == True  # noqa: E712
    ).order_by(Doctor.created_at.asc()).all()
    return doctors


@app.delete("/api/doctors/{doctor_id}", response_model=SuccessResponse)
def remove_doctor(
    doctor_id: str,
    current_patient: Patient = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """Remove a doctor profile (soft-deactivate)."""
    db_doctor = db.query(Doctor).filter(Doctor.id == doctor_id).first()
    if not db_doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")
    ensure_patient_access(current_patient, db_doctor.patient_id)

    db_doctor.active = False
    db.commit()
    return SuccessResponse(success=True, message=f"{db_doctor.name} removed from your doctors.")


# ============= Doctor Profile Endpoints =============

@app.post("/api/doctors", response_model=DoctorResponse)
def add_doctor(
    doctor: DoctorCreate,
    current_patient: Patient = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """Patient adds a doctor/clinic profile with contact details and working hours."""
    ensure_patient_access(current_patient, doctor.patient_id)
    patient = db.query(Patient).filter(Patient.id == doctor.patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    db_doctor = Doctor(
        id=f"doc_{uuid.uuid4().hex[:8]}",
        patient_id=doctor.patient_id,
        name=doctor.name.strip(),
        hospital=doctor.hospital,
        phone=doctor.phone,
        phone_additional=doctor.phone_additional,
        open_time=doctor.open_time,
        close_time=doctor.close_time,
        active=True,
    )
    db.add(db_doctor)
    db.commit()
    db.refresh(db_doctor)
    return db_doctor


@app.get("/api/doctors/{patient_id}", response_model=list[DoctorResponse])
def get_doctors(
    patient_id: str,
    current_patient: Patient = Depends(require_patient_access),
    db: Session = Depends(get_db),
):
    """List the patient's saved doctors (active only)."""
    doctors = db.query(Doctor).filter(
        Doctor.patient_id == patient_id,
        Doctor.active == True  # noqa: E712
    ).order_by(Doctor.created_at.asc()).all()
    return doctors


@app.delete("/api/doctors/{doctor_id}", response_model=SuccessResponse)
def remove_doctor(
    doctor_id: str,
    current_patient: Patient = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """Remove a saved doctor (soft-deactivate)."""
    db_doctor = db.query(Doctor).filter(Doctor.id == doctor_id).first()
    if not db_doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")
    ensure_patient_access(current_patient, db_doctor.patient_id)

    db_doctor.active = False
    db.commit()
    return SuccessResponse(success=True, message=f"{db_doctor.name} removed.")


# ============= Appointment Endpoints =============

@app.post("/api/appointments")
def create_appointment(
    payload: AppointmentCreate,
    current_patient: Patient = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """Create a new appointment record"""
    ensure_patient_access(current_patient, payload.patient_id)
    try:
        doctor = None
        if payload.doctor_id:
            doctor = db.query(Doctor).filter(
                Doctor.id == payload.doctor_id,
                Doctor.patient_id == payload.patient_id,
                Doctor.active == True  # noqa: E712
            ).first()
            if not doctor:
                raise HTTPException(status_code=404, detail="Doctor profile not found for this patient")

        appointment = Appointment(
            id=f"app_{uuid.uuid4().hex[:8]}",
            patient_id=payload.patient_id,
            appointment_date=datetime.fromisoformat(payload.appointment_date),
            appointment_type=payload.appointment_type,
            doctor_type=doctor.name if doctor else payload.doctor_type,
            doctor_id=doctor.id if doctor else None,
            title=payload.title,
            reason=payload.reason,
            description=payload.description,
            status="scheduled"
        )

        db.add(appointment)
        db.commit()
        db.refresh(appointment)

        return {
            "success": True,
            "appointment_id": appointment.id,
            "message": f"Appointment scheduled for {payload.appointment_date}",
            "appointment": {
                "id": appointment.id,
                "appointment_date": appointment.appointment_date.isoformat(),
                "appointment_type": appointment.appointment_type,
                "doctor_type": appointment.doctor_type,
                "doctor_id": appointment.doctor_id,
                "title": appointment.title,
                "reason": appointment.reason,
                "description": appointment.description,
                "status": appointment.status,
                "created_at": appointment.created_at.isoformat(),
                "doctor": _doctor_dict(doctor),
            }
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create appointment: {str(e)}")


def _doctor_dict(doctor):
    """Serialize a Doctor row (or None) for API responses."""
    if not doctor:
        return None
    return {
        "id": doctor.id,
        "name": doctor.name,
        "hospital": doctor.hospital,
        "phone": doctor.phone,
        "phone_additional": doctor.phone_additional,
        "open_time": doctor.open_time,
        "close_time": doctor.close_time,
    }


@app.get("/api/appointments/{patient_id}")
def get_appointments(
    patient_id: str,
    current_patient: Patient = Depends(require_patient_access),
    db: Session = Depends(get_db),
):
    """Get all appointments for a patient"""
    appointments = db.query(Appointment).filter(
        Appointment.patient_id == patient_id
    ).order_by(Appointment.appointment_date.desc()).all()

    doctor_cache = {}
    for app in appointments:
        if app.doctor_id and app.doctor_id not in doctor_cache:
            doctor_cache[app.doctor_id] = db.query(Doctor).filter(Doctor.id == app.doctor_id).first()

    return {
        "success": True,
        "appointments": [
            {
                "id": app.id,
                "appointment_date": app.appointment_date.isoformat(),
                "appointment_type": app.appointment_type,
                "doctor_type": app.doctor_type,
                "doctor_id": app.doctor_id,
                "title": app.title,
                "reason": app.reason,
                "description": app.description,
                "status": app.status,
                "created_at": app.created_at.isoformat(),
                "doctor": _doctor_dict(doctor_cache.get(app.doctor_id))
                    if app.doctor_id else None,
            }
            for app in appointments
        ]
    }


@app.delete("/api/appointments/{appointment_id}")
def delete_appointment(
    appointment_id: str,
    current_patient: Patient = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """Cancel/delete a booked appointment (owner only)."""
    try:
        appointment = db.query(Appointment).filter(
            Appointment.id == appointment_id
        ).first()
        if not appointment:
            raise HTTPException(status_code=404, detail="Appointment not found")
        ensure_patient_access(current_patient, appointment.patient_id)

        # Clean up any doctor recommendations attached to this appointment.
        db.query(DoctorRecommendation).filter(
            DoctorRecommendation.appointment_id == appointment_id
        ).delete(synchronize_session=False)

        title = appointment.title
        db.delete(appointment)
        db.commit()
        return {
            "success": True,
            "appointment_id": appointment_id,
            "message": f"Appointment '{title}' deleted",
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to delete appointment: {str(e)}")


# ============= Doctor Recommendations Endpoints =============

@app.post("/api/recommendations")
def save_recommendations(
    payload: RecommendationCreate,
    current_patient: Patient = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """Save doctor recommendations after consultation"""
    ensure_patient_access(current_patient, payload.patient_id)
    try:
        recommendation = DoctorRecommendation(
            id=f"rec_{uuid.uuid4().hex[:8]}",
            patient_id=payload.patient_id,
            appointment_id=payload.appointment_id,
            hydration_adjustment=payload.hydration_adjustment,
            dietary_changes=payload.dietary_changes,
            medication_changes=payload.medication_changes,
            monitoring_schedule=payload.monitoring_schedule,
            follow_up_date=datetime.fromisoformat(payload.follow_up_date) if payload.follow_up_date else None,
            appointment_date=datetime.fromisoformat(payload.appointment_date) if payload.appointment_date else None
        )

        db.add(recommendation)
        db.commit()
        db.refresh(recommendation)

        return {
            "success": True,
            "recommendation_id": recommendation.id,
            "message": "Doctor recommendations saved successfully",
            "follow_up_date": payload.follow_up_date
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to save recommendations: {str(e)}")


@app.get("/api/recommendations/{patient_id}")
def get_recommendations(
    patient_id: str,
    current_patient: Patient = Depends(require_patient_access),
    db: Session = Depends(get_db),
):
    """Get all doctor recommendations for a patient"""
    recommendations = db.query(DoctorRecommendation).filter(
        DoctorRecommendation.patient_id == patient_id
    ).order_by(DoctorRecommendation.created_at.desc()).all()

    return {
        "success": True,
        "recommendations": [
            {
                "id": rec.id,
                "appointment_id": rec.appointment_id,
                "hydration_adjustment": rec.hydration_adjustment,
                "dietary_changes": rec.dietary_changes,
                "medication_changes": rec.medication_changes,
                "monitoring_schedule": rec.monitoring_schedule,
                "follow_up_date": rec.follow_up_date.isoformat() if rec.follow_up_date else None,
                "appointment_date": rec.appointment_date.isoformat() if rec.appointment_date else None,
                "created_at": rec.created_at.isoformat()
            }
            for rec in recommendations
        ]
    }


@app.get("/api/recommendations/{patient_id}/latest")
def get_latest_recommendations(
    patient_id: str,
    current_patient: Patient = Depends(require_patient_access),
    db: Session = Depends(get_db),
):
    """Get the latest doctor recommendations for a patient"""
    recommendation = db.query(DoctorRecommendation).filter(
        DoctorRecommendation.patient_id == patient_id
    ).order_by(DoctorRecommendation.created_at.desc()).first()

    if not recommendation:
        return {
            "success": True,
            "recommendation": None,
            "message": "No recommendations yet"
        }

    return {
        "success": True,
        "recommendation": {
            "id": recommendation.id,
            "appointment_id": recommendation.appointment_id,
            "hydration_adjustment": recommendation.hydration_adjustment,
            "dietary_changes": recommendation.dietary_changes,
            "medication_changes": recommendation.medication_changes,
            "monitoring_schedule": recommendation.monitoring_schedule,
            "follow_up_date": recommendation.follow_up_date.isoformat() if recommendation.follow_up_date else None,
            "appointment_date": recommendation.appointment_date.isoformat() if recommendation.appointment_date else None,
            "created_at": recommendation.created_at.isoformat()
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
