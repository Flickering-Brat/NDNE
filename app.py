import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import oracledb
import time
import os
import calendar
from dotenv import load_dotenv
from st_aggrid import AgGrid, GridOptionsBuilder

st.set_page_config(page_title="IndianOil NDNE Sales Management", layout="wide", page_icon="🛢️")

# ── Brand Theming ─────────────────────────────────────────────────────────────
st.markdown("""
<style>
  .stApp { background-color: #0b1426; color: white; }
  h1, h2, h3 { color: #F58220 !important; }
  .stMetric { background-color: #0d1f3c; border-radius: 10px; padding: 15px;
              border: 1px solid #F58220; }
  div[data-testid="stMetricValue"] { color: #F58220; }
  div[data-testid="stMetricDelta"] > div { font-size: 0.85rem; }
  .stTabs [data-baseweb="tab-list"] { gap: 4px; }
  .stTabs [data-baseweb="tab"] { border-radius: 6px 6px 0 0; }
  .block-container { padding-top: 1.2rem; }
  .section-header {
    background: linear-gradient(90deg, #002F6C 0%, #0b1426 100%);
    border-left: 4px solid #F58220; padding: 8px 16px; border-radius: 4px;
    margin: 12px 0 8px 0; font-weight: 600; color: #F58220 !important;
  }
</style>
""", unsafe_allow_html=True)

# ── Env / DB ──────────────────────────────────────────────────────────────────
# Load from .env locally, but fallback to st.secrets for Streamlit Cloud
load_dotenv()

def get_secret(key, default=None):
    """Retrieve secret from st.secrets (Cloud) or os.getenv (Local)."""
    if key in st.secrets:
        return st.secrets[key]
    return os.getenv(key, default)

DB_USER     = get_secret("DB_USER")
DB_PASS     = get_secret("DB_PASS")
WALLET_PASS = get_secret("WALLET_PASS")
DB_DSN      = get_secret("DB_DSN")

PLOTLY_LAYOUT = dict(
    template="plotly_dark",
    plot_bgcolor="#0b1426",
    paper_bgcolor="#0b1426",
    font_color="#d0d8f0",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
)
IOC_ORANGE  = "#F58220"
IOC_BLUE    = "#4e91fc"
IOC_GREEN   = "#27c97e"
IOC_RED     = "#f45c5c"

# Month ordering for Indian FY (Apr = M1 … Mar = M12)
FY_MONTH_ORDER = ["2024-04","2024-05","2024-06","2024-07","2024-08","2024-09",
                  "2024-10","2024-11","2024-12","2025-01","2025-02","2025-03",
                  "2025-04","2025-05","2025-06","2025-07","2025-08","2025-09",
                  "2025-10","2025-11","2025-12","2026-01","2026-02","2026-03",
                  "2026-04","2026-05","2026-06","2026-07","2026-08","2026-09",
                  "2026-10","2026-11","2026-12","2027-01","2027-02","2027-03"]
MONTH_LABELS   = {
    "2024-04":"Apr'24","2024-05":"May'24","2024-06":"Jun'24","2024-07":"Jul'24",
    "2024-08":"Aug'24","2024-09":"Sep'24","2024-10":"Oct'24","2024-11":"Nov'24",
    "2024-12":"Dec'24","2025-01":"Jan'25","2025-02":"Feb'25","2025-03":"Mar'25",
    "2025-04":"Apr'25","2025-05":"May'25","2025-06":"Jun'25","2025-07":"Jul'25",
    "2025-08":"Aug'25","2025-09":"Sep'25","2025-10":"Oct'25","2025-11":"Nov'25",
    "2025-12":"Dec'25","2026-01":"Jan'26","2026-02":"Feb'26","2026-03":"Mar'26",
    "2026-04":"Apr'26","2026-05":"May'26","2026-06":"Jun'26","2026-07":"Jul'26",
    "2026-08":"Aug'26","2026-09":"Sep'26","2026-10":"Oct'26","2026-11":"Nov'26",
    "2026-12":"Dec'26","2027-01":"Jan'27","2027-02":"Feb'27","2027-03":"Mar'27",
}

def fy_month_key(m):
    try:    return FY_MONTH_ORDER.index(m)
    except: return 999

# ── Helpers ───────────────────────────────────────────────────────────────────
def load_ndne_materials():
    try:
        root_dir = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(root_dir, "ndne_mat_code.txt")
        with open(path, "r") as f:
            return [line.strip() for line in f if line.strip()]
    except Exception as e:
        print(f"Error loading material codes: {e}")
        return []

@st.cache_resource
def get_db_pool():
    if not all([DB_USER, DB_PASS, DB_DSN]):
        st.error("Missing database environment variables (DB_USER, DB_PASS, DB_DSN). Check your .env file or Streamlit Secrets.")
        return None
    
    try:
        root_dir = os.path.dirname(os.path.abspath(__file__))
        wallet_path = os.path.join(root_dir, "wallet")
        
        # ── Handle Secret-based Wallet (for Public Repos) ──
        wallet_b64 = get_secret("WALLET_ZIP_BASE64")
        if wallet_b64:
            import base64, zipfile, io, shutil
            wallet_path = os.path.join(root_dir, "temp_wallet")
            
            # 1. Extract if not already done
            if not os.path.exists(os.path.join(wallet_path, "tnsnames.ora")):
                if not os.path.exists(wallet_path):
                    os.makedirs(wallet_path)
                try:
                    z = zipfile.ZipFile(io.BytesIO(base64.b64decode(wallet_b64)))
                    z.extractall(wallet_path)
                except Exception as e:
                    st.error(f"Error extracting wallet secret: {e}")

            # 2. 💡 Fix: If files were extracted into a 'wallet/' subfolder, move them up
            sub_wallet = os.path.join(wallet_path, "wallet")
            if os.path.exists(sub_wallet) and os.path.isdir(sub_wallet):
                for f in os.listdir(sub_wallet):
                    src = os.path.join(sub_wallet, f)
                    dest = os.path.join(wallet_path, f)
                    if not os.path.exists(dest):
                        shutil.move(src, dest)
            
            # 3. Update sqlnet.ora to point to the correct directory
            sqlnet_path = os.path.join(wallet_path, "sqlnet.ora")
            if os.path.exists(sqlnet_path):
                with open(sqlnet_path, "r") as f:
                    content = f.read()
                if 'DIRECTORY="' in content:
                    content = content.replace('DIRECTORY="?/network/admin"', f'DIRECTORY="{wallet_path}"')
                    with open(sqlnet_path, "w") as f:
                        f.write(content)
            
            # Diagnostic: Verify tnsnames.ora exists
            if not os.path.exists(os.path.join(wallet_path, "tnsnames.ora")):
                available = []
                for root, dirs, files in os.walk(wallet_path):
                    for name in files: available.append(os.path.join(root, name))
                st.error(f"Wallet extraction failed. tnsnames.ora missing. Found: {available}")
        
        if os.path.exists(wallet_path):
            return oracledb.create_pool(
                user=DB_USER, 
                password=DB_PASS, 
                dsn=DB_DSN,
                min=1, max=5, increment=1,
                config_dir=wallet_path,
                wallet_location=wallet_path, 
                wallet_password=WALLET_PASS
            )
        else:
            st.sidebar.error("Wallet directory not found. If this is a public repo, ensure WALLET_ZIP_BASE64 is set in Streamlit Secrets.")
            
        return oracledb.create_pool(user=DB_USER, password=DB_PASS, dsn=DB_DSN, min=1, max=5, increment=1)
    except Exception as e:
        st.error(f"Database Pool Error: {e}")
        return None

def classify_and_weigh(material_name):
    import re
    name = str(material_name).upper()
    if 'NANOCUT'  in name: return '19kg Nanocut',    19.0
    if 'XTRA TEJ' in name: return '19kg Extra Tej',  19.0
    
    # Use regex for more robust weight extraction
    if re.search(r'47\.5\s*KG', name): return '47.5kg', 47.5
    if re.search(r'425\s*KG', name) or '425' in name: return '425kg Jumbo', 425.0
    if re.search(r'19\s*KG', name): return '19kg Plain', 19.0
    if re.search(r'5\s*KG', name): return '5kg FTL', 5.0
    
    # Fallback checks if "KG" is missing but number is present
    if '47.5' in name: return '47.5kg', 47.5
    if '19' in name: return '19kg Plain', 19.0
    if '5' in name: return '5kg FTL', 5.0
    
    return 'Other', 19.0

def aggrid_render(df, height=400, page_size=20, fit=True):
    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_pagination(paginationAutoPageSize=False, paginationPageSize=page_size)
    gb.configure_side_bar()
    gb.configure_default_column(filterable=True, sortable=True, resizable=True)
    AgGrid(df, gridOptions=gb.build(), fit_columns_on_grid_load=fit,
           theme='alpine', height=height, allow_unsafe_jscode=True)

def download_button(df, filename, label="📥 Download Report (CSV)"):
    csv = df.to_csv(index=False).encode('utf-8')
    st.download_button(
        label=label,
        data=csv,
        file_name=filename,
        mime='text/csv',
        key=filename
    )

def months_elapsed(max_date):
    """Indian FY starts Apr 1. Returns months elapsed from Apr."""
    if pd.isnull(max_date): return 1
    m = max_date.month
    return (m - 4) % 12 + 1   # Apr→1, May→2 … Mar→12

def safe_pct(num, den):
    return round((num / den * 100), 2) if den else 0.0

# ── Sidebar: Upload ───────────────────────────────────────────────────────────
with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/3208/3208154.png", width=50)
    st.title("Data Sync Console")
    
    with st.expander("🛠️ Admin: Database Setup"):
        st.markdown("**Upload Master Data**")
        file_master = st.file_uploader("Upload master.xlsx", type=["xlsx"])
        if file_master and st.button("Sync Master"):
            pool_m = get_db_pool()
            if pool_m:
                try:
                    with pool_m.acquire() as conn_m:
                        df_m = pd.read_excel(file_master)
                        cur_m = conn_m.cursor()
                        cur_m.execute("SELECT count(*) FROM user_tables WHERE table_name='NDNE_MASTER'")
                        if cur_m.fetchone()[0] == 0:
                            cur_m.execute("""CREATE TABLE NDNE_MASTER (
                                DIST_CODE VARCHAR2(50), DIST_NAME VARCHAR2(255),
                                DISTRICT VARCHAR2(255), LSA_NAME VARCHAR2(255))""")
                        cur_m.execute("TRUNCATE TABLE NDNE_MASTER")
                        batch_m = []
                        for _, row in df_m.iterrows():
                            batch_m.append((str(row.get('Customer', '')),
                                            str(row.get('Distributor_Name', '')),
                                            str(row.get('Sales_District', 'Unknown')),
                                            str(row.get('LSA', 'Unknown'))))
                        cur_m.executemany("INSERT INTO NDNE_MASTER VALUES (:1,:2,:3,:4)", batch_m)
                        conn_m.commit()
                    st.success("✅ Master Synced!")
                    st.cache_data.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"Master Sync Failed: {e}")

    st.divider()
    st.markdown("**Upload Daily Actuals**")
    st.info("💡 Uploading will replace all existing actuals data for the selected financial year.")
    file_act = st.file_uploader("Upload Actuals File", type=["xlsx"])
    if file_act and st.button("Sync Actuals"):
        pool_ = get_db_pool()
        if pool_:
            try:
                with pool_.acquire() as conn_:
                    # Read data once to avoid stream exhaustion and improve efficiency
                    df_all = pd.read_excel(file_act)
                    
                    ndne_codes = load_ndne_materials()
                    if not ndne_codes:
                        st.error("❌ Critical Error: 'ndne_mat_code.txt' not found or empty. Sync aborted to prevent data corruption.")
                        st.stop()
                    
                    # Compute MaterialCode once
                    df_all['MaterialCode'] = df_all['Material'].apply(
                        lambda x: str(x).split()[0] if isinstance(x, str) else '')
                    
                    # Split into NDNE and Domestic
                    df_up = df_all[df_all['MaterialCode'].isin(ndne_codes)].copy()
                    df_dom = df_all[~df_all['MaterialCode'].isin(ndne_codes)].copy()
                    
                    if df_up.empty and df_dom.empty:
                        st.warning("⚠️ The uploaded file appears to be empty or contains no recognizable data.")
                        st.stop()

                    cur = conn_.cursor()
                    cur.execute("SELECT count(*) FROM user_tables WHERE table_name='NDNE_ACTUALS'")
                    if cur.fetchone()[0] == 0:
                        cur.execute("""CREATE TABLE NDNE_ACTUALS (
                            DIST_CODE VARCHAR2(50), DIST_NAME VARCHAR2(255),
                            DISTRICT VARCHAR2(255), LSA_NAME VARCHAR2(255),
                            SEGMENT VARCHAR2(50), BILL_DATE DATE,
                            QTY_EA NUMBER, QTY_MT NUMBER,
                            MAT_CODE VARCHAR2(50))""")
                    
                    cur.execute("SELECT count(*) FROM user_tables WHERE table_name='DOMESTIC_ACTUALS'")
                    if cur.fetchone()[0] == 0:
                        cur.execute("""CREATE TABLE DOMESTIC_ACTUALS (
                            DIST_CODE VARCHAR2(50), BILL_DATE DATE, QTY_MT NUMBER)""")
                    
                    # Always truncate (Replace mode only)
                    cur.execute("TRUNCATE TABLE NDNE_ACTUALS")
                    cur.execute("TRUNCATE TABLE DOMESTIC_ACTUALS")
                    
                    # Prepare NDNE Batch
                    bar = st.progress(0)
                    batch_ndne = []
                    
                    # Robust column mapping
                    cols = df_all.columns.tolist()
                    qty_col = next((c for c in cols if c.lower() in ['inv. qty', 'quantity', 'billed qty', 'invoiced qty']), 'Inv. Qty')
                    date_col = next((c for c in cols if c.lower() in ['date', 'billing date', 'bill date']), 'Date')
                    dist_col = next((c for c in cols if c.lower() in ['sales district', 'district']), 'Sales district')
                    lsa_col = next((c for c in cols if c.lower() in ['sales group', 'lsa', 'lsa name']), 'Sales Group')

                    if not df_up.empty:
                        for idx, row in df_up.iterrows():
                            seg, ukg = classify_and_weigh(row['Material'])
                            sp = str(row['Ship-To Party']).split()
                            dc  = sp[0] if sp else ''
                            dn  = " ".join(sp[1:]) if len(sp)>1 else ''
                            dist = str(row.get(dist_col, 'Unknown'))
                            qty = float(row[qty_col]) if pd.notnull(row[qty_col]) else 0.0
                            
                            bill_date = pd.to_datetime(row[date_col], dayfirst=True)
                            mat_code = str(row['Material']).split()[0] if pd.notnull(row['Material']) else ''
                            
                            batch_ndne.append((dc, dn, dist, str(row.get(lsa_col, 'Unknown')), seg,
                                          bill_date, qty, qty * ukg / 1000.0, mat_code))
                            if idx % 500 == 0: bar.progress(min(idx/len(df_up)*0.4, 0.4))
                        cur.executemany("INSERT INTO NDNE_ACTUALS VALUES (:1,:2,:3,:4,:5,:6,:7,:8,:9)", batch_ndne)
                    
                    # Prepare Domestic Batch
                    if not df_dom.empty:
                        batch_dom = []
                        for idx, row in df_dom.iterrows():
                            sp = str(row['Ship-To Party']).split()
                            dc = sp[0] if sp else ''
                            qty = float(row[qty_col]) if pd.notnull(row[qty_col]) else 0.0
                            # Standard domestic weight 14.2kg
                            batch_dom.append((dc, pd.to_datetime(row[date_col], dayfirst=True), qty * 14.2 / 1000.0))
                        
                        if batch_dom:
                            cur.executemany("INSERT INTO DOMESTIC_ACTUALS VALUES (:1,:2,:3)", batch_dom)
                    
                    conn_.commit()
                st.success("✅ Synced! (NDNE & Domestic)")
                st.cache_data.clear()
                time.sleep(1)
                st.rerun()
            except Exception as e:
                st.error(f"Sync Failed: {e}")

    st.divider()
    st.markdown("**Dashboard Filters**")
    selected_fy = st.selectbox("Financial Year", ["2026-27", "2025-26", "2024-25"], index=0)
    
    fy_start_map = {
        "2024-25": "2024-04-01",
        "2025-26": "2025-04-01",
        "2026-27": "2026-04-01"
    }
    fy_end_map = {
        "2024-25": "2025-03-31",
        "2025-26": "2026-03-31",
        "2026-27": "2027-03-31"
    }
    
    start_date = fy_start_map[selected_fy]
    end_date = fy_end_map[selected_fy]

    st.divider()
    st.caption("🔄 Data refreshes every 10 min")
    if st.button("🗑️ Clear Cache"):
        st.cache_data.clear(); st.rerun()

# ── Main Header ───────────────────────────────────────────────────────────────
st.title("🛢️ IndianOil NDNE Advanced Analytics")
st.markdown(f"Enterprise Sales Tracking System — FY {selected_fy}")

conn_pool = get_db_pool()

if not conn_pool:
    st.warning("⚠️ Database connection not established. Check sidebar for details.")
    st.stop()
else:
    st.sidebar.success("📡 Database Connected")

# ── Data Load ─────────────────────────────────────────────────────────────────
@st.cache_data(ttl=600)
def load_data(s_date, e_date):
    try:
        with conn_pool.acquire() as conn:
            df_act = pd.read_sql("""
                SELECT DIST_CODE,
                       TO_CHAR(BILL_DATE,'YYYY-MM') AS MONTH,
                       BILL_DATE, QTY_EA, QTY_MT, SEGMENT, LSA_NAME, DISTRICT, MAT_CODE
                FROM NDNE_ACTUALS
                WHERE BILL_DATE >= TO_DATE(:1, 'YYYY-MM-DD')
                  AND BILL_DATE <= TO_DATE(:2, 'YYYY-MM-DD')""", 
                con=conn, params=[s_date, e_date])

            try:
                df_base = pd.read_sql("""
                    SELECT DIST_CODE, AVG_MONTHLY_QTY_MT, TOTAL_LY_QTY_EA, SEGMENT, TARGET_GROWTH_PCT
                    FROM NDNE_BASELINE""", con=conn)
            except:
                df_base = pd.DataFrame()

            try:
                df_master = pd.read_sql("""
                    SELECT DIST_CODE, DIST_NAME, DISTRICT, LSA_NAME
                    FROM NDNE_MASTER""", con=conn)
            except:
                df_master = pd.DataFrame()

            try:
                df_ly = pd.read_sql("""
                    SELECT DIST_CODE, MONTH_NUM, QTY_MT as LY_QTY_MT, 
                           QTY_EA as LY_QTY_EA, SEGMENT, MAT_CODE
                    FROM NDNE_LY_ACTUALS""", con=conn)
            except:
                df_ly = pd.DataFrame()

            try:
                df_dom = pd.read_sql("""
                    SELECT DIST_CODE, SUM(QTY_MT) as DOM_QTY_MT
                    FROM DOMESTIC_ACTUALS
                    WHERE BILL_DATE >= TO_DATE(:1, 'YYYY-MM-DD')
                      AND BILL_DATE <= TO_DATE(:2, 'YYYY-MM-DD')
                    GROUP BY DIST_CODE""", 
                con=conn, params=[s_date, e_date])
            except:
                df_dom = pd.DataFrame()

        if not df_ly.empty and not df_master.empty:
            df_ly = pd.merge(df_ly, df_master[['DIST_CODE', 'LSA_NAME', 'DISTRICT']].drop_duplicates(), on='DIST_CODE', how='left')
            
        if not df_act.empty and not df_master.empty:
            # Overwrite LSA_NAME and DISTRICT from master to ensure consistency
            df_act = df_act.drop(columns=['LSA_NAME', 'DISTRICT'])
            df_act = pd.merge(df_act, df_master[['DIST_CODE', 'LSA_NAME', 'DISTRICT']].drop_duplicates(), on='DIST_CODE', how='left')

        return df_act, df_base, df_master, df_ly, df_dom
    except Exception as e:
        st.error(f"❌ Database Query Error: {e}")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

df_act, df_base, df_master, df_ly, df_dom = load_data(start_date, end_date)


if df_master.empty:
    st.warning("🚨 No Master Data found! Upload master.xlsx via sidebar.")
    st.stop()
if df_act.empty:
    st.warning("No actuals data found. Please upload Daily Actuals.")
    st.stop()

df_act['BILL_DATE'] = pd.to_datetime(df_act['BILL_DATE'])
max_date      = df_act['BILL_DATE'].max()

# Calculate Fractional Months Elapsed for precise YTD Targets
def get_fractional_months(m_date):
    if pd.isnull(m_date): return 1.0
    full_m = (m_date.month - 4) % 12
    days_in_m = calendar.monthrange(m_date.year, m_date.month)[1]
    return full_m + (m_date.day / float(days_in_m))

months_passed = get_fractional_months(max_date)

# Extract elapsed month numbers (e.g., '04', '05') for LY comparison
elapsed_month_nums = df_act['BILL_DATE'].dt.strftime('%m').unique().tolist()
cur_month_num = max_date.strftime('%m')
days_in_cur_month = calendar.monthrange(max_date.year, max_date.month)[1]
ly_prorata_factor = max_date.day / float(days_in_cur_month)

# ── Build df_core (master + base + cumulative actuals) ────────────────────────
# Calculate LY Sales YTD from df_ly (matching current elapsed months)
ly_ytd = df_ly[df_ly['MONTH_NUM'].isin(elapsed_month_nums)].copy()

# Apply "Same Period" proration to the current month in ly_ytd ONLY
# This ensures other reports (like MoM) can still see the full month LY sales.
if not ly_ytd.empty:
    ly_ytd['LY_QTY_MT'] = ly_ytd.apply(
        lambda r: r['LY_QTY_MT'] * ly_prorata_factor if r['MONTH_NUM'] == cur_month_num else r['LY_QTY_MT'], axis=1)
    if 'LY_QTY_EA' in ly_ytd.columns:
        ly_ytd['LY_QTY_EA'] = ly_ytd.apply(
            lambda r: r['LY_QTY_EA'] * ly_prorata_factor if r['MONTH_NUM'] == cur_month_num else r['LY_QTY_EA'], axis=1)

dist_ly_ytd = ly_ytd.groupby('DIST_CODE')['LY_QTY_MT'].sum().reset_index().rename(columns={'LY_QTY_MT': 'CUMULATIVE_LY_SALES_MT'})

# Calculate Target YTD (LY Sales * (1 + Growth%))
# Note: Since Growth% can vary by segment, we'll join with df_base to get the percentage
ly_target_ytd = pd.merge(ly_ytd, df_base[['DIST_CODE', 'SEGMENT', 'TARGET_GROWTH_PCT']], on=['DIST_CODE', 'SEGMENT'], how='left').fillna(0)
ly_target_ytd['TARGET_MT'] = ly_target_ytd['LY_QTY_MT'] * (1 + ly_target_ytd['TARGET_GROWTH_PCT'] / 100.0)
dist_target_ytd = ly_target_ytd.groupby('DIST_CODE')['TARGET_MT'].sum().reset_index().rename(columns={'TARGET_MT': 'CUMULATIVE_TARGET_MT'})

dist_cum  = (df_act.groupby('DIST_CODE')['QTY_MT']
             .sum().reset_index().rename(columns={'QTY_MT':'CUMULATIVE_CY_MT'}))

df_core = pd.merge(df_master, dist_ly_ytd, on='DIST_CODE', how='left').fillna(0)
df_core = pd.merge(df_core, dist_target_ytd, on='DIST_CODE', how='left').fillna(0)
df_core = pd.merge(df_core, dist_cum,  on='DIST_CODE', how='left').fillna(0)

total_cy_mt  = df_core['CUMULATIVE_CY_MT'].sum()
total_ly_mt  = df_core['CUMULATIVE_LY_SALES_MT'].sum()
total_target_mt = df_core['CUMULATIVE_TARGET_MT'].sum()

overall_pct  = safe_pct(total_cy_mt, total_target_mt)
overall_delta = total_cy_mt - total_target_mt

# ── Monthly helper ────────────────────────────────────────────────────────────
def build_monthly_df(act_df, ly_df, base_df):
    """Build month-wise CY vs LY table with cumulative columns."""
    # CY Monthly
    m_cy = (act_df.groupby('MONTH')['QTY_MT'].sum().reset_index()
            .rename(columns={'QTY_MT':'CY (MT)'}))
    
    # LY Monthly (join with base to get growth target)
    ly_df['MONTH_YEAR'] = ly_df['MONTH_NUM'].apply(lambda x: f"2024-{x}" if int(x) >= 4 else f"2025-{x}") # Approx mapping for labels
    # Actually, we should map based on the CY months
    cy_months = sorted(act_df['MONTH'].unique(), key=fy_month_key)
    
    m_data = []
    for m in cy_months:
        m_num = m.split('-')[1]
        cy_val = m_cy[m_cy['MONTH'] == m]['CY (MT)'].sum()
        
        ly_m_data = ly_df[ly_df['MONTH_NUM'] == m_num].copy()
        
        # Apply Proration if it's the current month (Same Period Comparison)
        # We only do this for the summary table's LY/Target values
        if m_num == cur_month_num:
             ly_m_data['LY_QTY_MT'] = ly_m_data['LY_QTY_MT'] * ly_prorata_factor
             if 'LY_QTY_EA' in ly_m_data.columns:
                 ly_m_data['LY_QTY_EA'] = ly_m_data['LY_QTY_EA'] * ly_prorata_factor
        
        ly_val = ly_m_data['LY_QTY_MT'].sum()
        
        # Target = LY * (1 + Growth%)
        ly_target_m = pd.merge(ly_m_data, base_df[['DIST_CODE', 'SEGMENT', 'TARGET_GROWTH_PCT']], on=['DIST_CODE', 'SEGMENT'], how='left').fillna(0)
        target_val = (ly_target_m['LY_QTY_MT'] * (1 + ly_target_m['TARGET_GROWTH_PCT'] / 100.0)).sum()
        
        # Correct LY year label based on month number (Apr-Dec 2024, Jan-Mar 2025 for LY)
        ly_year = "2024" if int(m_num) >= 4 else "2025"
        m_label = MONTH_LABELS.get(m, m)
        ly_month_name = m_label.split("'")[0]
        
        m_data.append({
            'MONTH': m,
            'Month Label': m_label,
            'CY (MT)': cy_val,
            'LY Sales (MT)': ly_val,
            'LY Label': f"LY {ly_month_name} {ly_year}",
            'Target (MT)': target_val
        })
    
    m_df = pd.DataFrame(m_data)
    if m_df.empty: return m_df
    
    m_df['Growth (%)'] = m_df.apply(lambda r: safe_pct(r['CY (MT)'] - r['LY Sales (MT)'], r['LY Sales (MT)']), axis=1)
    m_df['CY Cumulative (MT)'] = m_df['CY (MT)'].cumsum()
    m_df['LY Cumulative Sales (MT)'] = m_df['LY Sales (MT)'].cumsum()
    m_df['Target Cumulative (MT)'] = m_df['Target (MT)'].cumsum()
    
    m_df['Ach % vs Target'] = m_df.apply(lambda r: safe_pct(r['CY Cumulative (MT)'], r['Target Cumulative (MT)']), axis=1)
    
    return m_df.round(2)

month_df = build_monthly_df(df_act, df_ly, df_base)

# ── KPI Row ───────────────────────────────────────────────────────────────────
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("YTD Sales (MT)",    f"{total_cy_mt:,.2f}")
c2.metric("YTD LY Sales (MT)", f"{total_ly_mt:,.2f}")
c3.metric("YTD Target (MT)",   f"{total_target_mt:,.2f}")
c4.metric("Achievement %",     f"{overall_pct:.2f}%",
          delta=f"{total_cy_mt - total_target_mt:+,.2f} vs target")
c5.metric("Active Months",     f"{months_passed}")
c6.metric("Days Tracked",      f"{df_act['BILL_DATE'].nunique()}")

st.divider()

# ═══════════════════════════════ TABS ════════════════════════════════════════
(tab_daily, tab_month, tab_cur_month, tab_cum, tab_ftl,
 tab_drill, tab_dist, tab_lsa, tab_product, tab_ratio, tab_targets) = st.tabs([
    "📆 Daily Velocity",
    "📅 Monthly Comparison",
    "📊 Monthly Performance",
    "📈 Cumulative Tracker",
    "📦 FTL Comparison",
    "🔍 Deep Drill Down",
    "🚚 Distributor Grid",
    "🏢 LSA Grid",
    "📦 Product YTD",
    "⚖️ NDNE/Dom Ratio",
    "🎯 Target Setting",
])

FTL_CODES = ['M00215', 'M00216', 'M00217', 'M00218']

# ─── TAB 1 : Daily Velocity ──────────────────────────────────────────────────
with tab_daily:
    st.markdown('<p class="section-header">Daily Sales Velocity — CY Actuals vs LY Daily Sales</p>',
                unsafe_allow_html=True)

    daily = df_act.groupby('BILL_DATE')['QTY_MT'].sum().reset_index()
    daily = daily.sort_values('BILL_DATE')
    daily['7-Day Rolling Avg'] = daily['QTY_MT'].rolling(7, min_periods=1).mean()
    
    # Use LY actuals for the current month as benchmark
    cur_m_num = max_date.strftime('%m')
    ly_cur_m_sales = df_ly[df_ly['MONTH_NUM'] == cur_m_num]['LY_QTY_MT'].sum()
    # Benchmark = Total LY sales for the month / Days in that month
    daily['LY Daily Sales (Ref)'] = ly_cur_m_sales / days_in_cur_month if days_in_cur_month > 0 else 0.0

    fig = go.Figure()
    fig.add_trace(go.Bar(x=daily['BILL_DATE'], y=daily['QTY_MT'],
                         name='CY Daily (MT)', marker_color=IOC_BLUE, opacity=0.7))
    fig.add_trace(go.Scatter(x=daily['BILL_DATE'], y=daily['7-Day Rolling Avg'],
                             name='7-Day Rolling Avg', line=dict(color=IOC_GREEN, width=2)))
    fig.add_trace(go.Scatter(x=daily['BILL_DATE'], y=daily['LY Daily Sales (Ref)'],
                             name='LY Daily Sales (Ref)', line=dict(color=IOC_ORANGE, width=2, dash='dash')))
    fig.update_layout(**PLOTLY_LAYOUT, title="Daily MT Dispatched", height=420)
    st.plotly_chart(fig, use_container_width=True)

    # mini table below chart
    with st.expander("📋 Daily Transactions Table"):
        daily_show = daily.copy()
        daily_show['BILL_DATE'] = daily_show['BILL_DATE'].dt.strftime('%d-%b-%Y')
        daily_show.columns = [c.replace('_',' ') for c in daily_show.columns]
        daily_show = daily_show.round(2)
        download_button(daily_show, "daily_velocity.csv")
        aggrid_render(daily_show.sort_values('BILL DATE', ascending=False), height=350)

# ─── TAB 2 : Monthly Comparison ─────────────────────────────────────────────
with tab_month:
    st.markdown('<p class="section-header">Distributor Month-over-Month Comparison</p>', unsafe_allow_html=True)

    if not df_ly.empty and not df_act.empty:
        cy_month = df_act.groupby(['DIST_CODE', 'MONTH'])['QTY_MT'].sum().reset_index()
        cy_month['MONTH_NUM'] = cy_month['MONTH'].str.split('-').str[1]
        
        ly_month = df_ly.groupby(['DIST_CODE', 'MONTH_NUM'])['LY_QTY_MT'].sum().reset_index()
        
        cy_months = sorted(cy_month['MONTH_NUM'].unique())
        pivot_df = df_master[['DIST_CODE', 'DIST_NAME', 'LSA_NAME']].copy()
        
        for m_num in cy_months:
            month_name = ""
            for full_m, short_m in MONTH_LABELS.items():
                if full_m.endswith(f"-{m_num}") and "26" in short_m:
                    month_name = short_m.replace("'26", "")
                    break
            if not month_name: month_name = m_num
            
            # Correct years: CY is 2026/27, LY is 2025/26
            cy_year = "2026" if int(m_num) >= 4 else "2027"
            ly_year = "2025" if int(m_num) >= 4 else "2026"
            
            cy_col = f"{month_name} {cy_year} Actuals"
            ly_col = f"{month_name} {ly_year} Actuals"
            gr_col = f"{month_name} Growth %"
            
            cy_m = cy_month[cy_month['MONTH_NUM'] == m_num][['DIST_CODE', 'QTY_MT']].rename(columns={'QTY_MT': cy_col})
            ly_m = ly_month[ly_month['MONTH_NUM'] == m_num][['DIST_CODE', 'LY_QTY_MT']].rename(columns={'LY_QTY_MT': ly_col})
            
            pivot_df = pd.merge(pivot_df, cy_m, on='DIST_CODE', how='left').fillna(0)
            pivot_df = pd.merge(pivot_df, ly_m, on='DIST_CODE', how='left').fillna(0)
            pivot_df[gr_col] = pivot_df.apply(lambda r: safe_pct(r[cy_col] - r[ly_col], r[ly_col]), axis=1)

        dist_pivot = pivot_df.drop(columns=['DIST_CODE']).round(2)
        download_button(dist_pivot, "distributor_mom_comparison.csv")
        aggrid_render(dist_pivot, height=500, fit=False)

        st.markdown('<p class="section-header">LSA Month-over-Month Comparison</p>', unsafe_allow_html=True)
        lsa_cols = [c for c in pivot_df.columns if 'Actuals' in c]
        lsa_pivot = pivot_df.groupby('LSA_NAME')[lsa_cols].sum().reset_index()
        
        for m_num in cy_months:
            month_name = [short_m.replace("'26", "") for full_m, short_m in MONTH_LABELS.items() if full_m.endswith(f"-{m_num}") and "26" in short_m]
            month_name = month_name[0] if month_name else m_num
            
            cy_year = "2026" if int(m_num) >= 4 else "2027"
            ly_year = "2025" if int(m_num) >= 4 else "2026"
            
            cy_col = f"{month_name} {cy_year} Actuals"
            ly_col = f"{month_name} {ly_year} Actuals"
            gr_col = f"{month_name} Growth %"
            lsa_pivot[gr_col] = lsa_pivot.apply(lambda r: safe_pct(r[cy_col] - r[ly_col], r[ly_col]), axis=1)
        
        total_lsa = pd.DataFrame([{'LSA_NAME': 'GRAND TOTAL'}])
        for c in lsa_cols: total_lsa[c] = lsa_pivot[c].sum()
        for m_num in cy_months:
            month_name = [short_m.replace("'26", "") for full_m, short_m in MONTH_LABELS.items() if full_m.endswith(f"-{m_num}") and "26" in short_m]
            month_name = month_name[0] if month_name else m_num
            
            cy_year = "2026" if int(m_num) >= 4 else "2027"
            ly_year = "2025" if int(m_num) >= 4 else "2026"
            
            cy_col = f"{month_name} {cy_year} Actuals"
            ly_col = f"{month_name} {ly_year} Actuals"
            
            total_lsa[f"{month_name} Growth %"] = safe_pct(total_lsa[cy_col].iloc[0] - total_lsa[ly_col].iloc[0], total_lsa[ly_col].iloc[0])
            
        lsa_pivot = pd.concat([lsa_pivot, total_lsa], ignore_index=True)
        lsa_pivot['IS_TOTAL'] = lsa_pivot['LSA_NAME'] == 'GRAND TOTAL'
        lsa_pivot = lsa_pivot.sort_values(by=['IS_TOTAL', 'LSA_NAME']).drop(columns=['IS_TOTAL']).round(2)
        
        col_order = ['LSA_NAME']
        for m_num in cy_months:
            month_name = [short_m.replace("'26", "") for full_m, short_m in MONTH_LABELS.items() if full_m.endswith(f"-{m_num}") and "26" in short_m]
            month_name = month_name[0] if month_name else m_num
            
            cy_year = "2026" if int(m_num) >= 4 else "2027"
            ly_year = "2025" if int(m_num) >= 4 else "2026"
            
            col_order.extend([f"{month_name} {cy_year} Actuals", f"{month_name} {ly_year} Actuals", f"{month_name} Growth %"])
        lsa_pivot = lsa_pivot[col_order]
        
        download_button(lsa_pivot, "lsa_mom_comparison.csv")
        aggrid_render(lsa_pivot, height=400, fit=True)
    else:
        st.warning("Historical data missing. Please upload historical data.")

# ─── TAB 3 : Current Month Performance ──────────────────────────────────────
with tab_cur_month:
    st.markdown('<p class="section-header">Current Month Performance — CY vs LY Actuals</p>', unsafe_allow_html=True)
    
    if not df_act.empty and not df_ly.empty:
        # Get current month from max date in actuals
        cur_month_str = max_date.strftime('%Y-%m')
        cur_month_num = max_date.strftime('%m')
        month_label = MONTH_LABELS.get(cur_month_str, cur_month_str)
        
        # Calculate days for pro-rata
        import calendar
        last_day_in_month = calendar.monthrange(max_date.year, max_date.month)[1]
        days_passed_in_month = max_date.day
        prorata_factor = days_passed_in_month / float(last_day_in_month)
        
        st.subheader(f"Performance for {month_label} (till day {days_passed_in_month} of {last_day_in_month})")
        
        # CY for current month
        cy_cur = df_act[df_act['MONTH'] == cur_month_str].groupby('DIST_CODE')['QTY_MT'].sum().reset_index()
        cy_cur.columns = ['DIST_CODE', 'CY_MT']
        
        # LY for same month (Actuals - applying local proration)
        ly_cur = df_ly[df_ly['MONTH_NUM'] == cur_month_num].copy()
        ly_cur['LY_QTY_MT'] = ly_cur['LY_QTY_MT'] * ly_prorata_factor
        
        dist_ly_cur = ly_cur.groupby('DIST_CODE')['LY_QTY_MT'].sum().reset_index().rename(columns={'LY_QTY_MT': 'LY_MT'})
        
        # Target for same month (LY * (1 + Growth%))
        # We calculate pro-rata target using the prorated LY values
        ly_t_cur = pd.merge(ly_cur, df_base[['DIST_CODE', 'SEGMENT', 'TARGET_GROWTH_PCT']], on=['DIST_CODE', 'SEGMENT'], how='left').fillna(0)
        ly_t_cur['TARGET_MT'] = ly_t_cur['LY_QTY_MT'] * (1 + ly_t_cur['TARGET_GROWTH_PCT'] / 100.0)
        dist_target_cur = ly_t_cur.groupby('DIST_CODE')['TARGET_MT'].sum().reset_index().rename(columns={'TARGET_MT': 'Pro-rata Target (MT)'})
        
        # Also get Full Month Target for reference
        ly_full = df_ly[df_ly['MONTH_NUM'] == cur_month_num].copy()
        ly_t_full = pd.merge(ly_full, df_base[['DIST_CODE', 'SEGMENT', 'TARGET_GROWTH_PCT']], on=['DIST_CODE', 'SEGMENT'], how='left').fillna(0)
        ly_t_full['FULL_TARGET_MT'] = ly_t_full['LY_QTY_MT'] * (1 + ly_t_full['TARGET_GROWTH_PCT'] / 100.0)
        dist_target_full = ly_t_full.groupby('DIST_CODE')['FULL_TARGET_MT'].sum().reset_index()

        # Merge with master
        cur_perf = pd.merge(df_master[['DIST_CODE', 'DIST_NAME', 'LSA_NAME']], cy_cur, on='DIST_CODE', how='left').fillna(0)
        cur_perf = pd.merge(cur_perf, dist_ly_cur, on='DIST_CODE', how='left').fillna(0)
        cur_perf = pd.merge(cur_perf, dist_target_cur, on='DIST_CODE', how='left').fillna(0)
        cur_perf = pd.merge(cur_perf, dist_target_full, on='DIST_CODE', how='left').fillna(0)
        
        cur_perf['Prorata Ach %'] = cur_perf.apply(lambda r: safe_pct(r['CY_MT'], r['Pro-rata Target (MT)']), axis=1)
        cur_perf['Growth % vs LY'] = cur_perf.apply(lambda r: safe_pct(r['CY_MT'] - r['LY_MT'], r['LY_MT']), axis=1)
        cur_perf['Gap vs Prorata Target (MT)'] = (cur_perf['CY_MT'] - cur_perf['Pro-rata Target (MT)']).round(2)
        
        cur_perf = cur_perf.rename(columns={
            'DIST_NAME': 'Distributor',
            'LSA_NAME': 'LSA',
            'CY_MT': f"CY {month_label} (MT)",
            'LY_MT': f"LY {month_label} (Same Period)",
            'FULL_TARGET_MT': f"Full {month_label} Target (MT)"
        }).sort_values(f"CY {month_label} (MT)", ascending=False)
        
        # Totals
        t_cy = cur_perf[f"CY {month_label} (MT)"].sum()
        t_ly = cur_perf[f"LY {month_label} (Same Period)"].sum()
        t_prorata = cur_perf['Pro-rata Target (MT)'].sum()
        t_full_target = cur_perf[f"Full {month_label} Target (MT)"].sum()
        
        col1, col2, col3, col4 = st.columns(4)
        col1.metric(f"Total CY {month_label}", f"{t_cy:,.2f} MT")
        col2.metric(f"LY {month_label} (Same Period)", f"{t_ly:,.2f} MT")
        col3.metric("Full Month Target", f"{t_full_target:,.2f} MT")
        col4.metric("Prorata Ach %", f"{safe_pct(t_cy, t_prorata):.2f}%", delta=f"{t_cy - t_prorata:+,.2f} MT vs Prorata")

        
        cur_perf_show = cur_perf.drop(columns=['DIST_CODE']).round(2)
        download_button(cur_perf_show, f"monthly_performance_{month_label}.csv")
        aggrid_render(cur_perf_show, height=500)
    else:
        st.info("Missing current year actuals or last year data for comparison.")

# ─── TAB 4 : Cumulative Tracker ─────────────────────────────────────────────
with tab_cum:
    st.markdown('<p class="section-header">Cumulative YTD Tracker — CY vs LY Sales vs Target</p>',
                unsafe_allow_html=True)

    st.markdown("**Cumulative Monthly Summary Table**")
    cum_tbl = month_df[['Month Label','CY (MT)','LY Sales (MT)','Target (MT)','Growth (%)',
                         'CY Cumulative (MT)','LY Cumulative Sales (MT)','Target Cumulative (MT)','Ach % vs Target']].copy().round(2)
    download_button(cum_tbl, "cumulative_ytd_tracker.csv")
    aggrid_render(cum_tbl, height=340, fit=True)

    st.divider()
    st.subheader("Distributor-wise ND Sales Performance (YTD)")
    
    if df_act.empty and df_ly.empty:
        st.info("Upload data to see detailed distributor comparison.")
    else:
        # CY YTD by Distributor
        cy_dist = df_act.groupby('DIST_CODE')['QTY_MT'].sum().reset_index().rename(columns={'QTY_MT': 'CY_YTD_MT'})
        # LY YTD by Distributor (using filtered/prorated ly_ytd)
        ly_dist = ly_ytd.groupby('DIST_CODE')['LY_QTY_MT'].sum().reset_index().rename(columns={'LY_QTY_MT': 'LY_YTD_MT'})
        
        # Merge Master + CY + LY
        dist_ytd = pd.merge(df_master[['DIST_CODE', 'DIST_NAME', 'LSA_NAME']], cy_dist, on='DIST_CODE', how='left').fillna(0)
        dist_ytd = pd.merge(dist_ytd, ly_dist, on='DIST_CODE', how='left').fillna(0)
        
        # Growth %
        dist_ytd['Growth %'] = dist_ytd.apply(lambda r: safe_pct(r['CY_YTD_MT'] - r['LY_YTD_MT'], r['LY_YTD_MT']), axis=1)
        
        dist_ytd_show = dist_ytd.rename(columns={
            'DIST_CODE': 'SAP Code',
            'DIST_NAME': 'Distributor Name',
            'LSA_NAME': 'LSA Name',
            'LY_YTD_MT': 'LY ND Sales (YTD)',
            'CY_YTD_MT': 'CY ND Sales (YTD)'
        }).round(2)

        download_button(dist_ytd_show, "distributor_nd_ytd_performance.csv")
        aggrid_render(dist_ytd_show, height=600)

# ─── TAB 5 : FTL Comparison (5kg) ───────────────────────────────────────────
with tab_ftl:
    st.markdown('<p class="section-header">📦 FTL Cylinder Comparison (5kg) — CY vs LY Performance</p>', unsafe_allow_html=True)
    
    if df_act.empty or df_ly.empty:
        st.info("Actuals or Last Year data missing. Please upload data to view FTL comparison.")
    else:
        # Filter for FTL codes
        ftl_cy = df_act[df_act['MAT_CODE'].isin(FTL_CODES)].copy()
        ftl_ly = df_ly[df_ly['MAT_CODE'].isin(FTL_CODES)].copy()
        
        if ftl_cy.empty and ftl_ly.empty:
            st.warning("No FTL cylinder data (M00215-M00218) found in the database.")
        else:
            # Current Month Info
            cur_month_str = max_date.strftime('%Y-%m')
            cur_month_num = max_date.strftime('%m')
            month_label = MONTH_LABELS.get(cur_month_str, cur_month_str)
            
            # CY Current Month
            cy_ftl_cur = ftl_cy[ftl_cy['MONTH'] == cur_month_str].groupby('DIST_CODE')['QTY_EA'].sum().reset_index()
            cy_ftl_cur.columns = ['DIST_CODE', 'CY_EA_CUR']
            
            # LY Current Month
            ly_ftl_cur = ftl_ly[ftl_ly['MONTH_NUM'] == cur_month_num].groupby('DIST_CODE')['LY_QTY_EA'].sum().reset_index()
            ly_ftl_cur.columns = ['DIST_CODE', 'LY_EA_CUR']
            
            # CY YTD
            cy_ftl_ytd = ftl_cy.groupby('DIST_CODE')['QTY_EA'].sum().reset_index()
            cy_ftl_ytd.columns = ['DIST_CODE', 'CY_EA_YTD']
            
            # LY YTD (up to current month num)
            ly_ftl_ytd = ftl_ly[ftl_ly['MONTH_NUM'] <= cur_month_num].groupby('DIST_CODE')['LY_QTY_EA'].sum().reset_index()
            ly_ftl_ytd.columns = ['DIST_CODE', 'LY_EA_YTD']
            
            # Target (from baseline - Total LY / 12)
            ftl_target = df_base[df_base['SEGMENT'] == '5kg FTL'].groupby('DIST_CODE')['TOTAL_LY_QTY_EA'].sum().reset_index()
            ftl_target['PRORATA_TARGET_EA'] = (ftl_target['TOTAL_LY_QTY_EA'] / 12.0).round(0)
            
            # Merge all
            ftl_perf = pd.merge(df_master[['DIST_CODE', 'DIST_NAME', 'LSA_NAME']], cy_ftl_cur, on='DIST_CODE', how='left')
            ftl_perf = pd.merge(ftl_perf, ly_ftl_cur, on='DIST_CODE', how='left')
            ftl_perf = pd.merge(ftl_perf, cy_ftl_ytd, on='DIST_CODE', how='left')
            ftl_perf = pd.merge(ftl_perf, ly_ftl_ytd, on='DIST_CODE', how='left')
            ftl_perf = pd.merge(ftl_perf, ftl_target[['DIST_CODE', 'PRORATA_TARGET_EA']], on='DIST_CODE', how='left')
            ftl_perf = ftl_perf.fillna(0)
            
            # Calculations
            ftl_perf['Monthly Upliftment'] = ftl_perf['CY_EA_CUR'] - ftl_perf['LY_EA_CUR']
            ftl_perf['Cumulative Upliftment'] = ftl_perf['CY_EA_YTD'] - ftl_perf['LY_EA_YTD']
            ftl_perf['Monthly Growth %'] = ftl_perf.apply(lambda r: safe_pct(r['Monthly Upliftment'], r['LY_EA_CUR']), axis=1)
            
            # Summary metrics
            t_cy_ea = ftl_perf['CY_EA_CUR'].sum()
            t_ly_ea = ftl_perf['LY_EA_CUR'].sum()
            t_ytd_uplift = ftl_perf['Cumulative Upliftment'].sum()
            
            fk1, fk2, fk3, fk4 = st.columns(4)
            fk1.metric(f"CY {month_label} (EA)", f"{t_cy_ea:,.0f}")
            fk2.metric(f"LY {month_label} (EA)", f"{t_ly_ea:,.0f}")
            fk3.metric("Monthly Growth", f"{t_cy_ea - t_ly_ea:+,.0f}", delta=f"{safe_pct(t_cy_ea - t_ly_ea, t_ly_ea):.2f}%")
            fk4.metric("YTD Upliftment", f"{t_ytd_uplift:+,.0f} EA")
            
            st.divider()
            
            # LSA Grid
            st.subheader("🏢 LSA-wise FTL Performance")
            lsa_ftl = ftl_perf.groupby('LSA_NAME').agg({
                'CY_EA_CUR': 'sum',
                'LY_EA_CUR': 'sum',
                'PRORATA_TARGET_EA': 'sum',
                'CY_EA_YTD': 'sum',
                'LY_EA_YTD': 'sum'
            }).reset_index()
            lsa_ftl['Monthly Uplift'] = lsa_ftl['CY_EA_CUR'] - lsa_ftl['LY_EA_CUR']
            lsa_ftl['Cum. Uplift'] = lsa_ftl['CY_EA_YTD'] - lsa_ftl['LY_EA_YTD']
            lsa_ftl = lsa_ftl.rename(columns={
                'LSA_NAME': 'LSA',
                'CY_EA_CUR': f'CY {month_label}',
                'LY_EA_CUR': f'LY {month_label}',
                'PRORATA_TARGET_EA': 'Prorata Target (EA)'
            }).sort_values(f'CY {month_label}', ascending=False)
            
            aggrid_render(lsa_ftl.round(0), height=300)
            
            st.divider()
            
            # Distributor Grid
            st.subheader("🚚 Distributor-wise FTL Breakdown")
            dist_ftl_show = ftl_perf.drop(columns=['DIST_CODE']).rename(columns={
                'DIST_NAME': 'Distributor',
                'LSA_NAME': 'LSA',
                'CY_EA_CUR': f'CY {month_label}',
                'LY_EA_CUR': f'LY {month_label}',
                'CY_EA_YTD': 'CY YTD (EA)',
                'LY_EA_YTD': 'LY YTD (EA)',
                'PRORATA_TARGET_EA': 'Prorata Target (EA)'
            }).sort_values(f'CY {month_label}', ascending=False)
            
            download_button(dist_ftl_show, f"ftl_comparison_{month_label}.csv")
            aggrid_render(dist_ftl_show, height=500)

# ─── TAB 6 : Deep Drill Down ────────────────────────────────────────────────
with tab_drill:
    st.markdown('<p class="section-header">Deep Drill Down — Hierarchical Sales Analysis</p>',
                unsafe_allow_html=True)

    # ── Filter row ────────────────────────────────────────────────────────────
    fc1, fc2, fc3, fc4 = st.columns([2, 2, 2, 2])

    all_lsa  = sorted(df_act['LSA_NAME'].dropna().unique())
    sel_lsa  = fc1.selectbox("🏢 LSA", ["ALL"] + list(all_lsa))

    act_f1 = df_act if sel_lsa == "ALL" else df_act[df_act['LSA_NAME'] == sel_lsa]
    all_dist = sorted(act_f1['DISTRICT'].dropna().unique())
    sel_dist = fc2.selectbox("📍 District", ["ALL"] + list(all_dist))

    act_f2 = act_f1 if sel_dist == "ALL" else act_f1[act_f1['DISTRICT'] == sel_dist]
    all_dc   = sorted(act_f2['DIST_CODE'].dropna().unique())

    # Get distributor names for display
    dc_name_map = (df_master.set_index('DIST_CODE')['DIST_NAME'].to_dict()
                   if not df_master.empty else {})
    dc_options  = ["ALL"] + [f"{d} – {dc_name_map.get(d, d)}" for d in all_dc]
    sel_dc_raw  = fc3.selectbox("🚚 Distributor", dc_options)
    sel_dc      = None if sel_dc_raw == "ALL" else sel_dc_raw.split(" – ")[0]

    act_f3  = act_f2 if sel_dc is None else act_f2[act_f2['DIST_CODE'] == sel_dc]
    all_seg = sorted(act_f3['SEGMENT'].dropna().unique())
    sel_seg = fc4.selectbox("📦 Segment", ["ALL"] + list(all_seg))

    act_filtered = act_f3 if sel_seg == "ALL" else act_f3[act_f3['SEGMENT'] == sel_seg]

    # Filter LY data with same logic
    ly_f1 = df_ly if sel_lsa == "ALL" else df_ly[df_ly['LSA_NAME'] == sel_lsa]
    ly_f2 = ly_f1 if sel_dist == "ALL" else ly_f1[ly_f1['DISTRICT'] == sel_dist]
    ly_f3 = ly_f2 if sel_dc is None else ly_f2[ly_f2['DIST_CODE'] == sel_dc]
    ly_filtered = ly_f3 if sel_seg == "ALL" else ly_f3[ly_f3['SEGMENT'] == sel_seg]

    # ── Compute LY monthly benchmark for the filtered scope ───────────────────
    base_filt = df_base.copy() if not df_base.empty else pd.DataFrame()
    if not base_filt.empty:
        if sel_dc:
            base_filt = base_filt[base_filt['DIST_CODE'] == sel_dc]
        if sel_seg != "ALL":
            base_filt = base_filt[base_filt['SEGMENT'] == sel_seg]
        ly_filt_monthly = base_filt['AVG_MONTHLY_QTY_MT'].sum()
    else:
        ly_filt_monthly = 0.0

    # ── KPIs ──────────────────────────────────────────────────────────────────
    cy_filt_total  = act_filtered['QTY_MT'].sum()
    # LY Sales YTD for filtered scope
    ly_filt_sales  = ly_filtered[ly_filtered['MONTH_NUM'].isin(elapsed_month_nums)]['LY_QTY_MT'].sum()
    
    # Target for filtered scope
    ly_t_filt = pd.merge(ly_filtered[ly_filtered['MONTH_NUM'].isin(elapsed_month_nums)], 
                         df_base[['DIST_CODE', 'SEGMENT', 'TARGET_GROWTH_PCT']], 
                         on=['DIST_CODE', 'SEGMENT'], how='left').fillna(0)
    ly_t_filt['TARGET_MT'] = ly_t_filt['LY_QTY_MT'] * (1 + ly_t_filt['TARGET_GROWTH_PCT'] / 100.0)
    ly_filt_target = ly_t_filt['TARGET_MT'].sum()

    pct_filt       = safe_pct(cy_filt_total, ly_filt_target)
    gap_filt       = cy_filt_total - ly_filt_target

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Filtered YTD (MT)",    f"{cy_filt_total:,.2f}")
    k2.metric("LY Sales YTD (MT)",   f"{ly_filt_sales:,.2f}")
    k3.metric("Target YTD (MT)",     f"{ly_filt_target:,.2f}")
    k4.metric("Achievement %",        f"{pct_filt:.2f}%",
              delta=f"{gap_filt:+,.2f} MT vs target")

    st.divider()

    # ── Charts ────────────────────────────────────────────────────────────────
    if act_filtered.empty:
        st.info("No sales data for this selection.")
    else:
        m_filt = build_monthly_df(act_filtered, ly_filtered, df_base)

        ch1, ch2 = st.columns(2)

        with ch1:
            fig_m = go.Figure()
            fig_m.add_trace(go.Bar(x=m_filt['Month Label'], y=m_filt['CY (MT)'],
                                   name='CY Actual', marker_color=IOC_BLUE))
            if not m_filt.empty:
                fig_m.add_trace(go.Bar(x=m_filt['Month Label'], y=m_filt['LY Sales (MT)'],
                                       name='LY Sales (2025)', marker_color='gray', opacity=0.5))
                fig_m.add_trace(go.Bar(x=m_filt['Month Label'], y=m_filt['Target (MT)'],
                                       name='Target', marker_color=IOC_ORANGE, opacity=0.6))
            fig_m.update_layout(**PLOTLY_LAYOUT, barmode='group',
                                title="Monthly Comparison", height=350,
                                yaxis=dict(title="MT"))
            st.plotly_chart(fig_m, use_container_width=True)

        with ch2:
            fig_c = go.Figure()
            fig_c.add_trace(go.Scatter(x=m_filt['Month Label'], y=m_filt['CY Cumulative (MT)'],
                                       name='CY Cumulative', fill='tozeroy',
                                       line=dict(color=IOC_BLUE, width=2),
                                       fillcolor='rgba(78,145,252,0.15)'))
            if not m_filt.empty:
                fig_c.add_trace(go.Scatter(x=m_filt['Month Label'], y=m_filt['LY Cumulative Sales (MT)'],
                                           name='LY Sales Cumulative',
                                           line=dict(color='gray', width=2, dash='dash')))
                fig_c.add_trace(go.Scatter(x=m_filt['Month Label'], y=m_filt['Target Cumulative (MT)'],
                                           name='Target Cumulative', fill='tozeroy',
                                           line=dict(color=IOC_ORANGE, width=2, dash='dot'),
                                           fillcolor='rgba(245,130,32,0.08)'))
            fig_c.update_layout(**PLOTLY_LAYOUT, title="Cumulative YTD", height=350,
                                yaxis=dict(title="MT"))
            st.plotly_chart(fig_c, use_container_width=True)

        # ── Detailed monthly table ────────────────────────────────────────────
        st.markdown("**Month-wise Breakdown (filtered scope)**")
        m_filt_show = m_filt[['Month Label','CY (MT)','LY Sales (MT)', 'Target (MT)',
                               'Growth (%)','CY Cumulative (MT)',
                               'LY Cumulative Sales (MT)', 'Target Cumulative (MT)', 'Ach % vs Target']].round(2)
        download_button(m_filt_show, "drill_down_monthly.csv")
        aggrid_render(m_filt_show, height=300, fit=True)

        # ── Segment breakdown (if not already at segment level) ───────────────
        if sel_seg == "ALL" and not act_filtered.empty:
            st.divider()
            st.markdown("**Segment-wise YTD Breakdown (within filtered scope)**")
            seg_agg = act_filtered.groupby('SEGMENT')['QTY_MT'].sum().reset_index()
            seg_agg.columns = ['Segment','CY YTD (MT)']
            seg_agg['% of Total'] = (seg_agg['CY YTD (MT)'] / seg_agg['CY YTD (MT)'].sum() * 100).round(1)
            seg_agg = seg_agg.sort_values('CY YTD (MT)', ascending=False).round(2)

            figs, figsb = st.columns([1, 1])
            with figs:
                pie = px.pie(seg_agg, values='CY YTD (MT)', names='Segment',
                             title='Segment Mix (CY)', color_discrete_sequence=px.colors.qualitative.Bold)
                pie.update_layout(**PLOTLY_LAYOUT, height=320)
                st.plotly_chart(pie, use_container_width=True)
            with figsb:
                download_button(seg_agg, "drill_down_segments.csv")
                aggrid_render(seg_agg, height=280, fit=True)

        # ── If single distributor → show individual transactions ──────────────
        if sel_dc:
            st.divider()
            st.markdown("**📋 Individual Transaction Log**")
            txn = act_filtered.copy()
            txn['BILL_DATE'] = txn['BILL_DATE'].dt.strftime('%d-%b-%Y')
            txn = txn[['BILL_DATE','DIST_CODE','LSA_NAME','DISTRICT',
                        'SEGMENT','QTY_EA','QTY_MT']].copy()
            txn.columns = ['Bill Date','Dist Code','LSA','District',
                           'Segment','Qty (EA)','Qty (MT)']
            txn['Qty (MT)'] = txn['Qty (MT)'].round(2)
            download_button(txn, f"transactions_{sel_dc}.csv")
            aggrid_render(txn.sort_values('Bill Date', ascending=False),
                          height=400, page_size=25, fit=False)

# ─── TAB 5 : Distributor Grid ────────────────────────────────────────────────
with tab_dist:
    st.markdown('<p class="section-header">Distributor YTD Performance Grid</p>',
                unsafe_allow_html=True)

    # extra filter
    lsa_opts = ["ALL"] + sorted(df_master['LSA_NAME'].dropna().unique())
    sel_lsa_d = st.selectbox("Filter by LSA", lsa_opts, key="dist_lsa")

    dist_final = df_core.copy()
    if sel_lsa_d != "ALL":
        dist_final = dist_final[dist_final['LSA_NAME'] == sel_lsa_d]

    dist_final = dist_final[[
        'DIST_CODE','DIST_NAME','LSA_NAME','DISTRICT',
        'CUMULATIVE_LY_SALES_MT','CUMULATIVE_TARGET_MT','CUMULATIVE_CY_MT']].copy()
    dist_final['Achievement (%)'] = dist_final.apply(
        lambda r: safe_pct(r['CUMULATIVE_CY_MT'], r['CUMULATIVE_TARGET_MT']), axis=1)
    dist_final['Gap vs Target (MT)'] = (dist_final['CUMULATIVE_CY_MT'] - dist_final['CUMULATIVE_TARGET_MT']).round(2)
    dist_final = dist_final.rename(columns={
        'DIST_CODE':'Code','DIST_NAME':'Distributor','LSA_NAME':'LSA',
        'DISTRICT':'District',
        'CUMULATIVE_LY_SALES_MT':'LY Sales (MT)',
        'CUMULATIVE_TARGET_MT':'Target (MT)',
        'CUMULATIVE_CY_MT':'Actual (MT)'})
    dist_final = dist_final.round(2).sort_values('Actual (MT)', ascending=False)

    # small KPI
    below75 = (dist_final['Achievement (%)'] < 75).sum()
    above100 = (dist_final['Achievement (%)'] >= 100).sum()
    tot_d = len(dist_final)
    d1, d2, d3 = st.columns(3)
    d1.metric("Total Distributors", tot_d)
    d2.metric("≥100% Achievers", above100, delta=f"{safe_pct(above100,tot_d):.2f}% of total")
    d3.metric("Below 75% ⚠️", below75, delta=f"-{safe_pct(below75,tot_d):.2f}%", delta_color="inverse")

    download_button(dist_final, "distributor_performance_grid.csv")
    aggrid_render(dist_final, height=600, page_size=25, fit=False)

# ─── TAB 6 : LSA Grid ────────────────────────────────────────────────────────
with tab_lsa:
    st.markdown('<p class="section-header">LSA Performance Summary</p>',
                unsafe_allow_html=True)

    # Period Selection for Chart
    month_options = ["Cumulative YTD"] + sorted(df_act['MONTH'].unique().tolist(), key=fy_month_key)
    selected_period = st.selectbox("Select Period for Chart", month_options)

    if selected_period == "Cumulative YTD":
        lsa_cy = df_act.groupby('LSA_NAME')['QTY_MT'].sum().reset_index().rename(columns={'QTY_MT': 'Actual (MT)'})
        
        # Use the already filtered and prorated ly_ytd
        lsa_ly = ly_ytd.groupby('LSA_NAME')['LY_QTY_MT'].sum().reset_index().rename(columns={'LY_QTY_MT': 'LY Sales (MT)'})
        
        ly_t = pd.merge(ly_ytd, df_base[['DIST_CODE', 'SEGMENT', 'TARGET_GROWTH_PCT']], on=['DIST_CODE', 'SEGMENT'], how='left').fillna(0)
        ly_t['TARGET_MT'] = ly_t['LY_QTY_MT'] * (1 + ly_t['TARGET_GROWTH_PCT'] / 100.0)
        lsa_target = ly_t.groupby('LSA_NAME')['TARGET_MT'].sum().reset_index().rename(columns={'TARGET_MT': 'Target (MT)'})
    else:
        m_num = selected_period.split('-')[1]
        lsa_cy = df_act[df_act['MONTH'] == selected_period].groupby('LSA_NAME')['QTY_MT'].sum().reset_index().rename(columns={'QTY_MT': 'Actual (MT)'})
        
        ly_f = df_ly[df_ly['MONTH_NUM'] == m_num].copy()
        lsa_ly = ly_f.groupby('LSA_NAME')['LY_QTY_MT'].sum().reset_index().rename(columns={'LY_QTY_MT': 'LY Sales (MT)'})
        
        ly_t = pd.merge(ly_f, df_base[['DIST_CODE', 'SEGMENT', 'TARGET_GROWTH_PCT']], on=['DIST_CODE', 'SEGMENT'], how='left').fillna(0)
        ly_t['TARGET_MT'] = ly_t['LY_QTY_MT'] * (1 + ly_t['TARGET_GROWTH_PCT'] / 100.0)
        lsa_target = ly_t.groupby('LSA_NAME')['TARGET_MT'].sum().reset_index().rename(columns={'TARGET_MT': 'Target (MT)'})

    # Merge with df_master to ensure all LSAs are present
    lsa_master_list = df_master['LSA_NAME'].unique()
    lsa_core = pd.DataFrame({'LSA_NAME': lsa_master_list})
    lsa_core = pd.merge(lsa_core, lsa_cy, on='LSA_NAME', how='left').fillna(0)
    lsa_core = pd.merge(lsa_core, lsa_ly, on='LSA_NAME', how='left').fillna(0)
    lsa_core = pd.merge(lsa_core, lsa_target, on='LSA_NAME', how='left').fillna(0)
    
    lsa_core['Achievement (%)'] = lsa_core.apply(
        lambda r: safe_pct(r['Actual (MT)'], r['Target (MT)']), axis=1)
    lsa_core['Gap vs Target (MT)'] = (lsa_core['Actual (MT)'] - lsa_core['Target (MT)']).round(2)

    total_row = pd.DataFrame([{
        'LSA_NAME':'GRAND TOTAL',
        'LY Sales (MT)': lsa_core['LY Sales (MT)'].sum(),
        'Target (MT)': lsa_core['Target (MT)'].sum(),
        'Actual (MT)': lsa_core['Actual (MT)'].sum(),
        'Gap vs Target (MT)': lsa_core['Gap vs Target (MT)'].sum(),
    }])
    total_row['Achievement (%)'] = safe_pct(
        total_row['Actual (MT)'].iloc[0],
        total_row['Target (MT)'].iloc[0])

    lsa_grid_df = pd.concat([lsa_core.sort_values('Actual (MT)', ascending=False),
                            total_row], ignore_index=True).rename(columns={'LSA_NAME':'LSA'})
    lsa_grid_df = lsa_grid_df.round(2)

    # bar chart
    fig_lsa = go.Figure()
    lsa_plot = lsa_grid_df[lsa_grid_df['LSA'] != 'GRAND TOTAL']
    fig_lsa.add_trace(go.Bar(x=lsa_plot['LSA'], y=lsa_plot['Actual (MT)'],
                             name='CY Actual', marker_color=IOC_BLUE))
    fig_lsa.add_trace(go.Bar(x=lsa_plot['LSA'], y=lsa_plot['LY Sales (MT)'],
                             name='LY Sales', marker_color='gray', opacity=0.5))
    fig_lsa.add_trace(go.Bar(x=lsa_plot['LSA'], y=lsa_plot['Target (MT)'],
                             name='Target', marker_color=IOC_ORANGE, opacity=0.6))
    fig_lsa.add_trace(go.Scatter(x=lsa_plot['LSA'], y=lsa_plot['Achievement (%)'],
                                 name='Achievement %', yaxis='y2',
                                 line=dict(color=IOC_GREEN, width=2), mode='lines+markers'))
    fig_lsa.update_layout(
        **PLOTLY_LAYOUT, barmode='group',
        title=f"LSA Comparison — {selected_period}", height=380,
        yaxis=dict(title="MT"),
        yaxis2=dict(title="Achievement %", overlaying='y', side='right', showgrid=False))
    st.plotly_chart(fig_lsa, use_container_width=True)

    download_button(lsa_grid_df, "lsa_performance_summary.csv")
    aggrid_render(lsa_grid_df, height=380, fit=True)

# ─── TAB 7 : Product YTD ─────────────────────────────────────────────────────
with tab_product:
    st.markdown('<p class="section-header">Product / Segment YTD Performance (EA & MT)</p>',
                unsafe_allow_html=True)

    cy_seg = (df_act.groupby('SEGMENT')[['QTY_EA','QTY_MT']]
              .sum().reset_index().rename(columns={'QTY_EA':'CY_YTD_EA','QTY_MT':'CY_YTD_MT'}))

    if not df_base.empty:
        ly_seg = (df_base.groupby('SEGMENT')[['TOTAL_LY_QTY_EA','AVG_MONTHLY_QTY_MT']]
                  .sum().reset_index())
        ly_seg['LY_YTD_PRORATED_EA'] = (ly_seg['TOTAL_LY_QTY_EA'] * (months_passed / 12.0)).round(0)
        ly_seg['LY_YTD_PRORATED_MT'] = (ly_seg['AVG_MONTHLY_QTY_MT'] * months_passed).round(2)
        prod = pd.merge(ly_seg, cy_seg, on='SEGMENT', how='left').fillna(0)
        prod['GROWTH_EA_%'] = prod.apply(
            lambda r: safe_pct(r['CY_YTD_EA'] - r['LY_YTD_PRORATED_EA'], r['LY_YTD_PRORATED_EA']), axis=1)
        prod['GROWTH_MT_%'] = prod.apply(
            lambda r: safe_pct(r['CY_YTD_MT'] - r['LY_YTD_PRORATED_MT'], r['LY_YTD_PRORATED_MT']), axis=1)
        prod = prod[['SEGMENT','LY_YTD_PRORATED_EA','CY_YTD_EA','GROWTH_EA_%',
                     'LY_YTD_PRORATED_MT','CY_YTD_MT','GROWTH_MT_%']].round(2)
        prod.columns = ['Segment','LY EA (Prorated)','CY EA','Growth EA%',
                        'LY MT (Prorated)','CY MT','Growth MT%']
    else:
        prod = cy_seg.rename(columns={'SEGMENT':'Segment'}).round(2)

    p1, p2 = st.columns(2)
    with p1:
        fig_p = px.bar(prod, x='Segment', y=['LY EA (Prorated)','CY EA'] if 'LY EA (Prorated)' in prod.columns else ['CY EA'],
                       barmode='group', title="Unit Sales by Segment (EA)",
                       color_discrete_map={'CY EA':IOC_BLUE,'LY EA (Prorated)':IOC_ORANGE})
        fig_p.update_layout(**PLOTLY_LAYOUT, height=320, yaxis_title="EA")
        st.plotly_chart(fig_p, use_container_width=True)
    with p2:
        if 'CY MT' in prod.columns:
            fig_pm = px.bar(prod, x='Segment', y=['LY MT (Prorated)','CY MT'],
                            barmode='group', title="Weight Sales by Segment (MT)",
                            color_discrete_map={'CY MT':IOC_BLUE,'LY MT (Prorated)':IOC_ORANGE})
            fig_pm.update_layout(**PLOTLY_LAYOUT, height=320, yaxis_title="MT")
            st.plotly_chart(fig_pm, use_container_width=True)

    download_button(prod, "product_segment_ytd.csv")
    aggrid_render(prod, height=280, fit=True)

# ─── TAB 9 : NDNE to Domestic Ratio ──────────────────────────────────────────
with tab_ratio:
    st.markdown('<p class="section-header">⚖️ NDNE to Domestic Sales Ratio Analysis</p>', unsafe_allow_html=True)
    
    if df_dom.empty:
        st.info("Domestic sales data not yet available. Please re-sync your cumulative actuals to populate this report.")
    else:
        # Aggregate NDNE by distributor
        ndne_sum = df_act.groupby('DIST_CODE')['QTY_MT'].sum().reset_index().rename(columns={'QTY_MT': 'NDNE_MT'})
        
        # Merge Master + NDNE + Domestic
        ratio_df = pd.merge(df_master[['DIST_CODE', 'DIST_NAME', 'LSA_NAME']], ndne_sum, on='DIST_CODE', how='left').fillna(0)
        ratio_df = pd.merge(ratio_df, df_dom, on='DIST_CODE', how='left').fillna(0)
        
        # Calculate Ratio
        ratio_df['Ratio %'] = ratio_df.apply(lambda r: safe_pct(r['NDNE_MT'], r['DOM_QTY_MT']), axis=1)
        ratio_df['GAP (MT)'] = (ratio_df['NDNE_MT'] - (ratio_df['DOM_QTY_MT'] * 0.05)).round(2)
        
        # Summary KPIs
        tot_ndne = ratio_df['NDNE_MT'].sum()
        tot_dom = ratio_df['DOM_QTY_MT'].sum()
        overall_ratio = safe_pct(tot_ndne, tot_dom)
        overall_gap_mt = tot_ndne - (tot_dom * 0.05)
        
        rk1, rk2, rk3 = st.columns(3)
        rk1.metric("Total NDNE Sales", f"{tot_ndne:,.2f} MT")
        rk2.metric("Total Domestic Sales", f"{tot_dom:,.2f} MT")
        rk3.metric("Overall NDNE/Dom Ratio", f"{overall_ratio:.2f}%", delta=f"{overall_gap_mt:+,.2f} MT vs 5% target")
        
        st.divider()
        st.subheader("LSA-wise Summary Report")
        lsa_sum = ratio_df.groupby('LSA_NAME').agg({
            'NDNE_MT': 'sum',
            'DOM_QTY_MT': 'sum'
        }).reset_index()
        lsa_sum['Ratio %'] = lsa_sum.apply(lambda r: safe_pct(r['NDNE_MT'], r['DOM_QTY_MT']), axis=1)
        lsa_sum['GAP (MT)'] = (lsa_sum['NDNE_MT'] - (lsa_sum['DOM_QTY_MT'] * 0.05))
        
        # Add Grand Total Row
        total_row = pd.DataFrame({
            'LSA_NAME': ['GRAND TOTAL'],
            'NDNE_MT': [lsa_sum['NDNE_MT'].sum()],
            'DOM_QTY_MT': [lsa_sum['DOM_QTY_MT'].sum()],
            'Ratio %': [safe_pct(lsa_sum['NDNE_MT'].sum(), lsa_sum['DOM_QTY_MT'].sum())],
            'GAP (MT)': [lsa_sum['GAP (MT)'].sum()]
        })
        lsa_sum = pd.concat([lsa_sum, total_row], ignore_index=True)

        lsa_show = lsa_sum.rename(columns={
            'LSA_NAME': 'LSA',
            'NDNE_MT': 'Cumulative NDNE (MT) till yesterday',
            'DOM_QTY_MT': 'Cumulative Domestic (MT) till yesterday'
        }).round(2)
        
        download_button(lsa_show, "ndne_lsa_summary.csv")
        aggrid_render(lsa_show, height=350)

        st.divider()
        st.subheader("Distributor-wise Ratio Breakdown")
        
        # Prepare for AgGrid
        ratio_show = ratio_df.drop(columns=['DIST_CODE']).rename(columns={
            'DIST_NAME': 'Distributor',
            'LSA_NAME': 'LSA',
            'NDNE_MT': 'NDNE (MT)',
            'DOM_QTY_MT': 'Domestic (MT)'
        }).sort_values('Ratio %', ascending=False).round(2)
        
        # Highlight logic (for future improvement, using standard AgGrid for now)
        download_button(ratio_show, "ndne_domestic_ratio.csv")
        aggrid_render(ratio_show, height=600)

# ─── TAB 10 : Target Setting ──────────────────────────────────────────────────
with tab_targets:
    st.markdown('<p class="section-header">🎯 Target Management — Set Monthly Targets (MT)</p>', unsafe_allow_html=True)
    
    if df_master.empty:
        st.warning("Master data required to set targets.")
    else:
        # Prepare target table
        target_df = pd.merge(df_master[['DIST_CODE', 'DIST_NAME', 'LSA_NAME']], 
                             df_base.groupby('DIST_CODE')['AVG_MONTHLY_QTY_MT'].sum().reset_index(),
                             on='DIST_CODE', how='left').fillna(0)
        target_df = target_df.rename(columns={'DIST_NAME': 'Distributor', 'LSA_NAME': 'LSA', 'AVG_MONTHLY_QTY_MT': 'Current Target (MT)'})
        
        st.info("You can update targets individually or apply a bulk target to all distributors.")
        
        with st.expander("批量更新 (Bulk Update All)"):
            bulk_val = st.number_input("Set global monthly target for ALL (MT)", min_value=0.0, step=1.0, value=0.0)
            if st.button("Apply Bulk Target to All"):
                try:
                    with conn_pool.acquire() as conn:
                        cursor = conn.cursor()
                        cursor.execute("UPDATE NDNE_BASELINE SET AVG_MONTHLY_QTY_MT = :1", [bulk_val])
                        conn.commit()
                        st.success(f"Updated all distributors to {bulk_val} MT")
                        st.cache_data.clear()
                        st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")

        with st.expander("📈 Update Target by Growth % over LY Actuals"):
            st.markdown("This will calculate the **actual last year sales for the same period** for each distributor and apply the growth percentage to set the new target.")
            growth_val = st.number_input("Enter Growth % (e.g. 30 for 30% growth)", value=30.0, step=1.0)
            if st.button("Apply Growth to All"):
                try:
                    with conn_pool.acquire() as conn:
                        cursor = conn.cursor()
                        cursor.execute("UPDATE NDNE_BASELINE SET TARGET_GROWTH_PCT = :1", [growth_val])
                        conn.commit()
                        st.success(f"Successfully set growth target to {growth_val}% for all distributors!")
                        st.cache_data.clear()
                        st.rerun()
                except Exception as e:
                    st.error(f"Growth Update Failed: {e}")

        st.divider()
        st.subheader("Individual Distributor Target Setting")
        
        # Filter for easier search
        search_lsa = st.selectbox("Search by LSA", ["ALL"] + sorted(df_master['LSA_NAME'].unique()))
        filt_target = target_df if search_lsa == "ALL" else target_df[target_df['LSA'] == search_lsa]
        
        sel_dist_target = st.selectbox("Select Distributor to Update", 
                                       [""] + filt_target.apply(lambda r: f"{r['DIST_CODE']} - {r['Distributor']}", axis=1).tolist())
        
        if sel_dist_target:
            d_code = sel_dist_target.split(" - ")[0]
            # Fetch current growth pct
            cur_growth = df_base[df_base['DIST_CODE'] == d_code]['TARGET_GROWTH_PCT'].iloc[0] if not df_base.empty else 0.0
            
            new_growth = st.number_input(f"New Growth % for {sel_dist_target}", 
                                      min_value=-100.0, step=1.0, value=float(cur_growth))
            
            if st.button("Update Individual Growth %"):
                try:
                    with conn_pool.acquire() as conn:
                        cursor = conn.cursor()
                        cursor.execute("UPDATE NDNE_BASELINE SET TARGET_GROWTH_PCT = :1 WHERE DIST_CODE = :2", [new_growth, d_code])
                        conn.commit()
                        st.success(f"Growth percentage updated for {d_code}!")
                        st.cache_data.clear()
                        st.rerun()
                except Exception as e:
                    st.error(f"Update Failed: {e}")

        st.divider()
        st.subheader("Target Overview")
        target_df_show = target_df.drop(columns=['DIST_CODE']).sort_values('Current Target (MT)', ascending=False).round(2)
        download_button(target_df_show, "distributor_targets.csv")
        aggrid_render(target_df_show)
