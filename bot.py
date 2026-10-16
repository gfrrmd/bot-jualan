"""
VIP Channel Sales Bot (Telegram) — QRIS + verifikasi admin
Stack: python-telegram-bot 21.x, SQLite. Siap deploy di Railway (worker).
"""
import asyncio
import csv
import html
import io
import logging
import os
import random
import sqlite3
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Kb, Update
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError
from telegram.ext import (Application, CallbackQueryHandler, ChatJoinRequestHandler,
                          CommandHandler, ContextTypes, MessageHandler, filters)

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
log = logging.getLogger("vipbot")

# ───────────────────────── Config ─────────────────────────
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}
TZ = ZoneInfo(os.getenv("TIMEZONE", "Asia/Jakarta"))
DB_PATH = os.getenv("DB_PATH") or ("/data/bot.db" if os.path.isdir("/data") else "bot.db")
HTML = ParseMode.HTML

# ───────────────────────── Database ─────────────────────────
_db = sqlite3.connect(DB_PATH, check_same_thread=False)
_db.row_factory = sqlite3.Row
_db.execute("PRAGMA journal_mode=WAL")
_db.executescript("""
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY, username TEXT, name TEXT,
    created_at INTEGER, blocked INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS plans(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, price INTEGER,
    days INTEGER, description TEXT DEFAULT '', active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT,
    chat_id INTEGER, url TEXT, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
    plan_id INTEGER, plan_name TEXT, days INTEGER, base INTEGER, discount INTEGER DEFAULT 0,
    unique_code INTEGER DEFAULT 0, total INTEGER, promo TEXT, status TEXT, proof_id TEXT,
    proof_type TEXT, proof_at INTEGER, created_at INTEGER, expires_at INTEGER,
    reviewed_by TEXT, reviewed_at INTEGER, reason TEXT);
CREATE TABLE IF NOT EXISTS subs(user_id INTEGER PRIMARY KEY, plan_name TEXT, end_ts INTEGER,
    active INTEGER DEFAULT 1, reminded INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS promos(code TEXT PRIMARY KEY, percent INTEGER, max_uses INTEGER DEFAULT 0,
    used INTEGER DEFAULT 0, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS admin_msgs(order_id INTEGER, chat_id INTEGER, message_id INTEGER);
""")
_db.commit()


def q(sql, args=(), one=False):
    rows = _db.execute(sql, args).fetchall()
    _db.commit()
    return (rows[0] if rows else None) if one else rows


def ex(sql, args=()):
    cur = _db.execute(sql, args)
    _db.commit()
    return cur


DEFAULTS = {"shop": "VIP Premium", "welcome": "", "support": "", "qris": "",
            "expiry": "30", "unique": "1"}


def gs(key):
    r = q("SELECT value FROM settings WHERE key=?", (key,), one=True)
    return r["value"] if r else DEFAULTS.get(key, "")


def ss(key, value):
    ex("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
       (key, str(value)))


# ───────────────────────── Helpers ─────────────────────────
STATUS = {"pending": "⏳ Menunggu pembayaran", "review": "🔎 Sedang diverifikasi",
          "paid": "✅ Berhasil", "rejected": "❌ Ditolak", "cancelled": "🚫 Dibatalkan",
          "expired": "⌛ Kedaluwarsa"}


def rp(n): return "Rp" + f"{int(n):,}".replace(",", ".")
def esc(s): return html.escape(str(s if s is not None else ""))
def inv(i): return f"INV-{int(i):05d}"
def fmt_dt(ts): return datetime.fromtimestamp(ts, TZ).strftime("%d/%m/%Y %H:%M")
def fmt_d(ts): return datetime.fromtimestamp(ts, TZ).strftime("%d/%m/%Y")
def is_admin(uid): return uid in ADMIN_IDS
def now(): return int(time.time())
def get_order(oid): return q("SELECT * FROM orders WHERE id=?", (oid,), one=True)
def get_plan(pid): return q("SELECT * FROM plans WHERE id=?", (pid,), one=True)
def active_sub(uid): return q("SELECT * FROM subs WHERE user_id=? AND active=1 AND end_ts>?", (uid, now()), one=True)


def touch(u):
    ex("INSERT INTO users(user_id,username,name,created_at) VALUES(?,?,?,?) "
       "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,name=excluded.name,blocked=0",
       (u.id, u.username, u.full_name, now()))


def support_btn():
    s = gs("support").strip()
    if s:
        return [Btn("💬 Hubungi Admin", url="https://t.me/" + s.lstrip("@"))]
    return None


async def show(update: Update, ctx, text, kb=None):
    """Edit pesan callback kalau bisa, kalau tidak kirim pesan baru."""
    cq = update.callback_query
    if cq:
        try:
            await cq.edit_message_text(text, reply_markup=kb, parse_mode=HTML, disable_web_page_preview=True)
            return
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return
            try:
                await cq.message.delete()
            except TelegramError:
                pass
        chat = cq.message.chat_id
    else:
        chat = update.effective_chat.id
    await ctx.bot.send_message(chat, text, reply_markup=kb, parse_mode=HTML, disable_web_page_preview=True)


# ───────────────────────── User: menu ─────────────────────────
async def show_home(update, ctx):
    u = update.effective_user
    welcome = gs("welcome") or ("Selamat datang! Dapatkan akses eksklusif ke channel VIP kami dengan "
                                "proses yang cepat, aman, dan terverifikasi.")
    text = (f"✨ <b>{esc(gs('shop'))}</b>\n\nHalo, <b>{esc(u.first_name)}</b> 👋\n{esc(welcome)}\n\n"
            "<b>Cara order (mudah &amp; cepat):</b>\n"
            "①  Pilih paket VIP\n②  Bayar via QRIS\n③  Kirim screenshot bukti bayar\n"
            "④  Admin verifikasi → link akses dikirim otomatis")
    rows = [[Btn("🛒 Beli Akses VIP", callback_data="plans")],
            [Btn("📦 Langganan Saya", callback_data="subs"), Btn("🧾 Riwayat Pesanan", callback_data="orders")],
            [Btn("❓ Bantuan", callback_data="help")]]
    if is_admin(u.id):
        rows.append([Btn("🛠 Panel Admin", callback_data="a:home")])
    await show(update, ctx, text, Kb(rows))


async def cmd_start(update, ctx):
    touch(update.effective_user)
    ctx.user_data.clear()
    await show_home(update, ctx)


async def show_plans(update, ctx):
    plans = q("SELECT * FROM plans WHERE active=1 ORDER BY price")
    if not plans:
        await show(update, ctx, "🛒 <b>Paket VIP</b>\n\nMaaf, belum ada paket yang tersedia saat ini.",
                   Kb([[Btn("⬅ Menu Utama", callback_data="home")]]))
        return
    rows = [[Btn(f"{p['name']} • {rp(p['price'])} • {p['days']} hari", callback_data=f"plan:{p['id']}")] for p in plans]
    rows.append([Btn("⬅ Menu Utama", callback_data="home")])
    await show(update, ctx, "🛒 <b>Pilih Paket VIP</b>\n\nSilakan pilih paket yang sesuai kebutuhan Anda 👇", Kb(rows))


def calc_price(plan, promo_code):
    base = plan["price"]
    if not promo_code:
        return base, 0, None
    p = q("SELECT * FROM promos WHERE code=? AND active=1", (promo_code,), one=True)
    if not p or (p["max_uses"] and p["used"] >= p["max_uses"]):
        return base, 0, None
    return base, base * p["percent"] // 100, p["code"]


async def show_plan(update, ctx, pid):
    plan = get_plan(pid)
    if not plan or not plan["active"]:
        return await show_plans(update, ctx)
    pr = ctx.user_data.get("promo")
    code = pr["code"] if pr and pr["plan"] == pid else None
    base, disc, code = calc_price(plan, code)
    nch = q("SELECT COUNT(*) c FROM channels WHERE active=1", one=True)["c"]
    text = f"📦 <b>{esc(plan['name'])}</b>\n\n"
    if plan["description"]:
        text += f"{esc(plan['description'])}\n\n"
    text += f"⏱ Durasi: <b>{plan['days']} hari</b>\n📢 Akses: <b>{nch} channel VIP</b>\n"
    if disc:
        text += f"💰 Harga: <s>{rp(base)}</s> → <b>{rp(base - disc)}</b>\n🏷 Promo <code>{esc(code)}</code> diterapkan ✅\n"
    else:
        text += f"💰 Harga: <b>{rp(base)}</b>\n"
    kb = Kb([[Btn("✅ Lanjut ke Pembayaran", callback_data=f"buy:{pid}")],
             [Btn("🏷 Punya Kode Promo?", callback_data=f"promo:{pid}")],
             [Btn("⬅ Pilih Paket Lain", callback_data="plans")]])
    await show(update, ctx, text, kb)


async def create_order(update, ctx, pid):
    cq = update.callback_query
    uid = update.effective_user.id
    plan = get_plan(pid)
    qris = gs("qris")
    if not plan or not plan["active"]:
        return await show_plans(update, ctx)
    if not qris:
        kb = [[Btn("⬅ Kembali", callback_data="plans")]]
        if support_btn():
            kb.insert(0, support_btn())
        return await show(update, ctx, "⚠️ Pembayaran belum tersedia saat ini. Silakan hubungi admin.", Kb(kb))
    ex("UPDATE orders SET status='cancelled' WHERE user_id=? AND status='pending'", (uid,))
    pr = ctx.user_data.get("promo")
    code = pr["code"] if pr and pr["plan"] == pid else None
    base, disc, code = calc_price(plan, code)
    net = max(base - disc, 0)
    unique = 0
    if gs("unique") == "1":
        for _ in range(60):
            c = random.randint(11, 999)
            if not q("SELECT 1 FROM orders WHERE status IN('pending','review') AND total=?", (net + c,), one=True):
                unique = c
                break
    total = net + unique
    exp = now() + int(gs("expiry") or 30) * 60
    oid = ex("INSERT INTO orders(user_id,plan_id,plan_name,days,base,discount,unique_code,total,promo,status,"
             "created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,'pending',?,?)",
             (uid, pid, plan["name"], plan["days"], base, disc, unique, total, code, now(), exp)).lastrowid
    ctx.user_data.pop("promo", None)
    rows_ = [("Paket", plan["name"]), ("Durasi", f"{plan['days']} hari"), ("Harga", rp(base))]
    if disc:
        rows_.append((f"Diskon", f"-{rp(disc)}"))
    if unique:
        rows_.append(("Kode unik", f"+{unique}"))
    receipt = "\n".join(f"{k_:<10}: {v_}" for k_, v_ in rows_)
    receipt += "\n" + "─" * 26 + f"\n{'TOTAL':<10}: {rp(total)}"
    cap = (f"🧾 <b>INVOICE {inv(oid)}</b>\n<i>{esc(gs('shop'))}</i>\n\n<pre>{esc(receipt)}</pre>\n"
           f"💳 <b>Total bayar: {rp(total)}</b>\n\n"
           f"⚠️ Transfer <b>tepat sesuai total</b> agar verifikasi cepat.\n⏳ Bayar sebelum <b>{fmt_dt(exp)}</b>\n\n"
            "<b>Langkah selanjutnya:</b>\n1️⃣ Scan QRIS di atas lewat e-wallet / m-banking\n"
            "2️⃣ Kirim <b>screenshot bukti pembayaran</b> ke chat ini 📸")
    try:
        await cq.message.delete()
    except TelegramError:
        pass
    await ctx.bot.send_photo(uid, qris, caption=cap, parse_mode=HTML,
                             reply_markup=Kb([[Btn("❌ Batalkan Pesanan", callback_data=f"cancel:{oid}")]]))


async def handle_proof(update, ctx):
    m, u = update.message, update.effective_user
    o = q("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 1", (u.id,), one=True)
    home = Kb([[Btn("🛒 Beli Akses VIP", callback_data="plans")]])
    if not o or o["status"] in ("cancelled", "expired", "paid"):
        return await m.reply_text("Belum ada pesanan yang menunggu pembayaran.\nSilakan pilih paket terlebih dahulu 👇",
                                  reply_markup=home)
    if o["status"] == "review":
        return await m.reply_text("🔎 Bukti pembayaran Anda sedang diverifikasi admin. Mohon ditunggu ya 🙏")
    if o["status"] == "rejected":
        return await m.reply_text("Pesanan ini sebelumnya ditolak. Tekan <b>🔁 Kirim Ulang Bukti</b> pada pesan penolakan, "
                                  "atau buat pesanan baru.", parse_mode=HTML, reply_markup=home)
    if o["expires_at"] < now():
        ex("UPDATE orders SET status='expired' WHERE id=?", (o["id"],))
        return await m.reply_text("⌛ Invoice sudah kedaluwarsa. Silakan buat pesanan baru.", reply_markup=home)
    if m.photo:
        fid, ftype = m.photo[-1].file_id, "photo"
    else:
        fid, ftype = m.document.file_id, "document"
    ex("UPDATE orders SET status='review',proof_id=?,proof_type=?,proof_at=? WHERE id=?", (fid, ftype, now(), o["id"]))
    await m.reply_text(f"✅ <b>Bukti pembayaran diterima!</b>\n\nPesanan: <b>{inv(o['id'])}</b>\nTotal: <b>{rp(o['total'])}</b>\n\n"
                       "Admin sedang memverifikasi pembayaran Anda. Link akses akan dikirim otomatis begitu disetujui. 🙏",
                       parse_mode=HTML)
    await notify_admins(ctx, o["id"])


async def handle_promo(update, ctx):
    pid = ctx.user_data.pop("await_promo")
    code = update.message.text.strip().upper()
    p = q("SELECT * FROM promos WHERE code=? AND active=1", (code,), one=True)
    if not p:
        await update.message.reply_text("❌ Kode promo tidak ditemukan / tidak aktif.")
    elif p["max_uses"] and p["used"] >= p["max_uses"]:
        await update.message.reply_text("❌ Kuota kode promo ini sudah habis.")
    else:
        ctx.user_data["promo"] = {"plan": pid, "code": code}
        await update.message.reply_text(f"✅ Kode promo <b>{esc(code)}</b> berhasil dipakai ({p['percent']}% off).",
                                        parse_mode=HTML)
    await show_plan(update, ctx, pid)


async def show_subs(update, ctx):
    uid = update.effective_user.id
    s = active_sub(uid)
    if not s:
        return await show(update, ctx, "📦 <b>Langganan Saya</b>\n\nAnda belum memiliki langganan aktif.",
                          Kb([[Btn("🛒 Beli Akses VIP", callback_data="plans")], [Btn("⬅ Menu Utama", callback_data="home")]]))
    left = max((s["end_ts"] - now()) // 86400, 0)
    text = (f"📦 <b>Langganan Saya</b>\n\n✅ Status: <b>Aktif</b>\n🎟 Paket: {esc(s['plan_name'])}\n"
            f"📅 Berakhir: <b>{fmt_d(s['end_ts'])}</b> ({left} hari lagi)")
    await show(update, ctx, text, Kb([[Btn("🔑 Ambil Link Akses", callback_data="getlink")],
                                      [Btn("🔄 Perpanjang", callback_data="plans")],
                                      [Btn("⬅ Menu Utama", callback_data="home")]]))


async def show_orders(update, ctx):
    rows = q("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 6", (update.effective_user.id,))
    text = "🧾 <b>Riwayat Pesanan</b>\n\n"
    text += "\n\n".join(f"<b>{inv(o['id'])}</b> · {esc(o['plan_name'])}\n{rp(o['total'])} · {STATUS[o['status']]}"
                        for o in rows) if rows else "Belum ada pesanan."
    await show(update, ctx, text, Kb([[Btn("⬅ Menu Utama", callback_data="home")]]))


async def show_help(update, ctx):
    text = ("❓ <b>Bantuan</b>\n\n• Pembayaran hanya via <b>QRIS</b>.\n• Setelah transfer, kirim <b>screenshot bukti</b> ke bot ini.\n"
            "• Verifikasi dilakukan oleh admin, biasanya hanya beberapa menit.\n"
            "• Link channel bersifat pribadi &amp; hanya bisa dipakai oleh akun Anda.\n"
            "• Link hilang? Buka <b>Langganan Saya → Ambil Link Akses</b>.")
    rows = [[Btn("⬅ Menu Utama", callback_data="home")]]
    if support_btn():
        rows.insert(0, support_btn())
    await show(update, ctx, text, Kb(rows))


# ───────────────────────── Akses channel (anti-bocor) ─────────────────────────
async def build_links(ctx, uid):
    """Link unik per user (mode 'request to join') + auto-approve hanya untuk pembeli aktif.
    Jika link bocor, orang lain tetap ditolak bot."""
    links, fails = [], []
    for ch in q("SELECT * FROM channels WHERE active=1"):
        if ch["chat_id"]:
            try:
                inv_l = await ctx.bot.create_chat_invite_link(ch["chat_id"], name=f"u{uid}"[:32],
                                                              expire_date=now() + 86400, creates_join_request=True)
                links.append((ch["title"], inv_l.invite_link))
            except TelegramError as e:
                log.warning("invite link gagal %s: %s", ch["title"], e)
                fails.append(ch["title"])
        elif ch["url"]:
            links.append((ch["title"], ch["url"]))
    return links, fails


async def send_access(ctx, uid, head):
    links, fails = await build_links(ctx, uid)
    rows = [[Btn(f"🔓 Gabung {t}", url=u)] for t, u in links]
    rows.append([Btn("📦 Langganan Saya", callback_data="subs")])
    if links:
        head += ("\n\n👇 <b>Tekan tombol di bawah untuk bergabung.</b>\nKetuk <i>Request to Join / Minta Bergabung</i> "
                 "→ Anda akan diterima otomatis.\n🔒 <i>Link ini pribadi, hanya akun Anda yang dapat masuk. "
                 "Jangan dibagikan.</i>")
    else:
        head += "\n\n⏳ Akses channel sedang disiapkan admin. Mohon tunggu sebentar."
    try:
        await ctx.bot.send_message(uid, head, reply_markup=Kb(rows), parse_mode=HTML, protect_content=True)
    except TelegramError as e:
        log.warning("kirim akses gagal: %s", e)
    return fails, bool(links)


async def remove_from_channels(ctx, uid):
    for ch in q("SELECT * FROM channels WHERE chat_id IS NOT NULL"):
        try:
            await ctx.bot.ban_chat_member(ch["chat_id"], uid)
            await ctx.bot.unban_chat_member(ch["chat_id"], uid, only_if_banned=True)
        except TelegramError as e:
            log.warning("kick gagal %s: %s", ch["title"], e)


async def on_join_request(update, ctx):
    """Auto-accept: hanya pembeli dengan langganan aktif (atau admin) yang diterima.
    Link pribadi dicabut setelah dipakai, sehingga tidak bisa dipakai ulang/dibagikan."""
    r = update.chat_join_request
    ch = q("SELECT * FROM channels WHERE chat_id=?", (r.chat.id,), one=True)
    if not ch:
        return
    uid = r.from_user.id
    try:
        if is_admin(uid) or active_sub(uid):
            await r.approve()
            log.info("join approved: user=%s channel=%s", uid, r.chat.title)
            try:
                await ctx.bot.send_message(r.user_chat_id, f"✅ <b>Permintaan diterima!</b>\n\nSelamat bergabung di "
                                           f"<b>{esc(r.chat.title)}</b> 🎉", parse_mode=HTML)
            except TelegramError:
                pass
            lk = r.invite_link
            if lk and lk.name == f"u{uid}"[:32] and lk.creator and lk.creator.id == ctx.bot.id:
                try:
                    await ctx.bot.revoke_chat_invite_link(r.chat.id, lk.invite_link)
                except TelegramError:
                    pass
        else:
            await r.decline()
            log.info("join declined: user=%s channel=%s", uid, r.chat.title)
            try:
                await ctx.bot.send_message(r.user_chat_id, "🔒 Channel ini khusus member VIP aktif. "
                                           "Silakan beli paket untuk mendapatkan akses.",
                                           reply_markup=Kb([[Btn("🛒 Beli Akses VIP", callback_data="plans")]]))
            except TelegramError:
                pass
    except TelegramError as e:
        log.warning("join request error: %s", e)


def activate(uid, plan_name, days):
    s = q("SELECT * FROM subs WHERE user_id=?", (uid,), one=True)
    start = s["end_ts"] if s and s["active"] and s["end_ts"] > now() else now()
    end = start + days * 86400
    ex("INSERT INTO subs(user_id,plan_name,end_ts,active,reminded) VALUES(?,?,?,1,0) ON CONFLICT(user_id) DO UPDATE SET "
       "plan_name=excluded.plan_name,end_ts=excluded.end_ts,active=1,reminded=0", (uid, plan_name, end))
    return end


# ───────────────────────── Notifikasi admin & review ─────────────────────────
def card_text(o):
    u = q("SELECT * FROM users WHERE user_id=?", (o["user_id"],), one=True)
    name = esc(u["name"]) if u else "-"
    uname = f"@{esc(u['username'])}" if u and u["username"] else "tanpa username"
    link = '<a href="tg://user?id=%d">%s</a>' % (o["user_id"], name)
    s = "🔔 <b>PEMBAYARAN BARU · %s</b>\n\n" % inv(o["id"])
    s += "👤 %s (%s)\n" % (link, uname)
    s += "🆔 <code>%s</code>\n" % o["user_id"]
    s += "📦 %s · %s hari\n" % (esc(o["plan_name"]), o["days"])
    s += "💰 Nominal yang harus masuk: <b>%s</b>\n" % rp(o["total"])
    if o["unique_code"]:
        s += f"    (harga {rp(o['base'] - o['discount'])} + kode unik {o['unique_code']})\n"
    if o["promo"]:
        s += f"🏷 Promo: {esc(o['promo'])} (-{rp(o['discount'])})\n"
    if o["proof_at"]:
        s += f"🕒 Bukti dikirim: {fmt_dt(o['proof_at'])}\n"
    s += "\n"
    if o["status"] == "review":
        s += "⏳ <i>Cek mutasi/riwayat dana masuk, lalu pilih tindakan di bawah.</i>"
    elif o["status"] == "paid":
        s += f"✅ <b>DISETUJUI</b> oleh {esc(o['reviewed_by'])}"
    elif o["status"] == "rejected":
        s += f"❌ <b>DITOLAK</b> oleh {esc(o['reviewed_by'])}\nAlasan: {esc(o['reason'])}"
    else:
        s += STATUS.get(o["status"], "")
    return s


async def send_review_card(ctx, chat_id, o):
    kb = Kb([[Btn("✅ Setujui", callback_data=f"a:ok:{o['id']}"), Btn("❌ Tolak", callback_data=f"a:no:{o['id']}")]])
    send = ctx.bot.send_photo if o["proof_type"] == "photo" else ctx.bot.send_document
    msg = await send(chat_id, o["proof_id"], caption=card_text(o), parse_mode=HTML, reply_markup=kb)
    ex("INSERT INTO admin_msgs VALUES(?,?,?)", (o["id"], chat_id, msg.message_id))


async def notify_admins(ctx, oid):
    o = get_order(oid)
    for a in ADMIN_IDS:
        try:
            await send_review_card(ctx, a, o)
        except TelegramError as e:
            log.warning("notif admin %s gagal: %s", a, e)


async def refresh_admin_msgs(ctx, oid):
    o = get_order(oid)
    for r in q("SELECT * FROM admin_msgs WHERE order_id=?", (oid,)):
        try:
            await ctx.bot.edit_message_caption(r["chat_id"], r["message_id"], caption=card_text(o),
                                               parse_mode=HTML, reply_markup=None)
        except TelegramError:
            pass


REASONS = {"1": "Nominal pembayaran tidak sesuai", "2": "Bukti tidak valid / tidak terbaca",
           "3": "Dana belum masuk ke rekening kami"}


async def reject_order(ctx, oid, admin_name, reason):
    cur = ex("UPDATE orders SET status='rejected',reviewed_by=?,reviewed_at=?,reason=? WHERE id=? AND status='review'",
             (admin_name, now(), reason, oid))
    if cur.rowcount == 0:
        return False
    o = get_order(oid)
    await refresh_admin_msgs(ctx, oid)
    rows = [[Btn("🔁 Kirim Ulang Bukti", callback_data=f"retry:{oid}")]]
    if support_btn():
        rows.append(support_btn())
    try:
        await ctx.bot.send_message(o["user_id"], f"❌ <b>Pembayaran Belum Dapat Diverifikasi</b>\n\nPesanan: <b>{inv(oid)}</b>\n"
                                   f"Alasan: {esc(reason)}\n\nSilakan kirim ulang bukti yang benar atau hubungi admin.",
                                   parse_mode=HTML, reply_markup=Kb(rows))
    except TelegramError:
        pass
    return True


# ───────────────────────── Callback router ─────────────────────────
async def on_cb(update: Update, ctx):
    cq = update.callback_query
    p = (cq.data or "").split(":")
    uid = cq.from_user.id
    touch(cq.from_user)
    if p[0] == "a":
        if not is_admin(uid):
            return await cq.answer("Akses ditolak", show_alert=True)
        return await admin_cb(update, ctx, p[1:])
    await cq.answer()
    k = p[0]
    if k == "home":
        ctx.user_data.clear()
        await show_home(update, ctx)
    elif k == "plans":
        await show_plans(update, ctx)
    elif k == "plan":
        ctx.user_data.pop("await_promo", None)
        await show_plan(update, ctx, int(p[1]))
    elif k == "promo":
        ctx.user_data["await_promo"] = int(p[1])
        await show(update, ctx, "🏷 <b>Kode Promo</b>\n\nKetik kode promo Anda di chat ini.",
                   Kb([[Btn("⬅ Batal", callback_data=f"plan:{p[1]}")]]))
    elif k == "buy":
        await create_order(update, ctx, int(p[1]))
    elif k == "cancel":
        ex("UPDATE orders SET status='cancelled' WHERE id=? AND user_id=? AND status='pending'", (int(p[1]), uid))
        try:
            await cq.message.delete()
        except TelegramError:
            pass
        await ctx.bot.send_message(uid, "🚫 Pesanan dibatalkan. Anda bisa membuat pesanan baru kapan saja.",
                                   reply_markup=Kb([[Btn("🛒 Beli Akses VIP", callback_data="plans")]]))
    elif k == "retry":
        o = get_order(int(p[1]))
        if o and o["user_id"] == uid and o["status"] == "rejected":
            ex("UPDATE orders SET status='pending',expires_at=? WHERE id=?", (now() + 3600, o["id"]))
            await show(update, ctx, f"📸 Silakan kirim <b>screenshot bukti pembayaran</b> yang benar untuk <b>{inv(o['id'])}</b> "
                                    f"(total {rp(o['total'])}) ke chat ini.", None)
    elif k == "subs":
        await show_subs(update, ctx)
    elif k == "orders":
        await show_orders(update, ctx)
    elif k == "help":
        await show_help(update, ctx)
    elif k == "getlink":
        if not active_sub(uid):
            return await show_subs(update, ctx)
        await send_access(ctx, uid, "🔑 <b>Link Akses Anda</b>")


# ───────────────────────── Admin panel ─────────────────────────
def back(to="a:home", label="⬅ Panel Admin"): return [Btn(label, callback_data=to)]


async def admin_home(update, ctx):
    ctx.user_data.pop("st", None)
    pend = q("SELECT COUNT(*) c FROM orders WHERE status='review'", one=True)["c"]
    act = q("SELECT COUNT(*) c FROM subs WHERE active=1 AND end_ts>?", (now(),), one=True)["c"]
    text = (f"🛠 <b>Panel Admin · {esc(gs('shop'))}</b>\n\n🧾 Menunggu verifikasi: <b>{pend}</b>\n👥 Member aktif: <b>{act}</b>")
    kb = Kb([[Btn("📊 Statistik", callback_data="a:stats"), Btn(f"🧾 Pending ({pend})", callback_data="a:pend")],
             [Btn("📦 Paket", callback_data="a:plans"), Btn("📢 Channel & Link", callback_data="a:ch")],
             [Btn("👥 Member", callback_data="a:mem"), Btn("🏷 Promo", callback_data="a:promo")],
             [Btn("📣 Broadcast", callback_data="a:bc"), Btn("⚙️ Pengaturan", callback_data="a:set")],
             [Btn("📥 Export CSV", callback_data="a:csv"), Btn("⬅ Menu Utama", callback_data="home")]])
    await show(update, ctx, text, kb)


async def cmd_admin(update, ctx):
    if not is_admin(update.effective_user.id):
        return
    touch(update.effective_user)
    await admin_home(update, ctx)


async def cmd_cancel(update, ctx):
    ctx.user_data.clear()
    await update.message.reply_text("Dibatalkan.")


def member_card(uid):
    u = q("SELECT * FROM users WHERE user_id=?", (uid,), one=True)
    if not u:
        return None, None
    s = q("SELECT * FROM subs WHERE user_id=?", (uid,), one=True)
    st = q("SELECT COUNT(*) c, COALESCE(SUM(total),0) t FROM orders WHERE user_id=? AND status='paid'", (uid,), one=True)
    status = (f"✅ Aktif s/d {fmt_d(s['end_ts'])}" if s and s["active"] and s["end_ts"] > now() else "⚪ Tidak aktif")
    text = (f"👤 <b>{esc(u['name'])}</b> (@{esc(u['username'] or '-')})\n🆔 <code>{uid}</code>\n"
            f"Langganan: {status}\nTotal pembelian: {st['c']}x · {rp(st['t'])}\nBergabung: {fmt_d(u['created_at'])}")
    kb = Kb([[Btn("🎁 Beri Akses", callback_data=f"a:gp:{uid}"), Btn("🚫 Cabut Akses", callback_data=f"a:rv:{uid}")],
             back("a:mem", "⬅ Member")])
    return text, kb


async def admin_cb(update, ctx, p):
    cq = update.callback_query
    k = p[0]
    if k not in ("ok", "rj"):
        await cq.answer()
    adm = cq.from_user.full_name
    # ── Home / cancel
    if k in ("home", "cancel"):
        return await admin_home(update, ctx)
    # ── Statistik
    if k == "stats":
        d = datetime.now(TZ)
        t0 = int(d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        m0 = int(d.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())

        def rev(since): return q("SELECT COALESCE(SUM(total),0) t, COUNT(*) c FROM orders WHERE status='paid' AND reviewed_at>=?", (since,), one=True)
        a, b, c = rev(t0), rev(m0), rev(0)
        users = q("SELECT COUNT(*) c FROM users", one=True)["c"]
        buyers = q("SELECT COUNT(DISTINCT user_id) c FROM orders WHERE status='paid'", one=True)["c"]
        act = q("SELECT COUNT(*) c FROM subs WHERE active=1 AND end_ts>?", (now(),), one=True)["c"]
        exp3 = q("SELECT COUNT(*) c FROM subs WHERE active=1 AND end_ts BETWEEN ? AND ?", (now(), now() + 3 * 86400), one=True)["c"]
        top = q("SELECT plan_name, COUNT(*) c FROM orders WHERE status='paid' GROUP BY plan_name ORDER BY c DESC LIMIT 3")
        text = (f"📊 <b>Statistik Penjualan</b>\n\n💵 Hari ini: <b>{rp(a['t'])}</b> ({a['c']} order)\n"
                f"📅 Bulan ini: <b>{rp(b['t'])}</b> ({b['c']} order)\n🏦 Total: <b>{rp(c['t'])}</b> ({c['c']} order)\n\n"
                f"👥 Total user: {users}\n🛍 Pembeli: {buyers} ({(buyers * 100 // users) if users else 0}% konversi)\n"
                f"✅ Member aktif: {act}\n⏰ Berakhir ≤3 hari: {exp3}\n")
        if top:
            text += "\n🏆 <b>Paket terlaris</b>\n" + "\n".join(f"• {esc(t['plan_name'])} — {t['c']}x" for t in top)
        return await show(update, ctx, text, Kb([back()]))
    # ── Pending
    if k == "pend":
        rows = q("SELECT * FROM orders WHERE status='review' ORDER BY id")
        if not rows:
            return await show(update, ctx, "🧾 Tidak ada pembayaran yang menunggu verifikasi. 🎉", Kb([back()]))
        await show(update, ctx, f"🧾 Mengirim {len(rows)} pembayaran pending di bawah 👇", Kb([back()]))
        for o in rows:
            await send_review_card(ctx, cq.from_user.id, o)
        return
    # ── Setujui / Tolak
    if k == "ok":
        oid = int(p[1])
        cur = ex("UPDATE orders SET status='paid',reviewed_by=?,reviewed_at=? WHERE id=? AND status='review'", (adm, now(), oid))
        if cur.rowcount == 0:
            await cq.answer("Pesanan ini sudah diproses.", show_alert=True)
            return await refresh_admin_msgs(ctx, oid)
        o = get_order(oid)
        end = activate(o["user_id"], o["plan_name"], o["days"])
        if o["promo"]:
            ex("UPDATE promos SET used=used+1 WHERE code=?", (o["promo"],))
        await cq.answer("Disetujui ✅")
        await refresh_admin_msgs(ctx, oid)
        head = (f"🎉 <b>Pembayaran Berhasil!</b>\n\nTerima kasih, pesanan <b>{inv(oid)}</b> telah disetujui.\n\n"
                f"📦 Paket: {esc(o['plan_name'])}\n📅 Aktif hingga: <b>{fmt_dt(end)}</b>")
        fails, has_links = await send_access(ctx, o["user_id"], head)
        if fails or not has_links:
            msg = ("⚠️ Pesanan %s disetujui, tapi link belum terkirim untuk: %s.\nPastikan bot adalah <b>admin</b> channel dengan izin "
                   "<i>Invite Users via Link</i>, lalu minta member tekan <b>Ambil Link Akses</b>."
                   % (inv(oid), esc(", ".join(fails) if fails else "belum ada channel terdaftar")))
            await ctx.bot.send_message(cq.from_user.id, msg, parse_mode=HTML)
        return
    if k == "no":
        oid = int(p[1])
        kb = Kb([[Btn(v, callback_data=f"a:rj:{oid}:{i}")] for i, v in REASONS.items()] +
                [[Btn("✍️ Tulis alasan sendiri", callback_data=f"a:rj:{oid}:0")],
                 [Btn("⬅ Batal", callback_data=f"a:bk:{oid}")]])
        try:
            await cq.edit_message_reply_markup(kb)
        except TelegramError:
            pass
        return
    if k == "bk":
        oid = int(p[1])
        try:
            await cq.edit_message_reply_markup(Kb([[Btn("✅ Setujui", callback_data=f"a:ok:{oid}"),
                                                    Btn("❌ Tolak", callback_data=f"a:no:{oid}")]]))
        except TelegramError:
            pass
        return
    if k == "rj":
        oid, rid = int(p[1]), p[2]
        o = get_order(oid)
        if not o or o["status"] != "review":
            await cq.answer("Pesanan ini sudah diproses.", show_alert=True)
            return await refresh_admin_msgs(ctx, oid)
        if rid == "0":
            await cq.answer()
            ctx.user_data["st"] = {"k": "reason", "oid": oid}
            return await ctx.bot.send_message(cq.from_user.id, f"✍️ Ketik alasan penolakan untuk <b>{inv(oid)}</b>:\n(/cancel untuk batal)",
                                              parse_mode=HTML)
        await reject_order(ctx, oid, adm, REASONS[rid])
        return await cq.answer("Ditolak ❌")
    # ── Paket
    if k == "plans":
        rows = q("SELECT * FROM plans ORDER BY price")
        kb = [[Btn(f"{'🟢' if r['active'] else '⚪'} {r['name']} · {rp(r['price'])} · {r['days']}h", callback_data=f"a:plan:{r['id']}")] for r in rows]
        kb += [[Btn("➕ Tambah Paket", callback_data="a:pnew")], back()]
        return await show(update, ctx, "📦 <b>Kelola Paket</b>\n\nPilih paket untuk diedit, atau tambah paket baru.", Kb(kb))
    if k == "plan":
        r = get_plan(int(p[1]))
        if not r:
            return await admin_cb(update, ctx, ["plans"])
        text = (f"📦 <b>{esc(r['name'])}</b>\n💰 {rp(r['price'])}\n⏱ {r['days']} hari\n📝 {esc(r['description'] or '-')}\n"
                f"Status: {'🟢 Aktif' if r['active'] else '⚪ Nonaktif'}")
        i = r["id"]
        kb = Kb([[Btn("✏️ Nama", callback_data=f"a:pe:{i}:name"), Btn("✏️ Harga", callback_data=f"a:pe:{i}:price")],
                 [Btn("✏️ Durasi", callback_data=f"a:pe:{i}:days"), Btn("✏️ Deskripsi", callback_data=f"a:pe:{i}:desc")],
                 [Btn("🔛 Aktif/Nonaktif", callback_data=f"a:pt:{i}"), Btn("🗑 Hapus", callback_data=f"a:pd:{i}")],
                 back("a:plans", "⬅ Daftar Paket")])
        return await show(update, ctx, text, kb)
    if k == "pnew":
        ctx.user_data["st"] = {"k": "plan_new"}
        return await show(update, ctx, "➕ <b>Tambah Paket</b>\n\nKirim dengan format:\n<code>Nama | Harga | Durasi(hari) | Deskripsi</code>\n\n"
                                       "Contoh:\n<code>VIP 30 Hari | 99000 | 30 | Akses semua channel + update harian</code>",
                          Kb([back("a:plans", "⬅ Batal")]))
    if k == "pe":
        ctx.user_data["st"] = {"k": "plan_edit", "id": int(p[1]), "f": p[2]}
        names = {"name": "nama", "price": "harga (angka)", "days": "durasi (hari)", "desc": "deskripsi"}
        return await show(update, ctx, f"✏️ Kirim <b>{names[p[2]]}</b> baru:", Kb([back(f"a:plan:{p[1]}", "⬅ Batal")]))
    if k == "pt":
        ex("UPDATE plans SET active=1-active WHERE id=?", (int(p[1]),))
        return await admin_cb(update, ctx, ["plan", p[1]])
    if k == "pd":
        return await show(update, ctx, "🗑 Yakin hapus paket ini?", Kb([[Btn("Ya, hapus", callback_data=f"a:pdy:{p[1]}"),
                                                                       Btn("Batal", callback_data=f"a:plan:{p[1]}")]]))
    if k == "pdy":
        ex("DELETE FROM plans WHERE id=?", (int(p[1]),))
        return await admin_cb(update, ctx, ["plans"])
    # ── Channel & Link
    if k == "ch":
        rows = q("SELECT * FROM channels ORDER BY id")
        text = "📢 <b>Channel &amp; Link Akses</b>\n\n"
        text += "\n".join(f"{'🟢' if r['active'] else '⚪'} <b>{esc(r['title'])}</b> — {'🔐 link unik otomatis' if r['chat_id'] else '⚠️ link statis'}"
                          for r in rows) if rows else "Belum ada channel."
        text += ("\n\n<i>Tips: tambahkan channel via Chat ID/forward agar bot membuat link unik per pembeli "
                 "(anti-bocor). Bot harus jadi admin channel dengan izin Invite Users.</i>")
        kb = [[Btn(f"{'⚪ Nonaktifkan' if r['active'] else '🟢 Aktifkan'} {r['title'][:14]}", callback_data=f"a:cht:{r['id']}"),
               Btn("🔍", callback_data=f"a:chc:{r['id']}"), Btn("🗑", callback_data=f"a:chd:{r['id']}")] for r in rows]
        kb += [[Btn("➕ Tambah Channel / Link", callback_data="a:chnew")], back()]
        return await show(update, ctx, text, Kb(kb))
    if k == "chnew":
        ctx.user_data["st"] = {"k": "ch_new"}
        return await show(update, ctx, "➕ <b>Tambah Channel</b>\n\nPilih salah satu cara:\n"
                                       "1️⃣ <b>Forward</b> 1 pesan dari channel tersebut ke sini (disarankan)\n"
                                       "2️⃣ Kirim <code>Nama | -100123456789</code> atau <code>Nama | @usernamechannel</code>\n"
                                       "3️⃣ Link statis: <code>Nama | https://t.me/+abcd</code> (kurang aman)",
                          Kb([back("a:ch", "⬅ Batal")]))
    if k == "cht":
        ex("UPDATE channels SET active=1-active WHERE id=?", (int(p[1]),))
        return await admin_cb(update, ctx, ["ch"])
    if k == "chd":
        ex("DELETE FROM channels WHERE id=?", (int(p[1]),))
        return await admin_cb(update, ctx, ["ch"])
    if k == "chc":
        r = q("SELECT * FROM channels WHERE id=?", (int(p[1]),), one=True)
        if not r or not r["chat_id"]:
            return await cq.answer("Link statis — tidak ada yang dicek.", show_alert=True)
        ok, info = await check_bot_in_channel(ctx, r["chat_id"])
        return await cq.answer(("✅ Bot siap: " if ok else "❌ ") + info, show_alert=True)
    # ── Member
    if k == "mem":
        rows = q("SELECT s.*, u.name, u.username FROM subs s LEFT JOIN users u USING(user_id) WHERE s.active=1 AND s.end_ts>? "
                 "ORDER BY s.end_ts LIMIT 25", (now(),))
        text = "👥 <b>Member Aktif</b> (urut paling cepat habis)\n\n"
        text += "\n".join(f"• {esc(r['name'])} (<code>{r['user_id']}</code>) — {fmt_d(r['end_ts'])}" for r in rows) if rows else "Belum ada member aktif."
        return await show(update, ctx, text, Kb([[Btn("🔎 Cari / Kelola Member", callback_data="a:find")], back()]))
    if k == "find":
        ctx.user_data["st"] = {"k": "find"}
        return await show(update, ctx, "🔎 Kirim <b>User ID</b> atau <b>@username</b> member:", Kb([back("a:mem", "⬅ Batal")]))
    if k == "mc":
        text, kb = member_card(int(p[1]))
        return await show(update, ctx, text, kb)
    if k == "gp":
        plans = q("SELECT * FROM plans WHERE active=1")
        kb = [[Btn(f"{r['name']} ({r['days']} hari)", callback_data=f"a:gr:{p[1]}:{r['id']}")] for r in plans]
        kb.append(back(f"a:mc:{p[1]}", "⬅ Batal"))
        return await show(update, ctx, "🎁 Pilih paket yang akan diberikan <b>gratis/manual</b>:", Kb(kb))
    if k == "gr":
        uid, plan = int(p[1]), get_plan(int(p[2]))
        end = activate(uid, plan["name"], plan["days"])
        await send_access(ctx, uid, f"🎁 <b>Akses VIP Diberikan</b>\n\n📦 Paket: {esc(plan['name'])}\n📅 Aktif hingga: <b>{fmt_dt(end)}</b>")
        return await admin_cb(update, ctx, ["mc", p[1]])
    if k == "rv":
        uid = int(p[1])
        ex("UPDATE subs SET active=0 WHERE user_id=?", (uid,))
        await remove_from_channels(ctx, uid)
        try:
            await ctx.bot.send_message(uid, "ℹ️ Akses VIP Anda telah dinonaktifkan oleh admin.")
        except TelegramError:
            pass
        return await admin_cb(update, ctx, ["mc", p[1]])
    # ── Promo
    if k == "promo":
        rows = q("SELECT * FROM promos ORDER BY code")
        text = "🏷 <b>Kode Promo</b>\n\n" + ("\n".join(f"• <code>{r['code']}</code> — {r['percent']}% · terpakai {r['used']}/{r['max_uses'] or '∞'}" for r in rows) if rows else "Belum ada promo.")
        kb = [[Btn(f"🗑 {r['code']}", callback_data=f"a:prd:{r['code']}")] for r in rows]
        kb += [[Btn("➕ Buat Promo", callback_data="a:prnew")], back()]
        return await show(update, ctx, text, Kb(kb))
    if k == "prnew":
        ctx.user_data["st"] = {"k": "promo_new"}
        return await show(update, ctx, "➕ Kirim: <code>KODE | Persen | MaksPemakaian</code>\nContoh: <code>HEMAT20 | 20 | 50</code>\n(0 = tanpa batas)",
                          Kb([back("a:promo", "⬅ Batal")]))
    if k == "prd":
        ex("DELETE FROM promos WHERE code=?", (p[1],))
        return await admin_cb(update, ctx, ["promo"])
    # ── Broadcast
    if k == "bc":
        kb = Kb([[Btn("📢 Semua user", callback_data="a:bct:all")],
                 [Btn("✅ Member aktif", callback_data="a:bct:active")],
                 [Btn("🔄 Member expired (ajak perpanjang)", callback_data="a:bct:expired")],
                 [Btn("🎯 Belum pernah beli (promo)", callback_data="a:bct:never")], back()])
        return await show(update, ctx, "📣 <b>Broadcast</b>\n\nPilih target penerima:", kb)
    if k == "bct":
        ctx.user_data["st"] = {"k": "bc", "t": p[1]}
        return await show(update, ctx, "✍️ Kirim pesan yang ingin di-broadcast (teks / foto + caption).\nSaya tampilkan preview dulu sebelum dikirim.",
                          Kb([back("a:bc", "⬅ Batal")]))
    if k == "bcgo":
        st = ctx.user_data.pop("st", None)
        if not st or st.get("k") != "bc_ready":
            return
        ids = broadcast_targets(st["t"])
        await cq.edit_message_text(f"🚀 Broadcast dimulai ke {len(ids)} user...")
        ctx.application.create_task(run_broadcast(ctx, cq.from_user.id, ids, st["chat"], st["msg"]))
        return
    # ── Pengaturan
    if k == "set":
        text = (f"⚙️ <b>Pengaturan</b>\n\n🏪 Nama toko: {esc(gs('shop'))}\n💬 Support: {esc(gs('support') or '-')}\n"
                f"⏳ Batas bayar: {gs('expiry')} menit\n🔢 Kode unik: {'ON' if gs('unique') == '1' else 'OFF'}\n"
                f"🖼 QRIS: {'✅ terpasang' if gs('qris') else '❌ belum diatur'}\n👋 Welcome: {esc((gs('welcome') or '(default)')[:60])}")
        kb = Kb([[Btn("🖼 Ganti QRIS", callback_data="a:s:qris"), Btn("🏪 Nama Toko", callback_data="a:s:shop")],
                 [Btn("👋 Pesan Welcome", callback_data="a:s:welcome"), Btn("💬 Kontak Support", callback_data="a:s:support")],
                 [Btn("⏳ Batas Bayar", callback_data="a:s:expiry"), Btn("🔢 Kode Unik ON/OFF", callback_data="a:s:uq")], back()])
        return await show(update, ctx, text, kb)
    if k == "s":
        key = p[1]
        if key == "uq":
            ss("unique", "0" if gs("unique") == "1" else "1")
            return await admin_cb(update, ctx, ["set"])
        ctx.user_data["st"] = {"k": "qris"} if key == "qris" else {"k": "set", "key": key}
        prompts = {"qris": "🖼 Kirim <b>gambar QRIS</b> (sebagai foto).", "shop": "🏪 Kirim nama toko/brand.",
                   "welcome": "👋 Kirim pesan sambutan (kirim <code>-</code> untuk reset default).",
                   "support": "💬 Kirim username admin support, mis. <code>@adminku</code> (kirim <code>-</code> untuk hapus).",
                   "expiry": "⏳ Kirim batas waktu bayar dalam <b>menit</b>, mis. <code>30</code>."}
        return await show(update, ctx, prompts[key], Kb([back("a:set", "⬅ Batal")]))
    # ── CSV
    if k == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["invoice", "user_id", "paket", "total", "status", "dibuat", "diverifikasi", "oleh", "promo"])
        for o in q("SELECT * FROM orders ORDER BY id"):
            w.writerow([inv(o["id"]), o["user_id"], o["plan_name"], o["total"], o["status"], fmt_dt(o["created_at"]),
                        fmt_dt(o["reviewed_at"]) if o["reviewed_at"] else "", o["reviewed_by"] or "", o["promo"] or ""])
        f = io.BytesIO(buf.getvalue().encode())
        f.name = f"orders_{datetime.now(TZ):%Y%m%d}.csv"
        await ctx.bot.send_document(cq.from_user.id, f, caption="📥 Export semua pesanan")


async def check_bot_in_channel(ctx, chat_id):
    try:
        m = await ctx.bot.get_chat_member(chat_id, ctx.bot.id)
        if m.status != ChatMemberStatus.ADMINISTRATOR:
            return False, "bot belum jadi admin channel"
        if not getattr(m, "can_invite_users", False):
            return False, "izin 'Invite Users via Link' belum aktif"
        return True, "bot admin & izin lengkap"
    except TelegramError as e:
        return False, f"tidak bisa akses channel ({e})"


def broadcast_targets(t):
    if t == "active":
        r = q("SELECT user_id FROM subs WHERE active=1 AND end_ts>?", (now(),))
    elif t == "expired":
        r = q("SELECT s.user_id FROM subs s JOIN users u USING(user_id) WHERE u.blocked=0 AND (s.active=0 OR s.end_ts<=?)", (now(),))
    elif t == "never":
        r = q("SELECT user_id FROM users WHERE blocked=0 AND user_id NOT IN (SELECT user_id FROM orders WHERE status='paid')")
    else:
        r = q("SELECT user_id FROM users WHERE blocked=0")
    return [x["user_id"] for x in r]


async def run_broadcast(ctx, admin_id, ids, chat, msg):
    ok = fail = 0
    for uid in ids:
        for attempt in range(2):
            try:
                await ctx.bot.copy_message(uid, chat, msg)
                ok += 1
                break
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except Forbidden:
                ex("UPDATE users SET blocked=1 WHERE user_id=?", (uid,))
                fail += 1
                break
            except TelegramError:
                fail += 1
                break
        await asyncio.sleep(0.06)
    await ctx.bot.send_message(admin_id, f"📣 <b>Broadcast selesai</b>\n✅ Terkirim: {ok}\n❌ Gagal: {fail}", parse_mode=HTML)


# ───────────────────────── Input teks admin (state) ─────────────────────────
async def admin_input(update, ctx):
    st, m = ctx.user_data["st"], update.message
    t = (m.text or "").strip()
    k = st["k"]
    reply = lambda s, **kw: m.reply_text(s, parse_mode=HTML, **kw)
    done_kb = Kb([[Btn("🛠 Panel Admin", callback_data="a:home")]])

    if k == "qris":
        if not m.photo:
            await reply("Kirim QRIS sebagai <b>foto</b> ya.")
            return True
        ss("qris", m.photo[-1].file_id)
        ctx.user_data.pop("st")
        await reply("✅ QRIS berhasil diperbarui.", reply_markup=done_kb)
        return True
    if k == "bc":
        ctx.user_data["st"] = {"k": "bc_ready", "t": st["t"], "chat": m.chat_id, "msg": m.message_id}
        await reply(f"👆 <b>Preview</b> — target: <b>{st['t']}</b> ({len(broadcast_targets(st['t']))} user). Kirim sekarang?",
                    reply_markup=Kb([[Btn("🚀 Kirim", callback_data="a:bcgo"), Btn("❌ Batal", callback_data="a:home")]]),
                    reply_to_message_id=m.message_id)
        return True
    if k == "ch_new":
        title = chat_id = url = None
        fo = getattr(m, "forward_origin", None)
        if fo is not None and getattr(fo, "chat", None):
            chat_id, title = fo.chat.id, fo.chat.title
        elif t:
            parts = [x.strip() for x in t.split("|")]
            title, val = (parts[0], parts[1]) if len(parts) == 2 else (None, parts[0])
            if val.lstrip("-").isdigit():
                chat_id = int(val)
            elif val.startswith("@"):
                chat_id = val
            elif val.startswith("http"):
                url = val
        if chat_id is None and not url:
            await reply("Format tidak dikenali. Forward pesan channel atau gunakan format yang dicontohkan.")
            return True
        if chat_id is not None:
            try:
                chat = await ctx.bot.get_chat(chat_id)
            except TelegramError as e:
                await reply(f"❌ Channel tidak ditemukan / bot belum ditambahkan: {esc(e)}")
                return True
            chat_id, title = chat.id, title or chat.title
            ok, info = await check_bot_in_channel(ctx, chat_id)
            if not ok:
                await reply(f"❌ {esc(info)}.\nJadikan bot admin channel dengan izin <b>Invite Users via Link</b>, lalu coba lagi.")
                return True
        ex("INSERT INTO channels(title,chat_id,url) VALUES(?,?,?)", (title or "Channel VIP", chat_id, url))
        ctx.user_data.pop("st")
        await reply(f"✅ <b>{esc(title or 'Channel VIP')}</b> ditambahkan." + ("" if chat_id else "\n⚠️ Link statis bisa dibagikan pembeli; disarankan pakai Chat ID."),
                    reply_markup=Kb([[Btn("📢 Daftar Channel", callback_data="a:ch")]]))
        return True
    if not t:
        return False
    if k == "reason":
        ctx.user_data.pop("st")
        ok = await reject_order(ctx, st["oid"], update.effective_user.full_name, t[:200])
        await reply("❌ Pesanan ditolak & pembeli diberi tahu." if ok else "Pesanan ini sudah diproses.", reply_markup=done_kb)
        return True
    if k == "plan_new":
        parts = [x.strip() for x in t.split("|")]
        if len(parts) < 3 or not parts[1].isdigit() or not parts[2].isdigit():
            await reply("Format salah. Contoh: <code>VIP 30 Hari | 99000 | 30 | Deskripsi</code>")
            return True
        ex("INSERT INTO plans(name,price,days,description) VALUES(?,?,?,?)",
           (parts[0], int(parts[1]), int(parts[2]), parts[3] if len(parts) > 3 else ""))
        ctx.user_data.pop("st")
        await reply("✅ Paket ditambahkan.", reply_markup=Kb([[Btn("📦 Daftar Paket", callback_data="a:plans")]]))
        return True
    if k == "plan_edit":
        col = {"name": "name", "price": "price", "days": "days", "desc": "description"}[st["f"]]
        val = t
        if st["f"] in ("price", "days"):
            if not t.isdigit():
                await reply("Masukkan angka saja.")
                return True
            val = int(t)
        ex(f"UPDATE plans SET {col}=? WHERE id=?", (val, st["id"]))
        ctx.user_data.pop("st")
        await reply("✅ Paket diperbarui.", reply_markup=Kb([[Btn("📦 Lihat Paket", callback_data=f"a:plan:{st['id']}")]]))
        return True
    if k == "promo_new":
        parts = [x.strip() for x in t.split("|")]
        try:
            code, pct, mx = parts[0].upper().replace(" ", ""), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0
            assert code.isalnum() and 1 <= pct <= 99
        except Exception:
            await reply("Format salah. Contoh: <code>HEMAT20 | 20 | 50</code> (persen 1–99)")
            return True
        ex("INSERT INTO promos(code,percent,max_uses) VALUES(?,?,?) ON CONFLICT(code) DO UPDATE SET percent=excluded.percent,"
           "max_uses=excluded.max_uses,active=1", (code, pct, mx))
        ctx.user_data.pop("st")
        await reply(f"✅ Promo <code>{code}</code> aktif.", reply_markup=Kb([[Btn("🏷 Daftar Promo", callback_data="a:promo")]]))
        return True
    if k == "set":
        key, val = st["key"], t
        if key == "expiry":
            if not t.isdigit() or not 5 <= int(t) <= 1440:
                await reply("Masukkan angka menit (5–1440).")
                return True
        if key in ("welcome", "support") and t == "-":
            val = ""
        ss(key, val)
        ctx.user_data.pop("st")
        await reply("✅ Pengaturan disimpan.", reply_markup=Kb([[Btn("⚙️ Pengaturan", callback_data="a:set")]]))
        return True
    if k == "find":
        r = q("SELECT user_id FROM users WHERE user_id=? OR lower(username)=lower(?)",
              (int(t) if t.lstrip("-").isdigit() else 0, t.lstrip("@")), one=True)
        if not r:
            await reply("User tidak ditemukan (user harus pernah /start bot).")
            return True
        ctx.user_data.pop("st")
        text, kb = member_card(r["user_id"])
        await reply(text, reply_markup=kb)
        return True
    return False


async def on_message(update: Update, ctx):
    m, u = update.message, update.effective_user
    touch(u)
    if is_admin(u.id) and ctx.user_data.get("st"):
        if await admin_input(update, ctx):
            return
    if ctx.user_data.get("await_promo") and m.text:
        return await handle_promo(update, ctx)
    if m.photo or (m.document and (m.document.mime_type or "").startswith("image/")):
        return await handle_proof(update, ctx)
    await show_home(update, ctx)


# ───────────────────────── Jobs otomatis ─────────────────────────
async def maintenance(ctx: ContextTypes.DEFAULT_TYPE):
    # invoice kedaluwarsa
    for o in q("SELECT * FROM orders WHERE status='pending' AND expires_at<?", (now(),)):
        ex("UPDATE orders SET status='expired' WHERE id=?", (o["id"],))
        try:
            await ctx.bot.send_message(o["user_id"], f"⌛ Invoice <b>{inv(o['id'])}</b> telah kedaluwarsa. Buat pesanan baru kapan saja 👇",
                                       parse_mode=HTML, reply_markup=Kb([[Btn("🛒 Beli Akses VIP", callback_data="plans")]]))
        except TelegramError:
            pass
    # pengingat H-3
    for s in q("SELECT * FROM subs WHERE active=1 AND reminded=0 AND end_ts BETWEEN ? AND ?", (now(), now() + 3 * 86400)):
        ex("UPDATE subs SET reminded=1 WHERE user_id=?", (s["user_id"],))
        try:
            await ctx.bot.send_message(s["user_id"], f"⏰ <b>Pengingat</b>\n\nAkses VIP Anda berakhir pada <b>{fmt_dt(s['end_ts'])}</b>.\n"
                                       "Perpanjang sekarang agar akses tidak terputus — sisa hari tetap dihitung! 🎁",
                                       parse_mode=HTML, reply_markup=Kb([[Btn("🔄 Perpanjang Sekarang", callback_data="plans")]]))
        except TelegramError:
            pass
    # langganan habis → keluarkan dari channel
    for s in q("SELECT * FROM subs WHERE active=1 AND end_ts<=?", (now(),)):
        ex("UPDATE subs SET active=0 WHERE user_id=?", (s["user_id"],))
        await remove_from_channels(ctx, s["user_id"])
        try:
            await ctx.bot.send_message(s["user_id"], "⌛ <b>Masa aktif VIP Anda telah berakhir.</b>\n\nTerima kasih sudah bergabung! "
                                       "Perpanjang untuk kembali mendapat akses 👇", parse_mode=HTML,
                                       reply_markup=Kb([[Btn("🔄 Perpanjang Sekarang", callback_data="plans")]]))
        except TelegramError:
            pass


async def on_error(update, ctx):
    log.error("Error: %s", ctx.error, exc_info=ctx.error)


def main():
    if not ADMIN_IDS:
        log.warning("ADMIN_IDS kosong! Set env ADMIN_IDS agar admin bisa memakai bot.")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(ChatJoinRequestHandler(on_join_request))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, on_message))
    app.add_error_handler(on_error)
    app.job_queue.run_repeating(maintenance, interval=900, first=30)
    log.info("Bot berjalan. DB: %s", DB_PATH)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()

