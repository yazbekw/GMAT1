#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
بوت أسئلة شامل - الحل النهائي المتكامل
جميع الأزرار تعمل، والاختبار المخصص يظهر السؤال الأول فوراً
مع عرض الخيارات كاملة كنص وأزرار اختيار رقمية صغيرة (1,2,3,4...)
"""

import os
import re
import json
import sqlite3
import random
import logging
import asyncio
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
    ConversationHandler,
)
from telegram.constants import ParseMode

# ======================== التهيئة ========================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "bot_database.db")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(","))) if os.getenv("ADMIN_IDS") else []
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("BOT_TOKEN not set in environment")

# ======================== دوال مساعدة ========================
def escape_html(text: str) -> str:
    if not text:
        return ""
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

def get_option_label(index: int) -> str:
    """إرجاع رقم الخيار (1,2,3,...) لاستخدامه في الأزرار وعرض الخيارات"""
    return str(index + 1)

# ======================== دالة تطبيع الخيارات ========================
def normalize_options(options):
    """
    تحويل الخيارات إلى قائمة من النصوص مع الحفاظ على الترتيب.
    - إذا كانت الخيارات قاموساً (dict)، يتم ترتيب المفاتيح واستخراج القيم.
    - إذا كانت الخيارات قائمة، تُرجع كما هي.
    - وإلا ترجع قائمة فارغة.
    """
    if isinstance(options, dict):
        keys = sorted(options.keys(), key=lambda x: int(x) if x.isdigit() else x)
        return [options[k] for k in keys]
    elif isinstance(options, list):
        return options
    else:
        return []

def get_answer_index(options, correct_answer):
    """
    تحويل الإجابة الصحيحة (التي قد تكون حرفاً أو رقماً) إلى مؤشر صحيح (0-index).
    """
    if isinstance(options, dict):
        keys = sorted(options.keys(), key=lambda x: int(x) if x.isdigit() else x)
        if correct_answer in keys:
            return keys.index(correct_answer)
        for idx, key in enumerate(keys):
            if str(correct_answer) == key:
                return idx
        return 0
    else:
        try:
            return int(correct_answer) - 1
        except:
            return 0

# ======================== قاعدة البيانات ========================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        question TEXT NOT NULL,
        options TEXT NOT NULL,
        answer INTEGER NOT NULL,
        explanation TEXT,
        category TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        state TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS answers_log (
        user_id INTEGER,
        question_id INTEGER,
        correct INTEGER,
        answered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, question_id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS bookmarks (
        user_id INTEGER,
        question_id INTEGER,
        PRIMARY KEY (user_id, question_id)
    )''')
    conn.commit()
    conn.close()

def get_db_connection():
    return sqlite3.connect(DB_PATH)

def load_questions_from_json():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM questions")
    pattern = os.path.join(BASE_DIR, '*.json')
    import glob
    files = glob.glob(pattern)
    files = [f for f in files if not f.endswith('_export.csv') and 'user_data' not in f]
    
    def extract_num(f):
        nums = re.findall(r'\d+', f)
        return int(nums[0]) if nums else float('inf')
    files.sort(key=extract_num)
    
    count = 0
    for file in files:
        try:
            with open(file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            raw = []
            if 'exam' in data and 'questions' in data['exam']:
                raw = data['exam']['questions']
            elif 'questions' in data:
                raw = data['questions']
            else:
                for val in data.values():
                    if isinstance(val, list) and val and isinstance(val[0], dict) and 'question' in val[0]:
                        raw.extend(val)
            for q in raw:
                if q.get('question') and q.get('options'):
                    options_raw = q['options']
                    options_list = normalize_options(options_raw)
                    if not options_list:
                        continue
                    answer_idx = get_answer_index(options_raw, q.get('correct_answer', 0))
                    if answer_idx < 0 or answer_idx >= len(options_list):
                        answer_idx = 0
                    options_json = json.dumps(options_list)
                    c.execute(
                        "INSERT INTO questions (question, options, answer, explanation, category) VALUES (?, ?, ?, ?, ?)",
                        (q['question'], options_json, answer_idx, q.get('explanation', ''), q.get('category', 'غير مصنف'))
                    )
                    count += 1
        except Exception as e:
            logger.error(f"خطأ في تحميل {file}: {e}")
    conn.commit()
    conn.close()
    logger.info(f"تم تحميل {count} سؤال من JSON إلى قاعدة البيانات")
    return count

def get_all_questions():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, question, options, answer, explanation, category FROM questions")
    rows = c.fetchall()
    conn.close()
    result = []
    for row in rows:
        options = normalize_options(json.loads(row[2]))
        result.append((row[0], row[1], options, row[3], row[4], row[5]))
    return result

def get_question_by_id(qid):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, question, options, answer, explanation, category FROM questions WHERE id=?", (qid,))
    row = c.fetchone()
    conn.close()
    if row:
        options = normalize_options(json.loads(row[2]))
        return (row[0], row[1], options, row[3], row[4], row[5])
    return None

def get_categories():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT DISTINCT category FROM questions ORDER BY category")
    rows = c.fetchall()
    conn.close()
    return [row[0] for row in rows]

def get_questions_by_category(category):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, question, options, answer, explanation, category FROM questions WHERE category=?", (category,))
    rows = c.fetchall()
    conn.close()
    result = []
    for row in rows:
        options = normalize_options(json.loads(row[2]))
        result.append((row[0], row[1], options, row[3], row[4], row[5]))
    return result

# ======================== دوال حالة المستخدم ========================
def get_user_state(user_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT state FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    if row:
        state = json.loads(row[0])
        if 'used_questions' not in state:
            state['used_questions'] = []
    else:
        all_q = get_all_questions()
        qids = [q[0] for q in all_q]
        state = {
            'current_ids': qids,
            'current_index': 0,
            'answers': {},
            'bookmarks': [],
            'mode': 'normal',
            'quiz_time': None,
            'start_time': None,
            'used_questions': [],
        }
        c.execute("INSERT INTO users (user_id, state) VALUES (?, ?)", (user_id, json.dumps(state)))
        conn.commit()
    conn.close()
    return state

def save_user_state(user_id, state):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE users SET state=? WHERE user_id=?", (json.dumps(state), user_id))
    conn.commit()
    conn.close()

def get_answer_log(user_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT question_id, correct FROM answers_log WHERE user_id=?", (user_id,))
    rows = c.fetchall()
    conn.close()
    return {qid: bool(correct) for qid, correct in rows}

def log_answer(user_id, qid, correct):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO answers_log (user_id, question_id, correct, answered_at) VALUES (?, ?, ?, ?)",
              (user_id, qid, 1 if correct else 0, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def toggle_bookmark(user_id, qid):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT 1 FROM bookmarks WHERE user_id=? AND question_id=?", (user_id, qid))
    exists = c.fetchone()
    if exists:
        c.execute("DELETE FROM bookmarks WHERE user_id=? AND question_id=?", (user_id, qid))
        conn.commit()
        conn.close()
        return False
    else:
        c.execute("INSERT INTO bookmarks (user_id, question_id) VALUES (?, ?)", (user_id, qid))
        conn.commit()
        conn.close()
        return True

def get_bookmarks(user_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT question_id FROM bookmarks WHERE user_id=?", (user_id,))
    rows = c.fetchall()
    conn.close()
    return [row[0] for row in rows]

def get_wrong_questions(user_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT question_id FROM answers_log WHERE user_id=? AND correct=0", (user_id,))
    rows = c.fetchall()
    conn.close()
    return [row[0] for row in rows]

def get_weak_categories(user_id):
    log = get_answer_log(user_id)
    if not log:
        return []
    cat_stats = {}
    for qid, correct in log.items():
        q = get_question_by_id(qid)
        if q:
            cat = q[5] or 'غير مصنف'
            if cat not in cat_stats:
                cat_stats[cat] = {'correct': 0, 'total': 0}
            cat_stats[cat]['total'] += 1
            if correct:
                cat_stats[cat]['correct'] += 1
    weak = []
    for cat, stats in cat_stats.items():
        ratio = stats['correct'] / stats['total'] if stats['total'] > 0 else 0
        if ratio < 0.5:
            weak.append(cat)
    return weak

def get_unanswered_questions(user_id):
    answered = get_answer_log(user_id).keys()
    state = get_user_state(user_id)
    used = set(state.get('used_questions', []))
    all_q = get_all_questions()
    all_ids = [q[0] for q in all_q]
    available = [qid for qid in all_ids if qid not in answered and qid not in used]
    return available

# ======================== دوال واجهة المستخدم ========================
def build_main_menu(user_id=None):
    buttons = [
        [InlineKeyboardButton("📝 اختبار عادي", callback_data="start_quiz")],
        [InlineKeyboardButton("📝 اختبار مخصص", callback_data="custom_quiz")],
        [InlineKeyboardButton("📖 وضع التعلم", callback_data="study_mode")],
        [InlineKeyboardButton("📂 تصفية حسب الفئة", callback_data="categories")],
        [InlineKeyboardButton("⭐ إشاراتي", callback_data="bookmarks")],
        [InlineKeyboardButton("❌ الأخطاء فقط", callback_data="wrong_only")],
        [InlineKeyboardButton("💡 اقتراح أسئلة (نقاط ضعف)", callback_data="suggest")],
        [InlineKeyboardButton("📊 إحصائياتي", callback_data="stats")],
        [InlineKeyboardButton("📥 تصدير النتائج", callback_data="export")],
        [InlineKeyboardButton("🔄 إعادة تعيين", callback_data="reset")],
    ]
    if user_id and user_id in ADMIN_IDS:
        buttons.append([InlineKeyboardButton("⚙️ لوحة تحكم المشرف", callback_data="admin_panel")])
    return InlineKeyboardMarkup(buttons)

def build_back_button():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="menu")]])

def build_question_keyboard(qid, idx, total, state, time_left=None):
    buttons = []
    nav = []
    if idx > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"nav_prev_{idx}"))
    if idx < total - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"nav_next_{idx}"))
    nav.append(InlineKeyboardButton("📖 شرح", callback_data=f"show_explain_{qid}"))
    bookmarked = qid in state.get('bookmarks', [])
    star = "⭐" if bookmarked else "☆"
    nav.append(InlineKeyboardButton(star, callback_data=f"bookmark_{qid}"))
    buttons.append(nav)
    
    # زر لعرض الخيارات كاملة (اختياري، لكن الخيارات تظهر بالفعل كاملة في النص)
    buttons.append([InlineKeyboardButton("📄 عرض الخيارات كاملة", callback_data=f"show_full_opts_{qid}")])
    
    buttons.append([InlineKeyboardButton("🏠 القائمة", callback_data="menu")])
    if state.get('mode') == 'study':
        buttons.append([InlineKeyboardButton("🔄 إنهاء وضع التعلم", callback_data="exit_study")])
    if time_left is not None:
        mins, secs = divmod(int(time_left), 60)
        buttons.append([InlineKeyboardButton(f"⏱ {mins:02d}:{secs:02d}", callback_data="noop")])
    return InlineKeyboardMarkup(buttons)

def build_option_buttons(q, state):
    """
    بناء أزرار اختيار صغيرة مرقمة (1,2,3,4,...) 
    تعرض فقط الأرقام مع علامات الصح والخطأ عند الإجابة.
    """
    qid, question, options, answer, explanation, category = q
    buttons = []
    ans = state.get('answers', {})
    selected = ans.get(str(qid))
    answered = selected is not None
    
    row = []
    for i, _ in enumerate(options):
        label = get_option_label(i)  # "1", "2", "3", ...
        if answered:
            if i == answer:
                label = "✅ " + label
            elif i == selected:
                label = "❌ " + label
        callback = f"answer_{qid}_{i}" if not answered else "noop"
        row.append(InlineKeyboardButton(label, callback_data=callback))
        if len(row) == 5:  # 5 أزرار في كل صف
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)

def format_question_header(q, idx, total, time_left=None, show_options=True):
    """
    تنسيق رأس السؤال مع عرض الخيارات كاملة كنص مرقم (1. نص الخيار, 2. نص الخيار, ...)
    """
    qid, question, options, answer, explanation, category = q
    cat = escape_html(category or "غير مصنف")
    q_text = escape_html(question)
    
    progress = int((idx / total) * 20) if total else 0
    bar = "█" * progress + "░" * (20 - progress)
    progress_text = f"<code>[{bar}] {int((idx/total)*100) if total else 0}%</code>"
    time_str = ""
    if time_left is not None:
        if time_left > 0:
            mins, secs = divmod(int(time_left), 60)
            time_str = f"⏳ <b>الوقت المتبقي:</b> <code>{mins:02d}:{secs:02d}</code>"
        else:
            time_str = "⏰ <b>انتهى الوقت!</b>"
    
    text = (
        f"📌 <b>السؤال {idx+1}/{total}</b>\n"
        f"📂 <b>الفئة:</b> {cat}\n"
        f"{progress_text}\n"
        f"{time_str}\n\n"
        f"<b>{q_text}</b>\n"
    )
    
    if show_options:
        options_text = "\n".join([f"{get_option_label(i)}. {escape_html(opt)}" for i, opt in enumerate(options)])
        text += f"\n📋 <b>الخيارات:</b>\n{options_text}"
    
    return text

# ======================== معالجات الأوامر ========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    state = get_user_state(user_id)
    save_user_state(user_id, state)
    welcome_text = (
        f"👋 <b>أهلاً بك {escape_html(user.first_name)} في بوت الأسئلة الشامل!</b>\n\n"
        "✨ <b>مزايا البوت:</b>\n"
        "• اختبارات عشوائية أو حسب الفئة\n"
        "• اختبار مخصص بعدد أسئلة محدد (بدون تكرار الأسئلة)\n"
        "• وضع التعلم لعرض السؤال مع الإجابة والشرح فوراً\n"
        "• مؤقت زمني في الاختبارات مع عرض تنازلي\n"
        "• إشارات مرجعية للأسئلة المهمة\n"
        "• تتبع الأخطاء واقتراح أسئلة لنقاط الضعف\n"
        "• إحصائيات متقدمة وتصدير النتائج\n"
        "• لوحة تحكم للمشرفين لإدارة الأسئلة\n\n"
        "استخدم الأزرار أدناه للبدء 🚀"
    )
    await update.message.reply_text(
        welcome_text,
        parse_mode=ParseMode.HTML,
        reply_markup=build_main_menu(user_id)
    )

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    await query.edit_message_text(
        "🏠 <b>القائمة الرئيسية</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=build_main_menu(user_id)
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 <b>الأوامر المتاحة:</b>\n"
        "/start - عرض القائمة الرئيسية\n"
        "/stats - عرض إحصائياتي\n"
        "/reset - إعادة تعيين التقدم\n"
        "/export - تصدير النتائج كملف CSV\n"
        "/shuffle - خلط الأسئلة الحالية\n"
        "/study - تفعيل وضع التعلم\n"
        "/normal - العودة للوضع العادي\n"
        "استخدم الأزرار للتفاعل.",
        parse_mode=ParseMode.HTML
    )

async def shuffle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = get_user_state(user_id)
    qids = state['current_ids']
    random.shuffle(qids)
    state['current_ids'] = qids
    state['current_index'] = 0
    state['answers'] = {}
    save_user_state(user_id, state)
    await update.message.reply_text("🔀 <b>تم خلط الأسئلة.</b>", parse_mode=ParseMode.HTML)
    await show_current_question(update, context, user_id)

# ======================== اختبار مخصص ========================
async def custom_quiz_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['awaiting_custom_quiz'] = True
    await query.edit_message_text(
        "📝 <b>اختبار مخصص</b>\n"
        "أدخل عدد الأسئلة و المدة (بالدقائق) بالصيغة:\n\n"
        "<code>عدد_الأسئلة المدة</code>\n"
        "مثال: <code>10 5</code> (يعني 10 أسئلة و 5 دقائق)\n\n"
        "ملاحظة: سيتم اختيار الأسئلة من الأسئلة التي لم تجب عليها مسبقاً.",
        parse_mode=ParseMode.HTML
    )

async def handle_quiz_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('awaiting_custom_quiz'):
        return
    
    user_id = update.effective_user.id
    text = update.message.text.strip()
    parts = text.split()
    if len(parts) != 2:
        await update.message.reply_text(
            "❌ الصيغة غير صحيحة. استخدم: <code>10 5</code>",
            parse_mode=ParseMode.HTML
        )
        return
    
    try:
        count = int(parts[0])
        minutes = int(parts[1])
    except ValueError:
        await update.message.reply_text("❌ أرقام فقط. حاول مرة أخرى.")
        return
    
    if count <= 0 or minutes <= 0:
        await update.message.reply_text("❌ يجب أن تكون الأرقام أكبر من صفر.")
        return
    
    context.user_data['awaiting_custom_quiz'] = False
    
    available_qids = get_unanswered_questions(user_id)
    if not available_qids:
        await update.message.reply_text(
            "⚠️ لا توجد أسئلة متاحة للإجابة عليها.",
            reply_markup=build_main_menu(user_id)
        )
        return
    
    if count > len(available_qids):
        count = len(available_qids)
        await update.message.reply_text(f"⚠️ سيتم استخدام {count} سؤال.")
    
    selected_qids = random.sample(available_qids, count)
    
    state = get_user_state(user_id)
    state['current_ids'] = selected_qids
    state['current_index'] = 0
    state['answers'] = {}
    state['mode'] = 'normal'
    state['quiz_time'] = minutes * 60
    state['start_time'] = datetime.now().isoformat()
    state['used_questions'] = state.get('used_questions', []) + selected_qids
    save_user_state(user_id, state)
    
    job = context.job_queue.run_once(
        quiz_timeout,
        minutes * 60,
        user_id=user_id,
        name=f"quiz_timeout_{user_id}"
    )
    context.user_data['timeout_job'] = job
    
    await update.message.reply_text(
        f"⏱ بدء الاختبار: {count} سؤالاً، {minutes} دقيقة. حظاً موفقاً!",
        parse_mode=ParseMode.HTML
    )
    
    await show_current_question(update, context, user_id)

# ======================== وظائف الاختبار ========================
async def start_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE, mode='normal'):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    state = get_user_state(user_id)
    all_q = get_all_questions()
    qids = [q[0] for q in all_q]
    state['current_ids'] = qids
    state['current_index'] = 0
    state['mode'] = mode
    state['answers'] = {}
    state['quiz_time'] = None
    state['start_time'] = None
    save_user_state(user_id, state)
    await show_current_question(update, context, user_id)

async def show_current_question(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id=None):
    if not user_id:
        if update.callback_query:
            user_id = update.callback_query.from_user.id
        elif update.message:
            user_id = update.effective_user.id
        else:
            return
    
    state = get_user_state(user_id)
    qids = state['current_ids']
    idx = state['current_index']
    if idx >= len(qids):
        await send_quiz_complete(update, context, user_id)
        return
    
    qid = qids[idx]
    q = get_question_by_id(qid)
    if not q:
        if update.callback_query:
            await update.callback_query.answer("السؤال غير موجود!")
        else:
            await update.message.reply_text("السؤال غير موجود!")
        return
    
    time_left = None
    if state.get('quiz_time') and state.get('start_time'):
        start = datetime.fromisoformat(state['start_time'])
        elapsed = (datetime.now() - start).total_seconds()
        time_left = max(0, state['quiz_time'] - elapsed)
        if time_left <= 0:
            await quiz_timeout(context, user_id=user_id)
            return
    
    show_explanation = (state.get('mode') == 'study')
    if show_explanation:
        state['answers'][str(qid)] = q[3]
        save_user_state(user_id, state)
    
    total = len(qids)
    # عرض الخيارات كاملة في النص
    header_text = format_question_header(q, idx, total, time_left, show_options=True)
    if show_explanation and q[4]:
        header_text += f"\n📖 <b>الشرح:</b>\n{escape_html(q[4])}"
    
    option_keyboard = build_option_buttons(q, state)
    nav_keyboard = build_question_keyboard(qid, idx, total, state, time_left)
    combined_keyboard = InlineKeyboardMarkup(
        option_keyboard.inline_keyboard + nav_keyboard.inline_keyboard
    )
    
    try:
        if update.callback_query:
            await update.callback_query.edit_message_text(
                header_text,
                parse_mode=ParseMode.HTML,
                reply_markup=combined_keyboard
            )
        else:
            await update.message.reply_text(
                header_text,
                parse_mode=ParseMode.HTML,
                reply_markup=combined_keyboard
            )
    except Exception as e:
        logger.error(f"خطأ في عرض السؤال: {e}")
        if update.callback_query:
            await update.callback_query.message.reply_text(
                header_text,
                parse_mode=ParseMode.HTML,
                reply_markup=combined_keyboard
            )
        else:
            await update.message.reply_text(
                header_text,
                parse_mode=ParseMode.HTML,
                reply_markup=combined_keyboard
            )

async def send_quiz_complete(update, context, user_id):
    state = get_user_state(user_id)
    total = len(state['current_ids'])
    answered = len(state['answers'])
    correct = 0
    for qid_str, opt in state['answers'].items():
        qid = int(qid_str)
        q = get_question_by_id(qid)
        if q and q[3] == opt:
            correct += 1
    text = (
        f"🎉 <b>انتهى الاختبار!</b>\n"
        f"📊 <b>النتيجة:</b>\n"
        f"✅ صحيح: {correct}\n"
        f"❌ خطأ: {answered - correct}\n"
        f"📝 تم الإجابة على {answered}/{total}"
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=build_main_menu(user_id)
        )
    else:
        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=build_main_menu(user_id)
        )

# ======================== معالجات الأزرار ========================
async def answer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    
    if not data.startswith("answer_"):
        return
    
    try:
        parts = data.split("_")
        if len(parts) != 3:
            return
        qid = int(parts[1])
        opt = int(parts[2])
    except (ValueError, IndexError):
        return
    
    state = get_user_state(user_id)
    
    if qid not in state['current_ids']:
        await query.answer("السؤال ليس في القائمة الحالية!")
        return
    
    q = get_question_by_id(qid)
    if not q:
        await query.answer("السؤال غير موجود!")
        return
    
    if str(qid) in state.get('answers', {}):
        await query.answer("لقد أجبت بالفعل!")
        return
    
    correct = (opt == q[3])
    state['answers'][str(qid)] = opt
    log_answer(user_id, qid, correct)
    save_user_state(user_id, state)
    
    await show_current_question(update, context, user_id)

async def nav_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    if data.startswith("nav_prev_"):
        _, _, idx = data.split("_")
        idx = int(idx)
        state = get_user_state(user_id)
        if idx > 0:
            state['current_index'] = idx - 1
            save_user_state(user_id, state)
            await show_current_question(update, context, user_id)
    elif data.startswith("nav_next_"):
        _, _, idx = data.split("_")
        idx = int(idx)
        state = get_user_state(user_id)
        if idx < len(state['current_ids']) - 1:
            state['current_index'] = idx + 1
            save_user_state(user_id, state)
            await show_current_question(update, context, user_id)

async def explain_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    if data.startswith("show_explain_"):
        qid = int(data.split("_")[2])
        q = get_question_by_id(qid)
        if q:
            expl = escape_html(q[4]) if q[4] else "لا يوجد شرح."
            text = f"📖 <b>الشرح:</b>\n{expl}"
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=f"back_from_explain_{qid}")]])
            )

async def back_from_explain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    await show_current_question(update, context, user_id)

async def bookmark_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    if data.startswith("bookmark_"):
        qid = int(data.split("_")[1])
        bookmarked = toggle_bookmark(user_id, qid)
        state = get_user_state(user_id)
        if bookmarked:
            if qid not in state['bookmarks']:
                state['bookmarks'].append(qid)
        else:
            if qid in state['bookmarks']:
                state['bookmarks'].remove(qid)
        save_user_state(user_id, state)
        await show_current_question(update, context, user_id)

# ======================== عرض الخيارات كاملة ========================
async def show_full_options_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if not data.startswith("show_full_opts_"):
        return
    qid = int(data.split("_")[3])
    q = get_question_by_id(qid)
    if not q:
        await query.edit_message_text("⚠️ السؤال غير موجود.", reply_markup=build_back_button())
        return
    options_text = "\n".join([f"{get_option_label(i)}. {escape_html(opt)}" for i, opt in enumerate(q[2])])
    text = f"📋 <b>الخيارات الكاملة للسؤال {qid}:</b>\n\n{options_text}"
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع للسؤال", callback_data=f"back_from_fullopts_{qid}")]])
    )

async def back_from_fullopts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await show_current_question(update, context)

# ======================== وضع التعلم ========================
async def study_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    state = get_user_state(user_id)
    if not state['current_ids']:
        all_q = get_all_questions()
        state['current_ids'] = [q[0] for q in all_q]
        state['current_index'] = 0
    state['mode'] = 'study'
    state['answers'] = {}
    save_user_state(user_id, state)
    await show_current_question(update, context, user_id)

async def exit_study(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    state = get_user_state(user_id)
    state['mode'] = 'normal'
    save_user_state(user_id, state)
    await show_current_question(update, context, user_id)

# ======================== اقتراح الأسئلة حسب نقاط الضعف ========================
async def suggest_questions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    weak_cats = get_weak_categories(user_id)
    if not weak_cats:
        await query.edit_message_text(
            "🎉 <b>ممتاز!</b> ليس لديك نقاط ضعف واضحة. استمر في التدريب.",
            parse_mode=ParseMode.HTML,
            reply_markup=build_back_button()
        )
        return
    suggested = []
    for cat in weak_cats:
        qs = get_questions_by_category(cat)
        suggested.extend(qs)
    if not suggested:
        await query.edit_message_text(
            "⚠️ لا توجد أسئلة في الفئات التي تحتاج تحسيناً.",
            parse_mode=ParseMode.HTML,
            reply_markup=build_back_button()
        )
        return
    random.shuffle(suggested)
    selected = suggested[:10]
    state = get_user_state(user_id)
    state['current_ids'] = [q[0] for q in selected]
    state['current_index'] = 0
    state['answers'] = {}
    state['mode'] = 'normal'
    save_user_state(user_id, state)
    await show_current_question(update, context, user_id)

# ======================== إدارة المؤقت ========================
async def quiz_timeout(context: ContextTypes.DEFAULT_TYPE, user_id=None):
    if not user_id:
        user_id = context.job.user_id
    state = get_user_state(user_id)
    total = len(state['current_ids'])
    answered = len(state['answers'])
    correct = 0
    for qid_str, opt in state['answers'].items():
        qid = int(qid_str)
        q = get_question_by_id(qid)
        if q and q[3] == opt:
            correct += 1
    text = (
        f"⏰ <b>انتهى الوقت!</b>\n"
        f"📊 <b>النتيجة:</b>\n"
        f"✅ صحيح: {correct}\n"
        f"❌ خطأ: {answered - correct}\n"
        f"📝 تم الإجابة على {answered}/{total}"
    )
    await context.bot.send_message(
        user_id,
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=build_main_menu(user_id)
    )
    state['quiz_time'] = None
    state['start_time'] = None
    save_user_state(user_id, state)

# ======================== إحصائيات ========================
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    log = get_answer_log(user_id)
    total = len(log)
    correct = sum(1 for v in log.values() if v)
    wrong = total - correct
    pct = (correct / total * 100) if total > 0 else 0
    cat_stats = {}
    for qid, status in log.items():
        q = get_question_by_id(qid)
        if q:
            cat = q[5] or 'غير مصنف'
            if cat not in cat_stats:
                cat_stats[cat] = {'correct': 0, 'wrong': 0}
            if status:
                cat_stats[cat]['correct'] += 1
            else:
                cat_stats[cat]['wrong'] += 1
    lines = [
        f"📊 <b>إحصائياتي</b>",
        f"📝 الإجمالي: {total}",
        f"✅ صحيح: {correct}",
        f"❌ خطأ: {wrong}",
        f"🎯 النسبة: {pct:.1f}%",
        "",
        "📂 <b>حسب الفئة:</b>"
    ]
    for cat, vals in cat_stats.items():
        c = vals['correct']
        w = vals['wrong']
        if c + w > 0:
            ratio = c / (c + w) * 100
            lines.append(f"• {escape_html(cat)}: {c}/{c+w} ({ratio:.0f}%)")
    text = "\n".join(lines)
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=build_back_button()
    )

# ======================== تصدير ========================
async def export_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    state = get_user_state(user_id)
    log = get_answer_log(user_id)
    import csv
    import io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['السؤال', 'إجابتك', 'الصحيح', 'صحيح؟', 'الفئة'])
    for qid in state['current_ids']:
        q = get_question_by_id(qid)
        if not q:
            continue
        user_ans = state['answers'].get(str(qid))
        correct_ans = q[3]
        is_correct = log.get(qid, False)
        user_ans_text = q[2][user_ans] if user_ans is not None else ''
        correct_text = q[2][correct_ans] if correct_ans < len(q[2]) else ''
        writer.writerow([
            q[1],
            user_ans_text,
            correct_text,
            'نعم' if is_correct else 'لا',
            q[5]
        ])
    output.seek(0)
    await query.message.reply_document(
        document=output.getvalue().encode('utf-8-sig'),
        filename=f"results_{user_id}.csv",
        caption="📥 <b>نتائجك</b>"
    )
    await query.edit_message_text(
        "✅ تم التصدير بنجاح.",
        parse_mode=ParseMode.HTML,
        reply_markup=build_back_button()
    )

# ======================== إعادة تعيين ========================
async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM answers_log WHERE user_id=?", (user_id,))
    c.execute("DELETE FROM bookmarks WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()
    all_q = get_all_questions()
    state = {
        'current_ids': [q[0] for q in all_q],
        'current_index': 0,
        'answers': {},
        'bookmarks': [],
        'mode': 'normal',
        'quiz_time': None,
        'start_time': None,
        'used_questions': [],
    }
    save_user_state(user_id, state)
    await query.edit_message_text(
        "🔄 <b>تمت إعادة التعيين بنجاح.</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=build_main_menu(user_id)
    )

# ======================== تصفية حسب الفئة ========================
async def categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cats = get_categories()
    buttons = []
    row = []
    for cat in cats:
        row.append(InlineKeyboardButton(cat, callback_data=f"filter_cat_{cat}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data="menu")])
    await query.edit_message_text(
        "📂 <b>اختر فئة:</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def filter_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    cat = query.data.split("_")[2]
    qs = get_questions_by_category(cat)
    if not qs:
        await query.answer("لا توجد أسئلة في هذه الفئة.")
        return
    state = get_user_state(user_id)
    state['current_ids'] = [q[0] for q in qs]
    state['current_index'] = 0
    state['answers'] = {}
    state['mode'] = 'normal'
    save_user_state(user_id, state)
    await show_current_question(update, context, user_id)

# ======================== الإشارات المرجعية ========================
async def bookmarks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    bookmarks = get_bookmarks(user_id)
    if not bookmarks:
        await query.edit_message_text(
            "⭐ لا توجد إشارات مرجعية.",
            parse_mode=ParseMode.HTML,
            reply_markup=build_back_button()
        )
        return
    qs = []
    for qid in bookmarks:
        q = get_question_by_id(qid)
        if q:
            qs.append(q)
    if not qs:
        await query.edit_message_text(
            "⚠️ بعض الإشارات غير صالحة.",
            parse_mode=ParseMode.HTML,
            reply_markup=build_back_button()
        )
        return
    state = get_user_state(user_id)
    state['current_ids'] = [q[0] for q in qs]
    state['current_index'] = 0
    state['answers'] = {}
    state['mode'] = 'normal'
    save_user_state(user_id, state)
    await show_current_question(update, context, user_id)

# ======================== الأخطاء فقط ========================
async def wrong_only(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    wrong_ids = get_wrong_questions(user_id)
    if not wrong_ids:
        await query.edit_message_text(
            "🥳 <b>لا توجد أخطاء! أحسنت!</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=build_back_button()
        )
        return
    qs = []
    for qid in wrong_ids:
        q = get_question_by_id(qid)
        if q:
            qs.append(q)
    if not qs:
        await query.edit_message_text(
            "⚠️ لا توجد أسئلة خاطئة صالحة.",
            parse_mode=ParseMode.HTML,
            reply_markup=build_back_button()
        )
        return
    state = get_user_state(user_id)
    state['current_ids'] = [q[0] for q in qs]
    state['current_index'] = 0
    state['answers'] = {}
    state['mode'] = 'normal'
    save_user_state(user_id, state)
    await show_current_question(update, context, user_id)

# ======================== لوحة تحكم المشرف ========================
ADD_QUESTION_STATE = 1
ADD_QUESTION_OPTIONS = 2
ADD_QUESTION_ANSWER = 3
ADD_QUESTION_EXPLANATION = 4
ADD_QUESTION_CATEGORY = 5
DELETE_QUESTION_STATE = 1
EDIT_QUESTION_STATE = 1

def add_question(question, options, answer, explanation, category):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        "INSERT INTO questions (question, options, answer, explanation, category) VALUES (?, ?, ?, ?, ?)",
        (question, json.dumps(options), answer, explanation, category)
    )
    qid = c.lastrowid
    conn.commit()
    conn.close()
    return qid

def update_question(qid, question, options, answer, explanation, category):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        "UPDATE questions SET question=?, options=?, answer=?, explanation=?, category=? WHERE id=?",
        (question, json.dumps(options), answer, explanation, category, qid)
    )
    affected = c.rowcount
    conn.commit()
    conn.close()
    return affected

def delete_question(qid):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM questions WHERE id=?", (qid,))
    affected = c.rowcount
    conn.commit()
    conn.close()
    return affected

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await query.edit_message_text("⛔ <b>غير مصرح لك.</b>", parse_mode=ParseMode.HTML)
        return
    buttons = [
        [InlineKeyboardButton("➕ إضافة سؤال", callback_data="admin_add")],
        [InlineKeyboardButton("🗑 حذف سؤال", callback_data="admin_delete")],
        [InlineKeyboardButton("✏️ تعديل سؤال", callback_data="admin_edit")],
        [InlineKeyboardButton("📋 عرض الأسئلة", callback_data="admin_list")],
        [InlineKeyboardButton("📥 استيراد من JSON", callback_data="admin_import_json")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="menu")],
    ]
    await query.edit_message_text(
        "⚙️ <b>لوحة تحكم المشرف</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def admin_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await query.edit_message_text("⛔ غير مصرح.")
        return
    await query.edit_message_text(
        "📝 <b>إضافة سؤال جديد</b>\nأدخل نص السؤال:",
        parse_mode=ParseMode.HTML
    )
    return ADD_QUESTION_STATE

async def admin_add_question_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data['new_question'] = text
    await update.message.reply_text(
        "📋 <b>أدخل الخيارات مفصولة بفواصل</b>\nمثال: <code>خيار1, خيار2, خيار3, خيار4</code>",
        parse_mode=ParseMode.HTML
    )
    return ADD_QUESTION_OPTIONS

async def admin_add_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    options = [opt.strip() for opt in text.split(',') if opt.strip()]
    if len(options) < 2:
        await update.message.reply_text(
            "⚠️ يجب أن يكون هناك خياران على الأقل. حاول مرة أخرى:",
            parse_mode=ParseMode.HTML
        )
        return ADD_QUESTION_OPTIONS
    context.user_data['new_options'] = options
    opts = "\n".join([f"{i+1}. {opt}" for i, opt in enumerate(options)])
    await update.message.reply_text(
        f"✅ الخيارات:\n{opts}\n\nأدخل رقم الإجابة الصحيحة (1-{len(options)}):",
        parse_mode=ParseMode.HTML
    )
    return ADD_QUESTION_ANSWER

async def admin_add_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        ans = int(update.message.text.strip())
        options = context.user_data['new_options']
        if ans < 1 or ans > len(options):
            raise ValueError
    except:
        await update.message.reply_text(
            f"⚠️ رقم غير صحيح. أدخل رقم بين 1 و {len(options)}:",
            parse_mode=ParseMode.HTML
        )
        return ADD_QUESTION_ANSWER
    context.user_data['new_answer'] = ans - 1
    await update.message.reply_text(
        "📖 <b>أدخل شرح السؤال</b> (أو أرسل <code>-</code> لتخطي):",
        parse_mode=ParseMode.HTML
    )
    return ADD_QUESTION_EXPLANATION

async def admin_add_explanation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    expl = update.message.text.strip()
    if expl == '-':
        expl = ''
    context.user_data['new_explanation'] = expl
    await update.message.reply_text(
        "📂 <b>أدخل الفئة</b> (أو أرسل <code>-</code> للفئة الافتراضية 'غير مصنف'):",
        parse_mode=ParseMode.HTML
    )
    return ADD_QUESTION_CATEGORY

async def admin_add_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cat = update.message.text.strip()
    if cat == '-':
        cat = 'غير مصنف'
    question = context.user_data['new_question']
    options = context.user_data['new_options']
    answer = context.user_data['new_answer']
    explanation = context.user_data['new_explanation']
    qid = add_question(question, options, answer, explanation, cat)
    await update.message.reply_text(
        f"✅ <b>تمت الإضافة بنجاح!</b>\nرقم السؤال: <code>{qid}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=build_main_menu(update.effective_user.id)
    )
    for key in ['new_question', 'new_options', 'new_answer', 'new_explanation']:
        context.user_data.pop(key, None)
    return ConversationHandler.END

async def admin_delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await query.edit_message_text("⛔ غير مصرح.")
        return
    await query.edit_message_text(
        "🗑 <b>حذف سؤال</b>\nأدخل رقم السؤال المراد حذفه:",
        parse_mode=ParseMode.HTML
    )
    return DELETE_QUESTION_STATE

async def admin_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        qid = int(update.message.text.strip())
    except:
        await update.message.reply_text("⚠️ رقم غير صحيح. حاول مرة أخرى:")
        return DELETE_QUESTION_STATE
    affected = delete_question(qid)
    if affected:
        await update.message.reply_text(
            f"✅ <b>تم حذف السؤال رقم {qid} بنجاح.</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=build_main_menu(update.effective_user.id)
        )
    else:
        await update.message.reply_text(
            f"⚠️ السؤال رقم {qid} غير موجود.",
            parse_mode=ParseMode.HTML,
            reply_markup=build_main_menu(update.effective_user.id)
        )
    return ConversationHandler.END

async def admin_edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await query.edit_message_text("⛔ غير مصرح.")
        return
    await query.edit_message_text(
        "✏️ <b>تعديل سؤال</b>\nأدخل رقم السؤال المراد تعديله:",
        parse_mode=ParseMode.HTML
    )
    return EDIT_QUESTION_STATE

async def admin_edit_get(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        qid = int(update.message.text.strip())
    except:
        await update.message.reply_text("⚠️ رقم غير صحيح. حاول مرة أخرى:")
        return EDIT_QUESTION_STATE
    q = get_question_by_id(qid)
    if not q:
        await update.message.reply_text("⚠️ السؤال غير موجود.")
        return ConversationHandler.END
    context.user_data['edit_qid'] = qid
    text = (
        f"📌 <b>السؤال الحالي (ID: {qid})</b>\n"
        f"السؤال: {escape_html(q[1])}\n"
        f"الخيارات: {', '.join(escape_html(opt) for opt in q[2])}\n"
        f"الإجابة الصحيحة: {q[3]+1} - {escape_html(q[2][q[3]])}\n"
        f"الشرح: {escape_html(q[4]) if q[4] else 'لا يوجد'}\n"
        f"الفئة: {escape_html(q[5])}\n\n"
        "أدخل البيانات الجديدة بالصيغة:\n"
        "<code>السؤال | الخيار1,خيار2,خيار3,خيار4 | رقم_الإجابة | الشرح | الفئة</code>\n"
        "(استخدم <code>-</code> لتخطي حقل معين)"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    return EDIT_QUESTION_STATE

async def admin_edit_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    parts = text.split('|')
    if len(parts) != 5:
        await update.message.reply_text(
            "⚠️ الصيغة غير صحيحة. يجب أن تحتوي على 5 حقول مفصولة بـ <code>|</code>.",
            parse_mode=ParseMode.HTML
        )
        return EDIT_QUESTION_STATE
    qid = context.user_data['edit_qid']
    q = get_question_by_id(qid)
    if not q:
        await update.message.reply_text("⚠️ السؤال الأصلي غير موجود.")
        return ConversationHandler.END
    question = parts[0].strip() if parts[0].strip() != '-' else q[1]
    options_str = parts[1].strip()
    if options_str != '-':
        options = [opt.strip() for opt in options_str.split(',') if opt.strip()]
        if len(options) < 2:
            await update.message.reply_text("⚠️ يجب أن يكون هناك خياران على الأقل.")
            return EDIT_QUESTION_STATE
    else:
        options = q[2]
    answer_str = parts[2].strip()
    if answer_str != '-':
        try:
            answer = int(answer_str) - 1
            if answer < 0 or answer >= len(options):
                raise ValueError
        except:
            await update.message.reply_text("⚠️ رقم الإجابة غير صحيح.")
            return EDIT_QUESTION_STATE
    else:
        answer = q[3]
    explanation = parts[3].strip() if parts[3].strip() != '-' else q[4]
    category = parts[4].strip() if parts[4].strip() != '-' else q[5]
    affected = update_question(qid, question, options, answer, explanation, category)
    if affected:
        await update.message.reply_text(
            f"✅ <b>تم تعديل السؤال رقم {qid} بنجاح.</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=build_main_menu(update.effective_user.id)
        )
    else:
        await update.message.reply_text("⚠️ حدث خطأ أثناء التحديث.")
    return ConversationHandler.END

async def admin_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await query.edit_message_text("⛔ غير مصرح.")
        return
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM questions")
    count = c.fetchone()[0]
    conn.close()
    await query.edit_message_text(
        f"📋 <b>إجمالي الأسئلة:</b> {count}\nاستخدم الأمر <code>/list_questions</code> لعرضها مع ترقيم.",
        parse_mode=ParseMode.HTML,
        reply_markup=build_back_button()
    )

async def list_questions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ غير مصرح.")
        return
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, question FROM questions ORDER BY id")
    rows = c.fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("لا توجد أسئلة.")
        return
    text = "📋 <b>قائمة الأسئلة:</b>\n"
    for qid, q in rows:
        text += f"<code>{qid}</code>: {escape_html(q[:50])}...\n"
    if len(text) > 4000:
        for i in range(0, len(text), 4000):
            await update.message.reply_text(text[i:i+4000], parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def admin_import_json(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await query.edit_message_text("⛔ غير مصرح.")
        return
    count = load_questions_from_json()
    await query.edit_message_text(
        f"✅ <b>تم استيراد {count} سؤال من ملفات JSON.</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=build_main_menu(user_id)
    )

# ======================== دوال التشغيل ========================
async def run_webhook_async(application):
    WEBHOOK_URL = os.getenv("WEBHOOK_URL")
    if not WEBHOOK_URL:
        logger.error("WEBHOOK_URL غير معرف")
        return

    try:
        from aiohttp import web
    except ImportError:
        logger.error("مكتبة aiohttp غير مثبتة")
        return

    await application.initialize()
    await application.start()

    async def webhook_handler(request):
        try:
            data = await request.json()
            if not data:
                return web.Response(text="Empty", status=400)
            from telegram import Update
            update = Update.de_json(data, application.bot)
            await application.process_update(update)
            return web.Response(text="OK", status=200)
        except Exception as e:
            logger.error(f"خطأ في webhook: {e}", exc_info=True)
            return web.Response(text=f"Error: {e}", status=500)

    async def health_check(request):
        return web.Response(text="OK", status=200)

    async def root(request):
        return web.Response(text="Bot is running", status=200)

    app = web.Application()
    app.router.add_get("/", root)
    app.router.add_get("/health", health_check)
    app.router.add_post(f"/{TOKEN}", webhook_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"خادم webhook يعمل على المنفذ {port}")

    webhook_url = f"{WEBHOOK_URL}/{TOKEN}"
    await application.bot.set_webhook(webhook_url)
    logger.info(f"تم تعيين webhook إلى {webhook_url}")

    await asyncio.Event().wait()

def main():
    init_db()
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM questions")
    if c.fetchone()[0] == 0:
        load_questions_from_json()
    conn.close()

    application = Application.builder().token(TOKEN).build()

    # إضافة المعالجات
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("shuffle", shuffle_command))
    application.add_handler(CommandHandler("list_questions", list_questions_command))
    application.add_handler(CommandHandler("stats", lambda u, c: stats(u, c)))

    # اختبار مخصص
    application.add_handler(CallbackQueryHandler(custom_quiz_start, pattern="^custom_quiz$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_quiz_input))

    # ConversationHandlers للإدارة
    admin_add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_start, pattern="^admin_add$")],
        states={
            ADD_QUESTION_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_question_text)],
            ADD_QUESTION_OPTIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_options)],
            ADD_QUESTION_ANSWER: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_answer)],
            ADD_QUESTION_EXPLANATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_explanation)],
            ADD_QUESTION_CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_category)],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
        per_user=True,
        per_chat=False,
        per_message=False,
    )
    application.add_handler(admin_add_conv)

    admin_delete_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_delete_start, pattern="^admin_delete$")],
        states={
            DELETE_QUESTION_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_delete_confirm)],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
        per_user=True,
        per_chat=False,
        per_message=False,
    )
    application.add_handler(admin_delete_conv)

    admin_edit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_edit_start, pattern="^admin_edit$")],
        states={
            EDIT_QUESTION_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_edit_get)],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
        per_user=True,
        per_chat=False,
        per_message=False,
    )
    application.add_handler(admin_edit_conv)

    # معالجات الأزرار الأساسية
    application.add_handler(CallbackQueryHandler(answer_callback, pattern="^answer_"))
    application.add_handler(CallbackQueryHandler(nav_callback, pattern="^nav_"))
    application.add_handler(CallbackQueryHandler(explain_callback, pattern="^show_explain_"))
    application.add_handler(CallbackQueryHandler(back_from_explain, pattern="^back_from_explain_"))
    application.add_handler(CallbackQueryHandler(bookmark_callback, pattern="^bookmark_"))
    application.add_handler(CallbackQueryHandler(show_full_options_callback, pattern="^show_full_opts_"))
    application.add_handler(CallbackQueryHandler(back_from_fullopts, pattern="^back_from_fullopts_"))
    
    # معالجات القائمة والإعدادات
    application.add_handler(CallbackQueryHandler(menu, pattern="^menu$"))
    application.add_handler(CallbackQueryHandler(start_quiz, pattern="^start_quiz$"))
    application.add_handler(CallbackQueryHandler(study_mode, pattern="^study_mode$"))
    application.add_handler(CallbackQueryHandler(exit_study, pattern="^exit_study$"))
    application.add_handler(CallbackQueryHandler(suggest_questions, pattern="^suggest$"))
    application.add_handler(CallbackQueryHandler(categories, pattern="^categories$"))
    application.add_handler(CallbackQueryHandler(filter_category, pattern="^filter_cat_"))
    application.add_handler(CallbackQueryHandler(bookmarks, pattern="^bookmarks$"))
    application.add_handler(CallbackQueryHandler(wrong_only, pattern="^wrong_only$"))
    application.add_handler(CallbackQueryHandler(stats, pattern="^stats$"))
    application.add_handler(CallbackQueryHandler(export_results, pattern="^export$"))
    application.add_handler(CallbackQueryHandler(reset, pattern="^reset$"))
    application.add_handler(CallbackQueryHandler(admin_panel, pattern="^admin_panel$"))
    application.add_handler(CallbackQueryHandler(admin_import_json, pattern="^admin_import_json$"))
    application.add_handler(CallbackQueryHandler(admin_list, pattern="^admin_list$"))
    application.add_handler(CallbackQueryHandler(lambda u, c: u.callback_query.answer(), pattern="^noop$"))

    USE_WEBHOOK = os.getenv("USE_WEBHOOK", "false").lower() == "true"

    if USE_WEBHOOK:
        asyncio.run(run_webhook_async(application))
    else:
        logger.info("تشغيل البوت باستخدام Polling")
        application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
