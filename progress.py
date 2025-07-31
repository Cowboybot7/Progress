import gspread
import json
import os
import re
import signal
import threading
import asyncio
from flask import Flask, request, Response
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler
)
from google.oauth2.service_account import Credentials
from datetime import datetime

# ===== Configuration =====
BOT_TOKEN = os.getenv('BOT_TOKEN')
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID')
SHEET_NAME = "Project Summary"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
CREDS_JSON = json.loads(os.environ['GOOGLE_CREDS_JSON'])

# Conversation states
SELECT_PROJECT, INPUT_ACTUAL, INPUT_PLANNED = range(3)

# Initialize Google Sheets API
def init_gsheet():
    creds = Credentials.from_service_account_info(CREDS_JSON, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)
    return sheet

# ===== Bot Handlers =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("/start", callback_data="cmd_start")],
        [InlineKeyboardButton("/update", callback_data="cmd_update")],
        [InlineKeyboardButton("/list", callback_data="cmd_list")],
        [InlineKeyboardButton("/help", callback_data="cmd_help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = "🏗️ Project Progress Tracker Bot\n\nChoose a command:"
    
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)

async def list_projects(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        sheet = init_gsheet()
        data = sheet.get_all_records()
        
        response = "📊 Current Project Status:\n\n"
        for project in data:
            attachment = project.get('Attachment', '')
            if attachment and attachment.startswith('http'):
                attachment_link = f'<a href="{attachment}">View Report</a>'
            else:
                attachment_link = 'N/A'
            
            update_progress = project.get('Update Progress', '')
            if update_progress and isinstance(update_progress, str):
                try:
                    update_date = datetime.strptime(update_progress, '%Y-%m-%d %H:%M:%S').strftime('%d %b %Y')
                except ValueError:
                    update_date = update_progress
            else:
                update_date = 'N/A'
            
            # Use emoji coloring instead of HTML styling
            delay_ahead = project.get('Delay/Ahead', 'N/A')
            try:
                # Extract numeric value from string (e.g., "-15 days" -> -15)
                da_value = float(''.join(filter(str.isdigit, delay_ahead)))
                if "-" in delay_ahead:
                    da_value = -da_value
                
                if da_value < 0:
                    delay_ahead_display = f'🔴 <b>{delay_ahead}</b>'  # Red circle for negative
                else:
                    delay_ahead_display = f'🟢 <b>{delay_ahead}</b>'  # Green circle for positive
            except (ValueError, TypeError):
                delay_ahead_display = f'<b>{delay_ahead}</b>'
            
            response += (
                f"▫️ <b>{project.get('Project Name', 'N/A')}</b>\n"
                f"   • Actual: <b>{project.get('Actual', 'N/A')}</b>\n"
                f"   • Planned: <b>{project.get('Planned', 'N/A')}</b>\n"
                f"   • Status: <b>{project.get('Status', 'N/A')}</b>\n"
                f"   • Increment: <b>{project.get('Increment', 'N/A')}</b>\n"
                f"   • Delay/Ahead: {delay_ahead_display}\n"
                f"   • Last Updated: <b>{update_date}</b>\n"
                f"   • Attachment: {attachment_link}\n\n"
            )
        
        if update.callback_query:
            query = update.callback_query
            await query.answer()
            await query.edit_message_text(response, parse_mode="HTML", disable_web_page_preview=True)
        else:
            await update.message.reply_text(response, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        error_msg = f"❌ Error fetching data: {str(e)}"
        if update.callback_query:
            await update.callback_query.message.reply_text(error_msg)
        else:
            await update.message.reply_text(error_msg)

async def update_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        sheet = init_gsheet()
        projects = sheet.col_values(2)[1:]  # Project names from column B
        
        keyboard = []
        for idx, name in enumerate(projects, 1):
            keyboard.append([InlineKeyboardButton(f"{idx}. {name}", callback_data=str(idx))])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if update.callback_query:
            query = update.callback_query
            await query.answer()
            await query.edit_message_text("🔧 Select project to update:", reply_markup=reply_markup)
        else:
            await update.message.reply_text("🔧 Select project to update:", reply_markup=reply_markup)
        
        return SELECT_PROJECT
    except Exception as e:
        error_msg = f"❌ Error: {str(e)}"
        if update.callback_query:
            await update.callback_query.message.reply_text(error_msg)
        else:
            await update.message.reply_text(error_msg)
        return ConversationHandler.END

async def select_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    context.user_data['project_row'] = int(query.data) + 1  # +1 for header row
    await query.edit_message_text("Enter new ACTUAL progress (0% - 100%):")
    return INPUT_ACTUAL

async def input_actual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        value = float(update.message.text)
        if not 0 <= value <= 100:
            raise ValueError
        context.user_data['actual'] = value / 100.0  # Convert percentage to decimal
        await update.message.reply_text("Enter new PLANNED progress (0% - 100%):")
        return INPUT_PLANNED
    except ValueError:
        await update.message.reply_text("❌ Invalid value! Must be number between 0 and 100\nTry again:")
        return INPUT_ACTUAL

async def input_planned(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        value = float(update.message.text)
        if not 0 <= value <= 100:
            raise ValueError
        
        sheet = init_gsheet()
        row = context.user_data['project_row']
        planned_decimal = value / 100.0  # Convert percentage to decimal
        
        sheet.update_cell(row, 9, context.user_data['actual'])  # Actual (I)
        sheet.update_cell(row, 10, planned_decimal)  # Planned (J)
        sheet.update_cell(row, 18, f"=NOW()")  # Update timestamp (R)
        
        project_name = sheet.cell(row, 2).value
        
        await update.message.reply_text(
            f"✅ Successfully updated <b>{project_name}</b>:\n"
            f"- New Actual: {context.user_data['actual']*100:.1f}%\n"
            f"- New Planned: {value:.1f}%",
            parse_mode="HTML"
        )
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("❌ Invalid value! Must be number between 0 and 100\nTry again:")
        return INPUT_PLANNED
    except Exception as e:
        await update.message.reply_text(f"❌ Update failed: {str(e)}")
        return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚠️ Update canceled.")
    return ConversationHandler.END

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 Bot Guide:\n\n"
        "/start - Initialize the bot\n"
        "/list - Show all project statuses\n"
        "/update - Modify progress values\n\n"
        "When updating:\n"
        "1. Select a project\n"
        "2. Enter ACTUAL progress (0%-100%)\n"
        "3. Enter PLANNED progress (0%-100%)\n\n"
        "Note: Values must be numbers between 0 and 100"
    )
    
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(text)
    else:
        await update.message.reply_text(text)

# ===== Flask Setup =====
app = Flask(__name__)

# Create Telegram Application
telegram_app = Application.builder().token(BOT_TOKEN).build()

# Add handlers to Telegram application
conv_handler = ConversationHandler(
    entry_points=[
        CommandHandler("update", update_start),
        CallbackQueryHandler(update_start, pattern="cmd_update")
    ],
    states={
        SELECT_PROJECT: [CallbackQueryHandler(select_project)],
        INPUT_ACTUAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_actual)],
        INPUT_PLANNED: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_planned)]
    },
    fallbacks=[CommandHandler("cancel", cancel)],
    allow_reentry=True
)

telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("list", list_projects))
telegram_app.add_handler(CommandHandler("help", help_command))
telegram_app.add_handler(conv_handler)
telegram_app.add_handler(CallbackQueryHandler(start, pattern="cmd_start"))
telegram_app.add_handler(CallbackQueryHandler(list_projects, pattern="cmd_list"))
telegram_app.add_handler(CallbackQueryHandler(help_command, pattern="cmd_help"))

# Webhook routes
@app.route(f'/{BOT_TOKEN}', methods=['POST'])
def telegram_webhook():
    json_data = request.get_json()
    update = Update.de_json(json_data, telegram_app.bot)
    telegram_app.update_queue.put(update)
    return Response(status=200)

@app.route('/wakeup', methods=['GET'])
def wakeup():
    return '🚀 Bot is awake and running!', 200

# Health check endpoint
@app.route('/health', methods=['GET'])
def health_check():
    return '✅ Bot is healthy', 200

# Start the Telegram bot in a background thread
def run_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    port = int(os.environ.get("PORT", 5000))
    webhook_base = os.environ.get("WEBHOOK_URL")
    
    if not webhook_base:
        raise RuntimeError("WEBHOOK_URL environment variable not set")
    
    webhook_base = webhook_base.rstrip('/')
    webhook_url = f"{webhook_base}/{BOT_TOKEN}"
    
    # Validate URL format
    if not re.match(r'^https?://[^\s/$.?#].[^\s]*$', webhook_url):
        raise ValueError(f"Invalid webhook URL: {webhook_url}")
    
    print(f"Starting bot with webhook URL: {webhook_url}")
    
    # Initialize and start the bot
    loop.run_until_complete(telegram_app.initialize())
    loop.run_until_complete(telegram_app.start())
    
    # Set webhook
    success = loop.run_until_complete(telegram_app.bot.set_webhook(webhook_url))
    if success:
        print("✅ Webhook set successfully")
    else:
        print("⚠️ Failed to set webhook!")
    
    # Run until stopped
    loop.run_forever()

# Start the bot thread
bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()

# Shutdown handler
def shutdown_handler(signum, frame):
    print("🛑 Shutting down bot...")
    asyncio.run(telegram_app.stop())
    asyncio.run(telegram_app.shutdown())
    print("Bot shutdown complete")
    exit(0)

# Main entry point
if __name__ == "__main__":
    # Register signal handlers
    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)
    
    # Start Flask app
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
