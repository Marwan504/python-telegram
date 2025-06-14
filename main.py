import telebot
from telebot import types
import os
import shutil
import img2pdf
from urllib.parse import urljoin
from DrissionPage import ChromiumPage
import logging
import asyncio
import telethon
import threading


api_id = 20306694 #غيره هدا بتاعتك 
api_hash = 'c72ed3735574dc46a7f0d447b3c61152'
TOKEN = '7136371530:AAGg6HBQU--G6-BGT7Zde53mmUlu6PtGKZ8'

bot = telebot.TeleBot(TOKEN)
telethon_client = None
main_loop = None
user_tasks = {}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logging.getLogger("img2pdf").setLevel(logging.ERROR)
logger = logging.getLogger(__name__)

LOGO_FILE = "logo.png" 

async def send_file_with_telethon(chat_id, file_path, caption, file_name):
    global telethon_client
    try:
        from telethon.tl.types import DocumentAttributeFilename
        await telethon_client.send_file(
            chat_id,
            file_path,
            caption=caption,
            attributes=[DocumentAttributeFilename(file_name=file_name)]
        )
    except Exception as e:
        logger.error(f"Failed to send file with Telethon: {e}")
        bot.send_message(chat_id, f"فشل إرسال الملف: {e}")

def scrape_manga_info(page, url):
    page.get(url)
    heading_element = page.ele('#chapter-heading')
    if not heading_element: return "Unknown Manga", "Unknown"
    full_title = heading_element.text.strip()
    if ' - ' in full_title:
        parts = full_title.rsplit(' - ', 1)
        return parts[0].strip(), parts[1].strip()
    return full_title, "Unknown"

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    user_name = message.from_user.first_name
    bot.reply_to(message, f'أهلاً بك يا {user_name}!\n\nأرسل لي رابط فصل واحد، أو ملف `.txt` يحتوي على عدة روابط.')

@bot.message_handler(content_types=['document'])
def handle_document(message):
    if not message.document.file_name.endswith('.txt'):
        bot.reply_to(message, "الرجاء إرسال ملف بصيغة .txt فقط.")
        return
    try:
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        urls = [url.strip() for url in downloaded_file.decode('utf-8').strip().splitlines() if url.strip().startswith('http')]
        if not urls:
            bot.reply_to(message, "الملف فارغ أو لا يحتوي على روابط صالحة.")
            return
        user_tasks[message.from_user.id] = urls
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton('نعم (دمج الكل)', callback_data='merge_yes'),
                   types.InlineKeyboardButton('لا (إرسال منفصل)', callback_data='merge_no'))
        bot.reply_to(message, f"تم العثور على {len(urls)} فصل. هل تريد دمجهم؟", reply_markup=markup)
    except Exception as e:
        logger.error(f"Error handling document: {e}")
        bot.reply_to(message, f"حدث خطأ: {e}")

@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    # ... (لا تغيير هنا)
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    bot.answer_callback_query(call.id)
    if user_id not in user_tasks:
        bot.edit_message_text("طلب قديم.", chat_id, call.message.message_id)
        return
    urls = user_tasks.pop(user_id)
    bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
    
    if call.data == 'merge_yes':
        bot.edit_message_text("اختيار موفق! بدأت عملية الدمج في الخلفية...", chat_id, call.message.message_id)
        threading.Thread(target=process_merged_chapters, args=(urls, chat_id)).start()
    elif call.data == 'merge_no':
        bot.edit_message_text(f"تمام! بدأت عملية إرسال {len(urls)} ملفات منفصلة...", chat_id, call.message.message_id)
        threading.Thread(target=process_separate_chapters, args=(urls, chat_id)).start()

@bot.message_handler(func=lambda message: message.text and message.text.startswith('http'))
def handle_single_url(message):
    url = message.text
    chat_id = message.chat.id
    status_msg = bot.send_message(chat_id, f"تم استلام الرابط. جاري معالجته الآن...")
    threading.Thread(target=process_single_chapter, args=(None, url, chat_id, 1, 1, status_msg)).start()


def process_separate_chapters(urls, chat_id):
    page = ChromiumPage()
    try:
        total = len(urls)
        for i, url in enumerate(urls, 1):
            status_msg = bot.send_message(chat_id, f"بدء معالجة الفصل {i}/{total}...")
            process_single_chapter(page, url, chat_id, i, total, status_msg)
    except Exception as e:
        logger.error(f"A major error occurred: {e}")
        bot.send_message(chat_id, f"حدث خطأ كبير، تم إيقاف العملية.")
    finally:
        page.quit()


def process_single_chapter(page, url, chat_id, current_num, total_num, status_msg):
    local_page_created = False
    if page is None:
        page = ChromiumPage()
        local_page_created = True

    temp_folder = f"single_temp_{chat_id}"
    if os.path.exists(temp_folder):
        shutil.rmtree(temp_folder)
    os.makedirs(temp_folder, exist_ok=True)
    
    pdf_path = None
    try:
        bot.edit_message_text(f"({current_num}/{total_num}) جاري استخراج المعلومات...", chat_id, status_msg.message_id)
        manga_name, chapter_number = scrape_manga_info(page, url)
        file_name_base = f"{manga_name} chap {chapter_number}"
        pdf_path = f"{file_name_base}.pdf"

        bot.edit_message_text(f"({current_num}/{total_num}) جاري تنزيل الصور...", chat_id, status_msg.message_id)
        container = page.wait.ele_displayed('.reading-content', timeout=45)
        image_urls = [urljoin(url, img.attr('src').strip()) for img in container.eles('tag:img') if img.attr('src')]
        
        for j, img_url in enumerate(image_urls):
            img_base_name = f"img{j:03d}"
            page.download(file_url=img_url, goal_path=temp_folder, rename=img_base_name, show_msg=False)
        
        all_files_in_dir = sorted(os.listdir(temp_folder))
        downloaded_images = [os.path.join(temp_folder, f) for f in all_files_in_dir]

        if os.path.exists(LOGO_FILE):
            downloaded_images.insert(0, LOGO_FILE)
            logger.info(f"Added {LOGO_FILE} to the beginning of the PDF.")
        else:
            logger.warning(f"{LOGO_FILE} not found. Creating PDF without it.")

        bot.edit_message_text(f"({current_num}/{total_num}) جاري إنشاء PDF...", chat_id, status_msg.message_id)
        with open(pdf_path, "wb") as f:
            f.write(img2pdf.convert(downloaded_images, rotation=img2pdf.Rotation.ifvalid))
            
        bot.edit_message_text(f"({current_num}/{total_num}) جاري إرسال الملف...", chat_id, status_msg.message_id)
        caption = f"ManGa : {manga_name}\nCHaPiTer : [{chapter_number}]\n\nBY\n@Speed_Manga"
        future = asyncio.run_coroutine_threadsafe(send_file_with_telethon(chat_id, pdf_path, caption, f"{file_name_base}.pdf"), main_loop)
        future.result()

    except Exception as e:
        logger.error(f"Error processing chapter {url}: {e}")
        bot.edit_message_text(f"فشل تحميل الفصل ({current_num}/{total_num})\nالسبب: {e}", chat_id, status_msg.message_id)
    finally:
        if local_page_created and page: page.quit()
        if os.path.exists(temp_folder): shutil.rmtree(temp_folder)
        if pdf_path and os.path.exists(pdf_path): os.remove(pdf_path)
        if 'e' not in locals():
            bot.delete_message(chat_id, status_msg.message_id)

def process_merged_chapters(urls, chat_id):
    main_temp_folder = f"merged_temp_{chat_id}"
    os.makedirs(main_temp_folder, exist_ok=True)
    page = ChromiumPage()
    pdf_path = None
    try:
        manga_name, start_chap = scrape_manga_info(page, urls[0])
        _, end_chap = scrape_manga_info(page, urls[-1])

        for i, url in enumerate(urls, 1):
            bot.send_message(chat_id, f"جاري تحميل صور الفصل {i}/{len(urls)}...", disable_notification=True)
            page.get(url)
            container = page.wait.ele_displayed('.reading-content', timeout=45)
            image_urls = [urljoin(url, img.attr('src').strip()) for img in container.eles('tag:img') if img.attr('src')]
            for j, img_url in enumerate(image_urls):
                img_base_name = f"chap{i:02d}_img{j:03d}"
                page.download(file_url=img_url, goal_path=main_temp_folder, rename=img_base_name, show_msg=False)

        all_files_in_dir = sorted(os.listdir(main_temp_folder))
        downloaded_images = [os.path.join(main_temp_folder, f) for f in all_files_in_dir]
        if not downloaded_images: raise Exception("لم يتم تنزيل أي صور.")

        if os.path.exists(LOGO_FILE):
            downloaded_images.insert(0, LOGO_FILE)
            logger.info(f"Added {LOGO_FILE} to the beginning of the merged PDF.")
        else:
            logger.warning(f"{LOGO_FILE} not found. Creating merged PDF without it.")

        final_msg = bot.send_message(chat_id, "تم تنزيل كل الصور. جاري إنشاء ملف PDF المدمج...")
        
        pdf_name_base = f"{manga_name} chap {start_chap}-{end_chap}"
        pdf_path = f"{pdf_name_base}.pdf"
        
        with open(pdf_path, "wb") as f:
            f.write(img2pdf.convert(downloaded_images, rotation=img2pdf.Rotation.ifvalid))

        bot.edit_message_text("اكتمل العمل! جاري إرسال الملف الكبير...", chat_id, final_msg.message_id)
        
        caption = f"ManGa : {manga_name}\nCHaPiTer : [{start_chap} - {end_chap}]\n\nBY\n@Speed_Manga"
        future = asyncio.run_coroutine_threadsafe(send_file_with_telethon(chat_id, pdf_path, caption, f"{pdf_name_base}.pdf"), main_loop)
        future.result()

        bot.delete_message(chat_id, final_msg.message_id)

    except Exception as e:
        logger.error(f"Error in merged processing: {e}")
        bot.send_message(chat_id, f"حدث خطأ أثناء الدمج: {e}")
    finally:
        page.quit()
        if os.path.exists(main_temp_folder): shutil.rmtree(main_temp_folder)
        if pdf_path and os.path.exists(pdf_path): os.remove(pdf_path)


async def start_bot_async():
    global telethon_client, main_loop
    main_loop = asyncio.get_running_loop()
    
    telethon_client = telethon.TelegramClient('manga_bot_session', api_id, api_hash)
    await telethon_client.start()
    logger.info("Telethon user client started successfully.")
    
    telebot_thread = threading.Thread(target=bot.infinity_polling, kwargs={'skip_pending': True})
    telebot_thread.daemon = True
    telebot_thread.start()
    logger.info("Telebot bot client is running in a separate thread.")

    while telebot_thread.is_alive():
        await asyncio.sleep(1)

if __name__ == '__main__':
    try:
        asyncio.run(start_bot_async())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot shutting down...")
