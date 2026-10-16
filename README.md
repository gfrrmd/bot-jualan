# 🤖 VIP Channel Sales Bot (QRIS + Verifikasi Admin)

## Fitur
- Pilih paket → invoice QRIS (kode unik otomatis) → upload bukti → admin Setujui/Tolak
- Link channel **unik per pembeli** (mode Request-to-Join, auto-approve hanya untuk pembeli aktif) → tidak bisa dibagikan
- Admin kelola dari bot: paket, channel/link, QRIS, promo, broadcast, member, statistik, export CSV
- Otomatis: invoice kedaluwarsa, pengingat H-3, kick member saat masa aktif habis

## 1. Buat bot
1. Chat **@BotFather** → `/newbot` → simpan **BOT_TOKEN**.
2. Cari Telegram ID kamu di **@userinfobot** → itu **ADMIN_IDS** (pisahkan koma jika lebih dari satu admin).
3. Semua admin harus sudah menekan **/start** di bot kamu.

## 2. Siapkan channel VIP
1. Tambahkan bot sebagai **Administrator** di channel VIP.
2. Aktifkan izin **Invite Users via Link** (wajib). Izin lain boleh dimatikan.
3. Untuk fitur auto-kick saat expired, aktifkan juga **Ban Users**.

## 3. Deploy ke Railway
1. Upload folder ini ke **GitHub** (repo baru).
2. Railway → **New Project → Deploy from GitHub repo** → pilih repo.
3. Tab **Variables**, tambahkan:
   - `BOT_TOKEN` = token dari BotFather
   - `ADMIN_IDS` = `11111111,22222222`
   - `TIMEZONE` = `Asia/Jakarta` (opsional)
4. **PENTING – Volume (agar data tidak hilang saat redeploy):**
   Klik kanan project canvas → **Add Volume** → hubungkan ke service → **Mount path: `/data`**.
   Bot otomatis menyimpan database di `/data/bot.db`.
5. Tab **Settings → Deploy**: Start Command isi `python bot.py` (atau biarkan, Procfile sudah ada).
   Bot ini memakai polling, jadi **tidak perlu domain/port**.
6. Lihat **Deployments → Logs**, harus muncul `Bot berjalan`.

## 4. Setup awal lewat bot (ketik /admin)
1. **⚙️ Pengaturan → 🖼 Ganti QRIS** → kirim gambar QRIS toko kamu sebagai **foto**.
   (QRIS statis dari bank/e-wallet/payment gateway kamu; screenshot/gambar jelas. Bot akan mengirim gambar ini ke setiap pembeli.)
2. **📦 Paket → ➕ Tambah Paket** → `VIP 30 Hari | 99000 | 30 | Akses semua channel`
3. **📢 Channel & Link → ➕ Tambah** → *forward 1 pesan dari channel* ke bot (paling mudah).
   Tombol 🔍 untuk mengecek izin bot, 🗑 untuk menghapus, atau nonaktifkan sementara.
4. (Opsional) Isi nama toko, pesan welcome, kontak support, kode promo.
5. Tes: buka akun lain → /start → beli → kirim bukti → setujui dari akun admin.

## Alur verifikasi
Pembeli kirim bukti → bot kirim foto + detail ke semua admin → admin cek mutasi lalu tekan
**✅ Setujui** (link dikirim otomatis) atau **❌ Tolak** (pilih alasan; pembeli bisa kirim ulang bukti).
Pesanan yang sudah diproses satu admin otomatis terkunci untuk admin lain.

## Cara kerja auto-accept join request
1. Setelah admin menyetujui pembayaran, bot membuat link khusus pembeli (mode *Request to Join*).
2. Pembeli menekan tombol → *Request to Join / Minta Bergabung*.
3. Bot (admin channel) langsung **menerima otomatis** jika langganan pembeli aktif, lalu mengirim konfirmasi.
4. Jika bukan pembeli aktif (misal link bocor), request **ditolak otomatis**.
5. Link pribadi dicabut (revoke) setelah dipakai sekali.
Syarat: bot admin channel dengan izin **Invite Users via Link**. Bot harus berjalan 24 jam (Railway).

## Catatan keamanan link
- Tombol link dikirim dengan `protect_content` (tidak bisa di-forward/disalin).
- Link dibuat per pembeli, kedaluwarsa 24 jam, dan butuh persetujuan bot: orang lain yang memakai link yang bocor akan **ditolak otomatis**.
- Channel yang ditambahkan lewat link statis (tanpa Chat ID) tidak mendapat perlindungan ini.

## Jalankan lokal
```
pip install -r requirements.txt
export BOT_TOKEN=xxx ADMIN_IDS=123
python bot.py
```
