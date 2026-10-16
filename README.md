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

## 🎁 Trial gratis (hitungan menit)
1. `/admin` → **🎁 Trial Gratis**.
2. **🔗 Atur Channel Trial** → pilih channel yang boleh dimasuki saat trial (wajib minimal 1).
3. **⏱ Ubah Durasi** → isi menit (1–1440), lalu **🟢 Aktifkan Trial**.
- Tombol "🎁 Coba Gratis" muncul di menu utama untuk member baru (belum pernah trial/beli).
- 1 akun hanya bisa trial **1x**.
- Saat waktu habis, bot otomatis mengeluarkan user dari channel lalu mengirim ajakan **Lanjutkan ke Pembayaran**.
- Pengecekan berjalan tiap 1 menit, jadi kick bisa terlambat maksimal ±1 menit.
- Jika user membeli paket saat trial masih berjalan, masa paket dihitung mulai saat itu.

## 🔗 Link channel khusus per paket
Contoh: Paket A → Link 1 & 3, Paket B → Link 2, Paket C → Link 4.
1. Tambahkan semua channel dulu di **📢 Channel & Link**.
2. `/admin` → **📦 Paket** → pilih paket → **🔗 Atur Channel Paket**.
3. Ketuk channel untuk memilih (✅) / membatalkan (⬜).
- Paket yang belum diatur otomatis mendapat **semua channel aktif** (default).
- Bot hanya mengirim link & menerima join request sesuai channel paket pembeli.
- Jika pembeli ganti paket, channel paket lama yang tidak ada di paket baru otomatis di-kick.
- Detail paket di sisi pembeli menampilkan nama channel yang didapat.

## 🧹 Reset statistik & riwayat
`/admin` → **⚙️ Pengaturan** → **🧹 Reset Data**
- **📊 Reset Statistik**: angka pendapatan & penjualan mulai dari nol, riwayat pesanan tetap aman. Bisa dikembalikan lewat **↩️ Tampilkan Semua Data Lagi**.
- **🗑 Hapus Riwayat Pesanan**: menghapus permanen pesanan selesai (berhasil/ditolak/dibatalkan/kedaluwarsa). Backup CSV dikirim otomatis sebelum dihapus. Member aktif dan pesanan yang sedang diproses tidak ikut terhapus.

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
