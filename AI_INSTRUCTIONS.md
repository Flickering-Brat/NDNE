# 🤖 AI Developer Guide: NDNE Sales Management System

Welcome, AI Assistant! You are tasked with maintaining, enhancing, or debugging the IndianOil NDNE Sales Management Dashboard. This document provides a comprehensive overview of the architecture, tech stack, and data pipelines.

## ⚠️ Security & Credentials (CRITICAL)
**DO NOT expose, print, or hardcode database credentials in any scripts you write.** 
The user's database credentials, Wallet passwords, and WhatsApp phone numbers are strictly isolated in the `.env` file located in the root directory. 

Whenever you write Python scripts connecting to the Oracle database, you MUST use the `dotenv` library to load variables dynamically:
```python
import os
from dotenv import load_dotenv
load_dotenv()

DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
# ...
```

## 🛠 Tech Stack
- **Frontend/UI**: Streamlit, Streamlit-AgGrid (for interactive tables), Plotly (for charts), PyDeck (for 3D Mapping)
- **Backend/Database**: Oracle Autonomous Database (mTLS Wallet connection via `oracledb` python driver)
- **Data Manipulation**: Pandas
- **Automation**: `pywhatkit` (WhatsApp), `crontab` (macOS Scheduler)

## 🗄️ Database Architecture (Oracle)
There are 3 core tables in the database:
1. `NDNE_MASTER`: The ultimate Source of Truth for all distributors. Contains `DIST_CODE`, `DIST_NAME`, `DISTRICT`, and `LSA_NAME`.
2. `NDNE_BASELINE`: Historical targets (Last FY). Contains `AVG_MONTHLY_QTY_MT` (which acts as the baseline for proration) and `TOTAL_LY_QTY_EA`.
3. `NDNE_ACTUALS`: Daily live sales for the Current Year (CY). Contains `QTY_MT`, `QTY_EA`, `BILL_DATE`, and `SEGMENT` (material classification like 19kg, 425kg Jumbo, etc).

**Data Joining Rule (The "Zero-Seller" Fix)**:
To calculate YTD metrics, the app **ALWAYS** starts with a Left Join from `NDNE_MASTER`. It left-joins Baseline targets, and then left-joins CY Actuals onto the master list. This guarantees that inactive distributors (who have 0 sales in CY and 0 in Baseline) still appear on the dashboard with a 0 MT actual and 0 MT target.

## 📂 Core Project Files
### 1. `app.py` (The Main Dashboard)
This is the Streamlit web application. It features a sidebar for uploading Daily Actuals (`process_actuals` function). It has 5 main UI branches (Tabs):
- **📆 Daily Velocity**: A multi-line Plotly chart comparing CY daily dispatches against the average LY daily target.
- **📅 Month-wise**: An AgGrid table pivoting CY Actuals by month, comparing them against the total Monthly Target.
- **🚚 Distributor Grid**: An AgGrid table listing every distributor, their Prorated YTD Target, their CY YTD Actuals, and their % Growth.
- **🏢 LSA Grid**: An aggregated version of the Distributor Grid grouped by `LSA_NAME`, complete with a "GRAND TOTAL" row at the bottom.
- **📦 Product YTD**: Compares Unit/EA sales by Material Segment (19kg, 425kg Jumbo, etc).

*(Note: There is also a dummy Geographic Heat Map using PyDeck that will be hooked to exact GPS coordinates in the future).*

### 2. `alert_bot.py` (The Automation Bot)
A standalone headless Python script designed to be run via macOS `crontab` at 8:00 AM daily.
- It queries the Oracle database to calculate the YTD achievement of all distributors.
- It identifies any distributor operating below **75% achievement**.
- It writes the results to `Action_Report.txt`.
- It uses `pywhatkit` to physically open the user's default browser and automatically send a WhatsApp alert to the user's phone.

### 3. `upload_baseline.py`
A standalone script used strictly as a one-time exercise to parse the Last FY Excel file and permanently write the 590+ baseline targets to the `NDNE_BASELINE` Oracle table. 

## 🚀 How to Enhance the App
If the user asks you to add a new "Analysis Branch" or Tab:
1. Ensure you pull data from the existing `df_core` dataframe in `app.py` (which is the pre-merged Master + Baseline + Actuals dataset).
2. For rendering tables, always use `AgGrid` configured with `fit_columns_on_grid_load=True` and `theme='alpine'` rather than `st.dataframe` to ensure maximum interactivity for the user.
3. For material logic, ensure any new material codes encountered by the user are added to `ndne_mat_code.txt` and accounted for in the `classify_and_weigh` python function!

## 🔮 Future Roadmap (Ideas for the Next AI)
If the user wants to expand the app, here are the highly recommended next steps to build:
1. **Real Geospatial Mapping**: The original `tab_map` was removed because we didn't have exact GPS coordinates. The next major upgrade should be taking a master file of District Lat/Lon coordinates and rebuilding the PyDeck 3D Heatmap.
2. **Predictive Forecasting**: Since we now have a robust Oracle database of historical daily sales, a future AI could implement `Prophet` or `scikit-learn` to forecast next month's sales velocity.
3. **Automated PDF Reports**: Use `fpdf` to generate a professional PDF version of the `Action_Report.txt` and attach it to an automated daily email via `smtplib` instead of just WhatsApp.
