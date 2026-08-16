/**
 * RenalCare AI - Frontend API Integration
 * Helper functions for the frontend to communicate with the backend.
 * Auth token is read from localStorage and attached to every request.
 */

const API_BASE_URL = "http://localhost:8001/api";

const getToken = () => localStorage.getItem("renalcare_token") || "";

async function request(url, options = {}, { auth = true } = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && typeof options.body === "string") {
    headers["Content-Type"] = "application/json";
  }
  if (auth) {
    const token = getToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
  }
  const response = await fetch(url, { ...options, headers });
  return response;
}

// ============= Auth =============

export const register = async (data) => {
  const response = await request(`${API_BASE_URL}/auth/register`, {
    method: "POST",
    body: JSON.stringify(data),
  }, { auth: false });
  return { ok: response.ok, status: response.status, data: await response.json() };
};

export const login = async (data) => {
  const response = await request(`${API_BASE_URL}/auth/login`, {
    method: "POST",
    body: JSON.stringify(data),
  }, { auth: false });
  return { ok: response.ok, status: response.status, data: await response.json() };
};

export const logout = async () => {
  const response = await request(`${API_BASE_URL}/auth/logout`, { method: "POST" });
  localStorage.removeItem("renalcare_token");
  localStorage.removeItem("renalcare_patient_id");
  localStorage.removeItem("renalcare_patient");
  return response;
};

export const getCurrentPatient = async () => {
  const response = await request(`${API_BASE_URL}/auth/me`);
  return response.json();
};

// ============= Patient Management =============

export const getPatient = async (patientId) => {
  const response = await request(`${API_BASE_URL}/patients/${patientId}`);
  return response.json();
};

export const getHealthSummary = async (patientId) => {
  const response = await request(`${API_BASE_URL}/patients/${patientId}/health-summary`);
  if (!response.ok) throw new Error(`Failed to load health summary (${response.status})`);
  return response.json();
};

// ============= Image Analysis =============

export const uploadScan = async (file, patientId, stoneType = "unknown") => {
  const formData = new FormData();
  formData.append("file", file);

  const url = new URL(`${API_BASE_URL}/analyze-scan`);
  url.searchParams.append("patient_id", patientId);
  url.searchParams.append("stone_type", stoneType);

  const response = await request(url.toString(), { method: "POST", body: formData }, { auth: true });

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `API Error: ${response.status} - ${response.statusText}`);
  }
  return response.json();
};

export const getPatientScans = async (patientId) => {
  const response = await request(`${API_BASE_URL}/scans/${patientId}`);
  if (!response.ok) throw new Error(`Failed to load scans (${response.status})`);
  return response.json();
};

export const getScanDetail = async (scanId) => {
  const response = await request(`${API_BASE_URL}/scans/detail/${scanId}`);
  return response.json();
};

export const getVisionMetrics = async () => {
  const response = await request(`${API_BASE_URL}/vision/metrics`, {}, { auth: false });
  return response.json();
};

// ============= Water Intake =============

export const logWaterIntake = async (patientId, amountMl, time, notes) => {
  const response = await request(`${API_BASE_URL}/water-intake`, {
    method: "POST",
    body: JSON.stringify({ patient_id: patientId, amount_ml: amountMl, time, notes }),
  });
  if (!response.ok) throw new Error(`Failed to log water intake (${response.status})`);
  return response.json();
};

export const getDailyWaterSummary = async (patientId, date = null) => {
  let url = `${API_BASE_URL}/water-intake/${patientId}/daily`;
  if (date) url += `?date=${date}`;
  const response = await request(url);
  if (!response.ok) {
    console.warn(`Water summary endpoint returned ${response.status}`);
    return {
      patient_id: patientId,
      date: new Date().toISOString().split("T")[0],
      total_intake_ml: 0,
      goal_ml: 2500,
      intakes: [],
    };
  }
  return response.json();
};

export const getWaterHistory = async (patientId, days = 7) => {
  const response = await request(`${API_BASE_URL}/water-intake/${patientId}/history?days=${days}`);
  return response.json();
};

export const getRiskInsights = async (patientId, days = 30) => {
  const response = await request(`${API_BASE_URL}/risk-insights/${patientId}?days=${days}`);
  if (!response.ok) throw new Error(`API Error: ${response.status}`);
  return response.json();
};

export const getRiskHistory = async (patientId, months = 6) => {
  const response = await request(`${API_BASE_URL}/risk-insights/${patientId}/history?months=${months}`);
  if (!response.ok) throw new Error(`API Error: ${response.status}`);
  return response.json();
};

export const resetWaterIntakeForDay = async (patientId, date = null) => {
  let url = `${API_BASE_URL}/water-intake/${patientId}/reset`;
  if (date) url += `?date=${date}`;
  const response = await request(url, { method: "DELETE" });
  if (!response.ok) throw new Error(`API Error: ${response.status}`);
  return response.json();
};

// ============= Meals =============

export const logMeal = async (mealData) => {
  const response = await request(`${API_BASE_URL}/meals`, {
    method: "POST",
    body: JSON.stringify(mealData),
  });
  return response.json();
};

export const getDailyMealSummary = async (patientId, date = null) => {
  const url = date
    ? `${API_BASE_URL}/meals/${patientId}/daily?date=${date}`
    : `${API_BASE_URL}/meals/${patientId}/daily`;
  const response = await request(url);
  return response.json();
};

export const getMealHistory = async (patientId, days = 7) => {
  const response = await request(`${API_BASE_URL}/meals/${patientId}/history?days=${days}`);
  return response.json();
};

// ============= Diet Recommendations =============

export const getDietRecommendations = async (stoneType) => {
  const response = await request(`${API_BASE_URL}/diet-recommendations/${stoneType}`);
  return response.json();
};

export const getAllDietRecommendations = async () => {
  const response = await request(`${API_BASE_URL}/diet-recommendations`);
  return response.json();
};

export const updatePatientDiet = async (patientId, stoneType) => {
  const response = await request(
    `${API_BASE_URL}/diet-recommendations/${patientId}?stone_type=${stoneType}`,
    { method: "POST" }
  );
  return response.json();
};

// ============= Risk Prediction =============

export const predictRisk = async (riskData) => {
  const response = await request(`${API_BASE_URL}/predict-risk`, {
    method: "POST",
    body: JSON.stringify(riskData),
  });
  return response.json();
};

export const getPatientRiskScore = async (patientId) => {
  const response = await request(`${API_BASE_URL}/patients/${patientId}/risk-score`);
  return response.json();
};

// ============= Health Check =============

export const checkApiHealth = async () => {
  const response = await request(`${API_BASE_URL.replace("/api", "")}/health`, {}, { auth: false });
  return response.json();
};

// ============= Medicines & Prescriptions =============

export const getMedicines = async (patientId) => {
  const response = await request(`${API_BASE_URL}/medicines/${patientId}`);
  if (!response.ok) throw new Error(`Failed to load medicines (${response.status})`);
  return response.json();
};

export const addMedicine = async (medicineData) => {
  const response = await request(`${API_BASE_URL}/medicines`, {
    method: "POST",
    body: JSON.stringify(medicineData),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Failed to add medicine (${response.status})`);
  }
  return response.json();
};

export const removeMedicine = async (medicineId) => {
  const response = await request(`${API_BASE_URL}/medicines/${medicineId}`, {
    method: "DELETE",
  });
  if (!response.ok) throw new Error(`Failed to remove medicine (${response.status})`);
  return response.json();
};

export const savePrescription = async (patientId, appointmentId, medicines) => {
  const response = await request(`${API_BASE_URL}/prescriptions`, {
    method: "POST",
    body: JSON.stringify({ patient_id: patientId, appointment_id: appointmentId, medicines }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Failed to save prescription (${response.status})`);
  }
  return response.json();
};

// ============= Doctors =============

export const addDoctor = async (doctorData) => {
  const response = await request(`${API_BASE_URL}/doctors`, {
    method: "POST",
    body: JSON.stringify(doctorData),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Failed to add doctor (${response.status})`);
  }
  return response.json();
};

export const getDoctors = async (patientId) => {
  const response = await request(`${API_BASE_URL}/doctors/${patientId}`);
  if (!response.ok) throw new Error(`Failed to load doctors (${response.status})`);
  return response.json();
};

export const removeDoctor = async (doctorId) => {
  const response = await request(`${API_BASE_URL}/doctors/${doctorId}`, {
    method: "DELETE",
  });
  if (!response.ok) throw new Error(`Failed to remove doctor (${response.status})`);
  return response.json();
};

// ============= Appointments =============

export const createAppointment = async (patientId, appointmentData) => {
  const response = await request(`${API_BASE_URL}/appointments`, {
    method: "POST",
    body: JSON.stringify({ patient_id: patientId, ...appointmentData }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Failed to book appointment (${response.status})`);
  }
  return response.json();
};

export const saveRecommendations = async (patientId, appointmentId, recommendations) => {
  const response = await request(`${API_BASE_URL}/recommendations`, {
    method: "POST",
    body: JSON.stringify({
      patient_id: patientId,
      appointment_id: appointmentId,
      hydration_adjustment: recommendations.hydrationAdjustment,
      dietary_changes: recommendations.dietaryChanges,
      medication_changes: recommendations.medicationChanges,
      monitoring_schedule: recommendations.monitoringSchedule,
      follow_up_date: recommendations.followUpDate,
      appointment_date: recommendations.appointmentDate,
    }),
  });
  return response.json();
};

export const getAppointments = async (patientId) => {
  const response = await request(`${API_BASE_URL}/appointments/${patientId}`);
  if (!response.ok) throw new Error(`Failed to load appointments (${response.status})`);
  return response.json();
};

export const getDashboardSummary = async (patientId) => {
  const response = await request(`${API_BASE_URL}/dashboard/summary/${patientId}`);
  if (!response.ok) throw new Error(`Failed to load dashboard summary (${response.status})`);
  return response.json();
};

export const deleteAppointment = async (appointmentId) => {
  const response = await request(`${API_BASE_URL}/appointments/${appointmentId}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Failed to delete appointment (${response.status})`);
  }
  return response.json();
};

export const getRecommendations = async (patientId) => {
  const response = await request(`${API_BASE_URL}/recommendations/${patientId}`);
  return response.json();
};

// ============= Health Goals =============

export const getHealthGoals = async (patientId) => {
  const response = await request(`${API_BASE_URL}/goals/${patientId}`);
  if (!response.ok) throw new Error(`Failed to load goals (${response.status})`);
  return response.json();
};
