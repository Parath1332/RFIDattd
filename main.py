# main.py
import os
import json
from io import BytesIO
from datetime import datetime
from flask import Flask, request, jsonify

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors

from telegram import Update, InputFile, Bot
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, filters, CallbackContext

# ================== FLASK SETUP ==================
app = Flask(__name__)

@app.route("/")
def home():
    return "RFID Telegram Bot is running!"

# ================== CONFIG ==================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
GOOGLE_SHEETS_KEY = json.loads(os.environ.get("GOOGLE_SHEETS_KEY"))

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN env variable not set!")

# Telegram bot state
ASK_DATE_RANGE = 1

# ================== GOOGLE SHEETS SETUP ==================
scope = ["https://spreadsheets.google.com/feeds",
         "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(GOOGLE_SHEETS_KEY, scope)
client = gspread.authorize(creds)

# Spreadsheet config
SHEET_NAME = "AttendanceDB"
EMPLOYEE_WS = "Employees"
ATTENDANCE_WS = "Attendance"

# ================== TELEGRAM HELPERS ==================
def get_employee_by_chat_id(chat_id):
    sheet = client.open(SHEET_NAME).worksheet(EMPLOYEE_WS)
    all_emps = sheet.get_all_records()
    for emp in all_emps:
        if str(emp.get("telegram_chat_id")) == str(chat_id):
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

    # Title
    elements.append(Paragraph("Attendance Report", styles['Title']))
    elements.append(Paragraph(f"{name} ({emp_id})", styles['Heading2']))
    elements.append(Paragraph(f"Period: {start_date} to {end_date}", styles['Normal']))
    elements.append(Spacer(1, 20))

    # Table
    data = [["#", "Date", "Time", "Log Type"]]
    for i, log in enumerate(logs, start=1):
        dt = log["check_time"].strftime("%Y-%m-%d")
        tm = log["check_time"].strftime("%I:%M %p")
        data.append([i, dt, tm, log["log_type"]])

    table = Table(data, colWidths=[40, 100, 100, 80])
    style = TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#4a90e2")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0,0), (-1,0), 10),
        ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.whitesmoke, colors.lightgrey])
    ])
    table.setStyle(style)
    elements.append(table)

    doc.build(elements)
    buffer.seek(0)
    return buffer

# ================== TELEGRAM HANDLERS ==================
bot = Bot(BOT_TOKEN)
dispatcher = Dispatcher(bot, None, workers=0)

def start(update: Update, context: CallbackContext):
    chat_id = str(update.effective_chat.id)
    emp = get_employee_by_chat_id(chat_id)
    if emp:
        update.message.reply_text(
            f"👋 Hi {emp['name']}!\nUse /mylog to get your attendance report.\nExample: /mylog\nThen reply with: 2025-10-01 to 2025-10-04"
        )
    else:
        update.message.reply_text("⚠️ You are not registered. Contact admin.")

def mylog_command(update: Update, context: CallbackContext):
    chat_id = str(update.effective_chat.id)
    emp = get_employee_by_chat_id(chat_id)
    if not emp:
        update.message.reply_text("⚠️ You are not registered. Contact admin.")
        return
    update.message.reply_text("📅 Enter date range (YYYY-MM-DD to YYYY-MM-DD):")
    # Store employee id in context
    context.user_data['emp_id'] = emp['emp_id']
    return ASK_DATE_RANGE

def handle_date_range(update: Update, context: CallbackContext):
    user_input = update.message.text.strip()
    try:
        start_str, end_str = user_input.split("to")
        start_date = start_str.strip()
        end_date = end_str.strip()
        datetime.strptime(start_date, "%Y-%m-%d")
        datetime.strptime(end_date, "%Y-%m-%d")

        emp_id = context.user_data.get('emp_id')
        emp = get_employee_by_chat_id(update.effective_chat.id)
        logs = fetch_logs(emp_id, start_date, end_date)
        if not logs:
            update.message.reply_text("📭 No attendance records found.")
            return

        pdf_buffer = generate_pdf(emp["name"], emp_id, start_date, end_date, logs)
        update.message.reply_document(InputFile(pdf_buffer, filename=f"{emp_id}_attendance.pdf"))

    except Exception as e:
        update.message.reply_text("⚠️ Invalid format. Use: YYYY-MM-DD to YYYY-MM-DD")
        print("Date parsing error:", e)

# Register handlers
dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("mylog", mylog_command))
dispatcher.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_date_range))

# ================== FLASK WEBHOOK ==================
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "ok"

# ================== RUN ==================
if __name__ == "__main__":
    # Render automatically assigns PORT
    port = int(os.environ.get("PORT", 5000))
    print(f"Bot running! Set webhook at /{BOT_TOKEN}")
    app.run(host="0.0.0.0", port=port)
