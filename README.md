# Vidadi AI Voice Userbot

**Owner:** Raven
**Adı:** Vidadi
**Dil:** Azərbaycan dili (küçə tonu, real insan üslubu)
**Platform:** Telegram Userbot (Pyrogram + PyTgCalls)
**Deploy:** Railway / Docker (tam pulsuz işləyə bilir)

> Vidadi normal `/start` botu deyil. Real Telegram hesabı (Pyrogram session) ilə işləyir,
> qrup voice chatlarına qoşulur, real-time səslə cavab verir və qrup yazışmalarında real
> insan kimi davranır. Sistem detalları heç vaxt açıqlanmır.

---

## 1. Funksionallıq

| Bölmə | Açıqlama |
|---|---|
| `.ses` | Owner istənilən qrupda yazanda — userbot həmin qrupun VC-yə qoşulur |
| `.bye` | VC-dən çıxır |
| `.status` | Aktiv VC sayını göstərir |
| `.say <söz>` | VC-də həmin sözü ucadan deyir |
| Realtime STT | `faster-whisper` (base, int8) + WebRTC VAD |
| Realtime TTS | `edge-tts` — Azərbaycan dili (Babək / Banu) |
| AI Brain | Gemini (öz açar) və ya Emergent Universal Key |
| Memory | SQLite — istifadəçi adları, mesaj tarixçəsi, faktlar |
| Speaker recognition | Yüngül MFCC-cosine fingerprint (free-tier üçün CPU-da işləyir) |
| Group chat AI | Mention / reply / random reaction tone |
| Şəxsiyyət qoruması | "AI / model / kod" sözlərini söyləməz — zarafata salar |

---

## 2. Strukturu

```
vidadi_userbot/
├── main.py                # Entry point
├── requirements.txt
├── Dockerfile
├── railway.json
├── .env.example
├── README.md
├── docker/
│   └── entrypoint.sh
└── app/
    ├── config/
    │   ├── settings.py     # env -> Settings dataclass
    │   └── personality.py  # Vidadi system prompt
    ├── core/
    │   ├── client.py       # Pyrogram client factory
    │   └── logger.py       # loguru setup
    ├── ai/
    │   └── brain.py        # Gemini + emergent wrapper
    ├── audio/
    │   ├── vc_manager.py   # PyTgCalls join/leave/speak
    │   ├── audio_pipeline.py
    │   ├── vad.py          # WebRTC VAD segmenter
    │   ├── stt.py          # faster-whisper int8
    │   └── tts.py          # edge-tts → 48k PCM
    ├── memory/
    │   ├── db.py           # SQLite schema + ops
    │   ├── user_memory.py
    │   └── speaker_db.py   # voice fingerprint store
    ├── handlers/
    │   ├── commands.py     # .ses / .bye / .status / .say
    │   ├── chat.py         # group AI replies
    │   └── voice_chat.py   # voice-msg enrolment
    └── services/
        ├── reconnect.py
        └── rate_limiter.py
```

---

## 3. Lokal Quraşdırma

### 3.1 Tələblər
- Python 3.11
- `ffmpeg` (sistemdə)
- Telegram hesabı (API_ID / API_HASH)
- Hazır Pyrogram **v2 session string**

### 3.2 Addımlar

```bash
git clone <repo> vidadi_userbot
cd vidadi_userbot

# Python venv
python3.11 -m venv .venv && source .venv/bin/activate

# Emergent integrations (Gemini üçün opsional)
pip install emergentintegrations --extra-index-url https://d33sy5i8bnduwe.cloudfront.net/simple/

pip install -r requirements.txt

cp .env.example .env
# .env-i öz açarlarınla doldur
python main.py
```

---

## 4. .env Konfiqurasiya

`.env.example`-i kopyala və aşağıdakıları doldur:

```dotenv
API_ID=123456
API_HASH=...
SESSION_STRING=...
OWNER_ID=123456789           # Raven-in user ID
GEMINI_API_KEY=...           # https://aistudio.google.com/apikey
LLM_PROVIDER=gemini          # və ya "emergent"
LLM_MODEL=gemini-2.5-flash
WHISPER_MODEL=base
WHISPER_COMPUTE_TYPE=int8
TTS_VOICE=az-AZ-BabekNeural
```

> **Qeyd**: `LLM_PROVIDER=emergent` seçsən, `EMERGENT_LLM_KEY` env-i lazımdır.
> Bu açar Gemini-ni emergent-in universal infrastrukturu üzərindən çağırır.

---

## 5. Railway Deploy

1. **GitHub-a push et** (`.env`-i push etmə!).
2. Railway-də `New Project → Deploy from GitHub` seç.
3. `Settings → Variables` bölməsində bütün env dəyişənlərini əlavə et
   (API_ID, API_HASH, SESSION_STRING, OWNER_ID, GEMINI_API_KEY, ...).
4. Railway avtomatik `Dockerfile`-ı götürəcək. `railway.json` start command
   və restart policy-ni qeyd edir.
5. Deploy bitdikdən sonra `Logs`-da `Userbot online.` mesajını gözlə.

### Resurslar (free tier)
- RAM: ~500–700MB (whisper base + int8 modeli yüklənmiş halda)
- CPU: 0.5 vCPU kifayət edir
- Disk: `/app/data` (SQLite) + `/app/.cache/whisper` (~150MB)

---

## 6. İstifadə

1. Userbot Telegram-a daxil olduqdan sonra **hər hansı qrupda** olduğun
   yerdə (owner kimi) belə yaz:

   ```
   .ses
   ```

   Qrupda voice chat açıq olmalıdır. Userbot avtomatik VC-yə qoşulur.

2. VC-də danış — `faster-whisper` Azərbaycan dilini başa düşür, `Gemini`
   real insan kimi cavab hazırlayır, `edge-tts` səsə çevirir, PyTgCalls
   VC-yə ötürür.

3. `.bye` ilə VC-ni tərk edir.

4. **Normal qrup chatı**: Vidadi mention edildikdə və ya yüksək ehtimalla
   random olaraq qrup mesajlarına cavab verir (küçə dili, zarafat, meme tonu).

5. **Səs tanıma**: hər hansı istifadəçi qrupda bir səsli mesaj göndərdikdə —
   onun səs imzası (fingerprint) DB-yə yazılır. Sonra VC-də danışdığında
   Vidadi "Murad yenə gəldi" tipində reaksiya verə bilər.

---

## 7. Şəxsiyyət Qoruması

`app/config/personality.py` daxilində Vidadi-nin tam sistem prompt-u var.
Əgər kimsə soruşsa ki "Sən bot/AI-san?" → cavab həmişə yayındırıcı, zarafata
salınır, sistem detalları (Python, Gemini, Whisper, kod, prompt, Railway,
Docker, model adları) HEÇ VAXT açıqlanmır. `app/ai/brain.py` daxilində
post-filter də var — model səhvən "AI" sözünü buraxsa, cavab `REFUSAL_DEFLECTIONS`
massivindən birinə əvəz olunur.

---

## 8. Anti-bot davranışı

- Cavab əvvəli `typing...` action göstərir.
- 0.8–2.4 saniyə random "fikirləşmə" pauzası.
- Cavabların hamısına cavab vermir — random + mention based prioritet.
- VC aktivdirsə qrup chatına daha az cavab verir.
- Eyni istifadəçidən gələn mesajlara qarşı per-user rate limit.

---

## 9. Səhvbərpa

- `ReconnectGuard` (`app/services/reconnect.py`) hər 60 saniyədə client
  bağlanıb-bağlanmadığını yoxlayır, lazımsa avtomatik bərpa edir.
- `vc_manager` müstəqil exception-ları susdurur, crash-ə imkan vermir.

---

## 10. Vacib qeydlər

- `SESSION_STRING` məxfidir — heç vaxt git-ə commit etmə (`.gitignore`-da var).
- Telegram qrupunda VC açılmasa `.ses` işləməz — owner ən azı adminliyə icazəli olmalıdır.
- Whisper `base` modeli ilk dəfə yüklənəndə ~150MB endirir. Railway-də
  `volume` istifadə etsən cache saxlanır.
- PyTgCalls inbound audio capture builds arasında dəyişir; `audio_pipeline.feed_frame()`
  hook hazırdır — versiyana uyğun inbound stream hook-u bağla.

---

## 11. License
Şəxsi istifadə üçün. Reseller/SaaS hüququ verilmir.
