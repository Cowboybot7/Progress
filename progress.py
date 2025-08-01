import gspread
import json
import os
import re
import logging
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

# ===== Setup Logging =====
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ===== Configuration =====
BOT_TOKEN = os.getenv('BOT_TOKEN')
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID')
SHEET_NAME = "Project Summary"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
CREDS_JSON = json.loads(os.environ['GOOGLE_CREDS_JSON'])
PORT = int(os.environ.get("PORT", 10000))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").rstrip('/') + f"/{BOT_TOKEN}"

# Validate webhook URL
if not re.match(r'^https?://[^\s/$.?#].[^\s]*$', WEBHOOK_URL):
    logger.error(f"Invalid webhook URL: {WEBHOOK_URL}")
    raise ValueError("Invalid webhook URL format")

# Conversation states
SELECT_PROJECT, INPUT_ACTUAL, INPUT_PLANNED = range(3)

# ===== Initialize Google Sheets API =====
def init_gsheet():
    try:
        logger.info("Initializing Google Sheets connection")
        creds = Credentials.from_service_account_info(CREDS_JSON, scopes=SCOPES)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)
        logger.info("Google Sheets connection successful")
        return sheet
    except Exception as e:
        logger.error(f"Google Sheets initialization failed: {str(e)}")
        raise

# ===== Bot Handlers =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Start command from {update.effective_user.id}")
    keyboard = [
        [InlineKeyboardButton("/start", callback_data="cmd_start")],
        [InlineKeyboardButton("/update", callback_data="cmd_update")],
        [InlineKeyboardButton("/list", callback_data="cmd_list")],
        [InlineKeyboardButton("/help", callback_data="cmd_help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = "🏗️ Project Progress Tracker Bot\n\nChoose a command:"
    
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)

async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Simple test command to verify bot functionality"""
    logger.info(f"Test command from {update.effective_user.id}")
    await update.message.reply_text("✅ Bot is working and responding!")

async def list_projects(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        logger.info("Listing projects")
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
            
            # Use emoji coloring for status
            delay_ahead = project.get('Delay/Ahead', 'N/A')
            try:
                da_value = float(''.join(filter(str.isdigit, delay_ahead)))
                if "-" in delay_ahead:
                    da_value = -da_value
                
                if da_value < 0:
                    delay_ahead_display = f'🔴 <b>{delay_ahead}</b>'
                else:
                    delay_ahead_display = f'🟢 <b>{delay_ahead}</b>'
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
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(
                response, 
                parse_mode="HTML", 
                disable_web_page_preview=True
            )
        else:
            await update.message.reply_text(
                response, 
                parse_mode="HTML", 
                disable_web_page_preview=True
            )
    except Exception as e:
        error_msg = f"❌ Error fetching data: {str(e)}"
        logger.error(error_msg)
        await update.message.reply_text(error_msg)

async def update_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        logger.info("Starting update process")
        sheet = init_gsheet()
        projects = sheet.col_values(2)[1:]
        
        keyboard = []
        for idx, name in enumerate(projects, 1):
            keyboard.append([InlineKeyboardButton(f"{idx}. {name}", callback_data=str(idx))])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(
                "🔧 Select project to update:", 
                reply_markup=reply_markup
            )
        else:
            await update.message.reply_text(
                "🔧 Select project to update:", 
                reply_markup=reply_markup
            )
        
        return SELECT_PROJECT
    except Exception as e:
        error_msg = f"❌ Error: {str(e)}"
        logger.error(error_msg)
        await update.message.reply_text(error_msg)
        return ConversationHandler.END

async def select_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    context.user_data['project_row'] = int(query.data) + 1
    await query.edit_message_text("Enter new ACTUAL progress (0% - 100%):")
    return INPUT_ACTUAL

async def input_actual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        value = float(update.message.text)
        if not 0 <= value <= 100:
            raise ValueError
        context.user_data['actual'] = value / 100.0
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
        planned_decimal = value / 100.0
        
        sheet.update_cell(row, 9, context.user_data['actual'])
        sheet.update_cell(row, 10, planned_decimal)
        sheet.update_cell(row, 18, f"=NOW()")
        
        project_name = sheet.cell(row, 2).value
        
        await update.message.reply_text(
            f"✅ Successfully updated <b>{project_name}</b>:\n"
            f"- New Actual: {context.user_data['actual']*100:.1f}%\n"
            f"- New Planned: {value:.1f}%",
            parse_mode="HTML"
        )
        logger.info(f"Updated project: {project_name}")
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("❌ Invalid value! Must be number between 0 and 100\nTry again:")
        return INPUT_PLANNED
    except Exception as e:
        error_msg = f"❌ Update failed: {str(e)}"
        logger.error(error_msg)
        await update.message.reply_text(error_msg)
        return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚠️ Update canceled.")
    return ConversationHandler.END

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 Bot Guide:\n\n"
        "/start - Initialize the bot\n"
        "/list - Show all project statuses\n"
        "/update - Modify progress values\n"
        "/test - Test if bot is working\n\n"
        "When updating:\n"
        "1. Select a project\n"
        "2. Enter ACTUAL progress (0%-100%)\n"
        "3. Enter PLANNED progress (0%-100%)\n\n"
        "Note: Values must be numbers between 0 and 100"
    )
    
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text)
    else:
        await update.message.reply_text(text)

# ===== Create Telegram Application =====
def main():
    # Create Application instance
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("test", test_command))
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("list", list_projects))
    application.add_handler(CommandHandler("help", help_command))
    
    # Conversation handler
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
    application.add_handler(conv_handler)
    
    # Callback handlers
    application.add_handler(CallbackQueryHandler(start, pattern="cmd_start"))
    application.add_handler(CallbackQueryHandler(list_projects, pattern="cmd_list"))
    application.add_handler(CallbackQueryHandler(help_command, pattern="cmd_help"))
    
    # Start the bot with webhook
    logger.info(f"Starting bot with webhook URL: {WEBHOOK_URL}")
    logger.info(f"Using port: {PORT}")
    
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_url=WEBHOOK_URL,
        url_path=BOT_TOKEN,
        cert=None,
        key=None,
        bootstrap_retries=0,
        drop_pending_updates=False,
    )

if __name__ == "__main__":
    main()
