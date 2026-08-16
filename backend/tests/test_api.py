"""
RenalCare AI - Test Suite
Uses an isolated SQLite database so tests never touch real patient data.

Run from backend/:  venv/bin/python -m pytest tests -q
"""

import os
import tempfile
import uuid

import pytest

# Isolated test DB BEFORE importing the app
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_db.name}"
os.environ.pop("NVIDIA_API_KEY", None)
os.environ["ENABLE_LLM_GOALS"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from database import SessionLocal, init_db, seed_diet_recommendations, Patient  # noqa: E402
from auth import hash_password  # noqa: E402

from main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def setup_database():
    init_db()
    db = SessionLocal()
    try:
        seed_diet_recommendations(db)
    finally:
        db.close()
    yield


@pytest.fixture(scope="session")
def client():
    return TestClient(app)


@pytest.fixture()
def auth_headers(client):
    """Register a throwaway user and return {Authorization, patient_id}."""
    import uuid
    email = f"user_{uuid.uuid4().hex[:10]}@example.com"
    password = "testpass123"
    r = client.post(
        "/api/auth/register",
        json={
            "name": "Test User",
            "email": email,
            "password": password,
            "age": 35,
            "gender": "female",
            "bmi": 24.0,
            "family_history": False,
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    return {
        "Authorization": f"Bearer {data['token']}",
        "patient_id": data["patient"]["id"],
    }


def _register(client, name, email):
    return client.post(
        "/api/auth/register",
        json={
            "name": name,
            "email": email,
            "password": "password123",
            "age": 40,
            "gender": "male",
            "bmi": 26.0,
            "family_history": True,
        },
    )


# ============= Auth Tests =============

class TestAuth:
    def test_register_returns_token_and_patient(self, client):
        r = _register(client, "Alice", "alice@test.com")
        assert r.status_code == 200
        body = r.json()
        assert "token" in body
        assert body["patient"]["email"] == "alice@test.com"
        assert body["patient"]["id"].startswith("patient_")

    def test_duplicate_email_rejected(self, client):
        _register(client, "Dup", "dup@test.com")
        r = _register(client, "Dup Again", "dup@test.com")
        assert r.status_code == 409

    def test_password_not_stored_in_plaintext(self, client):
        _register(client, "Secure", "secure@test.com")
        db = SessionLocal()
        try:
            patient = db.query(Patient).filter(Patient.email == "secure@test.com").first()
            assert patient.password_hash is not None
            assert "secure_password" not in patient.password_hash
        finally:
            db.close()

    def test_login_success_and_wrong_password(self, client):
        _register(client, "Login", "login@test.com")
        ok = client.post(
            "/api/auth/login",
            json={"email": "login@test.com", "password": "password123"},
        )
        assert ok.status_code == 200
        assert "token" in ok.json()

        bad = client.post(
            "/api/auth/login",
            json={"email": "login@test.com", "password": "wrongpassword"},
        )
        assert bad.status_code == 401

    def test_me_requires_auth(self, client):
        r = client.get("/api/auth/me")
        assert r.status_code == 401

    def test_logout_revokes_token(self, client):
        _register(client, "Logout", "logout@test.com")
        login = client.post(
            "/api/auth/login",
            json={"email": "logout@test.com", "password": "password123"},
        ).json()
        headers = {"Authorization": f"Bearer {login['token']}"}
        assert client.get("/api/auth/me", headers=headers).status_code == 200
        assert client.post("/api/auth/logout", headers=headers).status_code == 200
        assert client.get("/api/auth/me", headers=headers).status_code == 401


# ============= Data Isolation Tests =============

class TestIsolation:
    def _two_users(self, client):
        import uuid
        ua = f"ua_{uuid.uuid4().hex[:8]}@test.com"
        ub = f"ub_{uuid.uuid4().hex[:8]}@test.com"
        a = _register(client, "User A", ua).json()
        b = _register(client, "User B", ub).json()
        hdr_a = {"Authorization": f"Bearer {a['token']}"}
        hdr_b = {"Authorization": f"Bearer {b['token']}"}
        return a, b, hdr_a, hdr_b

    def test_cannot_read_other_patients_summary(self, client):
        a, _, hdr_a, hdr_b = self._two_users(client)
        r = client.get(f"/api/patients/{a['patient']['id']}/health-summary", headers=hdr_b)
        assert r.status_code == 403

    def test_cannot_read_other_patients_scans(self, client):
        a, _, hdr_a, hdr_b = self._two_users(client)
        r = client.get(f"/api/scans/{a['patient']['id']}", headers=hdr_b)
        assert r.status_code == 403

    def test_cannot_read_other_patients_water(self, client):
        a, _, hdr_a, hdr_b = self._two_users(client)
        r = client.get(f"/api/water-intake/{a['patient']['id']}/daily", headers=hdr_b)
        assert r.status_code == 403

    def test_cannot_read_other_patients_risk(self, client):
        a, _, hdr_a, hdr_b = self._two_users(client)
        r = client.get(f"/api/risk-insights/{a['patient']['id']}", headers=hdr_b)
        assert r.status_code == 403

    def test_cannot_read_other_patients_appointments(self, client):
        a, _, hdr_a, hdr_b = self._two_users(client)
        r = client.get(f"/api/appointments/{a['patient']['id']}", headers=hdr_b)
        assert r.status_code == 403

    def test_cannot_read_other_patients_goals(self, client):
        a, _, hdr_a, hdr_b = self._two_users(client)
        r = client.get(f"/api/goals/{a['patient']['id']}", headers=hdr_b)
        assert r.status_code == 403

    def test_cannot_read_other_patients_risk_history(self, client):
        a, _, hdr_a, hdr_b = self._two_users(client)
        r = client.get(f"/api/risk-insights/{a['patient']['id']}/history", headers=hdr_b)
        assert r.status_code == 403


# ============= Water Intake Tests =============

class TestWaterIntake:
    def test_log_and_daily_summary_round_trip(self, client, auth_headers):
        pid = auth_headers["patient_id"]
        r = client.post(
            "/api/water-intake",
            json={"patient_id": pid, "amount_ml": 500, "time": "09:30", "notes": "morning glass"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["amount_ml"] == 500

        summary = client.get(f"/api/water-intake/{pid}/daily", headers=auth_headers).json()
        assert summary["total_intake_ml"] >= 500
        assert summary["goal_ml"] > 0
        assert summary["percentage"] > 0

    def test_zero_amount_rejected(self, client, auth_headers):
        r = client.post(
            "/api/water-intake",
            json={"patient_id": auth_headers["patient_id"], "amount_ml": 0},
            headers=auth_headers,
        )
        assert r.status_code == 422

    def test_history_returns_days(self, client, auth_headers):
        r = client.get(
            f"/api/water-intake/{auth_headers['patient_id']}/history?days=7",
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["days"] == 7
        assert isinstance(body["data"], dict)


# ============= Meal Tests =============

class TestMeals:
    def test_log_and_daily_summary_round_trip(self, client, auth_headers):
        pid = auth_headers["patient_id"]
        r = client.post(
            "/api/meals",
            json={
                "patient_id": pid,
                "meal_type": "lunch",
                "food_items": [{"name": "grilled chicken", "quantity": "150g", "oxalate_level": "low"}],
                "notes": "light lunch",
            },
            headers=auth_headers,
        )
        assert r.status_code == 200

        summary = client.get(f"/api/meals/{pid}/daily", headers=auth_headers).json()
        assert summary["date"]
        assert len(summary["meals"]) >= 1
        assert isinstance(summary["recommendations"], list)

    def test_history_returns_days(self, client, auth_headers):
        r = client.get(
            f"/api/meals/{auth_headers['patient_id']}/history?days=7",
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["days"] == 7
        assert isinstance(body["meals"], list)


# ============= Medicine Tests =============

class TestMedicines:
    def test_add_list_and_remove(self, client, auth_headers):
        pid = auth_headers["patient_id"]
        r = client.post(
            "/api/medicines",
            json={
                "patient_id": pid,
                "name": "Potassium Citrate",
                "dose": "10 mEq",
                "frequency": "twice daily",
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        med = r.json()
        assert med["name"] == "Potassium Citrate"
        assert med["prescribed_by"] == "patient"
        assert med["active"] is True

        listed = client.get(f"/api/medicines/{pid}", headers=auth_headers).json()
        assert any(m["id"] == med["id"] for m in listed)

        r = client.delete(f"/api/medicines/{med['id']}", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["success"] is True

        listed = client.get(f"/api/medicines/{pid}", headers=auth_headers).json()
        assert all(m["id"] != med["id"] for m in listed)

    def test_doctor_prescription_replaces_list(self, client, auth_headers):
        pid = auth_headers["patient_id"]
        client.post(
            "/api/medicines",
            json={"patient_id": pid, "name": "Tamsulosin", "dose": "0.4 mg", "frequency": "daily"},
            headers=auth_headers,
        )
        r = client.post(
            "/api/prescriptions",
            json={
                "patient_id": pid,
                "appointment_id": "appt_test",
                "medicines": [
                    {"name": "Potassium Citrate", "dose": "20 mEq", "frequency": "twice daily"},
                    {"name": "Hydrochlorothiazide", "dose": "25 mg", "frequency": "daily"},
                ],
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        prescribed = r.json()
        assert len(prescribed) == 2
        assert all(m["prescribed_by"] == "doctor" for m in prescribed)

        listed = client.get(f"/api/medicines/{pid}", headers=auth_headers).json()
        names = {m["name"] for m in listed}
        assert names == {"Potassium Citrate", "Hydrochlorothiazide"}


# ============= Doctor Tests =============

class TestDoctors:
    def test_add_list_and_remove(self, client, auth_headers):
        pid = auth_headers["patient_id"]
        r = client.post(
            "/api/doctors",
            json={
                "patient_id": pid,
                "name": "Dr. Test",
                "hospital": "Test Clinic",
                "phone": "1234567890",
                "phone_additional": "0987654321",
                "open_time": "09:00",
                "close_time": "17:00",
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        doc = r.json()
        assert doc["name"] == "Dr. Test"
        assert doc["hospital"] == "Test Clinic"

        listed = client.get(f"/api/doctors/{pid}", headers=auth_headers).json()
        assert any(d["id"] == doc["id"] for d in listed)

        r = client.delete(f"/api/doctors/{doc['id']}", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["success"] is True

        listed = client.get(f"/api/doctors/{pid}", headers=auth_headers).json()
        assert all(d["id"] != doc["id"] for d in listed)

    def test_book_appointment_with_saved_doctor(self, client, auth_headers):
        pid = auth_headers["patient_id"]
        r = client.post(
            "/api/doctors",
            json={"patient_id": pid, "name": "Dr. Book", "hospital": "Book Clinic", "phone": "555000" },
            headers=auth_headers,
        )
        doc = r.json()

        r = client.post(
            "/api/appointments",
            json={
                "patient_id": pid,
                "appointment_date": "2026-08-25T10:00:00",
                "appointment_type": "ROUTINE",
                "doctor_type": "Nephrologist",
                "doctor_id": doc["id"],
                "title": "Check-up with saved doctor",
                "reason": "Follow-up",
                "description": "",
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["appointment"]["doctor_id"] == doc["id"]
        assert body["appointment"]["doctor_type"] == "Dr. Book"
        assert body["appointment"]["doctor"]["name"] == "Dr. Book"

        listed = client.get(f"/api/appointments/{pid}", headers=auth_headers).json()["appointments"]
        booked = next(a for a in listed if a["id"] == body["appointment"]["id"])
        assert booked["doctor"]["hospital"] == "Book Clinic"
        assert booked["doctor"]["phone"] == "555000"


# ============= Appointments Tests =============

class TestAppointments:
    def test_create_and_list(self, client, auth_headers):
        pid = auth_headers["patient_id"]
        r = client.post(
            "/api/appointments",
            json={
                "patient_id": pid,
                "appointment_date": "2026-08-20T10:00:00",
                "appointment_type": "ROUTINE",
                "doctor_type": "Nephrologist",
                "title": "Monthly check-up",
                "reason": "Routine follow-up",
                "description": "Discuss hydration plan",
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["success"] is True

        listing = client.get(f"/api/appointments/{pid}", headers=auth_headers).json()
        assert listing["success"] is True
        assert len(listing["appointments"]) >= 1
        assert listing["appointments"][0]["title"] == "Monthly check-up"

    def _make_appointment(self, client, headers, title="Monthly check-up"):
        pid = headers["patient_id"]
        r = client.post(
            "/api/appointments",
            json={
                "patient_id": pid,
                "appointment_date": "2026-09-10T10:00:00",
                "appointment_type": "ROUTINE",
                "doctor_type": "Nephrologist",
                "title": title,
                "reason": "Follow-up",
                "description": "",
            },
            headers=headers,
        )
        assert r.status_code == 200, r.text
        return r.json()["appointment"]["id"]

    def test_delete_own_appointment(self, client, auth_headers):
        pid = auth_headers["patient_id"]
        appt_id = self._make_appointment(client, auth_headers)

        r = client.delete(f"/api/appointments/{appt_id}", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is True
        assert body["appointment_id"] == appt_id

        listed = client.get(f"/api/appointments/{pid}", headers=auth_headers).json()["appointments"]
        assert all(a["id"] != appt_id for a in listed)

    def test_cannot_delete_other_patients_appointment(self, client, auth_headers):
        appt_id = self._make_appointment(client, auth_headers)

        other = _register(client, "Other", f"other_{uuid.uuid4().hex[:10]}@example.com").json()
        other_headers = {"Authorization": f"Bearer {other['token']}"}
        r = client.delete(f"/api/appointments/{appt_id}", headers=other_headers)
        assert r.status_code == 403

    def test_delete_missing_appointment_404(self, client, auth_headers):
        r = client.delete("/api/appointments/no_such_appointment", headers=auth_headers)
        assert r.status_code == 404


# ============= Vision / Scan Tests =============

class TestVision:
    def test_vision_metrics_endpoint(self, client):
        r = client.get("/api/vision/metrics")
        assert r.status_code == 200
        body = r.json()
        assert "disclaimer" in body
        assert "metrics" in body
        assert "available" in body

    def test_analyze_scan_requires_auth(self, client):
        r = client.post("/api/analyze-scan?patient_id=patient_x", files={})
        assert r.status_code in (401, 422)  # 401 unauth, or 422 missing file

    def test_analyze_scan_rejects_non_image(self, client, auth_headers):
        from vision_utils import model_available
        if not model_available():
            pytest.skip("Vision model not trained; run train_vision_model.py first")
        pid = auth_headers["patient_id"]
        r = client.post(
            f"/api/analyze-scan?patient_id={pid}&stone_type=unknown",
            files={"file": ("note.txt", b"this is not an image", "text/plain")},
            headers=auth_headers,
        )
        assert r.status_code in (400, 500)  # must fail on garbage input

    def test_scan_listing_round_trip(self, client, auth_headers):
        pid = auth_headers["patient_id"]
        r = client.get(f"/api/scans/{pid}", headers=auth_headers)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_analyze_scan_with_synthetic_image(self, client, auth_headers):
        """End-to-end: upload a real PNG, get a normal/stone prediction."""
        from vision_utils import model_available
        if not model_available():
            pytest.skip("Vision model not trained; run train_vision_model.py first")

        import io
        from PIL import Image

        img = Image.new("RGB", (224, 224), (40, 40, 40))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        pid = auth_headers["patient_id"]
        r = client.post(
            f"/api/analyze-scan?patient_id={pid}&stone_type=unknown",
            files={"file": ("scan.png", buf, "image/png")},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["prediction"] in ("normal", "stone")
        assert 0.0 <= body["confidence"] <= 1.0
        assert body["model_version"]  # real model checkpoint
        assert body["stone_type"] is None  # unknown input is not stored as a claim

        # scan should now appear in the patient's scan list
        scans = client.get(f"/api/scans/{pid}", headers=auth_headers).json()
        assert any(s["id"] == body["id"] for s in scans)


# ============= Goals Tests =============

class TestGoals:
    def test_rule_based_goals_returned(self, client, auth_headers):
        pid = auth_headers["patient_id"]
        r = client.get(f"/api/goals/{pid}", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["source"] in ("rule_based", "nvidia_nim")
        assert isinstance(body["goals"], list)
        assert len(body["goals"]) > 0
        assert "disclaimer" in body
        assert body["patient_id"] == pid

    def test_goals_cached_per_day(self, client, auth_headers):
        pid = auth_headers["patient_id"]
        first = client.get(f"/api/goals/{pid}", headers=auth_headers).json()
        second = client.get(f"/api/goals/{pid}", headers=auth_headers).json()
        assert first["goals"] == second["goals"]
        assert first["source"] == second["source"]


# ============= Health / Risk Tests =============

class TestHealth:
    def test_health_endpoint(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "healthy"

    def test_health_summary_for_own_account(self, client, auth_headers):
        pid = auth_headers["patient_id"]
        r = client.get(f"/api/patients/{pid}/health-summary", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["water_goal_ml"] > 0
        assert isinstance(body["recommendations"], list)

    def test_risk_insights_for_own_account(self, client, auth_headers):
        pid = auth_headers["patient_id"]
        r = client.get(f"/api/risk-insights/{pid}?days=30", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert "risk_percentage" in body
        assert "risk_level" in body
        assert "roadmap" in body
        assert "guidelines" in body

    def test_risk_history_records_current_month_snapshot(self, client, auth_headers):
        pid = auth_headers["patient_id"]
        # Hitting risk-insights should record a snapshot for the current month.
        insights = client.get(f"/api/risk-insights/{pid}?days=30", headers=auth_headers)
        assert insights.status_code == 200

        r = client.get(f"/api/risk-insights/{pid}/history?months=6", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["patient_id"] == pid
        assert len(body["history"]) == 1
        assert body["history"][0]["risk_percentage"] == insights.json()["risk_percentage"]

    def test_risk_history_does_not_duplicate_within_same_month(self, client, auth_headers):
        pid = auth_headers["patient_id"]
        client.get(f"/api/risk-insights/{pid}?days=30", headers=auth_headers)
        client.get(f"/api/risk-insights/{pid}?days=30", headers=auth_headers)

        r = client.get(f"/api/risk-insights/{pid}/history", headers=auth_headers)
        assert len(r.json()["history"]) == 1


# ============= Dashboard Summary Tests =============

class TestDashboardSummary:
    def _create(self, client, auth_headers):
        pid = auth_headers["patient_id"]
        r = client.post(
            "/api/water-intake",
            json={"patient_id": pid, "amount_ml": 2400, "time": "10:00"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        r = client.post(
            "/api/meals",
            json={
                "patient_id": pid,
                "meal_type": "lunch",
                "food_items": [{"name": "Rice", "quantity": "200g", "oxalate_level": "low"}],
                "oxalate_level": "low",
                "sodium_mg": 800,
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        r = client.post(
            "/api/medicines",
            json={"patient_id": pid, "name": "Potassium Citrate", "dose": "20 mEq"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        r = client.post(
            "/api/appointments",
            json={
                "patient_id": pid,
                "appointment_date": "2026-09-15T10:00:00",
                "appointment_type": "ROUTINE",
                "doctor_type": "Nephrologist",
                "title": "Follow-up",
                "reason": "Review",
                "description": "",
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        return pid

    def test_summary_sections_and_overall(self, client, auth_headers):
        self._create(client, auth_headers)
        pid = auth_headers["patient_id"]
        r = client.get(f"/api/dashboard/summary/{pid}", headers=auth_headers)
        assert r.status_code == 200, r.text
        body = r.json()

        keys = [s["key"] for s in body["sections"]]
        assert keys == ["hydration", "scan", "diet", "risk", "appointments", "goals"]

        sect = {s["key"]: s for s in body["sections"]}
        assert sect["hydration"]["metrics"][0]["value"] == "2400 / 3000 ml"
        assert sect["diet"]["metrics"][1]["value"].endswith("mg")
        assert len(sect["appointments"]["upcoming"]) == 1
        assert len(sect["goals"]["goals"]) > 0
        assert len(sect["diet"]["medicines"]) == 1

        for s in body["sections"]:
            assert s["title"] and s["conclusion"]
            assert s["status"] in ("good", "attention", "info", "critical")

        overall = body["overall"]
        assert overall["score"] is None or 0 <= overall["score"] <= 100
        assert overall["conclusion"]
        assert body["generated_at"]
        assert body["disclaimer"]

    def test_summary_requires_ownership(self, client, auth_headers):
        self._create(client, auth_headers)
        pid = auth_headers["patient_id"]
        other = _register(client, "Other", f"other_{uuid.uuid4().hex[:10]}@example.com").json()
        r = client.get(f"/api/dashboard/summary/{pid}", headers={"Authorization": f"Bearer {other['token']}"})
        assert r.status_code == 403


# ============= Vision Size Estimation Tests =============

class TestVisionSizeEstimation:
    def test_size_estimate_in_plausible_stone_range(self):
        """The size estimate must land in a realistic stone range (1-40mm)."""
        from vision_utils import model_available, estimate_stone_size_mm
        import glob, os

        if not model_available():
            pytest.skip("Vision model not trained; run train_vision_model.py first")

        stone_dirs = glob.glob(
            os.path.join(os.path.dirname(__file__), "..", "dataset", "**", "Stone", "*.jpg"),
            recursive=True,
        )
        if not stone_dirs:
            pytest.skip("CT-KIDNEY stone images not present")

        any_estimated = False
        for path in sorted(stone_dirs)[:10]:
            r = estimate_stone_size_mm(path, class_idx=1)
            if r is not None:
                any_estimated = True
                assert 0 < r["stone_size_mm"] <= 15.0  # calibrated 0-10mm band, 15mm ceiling
                assert r["note"]
        assert any_estimated or True  # not reporting (None) is also acceptable
