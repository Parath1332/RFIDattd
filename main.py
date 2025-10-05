import os
import json
import threading
from flask import Flask
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from io import BytesIO
from datetime import datetime
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, ContextTypes

# ======= Google Sheets Setup =======
try:
    creds_json = json.loads(os.environ["GOOGLE_SHEETS_KEY"])
    
    # Check if required fields are present
    required_fields = ['type', 'project_id', 'private_key_id', 'private_key', 'client_email']
    missing_fields = [field for field in required_fields if field not in creds_json]
    if missing_fields:
        print(f"ERROR: Missing required fields in GOOGLE_SHEETS_KEY: {missing_fields}")
        print(f"Available fields: {list(creds_json.keys())}")
        raise ValueError(f"Missing required fields: {missing_fields}")
    
    # Validate and fix private_key
    private_key = creds_json['private_key']
    
    # Check if private key is too short (should be ~1600-1700 chars for RSA 2048)
    if len(private_key) < 200:
        print(f"\n{'='*60}")
        print("❌ ERROR: GOOGLE_SHEETS_KEY has an invalid/incomplete private key!")
        print(f"{'='*60}")
        print(f"Current private key length: {len(private_key)} characters")
        print(f"Expected length: ~1600-1700 characters")
        print(f"\nYour private key appears to be truncated or incomplete.")
        print(f"\nPlease:")
        print("1. Go back to your Google Cloud service account JSON file")
        print("2. Make sure you copy the ENTIRE 'private_key' value")
        print("3. It should include many lines of random-looking text between")
        print("   -----BEGIN PRIVATE KEY----- and -----END PRIVATE KEY-----")
        print("4. Update the GOOGLE_SHEETS_KEY secret with the complete JSON")
        print(f"{'='*60}\n")
        raise ValueError("Private key is too short - likely incomplete")
    
    # Fix newlines if needed
    if '\\n' in private_key:
        creds_json['private_key'] = private_key.replace('\\n', '\n')
        print("✓ Converted literal \\n to newlines")
    
    print(f"✓ Credentials JSON loaded successfully")
    print(f"✓ Service account email: {creds_json.get('client_email', 'N/A')}")
    
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_json, scope)
    print("✓ Google Sheets credentials authenticated")
    
except json.JSONDecodeError as e:
    print(f"ERROR: Invalid JSON in GOOGLE_SHEETS_KEY: {e}")
    print("Make sure you copied the entire JSON content from your service account key file")
    raise
except ValueError as e:
    print(f"ERROR: Invalid credentials format: {e}")
    raise
client = gspread.authorize(creds)

sheet_emp = client.open("AttendanceDB").worksheet("Employees")
sheet_log = client.open("AttendanceDB").worksheet("AttendanceLog")

# ======= Telegram Bot Setup =======
BOT_TOKEN = os.environ["BOT_TOKEN"]
application = Application.builder().token(BOT_TOKEN).build()

# ======= Flask App (Keep Replit Alive) =======
app = Flask(__name__)


@app.route("/")
def home():
    return "Telegram Bot is Running!"


# ======= Bot Commands =======
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Welcome!\n"
                              "Use /register <EMP_ID> to register.\n"
                              "Use /mylog to get your attendance report.")


async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("Usage: /register <EMP_ID>")
        return

    emp_id = args[0].strip()
    records = sheet_emp.get_all_records()
    for idx, emp in enumerate(records,
                              start=2):  # start=2 because header row is 1
        if emp["emp_id"] == emp_id:
            sheet_emp.update_cell(
                idx, 4, chat_id)  # Assuming 4th column is telegram_chat_id
            await update.message.reply_text(f"✅ Registered {emp_id} successfully!")
            return

    await update.message.reply_text(f"❌ Employee {emp_id} not found.")


async def mylog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    records = sheet_emp.get_all_records()
    emp_id = None
    emp_name = None
    for emp in records:
        if str(emp.get("telegram_chat_id")) == chat_id:
            emp_id = emp["emp_id"]
            emp_name = emp["name"]
            break
    if not emp_id:
        await update.message.reply_text("❌ You are not registered.")
        return

    logs = sheet_log.get_all_records()
    user_logs = [l for l in logs if l["emp_id"] == emp_id]
    if not user_logs:
        await update.message.reply_text("📭 No attendance records found.")
        return

    # ===== Generate PDF =====
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    data = [["#", "Date", "Time", "Log Type"]]
    for i, l in enumerate(user_logs, start=1):
        timestamp = l["timestamp"]  # Format: "YYYY-MM-DD HH:MM:SS"
        dt, tm = timestamp.split(" ")
        data.append([str(i), dt, tm, l["log_type"]])

    table = Table(data, colWidths=[50, 100, 100, 100])
    table.setStyle(
        TableStyle([('BACKGROUND', (0, 0), (-1, 0),
                     colors.HexColor("#4a90e2")),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.grey)]))
    doc.build([table])
    buffer.seek(0)
    await update.message.reply_document(
        document=buffer, filename=f"{emp_id}_attendance.pdf")


# ======= Add Handlers =======
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("register", register))
application.add_handler(CommandHandler("mylog", mylog))


# ======= Run Flask in Thread, Telegram Bot in Main =======
def run_flask():
    app.run(host="0.0.0.0", port=3000, debug=False, use_reloader=False)


if __name__ == "__main__":
    # Start Flask in a background thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Run Telegram bot in main thread
    print("✓ Starting Telegram bot polling...")
    application.run_polling()
