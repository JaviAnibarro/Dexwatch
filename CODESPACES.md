# Menjalankan dexwatch di GitHub Codespaces

Codespaces menjalankan VS Code + terminal di cloud, jadi dashboard bisa dibuka
lewat URL HTTPS tanpa install apa pun di komputer. Panduan ini memakai
devcontainer yang sudah disertakan (`.devcontainer/devcontainer.json`) dan
launcher `codespace.sh`.

## Ringkas (5 langkah)

```bash
# 1) di komputer, dari folder proyek
cd /root/deep/dexwatch
git init
git add .
git commit -m "dexwatch: dashboard pool baru"
git branch -M main
git remote add origin https://github.com/<username>/dexwatch.git
git push -u origin main
```

2. Buka repo di GitHub → **Code** → **Codespaces** → **Create codespace on main**
   (atau `https://codespaces.new/<username>/dexwatch`).
3. Tunggu container selesai dibuat (Python 3.12 + validasi sintaks).
4. Di terminal Codespace:

   ```bash
   ./codespace.sh
   ```

5. Tab **Ports** (di samping Terminal) → port **8787** → **Open in Browser**.
   URL-nya berbentuk `https://<nama-codespace>-8787.app.github.dev`.

Selesai. Dashboard langsung menampilkan pool baru dan terus memindai.

## 1. Siapkan repo GitHub

Codespaces selalu dibuat dari repo GitHub. Pilih salah satu:

**A. Dari terminal (disarankan)**

Buat repo kosong di GitHub (tanpa README), lalu:

```bash
cd /root/deep/dexwatch
git init
git add .
git commit -m "dexwatch: dashboard pool baru"
git branch -M main
git remote add origin https://github.com/<username>/dexwatch.git
git push -u origin main
```

**B. Tanpa git CLI**

Buat repo di GitHub → **Add file** → **Upload files** → seret isi folder
`dexwatch` (termasuk `.devcontainer`, `dexwatch/`, `tests/`, `docs/`,
`README.md`, `pyproject.toml`, `dexwatch.sh`, `codespace.sh`). Jangan upload
`*.db`, `__pycache__`, atau file `.log`.

> File database (`dexwatch.db`) sudah masuk `.gitignore`; jangan di-commit.

## 2. Buat Codespace

Di halaman repo: **Code** → **Codespaces** → **Create codespace on main**.
Atau buka `https://codespaces.new/<username>/dexwatch`.

Container akan:

- memakai image Python 3.12,
- meneruskan port **8787** otomatis,
- memvalidasi sintaks (`python3 -m compileall -q dexwatch`).

Butuh 1–3 menit pada pembuatan pertama. Tidak ada dependensi eksternal, jadi
tidak perlu `pip install`. Kalau ingin perintah `dexwatch` tersedia global,
jalankan sekali: `python3 -m pip install -e .` (opsional).

## 3. Jalankan dashboard

Di terminal Codespace:

```bash
cd /workspaces/dexwatch     # biasanya sudah di sini
chmod +x codespace.sh dexwatch.sh
./codespace.sh
```

Contoh keluaran:

```
dexwatch UI: http://0.0.0.0:8787  (chain bsc · db dexwatch.db)
Ctrl+C untuk berhenti
```

`codespace.sh` = `serve --scan --host 0.0.0.0 --port 8787`:

- `--scan` memindai pool baru di background,
- `--host 0.0.0.0` wajib agar port bisa di-forward Codespaces,
- `--port 8787` sudah disiapkan di devcontainer.

### Varian perintah

```bash
./codespace.sh --chain base                 # Base
./codespace.sh --chain ethereum             # Ethereum
./codespace.sh --no-security                # tanpa GoPlus (lebih cepat)
./codespace.sh --refresh 30                 # data pasar tiap 30 detik
./codespace.sh --min-liq 5000               # sembunyikan likuiditas kecil
./codespace.sh --rpc https://rpc.ankr.com/bsc
```

Ingin tanpa scanner? Jalankan langsung:

```bash
python3 -m dexwatch serve --host 0.0.0.0 --port 8787
```

Database menyimpan data beberapa chain sekaligus; pil chain di header muncul
otomatis kalau database sudah berisi lebih dari satu chain.

## 4. Buka UI

- Tab **Ports** → cari **dexwatch UI (8787)** → klik ikon globe /
  **Open in Browser**.
- Atau notifikasi **Open preview** yang muncul otomatis.
- URL: `https://<nama-codespace>-8787.app.github.dev`.

Kalau port tidak muncul:

1. Pastikan proses masih jalan di terminal (tidak mati/Ctrl+C).
2. Tab **Ports** → **Forward a Port** → isi `8787`.
3. Cek dari dalam Codespace: `curl -s http://127.0.0.1:8787/api/health`
   harus `{"ok": true, ...}`.
4. Refresh halaman.

### Visibility port

Defaultnya **Private** — hanya kamu (yang login GitHub) yang bisa membuka.
Untuk berbagi ke orang lain: Ports → klik kanan port 8787 → **Port Visibility**
→ **Public**. Ingat: dashboard **tidak punya login**, jadi siapa pun yang punya
URL bisa melihat data dan memicu request API. Jangan dijadikan publik kalau
tidak perlu.

## 5. Supaya tetap hidup

- Menutup tab browser **tidak** mematikan Codespace/proses.
- Codespace berhenti otomatis setelah **idle 30 menit** (default). Ubah di
  `github.com/settings/codespaces` → **Default idle timeout** (maksimal 240
  menit), lalu stop manual lewat **Codespaces** → **Stop codespace**.
- Kalau terminal terputus, jalankan lagi `./codespace.sh`. Pakai `tmux` bila
  ingin proses tetap hidup setelah tab terminal ditutup:

  ```bash
  tmux new -s dexwatch
  ./codespace.sh
  # lepas: Ctrl+B lalu D
  # kembali: tmux attach -t dexwatch
  ```

  Kalau `tmux` belum ada: `sudo apt-get update && sudo apt-get install -y tmux`.

- Alternatif background + log:

  ```bash
  nohup ./codespace.sh > dexwatch.log 2>&1 &
  tail -f dexwatch.log
  ```

  Proses tetap berhenti saat Codespace di-stop, jadi jalankan ulang setelah
  start.

## 6. Data, database, dan biaya

- `dexwatch.db` dibuat di root repo dan **tidak** ikut ter-commit.
- Workspace Codespace bertahan saat stop/start dan rebuild container; database
  hilang hanya jika Codespace **dihapus**.
- Backup cepat:

  ```bash
  ./dexwatch.sh export --json > backup.jsonl
  # atau unduh dexwatch.db lewat klik kanan file → Download
  ```

- Kuota gratis GitHub: 120 core-hour/bulan + 15 GB storage. Mesin 2-core
  berarti sekitar 60 jam/bulan. **Stop Codespace** setelah selesai.

## 7. Troubleshooting

| Gejala | Penyebab / solusi |
|---|---|
| `Address already in use` | Proses lama masih jalan: `pkill -f "dexwatch serve"`, atau `PORT=8788 ./codespace.sh` lalu forward port 8788. |
| `python3: command not found` | Container belum selesai dibuat; tunggu, atau **Rebuild Container**. |
| Halaman kosong / 404 | Port belum di-forward, atau proses mati. Cek `curl http://127.0.0.1:8787/api/health`. |
| `pip install -e .` gagal | Tidak wajib. Jalankan `./codespace.sh` atau `python3 -m dexwatch serve`; install hanya untuk perintah `dexwatch` global. |
| Tidak ada pool baru | RPC publik sedang membatasi; coba `./codespace.sh --dex pancakeswap-v2` atau `./dexwatch.sh doctor` untuk cek endpoint. |
| Chart kosong | Pool sangat baru/belum terindeks GeckoTerminal; tunggu beberapa menit. |
| Harga/likuiditas USD kosong | Pool belum terindeks DexScreener; data on-chain tetap tampil. |
| `429`/rate limit | Kurangi frekuensi (`--refresh 40`), pakai `--rpc` berbayar, atau jalankan `--offline`. |
| Logo token tidak muncul | DexScreener belum punya profil token; UI memakai inisial gradien. |

## 8. Update kode

Setelah mengubah file dan push ke GitHub:

```bash
git add .
git commit -m "update"
git push
```

Di Codespace, tarik perubahan:

```bash
git pull
# kalau ada file static/UI yang berubah, cukup refresh browser (server
# membaca file statis langsung); kalau Python berubah, Ctrl+C lalu jalankan
# ./codespace.sh lagi.
```

> Dashboard ini hanya menampilkan data, tidak bisa membeli/menjual token.
> Bukan nasihat keuangan.
