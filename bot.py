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
    s = (f"🔔 <b>PEMBAYARAN BARU · {inv(o['id'])}</b>\n\n"
         f"👤 <a hr
