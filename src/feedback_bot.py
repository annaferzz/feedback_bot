import logging
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TOKEN")
GOOGLE_SHEETS_CREDENTIALS = "credentials.json"
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)


class GoogleServices:
    _drive_service = None
    _sheets_service = None

    @classmethod
    def get_drive_service(cls):
        if cls._drive_service is None:
            scope = ['https://www.googleapis.com/auth/drive']
            creds = ServiceAccountCredentials.from_json_keyfile_name(GOOGLE_SHEETS_CREDENTIALS, scope)
            cls._drive_service = build('drive', 'v3', credentials=creds)
        return cls._drive_service

    @classmethod
    def get_sheets_service(cls):
        if cls._sheets_service is None:
            scope = ['https://www.googleapis.com/auth/spreadsheets']
            creds = ServiceAccountCredentials.from_json_keyfile_name(GOOGLE_SHEETS_CREDENTIALS, scope)
            cls._sheets_service = gspread.authorize(creds)
        return cls._sheets_service


def upload_to_drive(file_path: str, file_name: str) -> str:
    """Загружает файл в Google Drive и возвращает публичную ссылку"""
    try:
        drive_service = GoogleServices.get_drive_service()

        file_metadata = {
            'name': file_name,
            'parents': [DRIVE_FOLDER_ID] if DRIVE_FOLDER_ID else []
        }

        media = MediaFileUpload(file_path, mimetype='image/jpeg')
        file = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id,webViewLink'
        ).execute()

        drive_service.permissions().create(
            fileId=file['id'],
            body={'type': 'anyone', 'role': 'reader'},
            fields='id'
        ).execute()

        return file.get('webViewLink', f"https://drive.google.com/file/d/{file['id']}/view")
    except Exception as e:
        logger.error(f"Ошибка загрузки в Drive: {e}")
        raise

# Авторизация в Google Sheets
def get_google_sheet():
    try:
        scope = ["https://spreadsheets.google.com/feeds",
                 "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(GOOGLE_SHEETS_CREDENTIALS, scope)
        client = gspread.authorize(creds)
        sheet = client.open(SPREADSHEET_NAME).sheet1
        return sheet
    except Exception as e:
        logger.error(f"Ошибка доступа к Google Sheets: {e}")
        raise

RATING, COMMENT = range(2)
rating_keyboard = ReplyKeyboardMarkup(
    [["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"]],
    resize_keyboard=True,
    one_time_keyboard=True
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔹 Оцените приложение PROШКА от 1 до 10:",
        reply_markup=rating_keyboard
    )
    return RATING


async def handle_rating(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        rating = update.message.text
        if not rating.isdigit() or int(rating) not in range(1, 11):
            await update.message.reply_text("❌ Пожалуйста, выберите оценку от 1 до 10!")
            return RATING

        context.user_data["rating"] = rating
        await update.message.reply_text("📝 Напишите комментарий (или /skip чтобы пропустить). Вы можете прикрепить до 10 скринов к сообщению:")
        return COMMENT
    except Exception as e:
        logger.error(f"Ошибка в handle_rating: {e}")
        await update.message.reply_text("❌ Произошла ошибка. Попробуйте снова.")
        return ConversationHandler.END


async def handle_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if update.message.caption or update.message.text:
            comment_text = update.message.caption if update.message.caption else update.message.text
            context.user_data["comment_text"] = comment_text

        if update.message.photo:
            if "photo_urls" not in context.user_data:
                context.user_data["photo_urls"] = []

            for photo in update.message.photo:
                if len(context.user_data["photo_urls"]) >= 10:
                    await update.message.reply_text("ℹ️ Достигнут лимит в 10 фотографий. Остальные фотографии не будут сохранены.")
                    break

                file = await photo.get_file()

                os.makedirs(os.path.join("../temp_photos"), exist_ok=True)
                file_path = os.path.join("../temp_photos",
                                         f"photo_{update.message.from_user.id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.jpg")
                await file.download_to_drive(file_path)

                file_name = f"Feedback_Photo_{update.message.from_user.id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.jpg"
                photo_url = upload_to_drive(file_path, file_name)

                os.remove(file_path)

                context.user_data["photo_urls"].append(photo_url)

        if "comment_text" not in context.user_data and "photo_urls" not in context.user_data:
            await update.message.reply_text("ℹ️ Пожалуйста, отправьте текст или фото")
            return COMMENT

        if update.message.text or update.message.caption or update.message.photo:
            return await finalize_feedback(update, context)

    except Exception as e:
        logger.error(f"Ошибка в handle_comment: {e}")
        await update.message.reply_text("❌ Ошибка при обработке комментария.")
        return ConversationHandler.END

async def finalize_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        comment_parts = []

        if "comment_text" in context.user_data:
            comment_parts.append(f"{context.user_data['comment_text']}")

        if "photo_urls" in context.user_data and context.user_data["photo_urls"]:
            for i, url in enumerate(context.user_data["photo_urls"], 1):
                comment_parts.append(f"{i}. {url}")

        full_comment = "\n".join(comment_parts) if comment_parts else "Нет комментария"

        await save_to_gsheets(update, context, full_comment)
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Ошибка в finalize_feedback: {e}")
        await update.message.reply_text("❌ Ошибка при сохранении отзыва.")
        return ConversationHandler.END

async def skip_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await save_to_gsheets(update, context, "Пользователь пропустил комментарий")
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Ошибка в skip_comment: {e}")
        await update.message.reply_text("❌ Ошибка при сохранении.")
        return ConversationHandler.END

async def save_to_gsheets(update: Update, context: ContextTypes.DEFAULT_TYPE, comment: str):
    try:
        user = update.message.from_user
        sheet = get_google_sheet()

        row = [
            str(user.id),  # User ID
            f"@{user.username}" if user.username else "Не указан",  # Username
            user.first_name or "Не указано",  # First Name
            context.user_data.get("rating", "N/A"),  # Rating
            comment,  # Comment
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # Timestamp
        ]

        sheet.append_row(row)
        await update.message.reply_text("✅ Спасибо за Ваш отзыв!")

        context.user_data.clear()

    except Exception as e:
        logger.error(f"Ошибка сохранения в Google Sheets: {e}")
        await update.message.reply_text("❌ Не удалось сохранить отзыв. Попробуйте позже.")


def main():
    try:
        app = Application.builder().token(TOKEN).build()

        conv_handler = ConversationHandler(
            entry_points=[CommandHandler("start", start)],
            states={
                RATING: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_rating)],
                COMMENT: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_comment),
                    MessageHandler(filters.PHOTO & ~filters.CAPTION, handle_comment),
                    MessageHandler(filters.PHOTO & filters.CAPTION, handle_comment),
                    CommandHandler("skip", skip_comment)
                ]
            },
            fallbacks=[]
        )

        app.add_handler(conv_handler)

        try:
            get_google_sheet()
            logger.info("Успешное подключение к Google Sheets")

            if DRIVE_FOLDER_ID:
                drive_service = GoogleServices.get_drive_service()
                drive_service.files().get(fileId=DRIVE_FOLDER_ID).execute()
                logger.info(f"Успешное подключение к Google Drive, папка ID: {DRIVE_FOLDER_ID}")
        except Exception as e:
            logger.error(f"Ошибка подключения к Google сервисам: {e}")
            raise

        app.run_polling()

    except Exception as e:
        logger.critical(f"Фатальная ошибка: {e}")


if __name__ == "__main__":
    main()