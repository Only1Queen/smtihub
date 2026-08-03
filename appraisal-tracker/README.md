# SMTI Appraisal Tracker — Desktop App

## How to build the .exe (one-time setup)

### Step 1 — Install Node.js
Download from https://nodejs.org and install the LTS version.

### Step 2 — Open a terminal in this folder
Right-click the `appraisal-tracker` folder → "Open in Terminal"

### Step 3 — Install dependencies
```
npm install
```

### Step 4 — Build the .exe
```
npm run dist
```

The installer will appear in the `dist-exe/` folder.
Run it to install the app — it will appear in your Start Menu as **SMTI Appraisal Tracker**.

---

## Marks structure (100 total)

| Goal | KPI | Max Marks |
|------|-----|-----------|
| A – Operational Tasks | A1 | 10 |
| | A2 | 5 |
| | A3 | 5 |
| B – Threat Intelligence | B1 | 10 |
| | B2 (quarterly) | 5 |
| | B3 | 5 |
| C – Security Monitoring | C1 | 10 |
| | C2 | 5 |
| | C3 (quarterly) | 5 |
| D – Security Posture | D1–D4 | 5 each |
| E – Collaboration | E1–E5 | 4 each |

## Notes
- Default Manager PIN: **2026** (change from Settings after first login)
- All data stored locally — no internet needed
- Quarterly KPIs scored in Jun, Sep, Dec, Mar only
