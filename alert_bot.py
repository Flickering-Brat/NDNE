import oracledb
import pandas as pd
import os
from datetime import datetime

import os
from dotenv import load_dotenv

load_dotenv()
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
WALLET_PASS = os.getenv("WALLET_PASS")
DB_DSN = os.getenv("DB_DSN")

def get_db_connection():
    try:
        wallet_path = os.path.join(os.getcwd(), "wallet")
        if os.path.exists(wallet_path):
            return oracledb.connect(user=DB_USER, password=DB_PASS, dsn=DB_DSN, wallet_location=wallet_path, wallet_password=WALLET_PASS)
        else:
            return oracledb.connect(user=DB_USER, password=DB_PASS, dsn=DB_DSN)
    except Exception as e:
        print(f"Database Connection Error: {e}")
        return None

def generate_alert_report():
    print("Running Daily Alert Bot...")
    conn = get_db_connection()
    if not conn: return
    
    try:
        query_act = "SELECT DIST_CODE, DIST_NAME, BILL_DATE, QTY_MT FROM NDNE_ACTUALS"
        df_act = pd.read_sql(query_act, con=conn)
        
        query_base = "SELECT DIST_CODE, AVG_MONTHLY_QTY_MT FROM NDNE_BASELINE"
        df_base = pd.read_sql(query_base, con=conn)
        
        if df_act.empty or df_base.empty:
            print("Not enough data to run alerts.")
            return
            
        # Calculate Proration
        max_date = pd.to_datetime(df_act['BILL_DATE']).max()
        if pd.notnull(max_date):
            month_num = max_date.month
            months_passed = month_num - 3 if month_num >= 4 else month_num + 9
            months_passed = max(1, months_passed)
        else:
            months_passed = 1
            
        dist_base = df_base.groupby('DIST_CODE')['AVG_MONTHLY_QTY_MT'].sum().reset_index()
        dist_cum = df_act.groupby(['DIST_CODE', 'DIST_NAME'])['QTY_MT'].sum().reset_index()
        
        merged = pd.merge(dist_cum, dist_base, on='DIST_CODE', how='inner')
        merged['PRORATED_TARGET'] = merged['AVG_MONTHLY_QTY_MT'] * months_passed
        merged['ACHIEVEMENT'] = (merged['QTY_MT'] / merged['PRORATED_TARGET']) * 100
        
        # Filter underperformers (< 75%)
        underperformers = merged[merged['ACHIEVEMENT'] < 75.0].sort_values(by='ACHIEVEMENT')
        
        report_path = "Action_Report.txt"
        report_text = f"🚨 *NDNE 8 AM ALERT REPORT* 🚨\nData as of: {max_date.strftime('%Y-%m-%d')}\nThreshold: < 75% YTD\n\n"
        
        with open(report_path, "w") as f:
            f.write(f"=== NDNE AUTOMATED ALERT REPORT ===\n")
            f.write(f"Generated On: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Data as of: {max_date.strftime('%Y-%m-%d')}\n")
            f.write(f"Alert Threshold: < 75% YTD Achievement\n")
            f.write(f"=====================================\n\n")
            
            f.write(f"ACTION REQUIRED: {len(underperformers)} Distributors require immediate follow-up:\n\n")
            
            for index, row in underperformers.iterrows():
                line = f"[{row['ACHIEVEMENT']:.1f}%] {row['DIST_NAME']} (Target: {row['PRORATED_TARGET']:.1f} MT | Actual: {row['QTY_MT']:.1f} MT)\n"
                f.write(line)
                report_text += line
                
        print(f"Alert Report successfully generated at {report_path}")
        
        # --- WHATSAPP INTEGRATION ---
        WHATSAPP_PHONE_NUMBER = os.getenv("WHATSAPP_PHONE_NUMBER") 
        
        if WHATSAPP_PHONE_NUMBER:
            try:
                import pywhatkit
                print(f"Opening WhatsApp Web to send alert to {WHATSAPP_PHONE_NUMBER}...")
                pywhatkit.sendwhatmsg_instantly(WHATSAPP_PHONE_NUMBER, report_text, wait_time=15, tab_close=True)
                print("WhatsApp message sent successfully!")
            except Exception as w_e:
                print(f"WhatsApp sending failed (make sure browser is open/unlocked): {w_e}")
        else:
            print("WhatsApp alert skipped: Please update the WHATSAPP_PHONE_NUMBER in alert_bot.py!")
            
    except Exception as e:
        print(f"Error generating report: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    generate_alert_report()
