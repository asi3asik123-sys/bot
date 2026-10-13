import os
import sqlite3
import logging
from contextlib import closing
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, CopyTextButton, ForceReply, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN', '').strip()
ADMIN_IDS = {int(x.strip()) for x in os.getenv('ADMIN_IDS', '').split(',') if x.strip().isdigit()}
DB_PATH = os.getenv('DB_PATH', 'owlproxy.db')
CURRENCY = os.getenv('CURRENCY', 'USD')
BOT_NAME = os.getenv('BOT_NAME', 'Proxy Shop')
# Payment-proof group configured for this bot. No /setpaymentgroup command is required.
PAYMENT_GROUP_ID = int(os.getenv('PAYMENT_GROUP_ID', '-1004380707014'))
WELCOME = os.getenv('WELCOME_MESSAGE', f'👋 Welcome to {BOT_NAME}!')
PAYMENT_INFO = os.getenv('PAYMENT_INFO', 'Contact admin for payment instructions.')
DEFAULT_PACKAGE_NAME = os.getenv('DEFAULT_PACKAGE_NAME', 'Standard Proxy')
DEFAULT_PACKAGE_PRICE = float(os.getenv('PROXY_PRICE', '5'))
MIN_DEPOSIT = float(os.getenv('MIN_DEPOSIT', '10'))
BINANCE_USDT_RATE = float(os.getenv('BINANCE_USDT_RATE', '120'))  # BDT per 1 USDT
BKASH_NUMBER = os.getenv('BKASH_NUMBER', '01865856607').strip()
BINANCE_PAY_ID = os.getenv('BINANCE_PAY_ID', '1016345361').strip()

logging.basicConfig(format='%(asctime)s | %(levelname)s | %(name)s | %(message)s', level=logging.INFO)
log = logging.getLogger('proxybot')

def db(): return sqlite3.connect(DB_PATH)

def init_db():
    with closing(db()) as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, balance REAL NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS packages(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, price REAL NOT NULL, description TEXT DEFAULT '', active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS stock(id INTEGER PRIMARY KEY AUTOINCREMENT, proxy TEXT NOT NULL UNIQUE, package_id INTEGER, status TEXT NOT NULL DEFAULT 'available', sold_to INTEGER, sold_at TEXT);
        CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, package_id INTEGER, quantity INTEGER NOT NULL DEFAULT 1, price REAL NOT NULL, proxy TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, amount REAL NOT NULL, method TEXT NOT NULL, reference TEXT DEFAULT '', proof_file_id TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        ''')
        # Migrate old stock/orders databases safely.
        pcols = {r[1] for r in con.execute('PRAGMA table_info(payments)')}
        if 'proof_file_id' not in pcols: con.execute("ALTER TABLE payments ADD COLUMN proof_file_id TEXT DEFAULT ''")
        cols = {r[1] for r in con.execute('PRAGMA table_info(stock)')}
        if 'package_id' not in cols: con.execute('ALTER TABLE stock ADD COLUMN package_id INTEGER')
        ocols = {r[1] for r in con.execute('PRAGMA table_info(orders)')}
        if 'package_id' not in ocols: con.execute('ALTER TABLE orders ADD COLUMN package_id INTEGER')
        if 'quantity' not in ocols: con.execute('ALTER TABLE orders ADD COLUMN quantity INTEGER NOT NULL DEFAULT 1')
        if not con.execute('SELECT 1 FROM packages LIMIT 1').fetchone():
            con.execute('INSERT INTO packages(name,price,description) VALUES(?,?,?)',(DEFAULT_PACKAGE_NAME,DEFAULT_PACKAGE_PRICE,'Default package'))
        default_pid = con.execute('SELECT id FROM packages ORDER BY id LIMIT 1').fetchone()[0]
        con.execute('UPDATE stock SET package_id=? WHERE package_id IS NULL',(default_pid,))
        con.commit()

def ensure_user(u):
    with closing(db()) as con:
        con.execute('''INSERT INTO users(user_id,username,first_name) VALUES(?,?,?)
                       ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name''',(u.id,u.username or '',u.first_name or ''))
        con.commit()

def is_admin(uid): return uid in ADMIN_IDS

def balance(uid):
    with closing(db()) as con:
        r=con.execute('SELECT balance FROM users WHERE user_id=?',(uid,)).fetchone(); return float(r[0]) if r else 0

def available(package_id=None):
    with closing(db()) as con:
        if package_id:
            return con.execute("SELECT COUNT(*) FROM stock WHERE status='available' AND package_id=?",(package_id,)).fetchone()[0]
        return con.execute("SELECT COUNT(*) FROM stock WHERE status='available'").fetchone()[0]

def main_kb(uid):
    rows=[[InlineKeyboardButton('💎 Buy Proxy',callback_data='buy')],[InlineKeyboardButton('💳 Balance',callback_data='bal'),InlineKeyboardButton('✦ Add Balance',callback_data='addbal')],[InlineKeyboardButton('◈ My Orders',callback_data='orders'),InlineKeyboardButton('◉ Stock',callback_data='stock')],[InlineKeyboardButton('♟ My Profile',callback_data='profile')]]
    if is_admin(uid): rows.append([InlineKeyboardButton('♛ Admin Panel',callback_data='admin')])
    return InlineKeyboardMarkup(rows)

def back_home(): return InlineKeyboardMarkup([[InlineKeyboardButton('⌂ Main Menu',callback_data='home')]])

def reply_menu(uid):
    rows=[
        ['💎 Buy Proxy','◉ Stock'],
        ['💳 Balance','✦ Add Balance'],
        ['◈ My Orders','♟ My Profile'],
    ]
    if is_admin(uid):
        rows.append(['♛ Admin Panel'])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)

async def admin_menu_message(update, context):
    if not is_admin(update.effective_user.id):
        return
    kb=[[InlineKeyboardButton('📦 Stock',callback_data='astock'),InlineKeyboardButton('➕ Add Stock',callback_data='aaddstock')],[InlineKeyboardButton('◉ Broadcast',callback_data='broadcast'),InlineKeyboardButton('💵 Packages',callback_data='apkg')],[InlineKeyboardButton('💳 Payments',callback_data='apay')],[InlineKeyboardButton('👥 Users',callback_data='ausers'),InlineKeyboardButton('🧾 Orders',callback_data='aorders')],[InlineKeyboardButton('⌂ Main Menu',callback_data='home')]]
    await update.effective_message.reply_text(
        '🔐 <b>ADMIN CONTROL CENTER</b>\n\n'
        '📦 Stock management\n'
        '💳 Payments & balance\n'
        '👥 Users & orders\n'
        '📢 Broadcast & packages\n\n'
        'Select an action below.',
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(kb)
    )


async def start(update, context):
    ensure_user(update.effective_user)
    uid=update.effective_user.id
    premium_text=(
        f'✨ <b>{BOT_NAME.upper()}</b> ✨\n\n'
        f'👋 Welcome, <b>{update.effective_user.first_name or "there"}</b>!\n\n'
        '🛡️ <b>Premium Proxy Store</b>\n'
        '⚡ Fast delivery • 🔒 Secure service\n\n'
        '👇 Choose an option from the menu below.'
    )
    await update.effective_message.reply_text(
        premium_text,
        parse_mode='HTML',
        reply_markup=reply_menu(uid)
    )

async def show_balance(update, context):
    uid=update.effective_user.id; ensure_user(update.effective_user)
    bal=balance(uid)
    usdt=bal if CURRENCY.upper() == 'USDT' else 0.0
    text=(
        '✦ ━━━━━━━ ◈ ━━━━━━━ ✦\n'
        '        💎 <b>𝐌𝐘 𝐖𝐀𝐋𝐋𝐄𝐓</b>\n\n'
        '   💰 <b>𝐁𝐃𝐓 𝐁𝐀𝐋𝐀𝐍𝐂𝐄</b>\n'
        f'       <b>{bal:.2f} BDT</b>\n\n'
        '   💠 <b>𝐔𝐒𝐃𝐓 𝐁𝐀𝐋𝐀𝐍𝐂𝐄</b>\n'
        f'       <b>{usdt:.2f} USDT</b>\n\n'
        '✦ ━━━━━━━ ◈ ━━━━━━━ ✦'
    )
    await update.effective_message.reply_text(text,parse_mode='HTML',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('✦ Add Balance',callback_data='addbal')],[InlineKeyboardButton('⌂ Main Menu',callback_data='home')]]))

async def show_stock(update, context):
    with closing(db()) as con:
        rows=con.execute('''SELECT p.id,p.name,p.price,COUNT(s.id) FROM packages p LEFT JOIN stock s ON s.package_id=p.id AND s.status='available' WHERE p.active=1 GROUP BY p.id ORDER BY p.id''').fetchall()
    text='<b>📊 AVAILABLE STOCK</b>\n\n' + ('\n'.join(f'<b>• {n} — {c} available — {pr:.2f} {CURRENCY}</b>' for _,n,pr,c in rows) if rows else '<b>No active packages.</b>')
    await update.effective_message.reply_text(text,parse_mode='HTML',reply_markup=back_home())

async def buy_menu(update, context):
    with closing(db()) as con: rows=con.execute('SELECT id,name,price FROM packages WHERE active=1 ORDER BY id').fetchall()
    if not rows: return await update.effective_message.reply_text('❌ No packages are available.',reply_markup=back_home())
    kb=[]
    for pid,name,price in rows:
        c=available(pid); kb.append([InlineKeyboardButton(f'📦 {name} — {price:.2f} {CURRENCY} ({c})',callback_data=f'pkg:{pid}')])
    kb.append([InlineKeyboardButton('⌂ Main Menu',callback_data='home')])
    await update.effective_message.reply_text('<b>🛒 SELECT A PACKAGE</b>',parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

async def package_menu(q, pid):
    with closing(db()) as con: p=con.execute('SELECT name,price,description FROM packages WHERE id=? AND active=1',(pid,)).fetchone()
    if not p: return await q.message.reply_text('❌ Package not found.',reply_markup=back_home())
    name,price,desc=p; c=available(pid)
    kb=[]
    for n in range(1,min(c,10)+1): kb.append([InlineKeyboardButton(f'Buy {n} × {price:.2f} = {n*price:.2f} {CURRENCY}',callback_data=f'qty:{pid}:{n}')])
    kb.append([InlineKeyboardButton('↩️ Packages',callback_data='buy'),InlineKeyboardButton('🏠 Menu',callback_data='home')])
    await q.message.edit_text(f'<b>📦 {name}</b>\n\n<b>{desc}</b>\n\n<b>💵 Unit price: {price:.2f} {CURRENCY}</b>\n<b>📊 Available: {c}</b>\n\n<b>Select quantity:</b>',parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

async def purchase(q, pid, qty):
    uid=q.from_user.id; ensure_user(q.from_user)
    with closing(db()) as con:
        try:
            con.execute('BEGIN IMMEDIATE')
            p=con.execute('SELECT name,price FROM packages WHERE id=? AND active=1',(pid,)).fetchone()
            if not p: raise ValueError('Package unavailable')
            name,unit=p; total=unit*qty
            if balance(uid)<total: con.rollback(); return await q.message.reply_text(f'❌ Insufficient balance.\nRequired: {total:.2f} {CURRENCY}\nBalance: {balance(uid):.2f} {CURRENCY}',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('✦ Add Balance',callback_data='addbal')],[InlineKeyboardButton('🛒 Packages',callback_data='buy')]]))
            rows=con.execute("SELECT id,proxy FROM stock WHERE status='available' AND package_id=? ORDER BY id LIMIT ?",(pid,qty)).fetchall()
            if len(rows)<qty: con.rollback(); return await q.message.reply_text('❌ Not enough stock for this quantity.',reply_markup=back_home())
            con.execute('UPDATE users SET balance=balance-? WHERE user_id=?',(total,uid))
            proxies=[]
            for sid,proxy in rows:
                con.execute("UPDATE stock SET status='sold',sold_to=?,sold_at=CURRENT_TIMESTAMP WHERE id=?",(uid,sid)); proxies.append(proxy)
            con.execute('INSERT INTO orders(user_id,package_id,quantity,price,proxy) VALUES(?,?,?,?,?)',(uid,pid,qty,total,'\n'.join(proxies)))
            oid=con.execute('SELECT last_insert_rowid()').fetchone()[0]; con.commit()
        except Exception as e:
            con.rollback(); log.exception('purchase failed'); return await q.message.reply_text(f'❌ Purchase failed: {e}',reply_markup=back_home())
    await q.message.reply_text(f'✅ Purchase successful!\n\n🧾 Order #{oid}\n📦 {name}\n🔢 Quantity: {qty}\n💵 Paid: {total:.2f} {CURRENCY}\n💰 Remaining: {balance(uid):.2f} {CURRENCY}\n\n🔑 Proxies:\n`{chr(10).join(proxies)}`',parse_mode='Markdown',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◈ My Orders',callback_data='orders')],[InlineKeyboardButton('⌂ Main Menu',callback_data='home')]]))

async def orders(update, context):
    uid=update.effective_user.id; ensure_user(update.effective_user)
    with closing(db()) as con: rows=con.execute('''SELECT o.id,p.name,o.quantity,o.price,o.proxy,o.created_at FROM orders o LEFT JOIN packages p ON p.id=o.package_id WHERE o.user_id=? ORDER BY o.id DESC LIMIT 20''',(uid,)).fetchall()
    if not rows: return await update.effective_message.reply_text('📦 You have no orders yet.',reply_markup=back_home())
    kb=[[InlineKeyboardButton(f'🧾 Order #{r[0]} — {r[3]:.2f} {CURRENCY}',callback_data=f'order:{r[0]}')] for r in rows]; kb.append([InlineKeyboardButton('⌂ Main Menu',callback_data='home')])
    await update.effective_message.reply_text('<b>📦 MY ORDERS</b>\n\n<b>Select an order for details:</b>',parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

async def order_detail(q, oid):
    with closing(db()) as con: r=con.execute('''SELECT o.id,p.name,o.quantity,o.price,o.proxy,o.created_at FROM orders o LEFT JOIN packages p ON p.id=o.package_id WHERE o.id=? AND o.user_id=?''',(oid,q.from_user.id)).fetchone()
    if not r: return await q.message.reply_text('❌ Order not found.',reply_markup=back_home())
    await q.message.reply_text(f'🧾 Order #{r[0]}\n\n📦 Package: {r[1] or "Legacy"}\n🔢 Quantity: {r[2]}\n💵 Total: {r[3]:.2f} {CURRENCY}\n🕒 Date: {r[5]}\n\n🔑 Proxy(s):\n`{r[4]}`',parse_mode='Markdown',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('↩️ Orders',callback_data='orders')],[InlineKeyboardButton('⌂ Main Menu',callback_data='home')]]))

async def profile(update, context):
    u=update.effective_user; ensure_user(u)
    with closing(db()) as con: r=con.execute('SELECT created_at FROM users WHERE user_id=?',(u.id,)).fetchone(); count=con.execute('SELECT COUNT(*) FROM orders WHERE user_id=?',(u.id,)).fetchone()[0]
    await update.effective_message.reply_text(f'👤 Profile\n\n🆔 User ID: `{u.id}`\n👤 Username: @{u.username or "N/A"}\n💰 Balance: {balance(u.id):.2f} {CURRENCY}\n📦 Orders: {count}\n📅 Joined: {r[0]}',parse_mode='Markdown',reply_markup=back_home())

async def add_balance_menu(update, context):
    # One clean payment menu. Payment screens are edited in-place to avoid message spam.
    kb=[
        [InlineKeyboardButton('🟢 bKash',callback_data='paymethod:bkash')],
        [InlineKeyboardButton('🟡 Binance',callback_data='paymethod:binance')],
        [InlineKeyboardButton('⌂ Main Menu',callback_data='home')]
    ]
    await update.effective_message.reply_text(
        f'💰 ADD BALANCE\n\n'
        f'<b>⭐ সর্বনিম্ন ডিপোজিট: {MIN_DEPOSIT:.0f} টাকা</b>\n\n'
        f'<b>নিচের payment method থেকে একটি নির্বাচন করুন 👇</b>',
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(kb)
    )

def payment_menu_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('🟢 bKash',callback_data='paymethod:bkash')],
        [InlineKeyboardButton('🟡 Binance',callback_data='paymethod:binance')],
        [InlineKeyboardButton('⌂ Main Menu',callback_data='home')]
    ])

def bkash_markup():
    # Telegram CopyTextButton copies the number directly to the user's clipboard.
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('📋 Copy bKash Number', copy_text=CopyTextButton(BKASH_NUMBER))],
        [InlineKeyboardButton('💰 Enter Amount',callback_data='enteramount:bkash')],
        [InlineKeyboardButton('‹ Back',callback_data='payback')]
    ])

def binance_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('📋 Copy Binance Pay ID', copy_text=CopyTextButton(BINANCE_PAY_ID))],
        [InlineKeyboardButton('💰 Enter Amount',callback_data='enteramount:binance')],
        [InlineKeyboardButton('‹ Back',callback_data='payback')]
    ])

async def payment_method(q,method):
    # Clear any unfinished amount prompt when changing method.
    if method=='bkash':
        text=(
            '🟢 bKash PAYMENT\n\n'
            f'📱 bKash Number: {BKASH_NUMBER}\n'
            '📋 উপরের Copy button চাপলে number copy হবে।\n\n'
            f'💰 সর্বনিম্ন ডিপোজিট: {MIN_DEPOSIT:.0f} টাকা\n'
            '💸 এই নম্বরে Send Money করুন।\n\n'
            '1️⃣ প্রথমে নিচের 💰 Enter Amount button চাপুন।\n'
            f'উদাহরণ: {MIN_DEPOSIT:.0f}\n\n'
            '2️⃣ Amount গ্রহণ হলে bot payment proof screenshot চাইবে।\n'
            '3️⃣ তারপর payment-এর screenshot/photo পাঠান।'
        )
        await q.message.edit_text('<b>'+text+'</b>',parse_mode='HTML',reply_markup=bkash_markup())
        # Store the selected method and prompt state for this user.
        # The actual amount is accepted only through the text-router.
        return
    else:
        text=(
            '🟡 Binance PAYMENT\n\n'
            f'🆔 Binance Pay ID: {BINANCE_PAY_ID}\n'
            '📋 উপরের Copy button চাপলে Pay ID copy হবে।\n\n'
            f'💰 সর্বনিম্ন ডিপোজিট: {MIN_DEPOSIT:.0f} টাকা\n'
            '💸 উপরের Pay ID-তে payment করুন।\n\n'
            '1️⃣ প্রথমে নিচের 💰 Enter Amount button চাপুন।\n'
            f'উদাহরণ: {MIN_DEPOSIT:.0f}\n\n'
            '2️⃣ Amount গ্রহণ হলে bot payment proof screenshot চাইবে।\n'
            '3️⃣ তারপর payment-এর screenshot/photo পাঠান।'
        )
        await q.message.edit_text('<b>'+text+'</b>',parse_mode='HTML',reply_markup=binance_markup())
        return

async def payment_back(q):
    # Remove the payment card and return to a single clean method menu.
    try:
        await q.message.delete()
    except Exception:
        try:
            await q.message.edit_text(
                f'💰 ADD BALANCE\n\n⭐ সর্বনিম্ন ডিপোজিট: {MIN_DEPOSIT:.0f} টাকা\n\nনিচের payment method থেকে একটি নির্বাচন করুন 👇',
                reply_markup=payment_menu_markup()
            )
            return
        except Exception:
            return
    await q.message.chat.send_message(
        f'💰 ADD BALANCE\n\n⭐ সর্বনিম্ন ডিপোজিট: {MIN_DEPOSIT:.0f} টাকা\n\nনিচের payment method থেকে একটি নির্বাচন করুন 👇',
        reply_markup=payment_menu_markup()
    )

async def amount_prompt(update, context, method):
    # Start a fresh deposit. ForceReply makes Telegram open Reply mode for this exact message.
    msg = update.effective_message
    if not msg:
        return
    prompt = await msg.reply_text(
        f'🟢━━━━━━━━━━━━━━━━━━🟢\n'
        f'💰 {method} AMOUNT\n'
        f'🟢━━━━━━━━━━━━━━━━━━🟢\n\n'
        f'<b>⭐ সর্বনিম্ন ডিপোজিট: {MIN_DEPOSIT:.0f} টাকা</b>\n\n'
        '📌 এই 🟢 MARK করা message-এ Reply করুন।\n'
        '✍️ Reply করে শুধু আপনার payment amount লিখুন।\n\n'
        f'👉 উদাহরণ: {MIN_DEPOSIT:.0f}',
        reply_markup=ForceReply(selective=True, input_field_placeholder='Amount লিখুন')
    )
    context.user_data['await_payment_amount'] = {
        'method': method,
        'prompt_message_id': prompt.message_id
    }

async def amount_text(update, context):
    state = context.user_data.get('await_payment_amount')
    if not state or not update.message or not update.message.text:
        return False

    # Require a reply to the bot's amount prompt, as requested.
    replied = update.message.reply_to_message
    if not replied or replied.message_id != state.get('prompt_message_id'):
        await update.message.reply_text(
            '⚠️ Amount দিতে হলে উপরের 🟢 MARK করা bot message-এ Reply করে শুধু amount লিখুন।\n'
            f'উদাহরণ: {MIN_DEPOSIT:.0f}'
        )
        return True

    raw = update.message.text.strip().replace(',', '')
    try:
        amount = float(raw)
        if amount < MIN_DEPOSIT:
            raise ValueError('minimum')
        if amount <= 0 or amount > 100000000:
            raise ValueError('range')
    except ValueError as e:
        if str(e) == 'minimum':
            await update.message.reply_text(
                f'❌ Minimum deposit {MIN_DEPOSIT:.0f} টাকা.\n'
                f'{MIN_DEPOSIT:.0f} টাকার নিচে deposit করা যাবে না।\n\n'
                f'আবার Reply করে amount লিখুন।'
            )
        else:
            await update.message.reply_text('❌ সঠিক amount দিন। উদাহরণ: 10 বা 100')
        return True

    method = state['method']
    context.user_data.pop('await_payment_amount', None)

    with closing(db()) as con:
        con.execute(
            'INSERT INTO payments(user_id,amount,method,reference) VALUES(?,?,?,?)',
            (update.effective_user.id, amount, method, '')
        )
        pid = con.execute('SELECT last_insert_rowid()').fetchone()[0]
        con.commit()

    context.user_data['await_payment_proof'] = pid
    if method == 'binance':
        usdt_amount = amount / BINANCE_USDT_RATE
        amount_text = (
            '✦ ━━━━━━━ ◈ ━━━━━━━ ✦\n'
            '💎 <b>𝐁𝐈𝐍𝐀𝐍𝐂𝐄 𝐃𝐄𝐏𝐎𝐒𝐈𝐓</b>\n\n'
            f'💰 <b>𝐁𝐃𝐓 𝐀𝐌𝐎𝐔𝐍𝐓:</b> {amount:.2f} BDT\n'
            f'💠 <b>𝐔𝐒𝐃𝐓 𝐀𝐌𝐎𝐔𝐍𝐓:</b> {usdt_amount:.4f} USDT\n'
            f'◇ <b>𝐑𝐀𝐓𝐄:</b> 1 USDT = {BINANCE_USDT_RATE:.2f} BDT\n\n'
            f'🧾 <b>Payment #{pid}</b>\n'
            '✦ ━━━━━━━ ◈ ━━━━━━━ ✦\n\n'
            '📸 এখন আপনার payment-এর <b>screenshot/proof photo</b> পাঠান।\n'
            '⚠️ Screenshot না পাঠালে admin payment approve করতে পারবে না।'
        )
        await update.message.reply_text(amount_text, parse_mode='HTML')
    else:
        await update.message.reply_text(
            f'✅ Amount received: {amount:.2f} {CURRENCY}\n\n'
            f'🧾 Payment #{pid}\n'
            f'💳 Method: {method}\n\n'
            '📸 এখন আপনার payment-এর **screenshot/proof photo** পাঠান।\n'
            '⚠️ Screenshot না পাঠালে admin payment approve করতে পারবে না।',
            parse_mode='Markdown'
        )
    return True

async def get_payment_group_id():
    # Use the saved group if present; otherwise use the configured default group.
    with closing(db()) as con:
        row=con.execute("SELECT value FROM settings WHERE key='payment_group_id'").fetchone()
        return int(row[0]) if row else PAYMENT_GROUP_ID

async def set_payment_group(update, context):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text('⛔ Admin only.')
    chat=update.effective_chat
    if not chat or chat.type not in ('group','supergroup'):
        return await update.message.reply_text('⚠️ এই command-টি payment group-এর ভিতর থেকে দিন।')
    with closing(db()) as con:
        con.execute("INSERT INTO settings(key,value) VALUES('payment_group_id',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(chat.id),))
        con.commit()
    await update.message.reply_text(f'✅ Payment group set successfully.\n🆔 Group ID: {chat.id}')

async def pay_cmd(update, context):
    ensure_user(update.effective_user)
    if len(context.args)<2:
        return await update.message.reply_text('Usage: /pay AMOUNT METHOD [REFERENCE]\n\nExample: /pay 100 bKash TX12345\n\nএরপর অবশ্যই payment proof screenshot পাঠাতে হবে।')
    try:
        amount=float(context.args[0]); assert amount>0
    except:
        return await update.message.reply_text('❌ Invalid amount.')
    method=context.args[1]; ref=' '.join(context.args[2:]) if len(context.args)>2 else ''
    group_id=await get_payment_group_id()
    if not group_id:
        return await update.message.reply_text('⚠️ Payment group এখনো সেট করা হয়নি। Admin-কে payment group-এ /setpaymentgroup দিতে বলুন।')
    with closing(db()) as con:
        con.execute('INSERT INTO payments(user_id,amount,method,reference) VALUES(?,?,?,?)',(update.effective_user.id,amount,method,ref))
        pid=con.execute('SELECT last_insert_rowid()').fetchone()[0]; con.commit()
    await update.message.reply_text(
        f'📝 Payment request #{pid} created.\n\n💰 Amount: {amount:.2f} {CURRENCY}\n💳 Method: {method}\n🔢 Reference: {ref or "N/A"}\n\n📸 এখন এই payment-এর proof screenshot পাঠান।\n⚠️ Screenshot না দিলে admin-এর কাছে request যাবে না.'
    )

async def payment_proof_photo(update, context):
    if not update.message or not update.message.photo:
        return False

    uid=update.effective_user.id
    expected_pid=context.user_data.get('await_payment_proof')

    with closing(db()) as con:
        if expected_pid:
            row=con.execute(
                "SELECT id,amount,method,reference FROM payments "
                "WHERE id=? AND user_id=? AND status='pending' AND COALESCE(proof_file_id,'')=''",
                (expected_pid,uid)
            ).fetchone()
        else:
            row=con.execute(
                "SELECT id,amount,method,reference FROM payments "
                "WHERE user_id=? AND status='pending' AND COALESCE(proof_file_id,'')='' "
                "ORDER BY id DESC LIMIT 1",
                (uid,)
            ).fetchone()

    if not row:
        return False

    group_id=await get_payment_group_id()
    if not group_id:
        await update.message.reply_text('⚠️ Payment group সেট করা নেই।')
        return True

    pid,amount,method,ref=row
    file_id=update.message.photo[-1].file_id

    with closing(db()) as con:
        con.execute('UPDATE payments SET proof_file_id=? WHERE id=?',(file_id,pid))
        con.commit()

    caption=(
        '🔔 NEW DEPOSIT REQUEST\n\n'
        f'👤 User: @{update.effective_user.username or "N/A"}\n'
        f'🆔 User ID: {uid}\n\n'
        f'💳 Method: {method}\n'
        f'💰 Amount: {amount:.2f} {CURRENCY}\n'
        f'🔢 Reference: {ref or "Not provided"}\n\n'
        f'🧾 Payment #{pid}\n\n'
        '📸 Payment proof attached above.'
    )
    kb=InlineKeyboardMarkup([[
        InlineKeyboardButton('✅ Approve',callback_data=f'approve:{pid}'),
        InlineKeyboardButton('❌ Reject',callback_data=f'reject:{pid}')
    ]])

    try:
        await context.bot.send_photo(group_id,file_id,caption=caption,reply_markup=kb)
    except Exception:
        with closing(db()) as con:
            con.execute("UPDATE payments SET proof_file_id='' WHERE id=?",(pid,))
            con.commit()
        await update.message.reply_text(
            '❌ Payment group-এ proof পাঠানো যায়নি।\n'
            'Bot-কে group-এ admin করে photo/message permission দিন।'
        )
        return True

    context.user_data.pop('await_payment_proof',None)
    await update.message.reply_text(
        f'✅ Payment proof received for #{pid}.\n\n'
        '⏳ Admin এখন proof review করবে। Approve হলে balance automatically যোগ হবে।'
    )
    return True

async def admin_menu(q):
    kb=[[InlineKeyboardButton('📦 Stock',callback_data='astock'),InlineKeyboardButton('➕ Add Stock',callback_data='aaddstock')],[InlineKeyboardButton('◉ Broadcast',callback_data='broadcast'),InlineKeyboardButton('💵 Packages',callback_data='apkg')],[InlineKeyboardButton('💳 Payments',callback_data='apay')],[InlineKeyboardButton('👥 Users',callback_data='ausers'),InlineKeyboardButton('🧾 Orders',callback_data='aorders')],[InlineKeyboardButton('⌂ Main Menu',callback_data='home')]]
    await q.message.edit_text('🔐 Admin Panel\n\nManage stock, packages, users, payments and orders.',reply_markup=InlineKeyboardMarkup(kb))

async def broadcast_start(q, context):
    if not is_admin(q.from_user.id): return
    context.user_data.pop('broadcast_draft',None)
    context.user_data['await_broadcast']=True
    await q.message.edit_text('📢 <b>BROADCAST CENTER</b>\n\n━━━━━━━━━━━━━━━━━━\n📝 Send a text message\n🖼️ Or send a photo with an optional caption\n━━━━━━━━━━━━━━━━━━\n\n✨ A preview will appear before anything is sent.', parse_mode='HTML', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⟐ Cancel',callback_data='broadcast_cancel')]]))

async def broadcast_text(update, context):
    if not is_admin(update.effective_user.id) or not context.user_data.get('await_broadcast'): return False
    text=update.message.text.strip()
    if not text: return True
    context.user_data['await_broadcast']=False
    context.user_data['broadcast_draft']={'type':'text','text':text}
    await update.message.reply_text('📢 <b>BROADCAST PREVIEW</b>\n\n━━━━━━━━━━━━━━━━━━\n'+text+'\n━━━━━━━━━━━━━━━━━━\n\n⚠️ Nothing has been sent yet.', parse_mode='HTML', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('✅ Confirm & Send',callback_data='broadcast_send')],[InlineKeyboardButton('⟐ Cancel',callback_data='broadcast_cancel')]]))
    return True

async def broadcast_photo(update, context):
    if not is_admin(update.effective_user.id) or not context.user_data.get('await_broadcast'):
        return await payment_proof_photo(update, context)
    photo=update.message.photo[-1]; caption=update.message.caption or ''
    context.user_data['await_broadcast']=False
    context.user_data['broadcast_draft']={'type':'photo','file_id':photo.file_id,'caption':caption}
    preview_caption='📢 <b>BROADCAST PHOTO PREVIEW</b>\n\n━━━━━━━━━━━━━━━━━━\n'+(caption if caption else '<i>No caption</i>')+'\n━━━━━━━━━━━━━━━━━━\n\n⚠️ Nothing has been sent yet.'
    await update.message.reply_photo(photo=photo.file_id, caption=preview_caption, parse_mode='HTML', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('✅ Confirm & Send',callback_data='broadcast_send')],[InlineKeyboardButton('⟐ Cancel',callback_data='broadcast_cancel')]]))
    return True

async def broadcast_send(q, context):
    if not is_admin(q.from_user.id): return
    draft=context.user_data.get('broadcast_draft')
    if not draft: return await q.message.reply_text('❌ No broadcast draft found.')
    with closing(db()) as con: users=[r[0] for r in con.execute('SELECT user_id FROM users').fetchall()]
    await q.message.edit_text(f'📢 <b>SENDING BROADCAST</b>\n\n👥 Recipients: <b>{len(users)}</b>\n\n⏳ Please wait...', parse_mode='HTML')
    sent=failed=0
    for uid in users:
        try:
            if draft['type']=='photo': await q.get_bot().send_photo(chat_id=uid,photo=draft['file_id'],caption=draft['caption'])
            else: await q.get_bot().send_message(chat_id=uid,text=draft['text'])
            sent+=1
        except Exception as e:
            failed+=1; log.warning('Broadcast failed for %s: %s',uid,e)
    context.user_data.pop('broadcast_draft',None); context.user_data.pop('await_broadcast',None)
    await q.message.reply_text('✅ <b>BROADCAST COMPLETED</b>\n\n👥 Total: <b>'+str(len(users))+'</b>\n✅ Sent: <b>'+str(sent)+'</b>\n❌ Failed/blocked: <b>'+str(failed)+'</b>', parse_mode='HTML', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('📢 Broadcast Again',callback_data='broadcast')],[InlineKeyboardButton('♛ Admin Panel',callback_data='admin')]]))

async def broadcast_cancel(q, context):
    if not is_admin(q.from_user.id): return
    context.user_data.pop('broadcast_draft',None); context.user_data.pop('await_broadcast',None)
    await q.message.edit_text('❌ <b>Broadcast cancelled.</b>', parse_mode='HTML', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('♛ Admin Panel',callback_data='admin')]]))

async def admin_stock_view(q):
    with closing(db()) as con:
        rows=con.execute("""SELECT s.id,s.proxy,p.name FROM stock s LEFT JOIN packages p ON p.id=s.package_id WHERE s.status='available' ORDER BY s.id DESC LIMIT 50""").fetchall()
    text='📦 Available Stock\n\n'+('\n'.join(f"#{i} | {n or 'Unassigned'} | {x}" for i,x,n in rows) if rows else 'Empty')
    kb=[]
    if rows:
        kb.append([InlineKeyboardButton('🗑️ Delete One',callback_data='delmode:one'),InlineKeyboardButton('🗑️🗑️ Delete Multiple',callback_data='delmode:multi')])
    kb.append([InlineKeyboardButton('➕ Add Stock',callback_data='aaddstock')])
    kb.append([InlineKeyboardButton('🔐 Admin',callback_data='admin')])
    await q.message.edit_text(text[:4000],reply_markup=InlineKeyboardMarkup(kb))

async def delete_one_view(q):
    with closing(db()) as con:
        rows=con.execute("""SELECT s.id,s.proxy,p.name FROM stock s LEFT JOIN packages p ON p.id=s.package_id WHERE s.status='available' ORDER BY s.id DESC LIMIT 50""").fetchall()
    if not rows:
        return await q.message.edit_text('📦 Available Stock\n\nEmpty.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🔐 Admin',callback_data='admin')]]))
    kb=[]
    for i,x,n in rows:
        label=(x[:28]+'…') if len(x)>29 else x
        kb.append([InlineKeyboardButton(f'🗑️ Delete #{i} — {label}',callback_data=f'delproxy:{i}')])
    kb.append([InlineKeyboardButton('⬅️ Back',callback_data='astock')])
    await q.message.edit_text('🗑️ Delete One Proxy\n\nSelect the proxy you want to remove:',reply_markup=InlineKeyboardMarkup(kb))

async def delete_multi_view(q,context):
    selected=set(context.user_data.get('delete_selected',[]))
    with closing(db()) as con:
        rows=con.execute("""SELECT s.id,s.proxy,p.name FROM stock s LEFT JOIN packages p ON p.id=s.package_id WHERE s.status='available' ORDER BY s.id DESC LIMIT 50""").fetchall()
    if not rows:
        context.user_data.pop('delete_selected',None)
        return await q.message.edit_text('📦 Available Stock\n\nEmpty.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🔐 Admin',callback_data='admin')]]))
    available={i for i,_,_ in rows}
    selected &= available
    context.user_data['delete_selected']=list(selected)
    kb=[]
    for i,x,n in rows:
        mark='☑️' if i in selected else '⬜'
        label=(x[:25]+'…') if len(x)>26 else x
        kb.append([InlineKeyboardButton(f'{mark} #{i} — {label}',callback_data=f'delselect:{i}')])
    kb.append([InlineKeyboardButton('☑️ Select All',callback_data='delselectall'),InlineKeyboardButton('❌ Clear',callback_data='delclear')])
    kb.append([InlineKeyboardButton(f'🗑️ Delete Selected ({len(selected)})',callback_data='delmulti_confirm')])
    kb.append([InlineKeyboardButton('⬅️ Back',callback_data='astock')])
    await q.message.edit_text('🗑️🗑️ Delete Multiple Proxies\n\nSelect one or more proxies, then press Delete Selected.',reply_markup=InlineKeyboardMarkup(kb))

async def delete_multi_confirm(q,context):
    selected=list(dict.fromkeys(context.user_data.get('delete_selected',[])))
    if not selected:
        return await q.answer('Please select at least one proxy.',show_alert=True)
    with closing(db()) as con:
        placeholders=','.join('?' for _ in selected)
        rows=con.execute(f"SELECT id,proxy FROM stock WHERE id IN ({placeholders}) AND status='available' ORDER BY id DESC",selected).fetchall()
    if not rows:
        context.user_data.pop('delete_selected',None)
        return await q.message.edit_text('❌ Selected proxies are no longer available.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('📦 Stock',callback_data='astock')]]))
    preview='\n'.join(f'#{i} — {x}' for i,x in rows[:20])
    if len(rows)>20: preview += f'\n…and {len(rows)-20} more'
    await q.message.edit_text(f'⚠️ Confirm Bulk Delete\n\nYou selected {len(rows)} available proxy(s):\n\n{preview}\n\nThis will permanently remove them from available stock.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(f'✅ Yes, Delete {len(rows)}',callback_data='delmulti_execute'),InlineKeyboardButton('⟐ Cancel',callback_data='delmulti_cancel')]]))

async def delete_multi_execute(q,context):
    selected=list(dict.fromkeys(context.user_data.get('delete_selected',[])))
    if not selected:
        return await q.message.edit_text('❌ No proxies selected.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('📦 Stock',callback_data='astock')]]))
    with closing(db()) as con:
        placeholders=','.join('?' for _ in selected)
        cur=con.execute(f"DELETE FROM stock WHERE id IN ({placeholders}) AND status='available'",selected)
        deleted=cur.rowcount
        con.commit()
    context.user_data.pop('delete_selected',None)
    await q.message.edit_text(f'✅ {deleted} proxy(s) deleted successfully from available stock.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('📦 Stock',callback_data='astock')],[InlineKeyboardButton('🔐 Admin',callback_data='admin')]]))

async def delete_proxy_confirm(q,pid):
    with closing(db()) as con:
        row=con.execute("SELECT s.id,s.proxy,p.name FROM stock s LEFT JOIN packages p ON p.id=s.package_id WHERE s.id=? AND s.status='available'",(pid,)).fetchone()
    if not row:
        return await q.message.edit_text('❌ Proxy not found or already deleted.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('📦 Stock',callback_data='astock')],[InlineKeyboardButton('🔐 Admin',callback_data='admin')]]))
    await q.message.edit_text(f'⚠️ Delete this proxy?\n\n🆔 Stock ID: #{row[0]}\n📦 Package: {row[2] or 'Unassigned'}\n🔑 Proxy: `{row[1]}`\n\nThis will permanently remove it from available stock.',parse_mode='Markdown',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('✅ Yes, Delete',callback_data=f'delproxy_confirm:{pid}'),InlineKeyboardButton('⟐ Cancel',callback_data='delproxy_cancel')]]))

async def delete_proxy(q,pid):
    with closing(db()) as con:
        row=con.execute("SELECT id,proxy FROM stock WHERE id=? AND status='available'",(pid,)).fetchone()
        if not row:
            return await q.message.edit_text('❌ Proxy not found or already deleted.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('📦 Stock',callback_data='astock')],[InlineKeyboardButton('🔐 Admin',callback_data='admin')]]))
        con.execute("DELETE FROM stock WHERE id=? AND status='available'",(pid,))
        con.commit()
    await q.message.edit_text(f'✅ Proxy #{pid} deleted successfully from available stock.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('📦 Stock',callback_data='astock')],[InlineKeyboardButton('🔐 Admin',callback_data='admin')]]))

async def admin_packages(q):
    with closing(db()) as con: rows=con.execute('SELECT id,name,price,active FROM packages ORDER BY id').fetchall()
    kb=[]
    for i,n,p,a in rows:
        kb.append([InlineKeyboardButton(f'✏️ Rename #{i} — {n}',callback_data=f'renamepkg:{i}')])
        kb.append([InlineKeyboardButton(f'💰 Set Price #{i} ({p:.2f} {CURRENCY})',callback_data=f'setprice:{i}')])
    kb.append([InlineKeyboardButton('➕ Add Package',callback_data='newpkg')])
    kb.append([InlineKeyboardButton('🔐 Admin',callback_data='admin')])
    text='💵 Packages\n\n'+('\n'.join(f'#{i} {n} — {p:.2f} {CURRENCY} — {"ON" if a else "OFF"}' for i,n,p,a in rows) if rows else 'No packages.')
    await q.message.edit_text(text+'\n\n✏️ Rename = change package name\n💰 Set Price = change package price',reply_markup=InlineKeyboardMarkup(kb))

async def rename_prompt(q,pid,context):
    with closing(db()) as con: r=con.execute('SELECT name FROM packages WHERE id=?',(pid,)).fetchone()
    if not r: return await q.message.reply_text('❌ Package not found.')
    context.user_data['await_rename']=pid
    await q.message.reply_text(f'✏️ Rename Package\n\n📦 Current name: {r[0]}\n\nSend the new package name only.\nExample: USA Premium Proxy')

async def rename_text(update,context):
    if not is_admin(update.effective_user.id) or not context.user_data.get('await_rename'): return False
    pid=context.user_data.pop('await_rename')
    name=update.message.text.strip() if update.message and update.message.text else ''
    if not name or len(name)>100:
        context.user_data['await_rename']=pid
        await update.message.reply_text('❌ Invalid name. Send a name between 1 and 100 characters.')
        return True
    with closing(db()) as con:
        changed=con.execute('UPDATE packages SET name=? WHERE id=?',(name,pid)).rowcount
        con.commit()
    if changed:
        await update.message.reply_text(f'✅ Package renamed successfully!\n\n📦 New name: {name}',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('💵 Packages',callback_data='apkg')],[InlineKeyboardButton('🔐 Admin',callback_data='admin')]]))
    else:
        await update.message.reply_text('❌ Package not found.')
    return True

async def price_prompt(q,pid,context):
    with closing(db()) as con:r=con.execute('SELECT name,price FROM packages WHERE id=?',(pid,)).fetchone()
    if not r:return await q.message.reply_text('❌ Package not found.',reply_markup=back_home())
    context.user_data['await_price']=pid
    await q.message.reply_text(f'💰 Set Price\n\n📦 {r[0]}\n💵 Current price: {r[1]:.2f} {CURRENCY}\n\nSend the new price only.\nExample: 150')

async def setprice_text(update,context):
    if not is_admin(update.effective_user.id) or not context.user_data.get('await_price'):return False
    pid=context.user_data.pop('await_price')
    try:
        price=float(update.message.text.strip())
        if price<0:raise ValueError
    except ValueError:
        context.user_data['await_price']=pid
        await update.message.reply_text('❌ Invalid price. Send a number only, e.g. 150')
        return True
    with closing(db()) as con:
        changed=con.execute('UPDATE packages SET price=? WHERE id=?',(price,pid)).rowcount
        name=con.execute('SELECT name FROM packages WHERE id=?',(pid,)).fetchone();con.commit()
    if changed:
        await update.message.reply_text(f'✅ Price Updated\n\n📦 {name[0] if name else "Package"}\n💰 New Price: {price:.2f} {CURRENCY}',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('💵 Packages',callback_data='apkg')],[InlineKeyboardButton('🔐 Admin',callback_data='admin')]]))
    else:await update.message.reply_text('❌ Package not found.')
    return True

async def admin_payments(q):
    with closing(db()) as con: rows=con.execute("SELECT id,user_id,amount,method,reference,status,created_at FROM payments WHERE status='pending' ORDER BY id DESC LIMIT 20").fetchall()
    if not rows: return await q.message.reply_text('💳 No pending payments.',reply_markup=back_home())
    kb=[[InlineKeyboardButton(f'#{i} {a:.2f} {m}',callback_data=f'payview:{i}')] for i,u,a,m,r,s,d in rows]; kb.append([InlineKeyboardButton('🔐 Admin',callback_data='admin')])
    await q.message.reply_text('💳 Pending Payments:',reply_markup=InlineKeyboardMarkup(kb))

async def payment_detail(q,pid):
    with closing(db()) as con: r=con.execute('SELECT user_id,amount,method,reference,status FROM payments WHERE id=?',(pid,)).fetchone()
    if not r:return await q.message.reply_text('Not found.')
    kb=[]
    if r[4]=='pending': kb=[[InlineKeyboardButton('✅ Approve',callback_data=f'approve:{pid}'),InlineKeyboardButton('❌ Reject',callback_data=f'reject:{pid}')]]
    kb.append([InlineKeyboardButton('↩️ Payments',callback_data='apay')])
    await q.message.reply_text(f'💳 Payment #{pid}\nUser: {r[0]}\nAmount: {r[1]:.2f} {CURRENCY}\nMethod: {r[2]}\nReference: {r[3] or "N/A"}\nStatus: {r[4]}',reply_markup=InlineKeyboardMarkup(kb))

async def approve_payment(q,pid,approve):
    with closing(db()) as con:
        con.execute('BEGIN IMMEDIATE'); r=con.execute("SELECT user_id,amount,status FROM payments WHERE id=?",(pid,)).fetchone()
        if not r or r[2]!='pending': con.rollback(); return await q.message.reply_text('Already processed.')
        status='approved' if approve else 'rejected'; con.execute('UPDATE payments SET status=? WHERE id=?',(status,pid))
        if approve: con.execute('UPDATE users SET balance=balance+? WHERE user_id=?',(r[1],r[0]))
        con.commit()
    await q.message.reply_text(f'✅ Payment #{pid} {status}.')
    try:
        if approve:
            await q.get_bot().send_message(r[0],f'💰 Payment #{pid} approved. Added {r[1]:.2f} {CURRENCY} to your balance.')
        else:
            await q.get_bot().send_message(r[0],f'❌ Payment #{pid} rejected. Please check your payment proof and contact admin if needed.')
    except Exception as e:
        log.exception('user notification error')
        try:
            await q.message.reply_text(f'⚠️ User notification failed for {r[0]}: {e}')
        except Exception:
            pass

async def admin_users(q):
    with closing(db()) as con: rows=con.execute('SELECT user_id,username,balance FROM users ORDER BY created_at DESC LIMIT 30').fetchall()
    text='👥 Users\n\n'+'\n'.join(f'{i} | @{u or "N/A"} | {b:.2f} {CURRENCY}' for i,u,b in rows)
    await q.message.reply_text(text[:4000]+'\n\nUse /addbalance USER_ID AMOUNT',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🔐 Admin',callback_data='admin')]]))

async def admin_orders(q):
    with closing(db()) as con: rows=con.execute('SELECT id,user_id,quantity,price,created_at FROM orders ORDER BY id DESC LIMIT 30').fetchall()
    text='🧾 Recent Orders\n\n'+'\n'.join(f'#{i} | user {u} | qty {n} | {p:.2f} {CURRENCY} | {d}' for i,u,n,p,d in rows)
    await q.message.reply_text(text[:4000],reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🔐 Admin',callback_data='admin')]]))

async def addstock_start(update, context):
    if not is_admin(update.effective_user.id): return await update.effective_message.reply_text('⛔ Admin only.')
    with closing(db()) as con: rows=con.execute('SELECT id,name,price FROM packages WHERE active=1 ORDER BY id').fetchall()
    if not rows: return await update.effective_message.reply_text('❌ No active packages found. Add a package first.')
    kb=[[InlineKeyboardButton(f'📦 {n} — {p:.2f} {CURRENCY}',callback_data=f'stockpkg:{i}')] for i,n,p in rows]; kb.append([InlineKeyboardButton('⟐ Cancel',callback_data='stockcancel')])
    await update.effective_message.reply_text('📦 <b>ADD STOCK</b>\n\n━━━━━━━━━━━━━━━━━━\n1️⃣ Select the package\n━━━━━━━━━━━━━━━━━━',parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

async def addstock_menu(q, context):
    if not is_admin(q.from_user.id): return
    with closing(db()) as con: rows=con.execute('SELECT id,name,price FROM packages WHERE active=1 ORDER BY id').fetchall()
    if not rows: return await q.message.edit_text('❌ No active packages found. Add a package first.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('♛ Admin Panel',callback_data='admin')]]))
    kb=[[InlineKeyboardButton(f'📦 {n} — {p:.2f} {CURRENCY}',callback_data=f'stockpkg:{i}')] for i,n,p in rows]; kb.append([InlineKeyboardButton('⟐ Cancel',callback_data='stockcancel')])
    await q.message.edit_text('📦 <b>ADD STOCK</b>\n\n━━━━━━━━━━━━━━━━━━\n1️⃣ Select the package\n━━━━━━━━━━━━━━━━━━',parse_mode='HTML',reply_markup=InlineKeyboardMarkup(kb))

async def addstock_package(q, context, pid):
    if not is_admin(q.from_user.id): return
    with closing(db()) as con: row=con.execute('SELECT id,name,price FROM packages WHERE id=? AND active=1',(pid,)).fetchone()
    if not row: return await q.message.edit_text('❌ Package not found or inactive.')
    context.user_data['await_stock']=True; context.user_data['stock_package_id']=pid
    await q.message.edit_text(f'📦 <b>ADD STOCK — STEP 2/2</b>\n\n📦 Package: <b>{row[1]}</b>\n💰 Price: <b>{row[2]:.2f} {CURRENCY}</b>\n\n📌 Send proxies, one per line.\n📌 Format: <code>host:port:user:pass</code>\n\nExample:\n<code>host:8080:user:pass\nhost:8081:user:pass</code>\n\n✨ After sending, you will get a preview before the stock is added.',parse_mode='HTML',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⟐ Cancel',callback_data='stockcancel')]]))

async def addstock_text(update, context):
    if not update.message or not update.message.text: return False
    if not is_admin(update.effective_user.id) or not context.user_data.get('await_stock'): return False
    pid=context.user_data.get('stock_package_id'); lines=[x.strip() for x in update.message.text.splitlines() if x.strip()]
    if not lines: return True
    valid=[]; dup=bad=0
    with closing(db()) as con:
        package=con.execute('SELECT id,name,price FROM packages WHERE id=? AND active=1',(pid,)).fetchone()
        if not package:
            context.user_data.pop('await_stock',None); context.user_data.pop('stock_package_id',None); await update.message.reply_text('❌ Package is no longer active.'); return True
        for proxy in lines:
            parts=proxy.split(':',3)
            try:
                if len(parts)!=4 or not parts[0] or not (1<=int(parts[1])<=65535) or not parts[2] or not parts[3]: raise ValueError
                if con.execute('SELECT 1 FROM stock WHERE proxy=?',(proxy,)).fetchone(): dup+=1
                else: valid.append(proxy)
            except (ValueError,TypeError): bad+=1
    context.user_data['stock_preview']={'pid':pid,'proxies':valid,'duplicate':dup,'invalid':bad,'package':package[1]}; context.user_data['await_stock']=False
    preview='\n'.join(f'• <code>{x}</code>' for x in valid[:20]) or '• No new valid proxies'
    if len(valid)>20: preview += f'\n• ...and {len(valid)-20} more'
    await update.message.reply_text('📦 <b>STOCK PREVIEW</b>\n\n📦 Package: <b>'+package[1]+'</b>\n➕ New: <b>'+str(len(valid))+'</b>\n♻️ Duplicate: <b>'+str(dup)+'</b>\n❌ Invalid: <b>'+str(bad)+'</b>\n\n'+preview+'\n\n⚠️ Nothing has been added yet.',parse_mode='HTML',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(f'✅ Confirm Add ({len(valid)})',callback_data='stockconfirm')],[InlineKeyboardButton('⟐ Cancel',callback_data='stockpreview_cancel')]]))
    return True

async def addstock_confirm(q, context):
    if not is_admin(q.from_user.id): return
    draft=context.user_data.get('stock_preview')
    if not draft: return await q.message.edit_text('❌ No stock preview found.')
    pid=draft['pid']; proxies=draft['proxies']; added=dup=0
    with closing(db()) as con:
        package=con.execute('SELECT id,name FROM packages WHERE id=? AND active=1',(pid,)).fetchone()
        if not package: return await q.message.edit_text('❌ Package is no longer active.')
        for proxy in proxies:
            try: con.execute('INSERT INTO stock(proxy,package_id) VALUES(?,?)',(proxy,pid)); added+=1
            except sqlite3.IntegrityError: dup+=1
        con.commit()
    context.user_data.pop('stock_preview',None)
    await q.message.edit_text('✅ <b>STOCK ADDED SUCCESSFULLY</b>\n\n📦 Package: <b>'+package[1]+'</b>\n➕ Added: <b>'+str(added)+'</b>\n♻️ Duplicate: <b>'+str(dup+draft['duplicate'])+'</b>\n❌ Invalid: <b>'+str(draft['invalid'])+'</b>\n\n📊 Available stock: <b>'+str(available(pid))+'</b>',parse_mode='HTML',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('📦 Add More Stock',callback_data='aaddstock')],[InlineKeyboardButton('📊 View Stock',callback_data='astock')],[InlineKeyboardButton('♛ Admin Panel',callback_data='admin')]]))

async def addstock_preview_cancel(q, context):
    if not is_admin(q.from_user.id): return
    context.user_data.pop('stock_preview',None)
    await q.message.edit_text('❌ <b>Stock addition cancelled.</b>',parse_mode='HTML',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('▣ Add Stock',callback_data='aaddstock')],[InlineKeyboardButton('♛ Admin Panel',callback_data='admin')]]))

async def addpackage(update,context):
    if not is_admin(update.effective_user.id): return
    raw=' '.join(context.args); parts=[x.strip() for x in raw.split('|')]
    if len(parts)<2:return await update.message.reply_text('Usage: /addpackage NAME | PRICE | DESCRIPTION')
    try: price=float(parts[1]); assert price>=0
    except:return await update.message.reply_text('Invalid price.')
    with closing(db()) as con: con.execute('INSERT INTO packages(name,price,description) VALUES(?,?,?)',(parts[0],price,parts[2] if len(parts)>2 else '')); con.commit()
    await update.message.reply_text('✅ Package added.')

async def setprice(update,context):
    if not is_admin(update.effective_user.id) or len(context.args)!=2:return
    try: pid=int(context.args[0]); price=float(context.args[1])
    except:return await update.message.reply_text('Invalid.')
    with closing(db()) as con: changed=con.execute('UPDATE packages SET price=? WHERE id=?',(price,pid)).rowcount; con.commit()
    await update.message.reply_text('✅ Price updated.' if changed else '❌ Package not found.')

async def togglepackage(update,context):
    if not is_admin(update.effective_user.id) or len(context.args)!=1:return
    try:pid=int(context.args[0])
    except:return
    with closing(db()) as con: changed=con.execute('UPDATE packages SET active=CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id=?',(pid,)).rowcount;con.commit()
    await update.message.reply_text('✅ Package toggled.' if changed else '❌ Not found.')

async def addbalance(update,context):
    if not is_admin(update.effective_user.id) or len(context.args)!=2:return await update.message.reply_text('Usage: /addbalance USER_ID AMOUNT')
    try: uid=int(context.args[0]); amount=float(context.args[1]); assert amount>0
    except:return await update.message.reply_text('Invalid.')
    with closing(db()) as con:
        con.execute('INSERT INTO users(user_id,balance) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET balance=balance+excluded.balance',(uid,amount));con.commit()
    await update.message.reply_text('✅ Balance added.')

async def callback(update,context):
    q=update.callback_query; await q.answer(); ensure_user(q.from_user); d=q.data
    try:
        if d=='home': await q.message.edit_text(f'{WELCOME}\n\n🛍️ Choose an option:',reply_markup=main_kb(q.from_user.id))
        elif d=='buy': await buy_menu(update,context)
        elif d.startswith('pkg:'): await package_menu(q,int(d.split(':')[1]))
        elif d.startswith('qty:'):
            _,pid,n=d.split(':'); await purchase(q,int(pid),int(n))
        elif d=='bal': await show_balance(update,context)
        elif d=='addbal': await add_balance_menu(update,context)
        elif d=='orders': await orders(update,context)
        elif d.startswith('order:'): await order_detail(q,int(d.split(':')[1]))
        elif d=='stock': await show_stock(update,context)
        elif d=='profile': await profile(update,context)
        elif d=='admin' and is_admin(q.from_user.id): await admin_menu(q)
        elif d=='astock' and is_admin(q.from_user.id): await admin_stock_view(q)
        elif d=='delmode:one' and is_admin(q.from_user.id): await delete_one_view(q)
        elif d=='delmode:multi' and is_admin(q.from_user.id): await delete_multi_view(q,context)
        elif d.startswith('delselect:') and is_admin(q.from_user.id):
            pid=int(d.split(':')[1]); selected=set(context.user_data.get('delete_selected',[]))
            if pid in selected: selected.remove(pid)
            else: selected.add(pid)
            context.user_data['delete_selected']=list(selected); await delete_multi_view(q,context)
        elif d=='delselectall' and is_admin(q.from_user.id):
            with closing(db()) as con: ids=[r[0] for r in con.execute("SELECT id FROM stock WHERE status='available' ORDER BY id DESC LIMIT 50").fetchall()]
            context.user_data['delete_selected']=ids; await delete_multi_view(q,context)
        elif d=='delclear' and is_admin(q.from_user.id):
            context.user_data.pop('delete_selected',None); await delete_multi_view(q,context)
        elif d=='delmulti_confirm' and is_admin(q.from_user.id): await delete_multi_confirm(q,context)
        elif d=='delmulti_execute' and is_admin(q.from_user.id): await delete_multi_execute(q,context)
        elif d=='delmulti_cancel' and is_admin(q.from_user.id):
            context.user_data.pop('delete_selected',None); await admin_stock_view(q)
        elif d.startswith('delproxy:') and is_admin(q.from_user.id): await delete_proxy_confirm(q,int(d.split(':')[1]))
        elif d.startswith('delproxy_confirm:') and is_admin(q.from_user.id): await delete_proxy(q,int(d.split(':')[1]))
        elif d=='delproxy_cancel' and is_admin(q.from_user.id): await delete_one_view(q)
        elif d=='aaddstock' and is_admin(q.from_user.id): await addstock_menu(q,context)
        elif d=='stockconfirm' and is_admin(q.from_user.id): await addstock_confirm(q,context)
        elif d=='stockpreview_cancel' and is_admin(q.from_user.id): await addstock_preview_cancel(q,context)
        elif d=='broadcast' and is_admin(q.from_user.id): await broadcast_start(q,context)
        elif d=='broadcast_send' and is_admin(q.from_user.id): await broadcast_send(q,context)
        elif d=='broadcast_cancel' and is_admin(q.from_user.id): await broadcast_cancel(q,context)
        elif d.startswith('stockpkg:') and is_admin(q.from_user.id): await addstock_package(q,context,int(d.split(':')[1]))
        elif d=='stockcancel' and is_admin(q.from_user.id):
            context.user_data.pop('await_stock',None); context.user_data.pop('stock_package_id',None); context.user_data.pop('stock_preview',None); await admin_menu(q)
        elif d=='apkg' and is_admin(q.from_user.id): await admin_packages(q)
        elif d.startswith('renamepkg:') and is_admin(q.from_user.id): await rename_prompt(q,int(d.split(':')[1]),context)
        elif d.startswith('setprice:') and is_admin(q.from_user.id): await price_prompt(q,int(d.split(':')[1]),context)
        elif d=='newpkg' and is_admin(q.from_user.id): await q.message.reply_text('Use /addpackage NAME | PRICE | DESCRIPTION')
        elif d=='paymethod:bkash':
            await payment_method(q,'bkash')
            context.user_data['selected_payment_method']='bKash'
        elif d=='paymethod:binance':
            await payment_method(q,'binance')
            context.user_data['selected_payment_method']='Binance'
        elif d=='payback': await payment_back(q)
        elif d=='enteramount:bkash': await amount_prompt(update,context,'bKash')
        elif d=='enteramount:binance': await amount_prompt(update,context,'Binance')
        elif d=='apay' and is_admin(q.from_user.id): await admin_payments(q)
        elif d.startswith('payview:') and is_admin(q.from_user.id): await payment_detail(q,int(d.split(':')[1]))
        elif d.startswith('approve:') and is_admin(q.from_user.id): await approve_payment(q,int(d.split(':')[1]),True)
        elif d.startswith('reject:') and is_admin(q.from_user.id): await approve_payment(q,int(d.split(':')[1]),False)
        elif d=='ausers' and is_admin(q.from_user.id): await admin_users(q)
        elif d=='aorders' and is_admin(q.from_user.id): await admin_orders(q)
    except Exception as e:
        log.exception('callback error')
        await q.message.reply_text(f'❌ Something went wrong.\n\nError: {type(e).__name__}: {e}')

async def keyboard_router(update, context):
    if not update.message or not update.message.text:
        return False
    t=update.message.text.strip()
    routes={
        '💎 Buy Proxy': buy_menu,
        '💳 Balance': show_balance,
        '✦ Add Balance': add_balance_menu,
        '◈ My Orders': orders,
        '◉ Stock': show_stock,
        '♟ My Profile': profile,
    }
    fn=routes.get(t)
    if fn:
        await fn(update, context)
        return True
    if t=='♛ Admin Panel':
        await admin_menu_message(update, context)
        return True
    return False

async def text_router(update,context):
    # Reply-keyboard buttons arrive as normal text messages, so route them
    # before checking the various admin/user input states.
    if await keyboard_router(update, context): return
    if await rename_text(update,context): return
    if await setprice_text(update,context): return
    if await amount_text(update,context): return
    if await broadcast_text(update,context): return
    await addstock_text(update,context)


def main():
    if not BOT_TOKEN: raise RuntimeError('BOT_TOKEN is missing.')
    if not ADMIN_IDS: raise RuntimeError('ADMIN_IDS is missing.')
    init_db(); app=Application.builder().token(BOT_TOKEN).build()
    for cmd,fn in [('start',start),('balance',show_balance),('stock',show_stock),('buy',buy_menu),('orders',orders),('setpaymentgroup',set_payment_group),('addstock',addstock_start),('addbalance',addbalance),('addpackage',addpackage),('setprice',setprice),('togglepackage',togglepackage)]: app.add_handler(CommandHandler(cmd,fn))
    app.add_handler(CallbackQueryHandler(callback)); app.add_handler(MessageHandler(filters.PHOTO, broadcast_photo)); app.add_handler(MessageHandler(filters.PHOTO, payment_proof_photo)); app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text_router))
    log.info('Bot started'); app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__=='__main__': main()
