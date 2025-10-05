# main.py
import os
import json
from io import BytesIO
from datetime import datetime
from flask import Flask
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from reportlab.lib.units import inch
from telegram import Update, InputFile
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters
)

# ================== FLASK SETUP ==================
app = Flask(__name__)

@app.route("/")
def home():
    return "RFID Telegram Bot is running!"

# ================== CONFIG ==================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
GOOGLE_SHEETS_KEY = json.loads(os.environ.get("GOOGLE_SHEETS_KEY"))

ASK_DATE_RANGE = 1

# ================== GOOGLE SHEETS SETUP ==================
scope = ["https://spreadsheets.google.com/feeds","https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(GOOGLE_SHEETS_KEY, scope)
client = gspread.authorize(creds)

SHEET_NAME = "AttendanceDB"
EMPLOYEE_WS = "Employees"
ATTENDANCE_WS = "Attendance"

# ================== HELPERS ==================
def get_employee_by_chat_id(chat_id):
    sheet = client.open(SHEET_NAME).worksheet(EMPLOYEE_WS)
    all_emps = sheet.get_all_records()
    for emp in all_emps:
        if str(emp.get("telegram_chat_id")) == str(chat_id):
            return emp
    return None

def get_employee_by_id(emp_id):
    sheet = client.open(SHEET_NAME).worksheet(EMPLOYEE_WS)
    all_emps = sheet.get_all_records()
    for emp in all_emps:
        if str(emp.get("emp_id")) == str(emp_id):
            return emp
    return None

def fetch_logs(emp_id, start_date, end_date):
    sheet = client.open(SHEET_NAME).worksheet(ATTENDANCE_WS)
    all_logs = sheet.get_all_records()
    filtered = []
    for log in all_logs:
        if str(log.get("emp_id")) == str(emp_id):
            log_date = log.get("check_time").split(" ")[0]
            if start_date <= log_date <= end_date:
                filtered.append({
                    "check_time": datetime.strptime(log["check_time"], "%Y-%m-%d %H:%M:%S"),
                    "log_type": log["log_type"]
                })
    return filtered

def generate_pdf(name, emp_id, start_date, end_date, logs):
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = []

    elements.append(Paragraph("Attendance Report", styles['Title']))
    elements.append(Paragraph(f"{name} ({emp_id})", styles['Heading2']))
    elements.append(Paragraph(f"Period: {start_date} to {end_date}", styles['Normal']))
    elements.append(Spacer(1, 0.2*inch))

    data = [["#", "Date", "Time", "Log Type"]]
    for i, log in enumerate(logs, start=1):
        dt = log["check_time"].strftime("%Y-%m-%d")
        tm = log["check_time"].strftime("%I:%M %p")
        data.append([i, dt, tm, log["log_type"]])

    table = Table(data, colWidths=[0.6*inch,1.5*inch,1.5*inch,1.2*inch])
    style = TableStyle([
        ('BACKGROUND',(0,0),(-1,0),colors.HexColor("#4a90e2")),
        ('TEXTCOLOR',(0,0),(-1,0),colors.white),
        ('ALIGN',(0,0),(-1,-1),'CENTER'),
        ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
        ('BOTTOMPADDING',(0,0),(-1,0),10),
        ('GRID',(0,0),(-1,-1),0.5,colors.grey),
        ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.whitesmoke, colors.lightgrey])
    ])
    table.setStyle(style)
    elements.append(table)

    doc.build(elements)
    buffer.seek(0)
    return buffer

# ================== TELEGRAM HANDLERS ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    emp = get_employee_by_chat_id(chat_id)
    if emp:
        await update.message.reply_text(
            f"👋 Hi {emp['name']}!\nUse /mylog to get your attendance report.\nExample: /mylog\nThen reply with: 2025-10-01 to 2025-10-04"
        )
    else:
        await update.message.reply_text(
            "⚠️ You are not registered. Use /register <EMPLOYEE_ID> to register your Telegram ID."
        )

async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    args = context.args
    if not args:
        await update.message.reply_text("⚠️ Usage: /register <EMPLOYEE_ID>")
        return
    emp_id = args[0].strip()
    emp = get_employee_by_id(emp_id)
    if not emp:
        await update.message.reply_text("⚠️ Invalid Employee ID.")
        return
    # Update Google Sheet
    sheet = client.open(SHEET_NAME).worksheet(EMPLOYEE_WS)
    all_emps = sheet.get_all_records()
    for i, e in enumerate(all_emps, start=2):  # gspread rows start at 2 due to header
        if str(e.get("emp_id")) == emp_id:
            sheet.update_cell(i, all_emps[0].keys().index("telegram_chat_id")+1, chat_id)
            await update.message.reply_text(f"✅ Registered successfully for {emp['name']} ({emp_id})")
            return

async def mylog_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    emp = get_employee_by_chat_id(chat_id)
    if not emp:
        await update.message.reply_text("⚠️ You are not registered. Use /register first.")
        return ConversationHandler.END
    await update.message.reply_text("📅 Enter date range (YYYY-MM-DD to YYYY-MM-DD):")
    return ASK_DATE_RANGE

async def handle_date_range(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip()
    try:
        start_str, end_str = user_input.split("to")
        start_date = start_str.strip()
        end_date = end_str.strip()
        datetime.strptime(start_date, "%Y-%m-%d")
        datetime.strptime(end_date, "%Y-%m-%d")

        chat_id = str(update.effective_chat.id)
        emp = get_employee_by_chat_id(chat_id)
        if not emp:
            await update.message.reply_text("⚠️ Not registered.")
            return ConversationHandler.END

        logs = fetch_logs(emp["emp_id"], start_date, end_date)
        if not logs:
            await update.message.reply_text("📭 No attendance records found.")
            return ConversationHandler.END

        pdf_buffer = generate_pdf(emp["name"], emp["emp_id"], start_date, end_date, logs)
        await update.message.reply_document(InputFile(pdf_buffer, filename=f"{emp['emp_id']}_attendance.pdf"))

    except Exception as e:
        await update.message.reply_text("⚠️ Invalid format. Use: YYYY-MM-DD to YYYY-MM-DD")
        print("Date parsing error:", e)

    return ConversationHandler.END

# ================== MAIN FUNCTION ==================
def main():
    app_telegram = ApplicationBuilder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("mylog", mylog_command)],
        states={ASK_DATE_RANGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_date_range)]},
        fallbacks=[]
    )

    app_telegram.add_handler(CommandHandler("start", start))
    app_telegram.add_handler(CommandHandler("register", register))
    app_telegram.add_handler(conv_handler)

    print("✅ Bot running...")
    app_telegram.run_polling()

# ================== RUN BOT + FLASK ==================
if __name__ == "__main__":
    import threading
    # Run Flask in a separate thread
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))).start()
    # Run Telegram bot
    main()
