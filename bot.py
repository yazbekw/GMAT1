#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
بوت أسئلة شامل - نسخة محسنة مع معالجة أخطاء التحرير
"""

import os
import json
import sqlite3
import random
import logging
import asyncio
from datetime import datetime
from typing import List, Dict, Any, Optional, Union
from pathlib import Path

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
from telegram.error import BadRequest

# ============================================================
# التهيئة والإعدادات
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "bot_database.db")
QUESTIONS_DIR = os.path.join(BASE_DIR, "questions")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(","))) if os.getenv("ADMIN_IDS") else []
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("BOT_TOKEN not set in environment")

os.makedirs(QUESTIONS_DIR, exist_ok=True)

# ============================================================
# دوال مساعدة
# ============================================================
def escape_html(text: str) -> str:
    if not text:
        return ""
    return str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

def parse_options(options_raw: Union[dict, list]) -> List[str]:
    if isinstance(options_raw, dict):
        keys = sorted(options_raw.keys())
        return [str(options_raw[k]) for k in keys]
    elif isinstance(options_raw, list):
        return [str(opt) for opt in options_raw]
    return []

def parse_answer(answer_raw, options_count: int) -> Optional[int]:
    if isinstance(answer_raw, int) and 0 <= answer_raw < options_count:
        return answer_raw
    if isinstance(answer_raw, str) and answer_raw.isdigit():
        idx = int(answer_raw) - 1
        if 0 <= idx < options_count:
            return idx
    if isinstance(answer_raw, str) and len(answer_raw) == 1 and answer_raw.isalpha():
        idx = ord(answer_raw.upper()) - ord('A')
        if 0 <= idx < options_count:
            return idx
    return None

async def safe_edit_message(query, text, reply_markup=None, parse_mode=ParseMode.HTML):
    """تعديل آمن للرسالة يتجاهل خطأ 'Message is not modified'."""
    try:
        await query.edit_message_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    except BadRequest as e:
        if "Message is not modified" in str(e):
            # تجاهل الخطأ لأن المحتوى لم يتغير
            pass
        else:
            raise

# ============================================================
# قاعدة البيانات
# ============================================================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        question TEXT NOT NULL,
        options TEXT NOT NULL,
        answer INTEGER NOT NULL,
        explanation TEXT,
        category TEXT,
        source_file TEXT
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

# ============================================================
# تحميل الأسئلة
# ============================================================
def load_questions_from_files():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM questions")

    # البحث في المجلد الرئيسي ومجلد questions
    main_files = list(Path(BASE_DIR).glob("*.json"))
    if Path(QUESTIONS_DIR).exists():
        main_files.extend(list(Path(QUESTIONS_DIR).glob("*.json")))

    if not main_files:
        logger.warning("لا توجد ملفات JSON")
        return 0

    total_count = 0
    for file_path in main_files:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"خطأ في قراءة {file_path}: {e}")
            continue

        # استخراج قائمة الأسئلة من الملف
        questions_list = []
        if isinstance(data, list):
            questions_list = data
        elif isinstance(data, dict):
            if 'questions' in data:
                questions_list = data['questions']
            elif 'exam' in data and 'questions' in data['exam']:
                questions_list = data['exam']['questions']
            else:
                for val in data.values():
                    if isinstance(val, list) and val and isinstance(val[0], dict) and 'question' in val[0]:
                        questions_list = val
                        break

        if not questions_list:
            continue

        source_name = file_path.stem
        count = 0
        for q in questions_list:
            if not q.get('question') or not q.get('options'):
                continue

            # استخراج الخيارات
            options = parse_options(q['options'])
            if len(options) < 2:
                continue

            # استخراج الإجابة
            answer = parse_answer(q.get('answer'), len(options))

            # ===== التعديل الجديد لمعالجة Data Sufficiency =====
            # إذا كانت الخيارات تحتوي على مفتاحين "1" و "2" والإجابة رمز A-E
            if isinstance(q['options'], dict) and set(q['options'].keys()) == {"1", "2"}:
                ans_str = q.get('answer')
                if isinstance(ans_str, str) and ans_str.upper() in "ABCDE":
                    # استبدال الخيارات بالنصوص القياسية لـ DS
                    ds_options = [
                        "Statement (1) ALONE is sufficient, but statement (2) alone is not sufficient.",
                        "Statement (2) ALONE is sufficient, but statement (1) alone is not sufficient.",
                        "BOTH statements TOGETHER are sufficient, but NEITHER statement ALONE is sufficient.",
                        "EACH statement ALONE is sufficient.",
                        "Statements (1) and (2) TOGETHER are NOT sufficient."
                    ]
                    options = ds_options
                    answer = ord(ans_str.upper()) - ord('A')  # A=0, B=1, ...
            # =================================================

            if answer is None:
                continue

            explanation = q.get('explanation', '')
            category = q.get('category', source_name)

            c.execute(
                "INSERT INTO questions (question, options, answer, explanation, category, source_file) VALUES (?, ?, ?, ?, ?, ?)",
                (q['question'], json.dumps(options), answer, explanation, category, source_name)
            )
            count += 1

        total_count += count
        logger.info(f"تم تحميل {count} سؤال من {file_path}")

    conn.commit()
    conn.close()
    logger.info(f"إجمالي الأسئلة المحملة: {total_count}")
    return total_count

# ============================================================
# دوال استرجاع الأسئلة
# ============================================================
def get_all_questions():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, question, options, answer, explanation, category FROM questions")
    rows = c.fetchall()
    conn.close()
    return rows

def get_question_by_id(qid):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, question, options, answer, explanation, category FROM questions WHERE id=?", (qid,))
    row = c.fetchone()
    conn.close()
    if row:
        options = json.loads(row[2])
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
    return rows

def get_source_files():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT DISTINCT source_file FROM questions ORDER BY source_file")
    rows = c.fetchall()
    conn.close()
    return [row[0] for row in rows]

def get_questions_by_source(source):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, question, options, answer, explanation, category FROM questions WHERE source_file=?", (source,))
    rows = c.fetchall()
    conn.close()
    return rows

# ============================================================
# دوال حالة المستخدم
# ============================================================
def get_user_state(user_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT state FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    if row:
        state = json.loads(row[0])
        if 'used_questions' not in state:
            state['used_questions'] = []
        if 'bookmarks' not in state:
            state['bookmarks'] = []
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
            'quiz_count': 0,
            'quiz_minutes': 0,
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

# ============================================================
# دوال واجهة المستخدم
# ============================================================
def build_main_menu(user_id=None):
    buttons = [
        [InlineKeyboardButton("📝 اختبار عادي", callback_data="start_quiz")],
        [InlineKeyboardButton("📝 اختبار مخصص", callback_data="custom_quiz")],
        [InlineKeyboardButton("📖 وضع التعلم", callback_data="study_mode")],
        [InlineKeyboardButton("📂 تصفية حسب الفئة", callback_data="categories")],
        [InlineKeyboardButton("📁 تصفية حسب المصدر", callback_data="sources")],
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

def build_back_button(callback="menu", label="🔙 رجوع للقائمة"):
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=callback)]])

def build_result_buttons():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 اختبار جديد", callback_data="start_quiz")],
        [InlineKeyboardButton("📝 اختبار مخصص", callback_data="custom_quiz")],
        [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="menu")],
    ])

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
    buttons.append([InlineKeyboardButton("🏠 القائمة", callback_data="menu")])
    if state.get('mode') == 'study':
        buttons.append([InlineKeyboardButton("🔄 إنهاء وضع التعلم", callback_data="exit_study")])
    if time_left is not None and time_left > 0:
        mins, secs = divmod(int(time_left), 60)
        buttons.append([InlineKeyboardButton(f"⏱ {mins:02d}:{secs:02d}", callback_data="noop")])
    return InlineKeyboardMarkup(buttons)

def build_option_buttons(q, state):
    qid, question, options, answer, explanation, category = q
    buttons = []
    ans = state.get('answers', {})
    selected = ans.get(str(qid))
    answered = selected is not None

    for i, opt in enumerate(options):
        text = escape_html(opt)
        if answered:
            if i == answer:
                text = "✅ " + text
            elif i == selected:
                text = "❌ " + text
        callback = f"answer_{qid}_{i}" if not answered else "noop"
        buttons.append([InlineKeyboardButton(text, callback_data=callback)])
    return InlineKeyboardMarkup(buttons)

def format_question_header(q, idx, total, time_left=None):
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
    return text

# ============================================================
# معالجات الأوامر الأساسية
# ============================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    state = get_user_state(user_id)
    save_user_state(user_id, state)

    welcome_text = (
        f"👋 <b>أهلاً بك {escape_html(user.first_name)} في بوت الأسئلة الشامل!</b>\n\n"
        "✨ <b>المزايا:</b>\n"
        "• اختبارات عشوائية أو حسب الفئة/المصدر\n"
        "• اختبار مخصص بعدد أسئلة محدد ومؤقت زمني\n"
        "• وضع التعلم لعرض السؤال مع الإجابة والشرح فوراً\n"
        "• إشارات مرجعية للأسئلة المهمة\n"
        "• تتبع الأخطاء واقتراح أسئلة لنقاط الضعف\n"
        "• إحصائيات متقدمة وتصدير النتائج\n\n"
        "📌 <b>عدد الأسئلة المتاحة:</b> {}\n"
        "استخدم الأزرار أدناه للبدء 🚀"
    ).format(len(get_all_questions()))

    await update.message.reply_text(
        welcome_text,
        parse_mode=ParseMode.HTML,
        reply_markup=build_main_menu(user_id)
    )

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    await safe_edit_message(
        query,
        "🏠 <b>القائمة الرئيسية</b>",
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
        "/normal - العودة للوضع العادي\n\n"
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
    await update.message.reply_text(
        "🔀 <b>تم خلط الأسئلة.</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=build_back_button()
    )
    await show_current_question(update, context, user_id)

# ============================================================
# اختبار مخصص
# ============================================================
async def custom_quiz_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['awaiting_custom_quiz'] = True
    await safe_edit_message(
        query,
        "📝 <b>اختبار مخصص</b>\n\n"
        "أدخل عدد الأسئلة والمدة (بالدقائق) بالصيغة:\n"
        "<code>عدد_الأسئلة المدة</code>\n\n"
        "مثال: <code>10 5</code> (10 أسئلة، 5 دقائق)\n\n"
        "⚠️ سيتم اختيار الأسئلة من الأسئلة التي لم تجب عليها مسبقاً.\n\n"
        "للإلغاء أرسل <code>إلغاء</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ إلغاء", callback_data="menu")]
        ])
    )

async def handle_quiz_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('awaiting_custom_quiz'):
        return

    user_id = update.effective_user.id
    text = update.message.text.strip()

    if text.lower() == 'إلغاء':
        context.user_data['awaiting_custom_quiz'] = False
        await update.message.reply_text(
            "❌ تم إلغاء الاختبار.",
            reply_markup=build_main_menu(user_id)
        )
        return

    parts = text.split()
    if len(parts) != 2:
        await update.message.reply_text(
            "❌ الصيغة غير صحيحة. استخدم: <code>10 5</code>\nأو أرسل <code>إلغاء</code> للإلغاء.",
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
        await update.message.reply_text(f"⚠️ سيتم استخدام {count} سؤال (جميع الأسئلة المتاحة).")

    selected_qids = random.sample(available_qids, count)

    state = get_user_state(user_id)
    state['current_ids'] = selected_qids
    state['current_index'] = 0
    state['answers'] = {}
    state['mode'] = 'normal'
    state['quiz_time'] = minutes * 60
    state['start_time'] = datetime.now().isoformat()
    state['used_questions'] = state.get('used_questions', []) + selected_qids
    state['quiz_count'] = count
    state['quiz_minutes'] = minutes
    save_user_state(user_id, state)

    if context.job_queue:
        job = context.job_queue.run_once(
            quiz_timeout,
            minutes * 60,
            user_id=user_id,
            name=f"quiz_timeout_{user_id}"
        )
        context.user_data['timeout_job'] = job

    await update.message.reply_text(
        f"⏱ <b>بدء الاختبار:</b>\n"
        f"📝 عدد الأسئلة: {count}\n"
        f"⏳ المدة: {minutes} دقيقة\n\n"
        "حظاً موفقاً! 🍀",
        parse_mode=ParseMode.HTML
    )

    await show_current_question(update, context, user_id)

# ============================================================
# بدء الاختبار العادي
# ============================================================
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
    state['quiz_count'] = len(qids)
    state['quiz_minutes'] = 0
    save_user_state(user_id, state)
    await show_current_question(update, context, user_id)

# ============================================================
# عرض السؤال الحالي
# ============================================================
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
    header_text = format_question_header(q, idx, total, time_left)
    if show_explanation and q[4]:
        header_text += f"\n\n📖 <b>الشرح:</b>\n{escape_html(q[4])}"

    option_keyboard = build_option_buttons(q, state)
    nav_keyboard = build_question_keyboard(qid, idx, total, state, time_left)
    combined_keyboard = InlineKeyboardMarkup(
        option_keyboard.inline_keyboard + nav_keyboard.inline_keyboard
    )

    try:
        if update.callback_query:
            await safe_edit_message(
                update.callback_query,
                header_text,
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

# ============================================================
# انتهاء الاختبار
# ============================================================
async def send_quiz_complete(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id=None):
    if not user_id:
        if update.callback_query:
            user_id = update.callback_query.from_user.id
        elif update.message:
            user_id = update.effective_user.id
        else:
            return

    state = get_user_state(user_id)
    total = len(state['current_ids'])
    answered = len(state['answers'])
    correct = 0
    wrong = 0
    for qid_str, opt in state['answers'].items():
        qid = int(qid_str)
        q = get_question_by_id(qid)
        if q:
            if q[3] == opt:
                correct += 1
            else:
                wrong += 1

    time_used = "غير محدد"
    if state.get('start_time'):
        start = datetime.fromisoformat(state['start_time'])
        elapsed = (datetime.now() - start).total_seconds()
        mins, secs = divmod(int(elapsed), 60)
        time_used = f"{mins} دقيقة و {secs} ثانية"

    text = (
        f"🎉 <b>انتهى الاختبار!</b>\n\n"
        f"📊 <b>النتيجة:</b>\n"
        f"✅ صحيح: {correct}\n"
        f"❌ خطأ: {wrong}\n"
        f"📝 تم الإجابة على {answered}/{total}\n"
        f"🎯 النسبة: {(correct/total*100) if total > 0 else 0:.1f}%\n"
        f"⏱ الوقت المستغرق: {time_used}\n"
    )

    state['quiz_time'] = None
    state['start_time'] = None
    save_user_state(user_id, state)

    if update.callback_query:
        await safe_edit_message(
            update.callback_query,
            text,
            reply_markup=build_result_buttons()
        )
    else:
        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=build_result_buttons()
        )

# ============================================================
# معالجات الأزرار
# ============================================================
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
        parts = data.split("_")
        if len(parts) != 3:
            return
        idx = int(parts[2])
        state = get_user_state(user_id)
        if idx > 0:
            state['current_index'] = idx - 1
            save_user_state(user_id, state)
            await show_current_question(update, context, user_id)
        else:
            await query.answer("أنت في البداية!")
    elif data.startswith("nav_next_"):
        parts = data.split("_")
        if len(parts) != 3:
            return
        idx = int(parts[2])
        state = get_user_state(user_id)
        if idx < len(state['current_ids']) - 1:
            state['current_index'] = idx + 1
            save_user_state(user_id, state)
            await show_current_question(update, context, user_id)
        else:
            await query.answer("أنت في النهاية!")

async def explain_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("show_explain_"):
        qid = int(data.split("_")[2])
        q = get_question_by_id(qid)
        if q:
            expl = escape_html(q[4]) if q[4] else "لا يوجد شرح."
            text = f"📖 <b>الشرح:</b>\n{expl}"
            await safe_edit_message(
                query,
                text,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 رجوع للسؤال", callback_data=f"back_from_explain_{qid}")]
                ])
            )

async def back_from_explain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await show_current_question(update, context)

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

# ============================================================
# وضع التعلم
# ============================================================
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
    state['quiz_time'] = None
    state['start_time'] = None
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

# ============================================================
# اقتراح الأسئلة حسب نقاط الضعف
# ============================================================
async def suggest_questions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    weak_cats = get_weak_categories(user_id)
    if not weak_cats:
        await safe_edit_message(
            query,
            "🎉 <b>ممتاز!</b> ليس لديك نقاط ضعف واضحة. استمر في التدريب.",
            reply_markup=build_back_button()
        )
        return
    suggested = []
    for cat in weak_cats:
        qs = get_questions_by_category(cat)
        suggested.extend(qs)
    if not suggested:
        await safe_edit_message(
            query,
            "⚠️ لا توجد أسئلة في الفئات التي تحتاج تحسيناً.",
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
    state['quiz_time'] = None
    state['start_time'] = None
    save_user_state(user_id, state)
    await show_current_question(update, context, user_id)

# ============================================================
# إدارة المؤقت
# ============================================================
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
        f"⏰ <b>انتهى الوقت!</b>\n\n"
        f"📊 <b>النتيجة:</b>\n"
        f"✅ صحيح: {correct}\n"
        f"❌ خطأ: {answered - correct}\n"
        f"📝 تم الإجابة على {answered}/{total}\n"
        f"🎯 النسبة: {(correct/total*100) if total > 0 else 0:.1f}%\n"
    )

    state['quiz_time'] = None
    state['start_time'] = None
    save_user_state(user_id, state)

    await context.bot.send_message(
        user_id,
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=build_result_buttons()
    )

# ============================================================
# إحصائيات
# ============================================================
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
    for cat, vals in sorted(cat_stats.items()):
        c = vals['correct']
        w = vals['wrong']
        if c + w > 0:
            ratio = c / (c + w) * 100
            lines.append(f"• {escape_html(cat)}: {c}/{c+w} ({ratio:.0f}%)")

    if not cat_stats:
        lines.append("لا توجد بيانات كافية.")

    text = "\n".join(lines)
    await safe_edit_message(
        query,
        text,
        reply_markup=build_back_button()
    )

# ============================================================
# تصدير النتائج
# ============================================================
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
    writer.writerow(['رقم السؤال', 'السؤال', 'إجابتك', 'الإجابة الصحيحة', 'صحيح؟', 'الفئة'])

    for qid in state['current_ids']:
        q = get_question_by_id(qid)
        if not q:
            continue
        user_ans = state['answers'].get(str(qid))
        correct_ans = q[3]
        is_correct = log.get(qid, False)
        user_ans_text = q[2][user_ans] if user_ans is not None else 'لم يجب'
        correct_text = q[2][correct_ans] if correct_ans < len(q[2]) else ''
        writer.writerow([
            qid,
            q[1][:100] + '...' if len(q[1]) > 100 else q[1],
            user_ans_text,
            correct_text,
            'نعم' if is_correct else 'لا',
            q[5]
        ])

    output.seek(0)
    await query.message.reply_document(
        document=output.getvalue().encode('utf-8-sig'),
        filename=f"نتائجي_{user_id}.csv",
        caption="📥 <b>نتائجك</b>"
    )
    await safe_edit_message(
        query,
        "✅ تم التصدير بنجاح.",
        reply_markup=build_back_button()
    )

# ============================================================
# إعادة تعيين
# ============================================================
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
        'quiz_count': 0,
        'quiz_minutes': 0,
    }
    save_user_state(user_id, state)

    await safe_edit_message(
        query,
        "🔄 <b>تمت إعادة التعيين بنجاح.</b>\nتم حذف جميع الإجابات والإشارات.",
        reply_markup=build_main_menu(user_id)
    )

# ============================================================
# تصفية حسب الفئة والمصدر
# ============================================================
async def categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cats = get_categories()
    buttons = []
    row = []
    for cat in cats:
        row.append(InlineKeyboardButton(cat[:20], callback_data=f"filter_cat_{cat}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data="menu")])

    await safe_edit_message(
        query,
        "📂 <b>اختر فئة:</b>",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def filter_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    cat = query.data.split("_", 2)[2]
    qs = get_questions_by_category(cat)
    if not qs:
        await query.answer("لا توجد أسئلة في هذه الفئة.")
        return
    state = get_user_state(user_id)
    state['current_ids'] = [q[0] for q in qs]
    state['current_index'] = 0
    state['answers'] = {}
    state['mode'] = 'normal'
    state['quiz_time'] = None
    state['start_time'] = None
    save_user_state(user_id, state)
    await show_current_question(update, context, user_id)

async def sources(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    srcs = get_source_files()
    buttons = []
    row = []
    for src in srcs:
        row.append(InlineKeyboardButton(src, callback_data=f"filter_src_{src}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data="menu")])

    await safe_edit_message(
        query,
        "📁 <b>اختر المصدر:</b>",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def filter_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    src = query.data.split("_", 2)[2]
    qs = get_questions_by_source(src)
    if not qs:
        await query.answer("لا توجد أسئلة من هذا المصدر.")
        return
    state = get_user_state(user_id)
    state['current_ids'] = [q[0] for q in qs]
    state['current_index'] = 0
    state['answers'] = {}
    state['mode'] = 'normal'
    state['quiz_time'] = None
    state['start_time'] = None
    save_user_state(user_id, state)
    await show_current_question(update, context, user_id)

# ============================================================
# الإشارات المرجعية والأخطاء فقط
# ============================================================
async def bookmarks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    bookmarks = get_bookmarks(user_id)
    if not bookmarks:
        await safe_edit_message(
            query,
            "⭐ لا توجد إشارات مرجعية.",
            reply_markup=build_back_button()
        )
        return
    qs = []
    for qid in bookmarks:
        q = get_question_by_id(qid)
        if q:
            qs.append(q)
    if not qs:
        await safe_edit_message(
            query,
            "⚠️ بعض الإشارات غير صالحة.",
            reply_markup=build_back_button()
        )
        return
    state = get_user_state(user_id)
    state['current_ids'] = [q[0] for q in qs]
    state['current_index'] = 0
    state['answers'] = {}
    state['mode'] = 'normal'
    state['quiz_time'] = None
    state['start_time'] = None
    save_user_state(user_id, state)
    await show_current_question(update, context, user_id)

async def wrong_only(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    wrong_ids = get_wrong_questions(user_id)
    if not wrong_ids:
        await safe_edit_message(
            query,
            "🥳 <b>لا توجد أخطاء! أحسنت!</b>",
            reply_markup=build_back_button()
        )
        return
    qs = []
    for qid in wrong_ids:
        q = get_question_by_id(qid)
        if q:
            qs.append(q)
    if not qs:
        await safe_edit_message(
            query,
            "⚠️ لا توجد أسئلة خاطئة صالحة.",
            reply_markup=build_back_button()
        )
        return
    state = get_user_state(user_id)
    state['current_ids'] = [q[0] for q in qs]
    state['current_index'] = 0
    state['answers'] = {}
    state['mode'] = 'normal'
    state['quiz_time'] = None
    state['start_time'] = None
    save_user_state(user_id, state)
    await show_current_question(update, context, user_id)

# ============================================================
# دوال إدارة المشرف
# ============================================================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await safe_edit_message(query, "⛔ <b>غير مصرح لك.</b>", parse_mode=ParseMode.HTML)
        return
    buttons = [
        [InlineKeyboardButton("➕ إضافة سؤال", callback_data="admin_add")],
        [InlineKeyboardButton("🗑 حذف سؤال", callback_data="admin_delete")],
        [InlineKeyboardButton("✏️ تعديل سؤال", callback_data="admin_edit")],
        [InlineKeyboardButton("📋 عرض الأسئلة", callback_data="admin_list")],
        [InlineKeyboardButton("📥 إعادة تحميل الأسئلة", callback_data="admin_reload")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="menu")],
    ]
    await safe_edit_message(
        query,
        "⚙️ <b>لوحة تحكم المشرف</b>",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def admin_reload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await safe_edit_message(query, "⛔ غير مصرح.")
        return
    count = load_questions_from_files()
    await safe_edit_message(
        query,
        f"✅ <b>تم إعادة تحميل {count} سؤال من ملفات JSON.</b>",
        reply_markup=build_main_menu(user_id)
    )

async def admin_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await safe_edit_message(query, "⛔ غير مصرح.")
        return
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM questions")
    count = c.fetchone()[0]
    conn.close()
    await safe_edit_message(
        query,
        f"📋 <b>إجمالي الأسئلة في قاعدة البيانات:</b> {count}\n"
        f"📁 عدد الملفات المصدر: {len(get_source_files())}\n"
        f"📂 عدد الفئات: {len(get_categories())}",
        reply_markup=build_back_button()
    )

# ============================================================
# ConversationHandlers للإدارة
# ============================================================
ADD_QUESTION_STATE = 1
ADD_QUESTION_OPTIONS = 2
ADD_QUESTION_ANSWER = 3
ADD_QUESTION_EXPLANATION = 4
ADD_QUESTION_CATEGORY = 5
DELETE_QUESTION_STATE = 1
EDIT_QUESTION_STATE = 1

async def admin_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await safe_edit_message(query, "⛔ غير مصرح.")
        return
    await safe_edit_message(
        query,
        "📝 <b>إضافة سؤال جديد</b>\nأدخل نص السؤال:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="menu")]])
    )
    return ADD_QUESTION_STATE

async def admin_add_question_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("⚠️ السؤال لا يمكن أن يكون فارغاً. حاول مرة أخرى:")
        return ADD_QUESTION_STATE
    context.user_data['new_question'] = text
    await update.message.reply_text(
        "📋 <b>أدخل الخيارات مفصولة بفواصل</b>\nمثال: <code>خيار1, خيار2, خيار3, خيار4</code>\nأو أرسل <code>إلغاء</code> للإلغاء.",
        parse_mode=ParseMode.HTML
    )
    return ADD_QUESTION_OPTIONS

async def admin_add_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip().lower() == 'إلغاء':
        context.user_data.pop('new_question', None)
        await update.message.reply_text("❌ تم الإلغاء.", reply_markup=build_main_menu(update.effective_user.id))
        return ConversationHandler.END

    text = update.message.text.strip()
    options = [opt.strip() for opt in text.split(',') if opt.strip()]
    if len(options) < 2:
        await update.message.reply_text("⚠️ يجب أن يكون هناك خياران على الأقل. حاول مرة أخرى:")
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

    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        "INSERT INTO questions (question, options, answer, explanation, category, source_file) VALUES (?, ?, ?, ?, ?, ?)",
        (context.user_data['new_question'], json.dumps(context.user_data['new_options']),
         context.user_data['new_answer'], context.user_data['new_explanation'], cat, 'admin_added')
    )
    conn.commit()
    qid = c.lastrowid
    conn.close()

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
        await safe_edit_message(query, "⛔ غير مصرح.")
        return
    await safe_edit_message(
        query,
        "🗑 <b>حذف سؤال</b>\nأدخل رقم السؤال المراد حذفه:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="menu")]])
    )
    return DELETE_QUESTION_STATE

async def admin_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        qid = int(update.message.text.strip())
    except:
        await update.message.reply_text("⚠️ رقم غير صحيح. حاول مرة أخرى:")
        return DELETE_QUESTION_STATE

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM questions WHERE id=?", (qid,))
    affected = c.rowcount
    conn.commit()
    conn.close()

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
        await safe_edit_message(query, "⛔ غير مصرح.")
        return
    await safe_edit_message(
        query,
        "✏️ <b>تعديل سؤال</b>\nأدخل رقم السؤال المراد تعديله:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="menu")]])
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

    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        "UPDATE questions SET question=?, options=?, answer=?, explanation=?, category=? WHERE id=?",
        (question, json.dumps(options), answer, explanation, category, qid)
    )
    affected = c.rowcount
    conn.commit()
    conn.close()

    if affected:
        await update.message.reply_text(
            f"✅ <b>تم تعديل السؤال رقم {qid} بنجاح.</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=build_main_menu(update.effective_user.id)
        )
    else:
        await update.message.reply_text("⚠️ حدث خطأ أثناء التحديث.")
    return ConversationHandler.END

# ============================================================
# التشغيل
# ============================================================
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
        load_questions_from_files()
    conn.close()

    application = Application.builder().token(TOKEN).build()

    # الأوامر الأساسية
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("shuffle", shuffle_command))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("reset", reset))

    # اختبار مخصص
    application.add_handler(CallbackQueryHandler(custom_quiz_start, pattern="^custom_quiz$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_quiz_input))

    # إدارة المشرف (ConversationHandlers)
    application.add_handler(
        ConversationHandler(
            entry_points=[CallbackQueryHandler(admin_add_start, pattern="^admin_add$")],
            states={
                ADD_QUESTION_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_question_text)],
                ADD_QUESTION_OPTIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_options)],
                ADD_QUESTION_ANSWER: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_answer)],
                ADD_QUESTION_EXPLANATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_explanation)],
                ADD_QUESTION_CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_category)],
            },
            fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
        )
    )

    application.add_handler(
        ConversationHandler(
            entry_points=[CallbackQueryHandler(admin_delete_start, pattern="^admin_delete$")],
            states={
                DELETE_QUESTION_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_delete_confirm)],
            },
            fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
        )
    )

    application.add_handler(
        ConversationHandler(
            entry_points=[CallbackQueryHandler(admin_edit_start, pattern="^admin_edit$")],
            states={
                EDIT_QUESTION_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_edit_get)],
            },
            fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
        )
    )

    # معالجات الأزرار الأساسية
    application.add_handler(CallbackQueryHandler(answer_callback, pattern="^answer_"))
    application.add_handler(CallbackQueryHandler(nav_callback, pattern="^nav_"))
    application.add_handler(CallbackQueryHandler(explain_callback, pattern="^show_explain_"))
    application.add_handler(CallbackQueryHandler(back_from_explain, pattern="^back_from_explain_"))
    application.add_handler(CallbackQueryHandler(bookmark_callback, pattern="^bookmark_"))

    # معالجات القائمة
    application.add_handler(CallbackQueryHandler(menu, pattern="^menu$"))
    application.add_handler(CallbackQueryHandler(start_quiz, pattern="^start_quiz$"))
    application.add_handler(CallbackQueryHandler(study_mode, pattern="^study_mode$"))
    application.add_handler(CallbackQueryHandler(exit_study, pattern="^exit_study$"))
    application.add_handler(CallbackQueryHandler(suggest_questions, pattern="^suggest$"))
    application.add_handler(CallbackQueryHandler(categories, pattern="^categories$"))
    application.add_handler(CallbackQueryHandler(filter_category, pattern="^filter_cat_"))
    application.add_handler(CallbackQueryHandler(sources, pattern="^sources$"))
    application.add_handler(CallbackQueryHandler(filter_source, pattern="^filter_src_"))
    application.add_handler(CallbackQueryHandler(bookmarks, pattern="^bookmarks$"))
    application.add_handler(CallbackQueryHandler(wrong_only, pattern="^wrong_only$"))
    application.add_handler(CallbackQueryHandler(stats, pattern="^stats$"))
    application.add_handler(CallbackQueryHandler(export_results, pattern="^export$"))
    application.add_handler(CallbackQueryHandler(reset, pattern="^reset$"))

    # لوحة المشرف
    application.add_handler(CallbackQueryHandler(admin_panel, pattern="^admin_panel$"))
    application.add_handler(CallbackQueryHandler(admin_reload, pattern="^admin_reload$"))
    application.add_handler(CallbackQueryHandler(admin_list, pattern="^admin_list$"))

    # لا تفعل شيئاً
    application.add_handler(CallbackQueryHandler(lambda u, c: u.callback_query.answer(), pattern="^noop$"))

    USE_WEBHOOK = os.getenv("USE_WEBHOOK", "false").lower() == "true"

    if USE_WEBHOOK:
        asyncio.run(run_webhook_async(application))
    else:
        logger.info("تشغيل البوت باستخدام Polling")
        application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
