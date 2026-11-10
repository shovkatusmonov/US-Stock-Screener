# US Stock Screener — METRIQA Grade Platform

METRIQA darajasidagi professional, modulli va resurslarni tejovchi AQSh fond bozori screeneri.
Barcha AQSh birjalari (NASDAQ, NYSE, NYSE American, NYSE Arca, BATS, IEX) bo‘yicha 13,200+ ta faol aksiyalar, ETF va boshqa instrumentlarni monitoring qilish, texnik indikatorlarni hisoblash, bozor kengligini tahlil qilish va Telegram bot orqali avtomatlashtirilgan bildirishnomalar yuborish uchun mo‘ljallangan.

---

## 1. Arxitektura va Tuzilma

```
us_stock_screener/
├── config/
│   ├── __init__.py
│   └── settings.py              # Markaziy konfiguratsiya, yo'llar, timezone, batch limitlar
├── app/
│   ├── data/
│   │   ├── universe.py          # Universe Manager (13,200+ instrument, composite key, delisted tracking)
│   │   ├── provider.py          # Data Provider interfeysi (YFinanceProvider, MockDataProvider, exponential backoff)
│   │   └── loader.py            # Inkremental OHLCV ma'lumot yuklagich, SQLite WAL, multi-timeframe resampling
│   ├── indicators/
│   │   ├── technical.py         # SMA, EMA, RSI, MACD, Bollinger Bands, ATR, ADX, VWAP, RelVol, 52w High/Low
│   │   └── multi_timeframe.py   # Daily va Weekly timeframe confluence tahlili
│   ├── scanners/
│   │   ├── filters.py           # Dinamik filterlash qoidalari (ScreenFilter)
│   │   ├── presets.py           # Tayyor strategiyalar (bullish_breakout, pullback, oversold, volume_surge, ...)
│   │   └── engine.py            # Screener Engine (precomputed snapshot va kam RAM sarflovchi streaming)
│   ├── analytics/
│   │   └── breadth.py           # Market Breadth (Advance/Decline, % > SMA50/200, 52w extremes, etalonlar)
│   ├── telegram/
│   │   ├── bot.py               # Xavfsiz xususiy Telegram bot (ADMIN_USER_IDS filtri)
│   │   └── formatters.py        # Telegram jadvallari va kartochkalar formatlagichi (4096 belgi chegarasi)
│   ├── scheduler/
│   │   └── market_scheduler.py  # APScheduler (AQSh Eastern Time va DST hisobga olingan)
│   └── monitoring/
│       ├── health.py            # RAM/CPU resurs nazorati, DB integrity, ma'lumot eskirishi
│       └── logger.py            # Aylanuvchi fayl jurnali (RotatingFileHandler 5MB x 5)
├── tests/                       # 30 ta to'liq avtomatlashtirilgan unit/integration testlar
├── scripts/
│   └── systemd/                 # Production systemd service shablonlari
├── data/
│   └── screener.db              # SQLite ma'lumotlar bazasi (WAL rejimi)
├── logs/                        # Tizim loglari
├── main.py                      # Yagona CLI boshqaruv paneli
├── pytest.ini                   # Pytest konfiguratsiyasi
├── .env.example                 # Muhit o'zgaruvchilari shabloni
└── requirements.txt             # Kerakli Python kutubxonalari
```

---

## 2. 1 GB RAM VPS uchun Resurs Optimallashuvi

Tizim kichik resursli (952 MiB RAM, 1 GB Swap) VPS'larda barqaror ishlashga moslashtirilgan:
1. **Streaming Generatorlar:** Minglab instrumentlar birdaniga xotiraga yuklanmaydi, 25-50 talik kichik paketlarda (batch) ishlanadi.
2. **Xotira Chegarasi (Memory Guard):** Jarayon xotirasi 700 MB dan oshganda xotirani bo'shatish (`gc.collect()`) va ogohlantirish berish mexanizmi mavjud.
3. **SQLite WAL Mode:** Yozish va o'qish jarayonlari bir-birini bloklamaydi (`PRAGMA journal_mode = WAL`).
4. **Lightweight Monitoring:** Tashqi og'ir paketlarsiz to'g'ridan-to'g'ri Linux `/proc/self/status` orqali RSS o'lchanadi.

---

## 3. CLI Boshqaruv Buyruqlari (`main.py`)

Loyihada barcha operatsiyalar `main.py` orqali boshqariladi:

### 3.1 Universe Boshqaruvi
```bash
# Universe bo'yicha to'liq statistika (aktiv instrumentlar, aktiv turlari va birjalar kesimida)
python3 main.py universe stats

# Ma'lumot qamrovi (Coverage Audit) hisoboti (aktiv turlari va birjalar bo'yicha)
python3 main.py universe audit

# NASDAQ Trader katalogidan yangi ma'lumotlarni yuklab yangilash
python3 main.py universe sync

# Mavjud 'universe' jadvalidan yangi 'instruments' jadvaliga xavfsiz migratsiya qilish
python3 main.py universe migrate

# Instrumentlar ro'yxatini ko'rish (masalan, NASDAQ birjasidagi Common Stocklar)
python3 main.py universe list --asset-class CS --exchange NASDAQ --limit 20
```

### 3.2 Bozor Ma'lumotlari (Market Data)
```bash
# Muayyan aksiya bo'yicha lokal bazadagi ma'lumot holati
python3 main.py data info --symbol SPY

# Tanlangan aksiyalar bo'yicha oxirgi kunlik ma'lumotlarni yangilash
python3 main.py data update --symbols AAPL,MSFT,NVDA

# Barcha bozor instrumentlari uchun nazoratli va xavfsiz tarixiy backfill
python3 main.py data backfill-universe --limit 50 --batch-size 15 --asset-class CS --delay 0.35

# Muayyan etalonlar uchun tezkor backfill
python3 main.py data backfill --symbols SPY,QQQ,IWM,DIA --period 1y
```

### 3.3 Screener va Skannerlar
```bash
# Mavjud tayyor strategiyalar (presetlar) ro'yxati
python3 main.py screen --list-presets

# Texnik snapshotlarni inkremental yangilash (faqat yetishmayotgan shamlar hisoblanadi)
python3 main.py screen --refresh-snapshots

# Barcha 8 ta tayyor strategiyani real bazada benchmark qilish va tekshirish
python3 main.py screen --validate-presets

# Bullish Breakout strategiyasi bo'yicha skanerlash
python3 main.py screen --preset bullish_breakout --limit 20
```

### 3.4 Texnik Kotirovka va Tahlil
```bash
# Muayyan aksiya/ETF uchun to'liq texnik tahlil kartochkasi (RSI, MA, RelVol, 52w)
python3 main.py quote --symbol NVDA
```

### 3.5 Bozor Kengligi (Market Breadth)
```bash
# Advance/Decline, 50/200 SMA ustidagi aksiyalar ulushi va etalonlar hisoboti
python3 main.py breadth
```

### 3.6 Tizim Salomatligi (Health Check)
```bash
# VPS xotirasi (RSS), disk maydoni, DB yaxlitligi va ma'lumot yangiligi tekshiruvi
python3 main.py health
```

---

## 4. Telegram Bot

Telegram bot faqat ruxsat etilgan foydalanuvchilar (`ADMIN_USER_IDS`) uchun ishlaydi:

- `/start` — Xush kelibsiz xabari va menyu
- `/quote <TICKER>` — Aksiya narxi va barcha texnik indikatorlar kartasi
- `/screen <preset>` — Tanlangan strategiya bo'yicha eng yaxshi aksiyalar jadvali
- `/market` — Bozor kengligi (Market Breadth) va SPY, QQQ, IWM, DIA ko'rsatkichlari
- `/health` — VPS resurslari va baza holati
- `/status` — Tizim holati va servislar xulosasi
- `/wl` — Shaxsiy kuzatuv ro'yxati (Watchlist)

### Botni ishga tushirish:
```bash
python3 main.py bot
```

---

## 5. Avtomatlashtirilgan Jadval (Scheduler Timeline)

AQSh Eastern Time (ET) va yozgi/qishki vaqt (DST) to'liq hisobga olingan:

1. **08:30 ET (Dush-Juma):** Pre-market tayyorgarligi — Universe yangilanadi, asosiy etalonlar tekshiriladi.
2. **12:00 ET (Dush-Juma):** Midday skanerlash — Intraday o'zgarishlar va momentum aksiyalari.
3. **16:30 ET (Dush-Juma):** Market Close skanerlash — Kunlik barcha OHLCV shamlar yuklanadi, indikatorlar hisoblanadi, snapshotlar yangilanadi va Telegramga hisobot yuboriladi.
4. **Yakshanba 12:00 ET:** Haftalik profilaktika — Baza yaxlitligi tekshiruvi, WAL checkpoint, backup yaratish.

### Schedulerni ishga tushirish:
```bash
python3 main.py scheduler
```

---

## 6. Systemd Servislarini O'rnatish (VPS Daemon)

Xizmatlar fonda uzluksiz ishlashi uchun systemd fayllari taqdim etilgan:

```bash
# 1. Shablonlarni tizim katalogiga nusxalash
sudo cp scripts/systemd/stock-screener-bot.service /etc/systemd/system/
sudo cp scripts/systemd/stock-screener-scheduler.service /etc/systemd/system/

# 2. Daemonlarni qayta yuklash
sudo systemctl daemon-reload

# 3. Servislarni faollashtirish va ishga tushirish (agar Telegram token sozlangan bo'lsa)
sudo systemctl enable stock-screener-bot
sudo systemctl start stock-screener-bot

sudo systemctl enable stock-screener-scheduler
sudo systemctl start stock-screener-scheduler
```

---

## 7. Testlar va Tekshiruv

Loyihada 33 ta test to'liq avtomatlashtirilgan bo'lib, VPS muhitida 100% muvaffaqiyatli o'tadi:

```bash
.venv/bin/pytest tests/ -v
```

Natija:
```
============================== 33 passed in 4.32s ==============================
```
