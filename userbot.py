#!/usr/bin/env python3

import os
import sys
import time
import json
import aiohttp
import logging
import requests
import qrcode
import inspect
import asyncio
import re
import asyncio
import socket
import statistics
import platform
import tempfile
import shutil
import string
import random
import inspect as _inspect
import textwrap
import pyrogram
import sys, os

from pyrogram.raw import functions as raw_f
from pyrogram import types
from pyrogram.raw import types as raw_t
from pyrogram.raw import functions as raw_f, types as raw_t
from collections import deque
from typing import Deque, Optional, List
from pyrogram import filters
from pathlib import Path
from pyrogram.raw.types import InputStickerSetShortName, InputStickerSetItem, InputDocument
from pyrogram.file_id import FileId, FileType
from typing import Any
from pyrogram import Client, raw, utils
from pyrogram.file_id import FileId, FileType
from io import BytesIO
from pathlib import Path
from urllib.parse import quote_plus
from pyrogram import idle
from typing import Optional, Set, Dict
from datetime import datetime, timedelta, timezone
from pyrogram import filters as _filters
from pyrogram import Client, filters, enums
from pyfiglet import figlet_format
from pyrogram.types import Message, ChatPermissions, ChatPrivileges
from pyrogram.errors import FloodWait

try:
    from PIL import Image
except Exception:
    Image = None

try:
    import dns.resolver
    DNSPYTHON_AVAILABLE = True
except Exception:
    DNSPYTHON_AVAILABLE = False

try:
    import psutil
    PSUTIL_AVAILABLE = True
except Exception:
    PSUTIL_AVAILABLE = False

chat_titles = {}
MEDIA_DIR = "saved_media"
PROFILE_PICS_DIR = "profile_pics"
speed_history = []
logger = logging.getLogger(__name__)

for directory in [MEDIA_DIR, PROFILE_PICS_DIR, "downloads", "temp"]:
    os.makedirs(directory, exist_ok=True)

StickerSetClass = None
try:
  
    from pyrogram.types.sticker_set import StickerSet as _SS
    StickerSetClass = _SS
except Exception:
    try:
        # older pyrogram may expose at root (rare)
        import pyrogram.types as _t
        StickerSetClass = getattr(_t, "StickerSet", None)
    except Exception:
        StickerSetClass = None

async def _upload_sticker_document(client: Client, sticker, user_id="me"):
    """Upload sticker (path/URL/file_id/BytesIO) and return raw InputDocument for RPC."""
    # if sticker already file_id -> decode
    if isinstance(sticker, str) and not os.path.isfile(sticker) and not re.match(r"^https?://", sticker):
        try:
            decoded = FileId.decode(sticker)
            return raw.types.InputDocument(
                id=decoded.media_id,
                access_hash=decoded.access_hash,
                file_reference=decoded.file_reference
            )
        except Exception:
            # fallback to uploading as file_id string (send_document will error but we try)
            pass

    # upload to user (me) as document then convert to InputDocument
    msg = await client.send_document(
        user_id,
        sticker,
        force_document=True,
        disable_notification=True
    )
    file_id = None
    try:
        file_id = msg.document.file_id
    except Exception:
        # try photo -> not ideal but keep robust
        file_id = getattr(msg, "photo", None) and getattr(msg.photo, "file_id", None)
    if file_id is None:
        # cleanup then raise
        try:
            await msg.delete()
        except Exception:
            pass
        raise ValueError("Failed to upload sticker media (no file_id).")

    # convert file_id -> raw InputDocument
    uploaded = utils.get_input_media_from_file_id(file_id, FileType.DOCUMENT)
    # remove uploaded message
    try:
        await msg.delete()
    except Exception:
        pass
    return uploaded.id

async def _try_parse_stickerset(raw_response) -> Any:
    """
    Try to parse raw.rpc result into pyrogram types.StickerSet if possible.
    If not possible, return a simple dict with important fields.
    """
    if StickerSetClass is not None:
        try:
            # StickerSetClass._parse expects raw.TL object
            return StickerSetClass._parse(raw_response)  # type: ignore
        except Exception:
            pass

    # fallback: build minimal dict
    out = {}
    try:
        s = raw_response
        out["title"] = getattr(s, "title", None)
        out["short_name"] = getattr(s, "short_name", None)
        out["archived"] = getattr(s, "archived", None)
        out["official"] = getattr(s, "official", None)
        out["masks"] = getattr(s, "masks", None)
        out["animated"] = getattr(s, "animated", None)
        out["count"] = getattr(s, "count", None)
    except Exception:
        pass
    return out

# create_sticker_set
async def create_sticker_set(
    self: Client,
    title: str,
    short_name: str,
    sticker,
    emoji: str = "🤔",
    user_id="me",
    masks: bool = None
):
    media = await _upload_sticker_document(self, sticker, user_id)
    r = await self.invoke(
        raw.functions.stickers.CreateStickerSet(
            user_id=await self.resolve_peer(user_id),
            title=title,
            short_name=short_name,
            stickers=[
                raw.types.InputStickerSetItem(
                    document=media,
                    emoji=emoji
                )
            ],
            masks=masks
        )
    )
    # r.set is raw.TL stickerSet
    return await _try_parse_stickerset(r.set)

# add_sticker_to_set
async def add_sticker_to_set(
    self: Client,
    set_short_name: str,
    sticker,
    emoji: str = "🤔",
    user_id="me"
):
    media = await _upload_sticker_document(self, sticker, user_id)
    r = await self.invoke(
        raw.functions.stickers.AddStickerToSet(
            stickerset=raw.types.InputStickerSetShortName(short_name=set_short_name),
            sticker=raw.types.InputStickerSetItem(
                document=media,
                emoji=emoji
            )
        )
    )
    return await _try_parse_stickerset(r.set)

# get_sticker_set
async def get_sticker_set(
    self: Client,
    set_short_name: str
):
    r = await self.invoke(
        raw.functions.messages.GetStickerSet(
            stickerset=raw.types.InputStickerSetShortName(short_name=set_short_name),
            hash=0
        )
    )
    return await _try_parse_stickerset(r.set)

# attach to Client
Client.create_sticker_set = create_sticker_set
Client.add_sticker_to_set = add_sticker_to_set
Client.get_sticker_set = get_sticker_set

# ===== end patch =====

# -------- .env loader (reads ./.env if env var missing) ----------
def _load_dotenv(path: str = ".env"):
    p = Path(path)
    if not p.exists():
        return
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip(); v = v.strip().strip('"').strip("'")
            if not os.environ.get(k):
                os.environ[k] = v
    except Exception:
        pass

_load_dotenv()

# -------- logging ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("userbot")

# -------- env loader ----------
def _env_int(k: str) -> Optional[int]:
    v = os.environ.get(k)
    if not v:
        return None
    try:
        return int(v)
    except Exception:
        return None

API_ID = _env_int("API_ID")
API_HASH = os.environ.get("API_HASH", "") or None
OWNER_ID = _env_int("OWNER_ID")
SESSION_NAME = os.environ.get("SESSION_NAME", "userbot")

# Google CSE
GOOGLE_SEARCH_API_KEY = os.environ.get("GOOGLE_SEARCH_API_KEY") or os.environ.get("GOOGLE_API_KEY")
GOOGLE_CSE_ID = os.environ.get("GOOGLE_CSE_ID") or os.environ.get("GOOGLE_CX") or os.environ.get("GOOGLE_CUSTOM_SEARCH_CX")

if not API_ID or not API_HASH or not OWNER_ID:
    log.error("Missing env vars: API_ID/API_HASH/OWNER_ID. Fill them or create a .env file.")
    sys.exit(1)

# -------- storage (approved users, AI global mode) ----------
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
_APPROVED_FILE = DATA_DIR / "approved.json"
_AI_GLOBAL_FILE = DATA_DIR / "ai_global_mode.json"

def _load_approved() -> Set[int]:
    try:
        if _APPROVED_FILE.exists():
            with _APPROVED_FILE.open("r", encoding="utf-8") as f:
                arr = json.load(f)
                return set(arr if isinstance(arr, list) else [])
    except Exception:
        log.warning("Failed to load approved.json")
    return set()

def _save_approved(s: Set[int]):
    try:
        with _APPROVED_FILE.open("w", encoding="utf-8") as f:
            json.dump(sorted(list(s)), f, ensure_ascii=False, indent=2)
    except Exception:
        log.exception("Failed to save approved.json")

approved_users = _load_approved()
       
# ------sudo-------
_SUDO_FILE = DATA_DIR / "sudo.json"

def _load_sudo() -> set:
    try:
        if _SUDO_FILE.exists():
            with _SUDO_FILE.open("r", encoding="utf-8") as f:
                arr = json.load(f)
                return set(arr if isinstance(arr, list) else [])
    except:
        pass
    return set()

def _save_sudo(s: set):
    try:
        with _SUDO_FILE.open("w", encoding="utf-8") as f:
            json.dump(sorted(list(s)), f, ensure_ascii=False, indent=2)
    except:
        pass

sudo_users = _load_sudo()

# dynamic allowed filter: owner OR any id in sudo_users
def _allowed_filter_func(_, __, message):
    try:
        uid = getattr(message.from_user, "id", None)
        if uid is None:
            return False
        if uid == OWNER_ID:
            return True
        if uid in sudo_users:
            return True
        return False
    except Exception:
        return False

ALLOW_FILTER = _filters.create(_allowed_filter_func)

#----check-admin-----
async def check_admin(client: Client, chat_id: int, user_id: int):
    try:
        member = await client.get_chat_member(chat_id, user_id)
        return member.status in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]
    except:
        return False
        
# ---- AI GLOBAL MODE ----
AI_GLOBAL_MODE = "flash"

# AI global mode 
def _load_ai_global_mode() -> str:
    try:
        if _AI_GLOBAL_FILE.exists():
            v = json.loads(_AI_GLOBAL_FILE.read_text(encoding="utf-8"))
            if isinstance(v, dict) and "mode" in v:
                return v["mode"]
            if isinstance(v, str):
                return v
    except Exception:
        log.warning("Failed to load ai_global_mode.json")
    return "flash"

def _save_ai_global_mode(mode: str):
    try:
        _AI_GLOBAL_FILE.write_text(json.dumps({"mode": mode}, ensure_ascii=False, indent=2))
    except Exception:
        log.exception("Failed to save ai_global_mode.json")

AI_GLOBAL_MODE = _load_ai_global_mode()

# DM spam counters in-memory (not persisted)
dm_spam_counter = {}  # user_id -> int
MAX_SPAM = 3

# -------- pyrogram client ----------
app = Client(SESSION_NAME, api_id=API_ID, api_hash=API_HASH, workdir=".")
       
# config file for persisting default style
_QR_CONFIG_FILE = Path("qr_config.json")

# Small caption templates per style
_QR_STYLE_TEMPLATES = {
    "kawaii_pink": {
        "gen_status": "**🌸✨ Generating cute QR code... hold on~**",
        "caption":    "**🌸 QR for you, senpai:**\n{txt}",
        "error":      "**❣️ Failed to generate QR. Try again later.**"
    },
    "cyber_y2k": {
        "gen_status": "**🔮 Generating QR code… neon flux engaged**",
        "caption":    "**🔷 QR Generated • {txt}**",
        "error":      "**🚫 QR generation failed (server).**"
    },
    "minimal_anime": {
        "gen_status": "**✨ Creating QR… please wait**",
        "caption":    "**✨ QR for: {txt}**",
        "error":      "**⚠️ Couldn't create QR, try later.**"
    },
    "playful": {
        "gen_status": "**🛠️ Cookin' a QR… 1%… 99%… DONE 🍳**",
        "caption":    "**🤣 QR for: **{txt}\n(Scan it or suffer the consequences)",
        "error":      "**🚫 Oops, QR server kaput.**"
    },
}

# API params per style (colors / size)
# API params per style (clean black QR, no color modifications)
_QR_STYLES = {
    "kawaii_pink": {
        "size": "400x400"
    },
    "cyber_y2k": {
        "size": "420x420"
    },
    "minimal_anime": {
        "size": "400x400"
    },
    "playful": {
        "size": "400x400"
    },
}

# available styles sanity list
_AVAILABLE_STYLES = [k for k in _QR_STYLES.keys() if k in _QR_STYLE_TEMPLATES]

# default style
QR_STYLE = _AVAILABLE_STYLES[0] if _AVAILABLE_STYLES else list(_QR_STYLES.keys())[0]

def _load_qr_config():
    global QR_STYLE
    try:
        if _QR_CONFIG_FILE.exists():
            data = json.loads(_QR_CONFIG_FILE.read_text(encoding="utf-8"))
            name = data.get("qr_style")
            if name and name in _QR_STYLES:
                QR_STYLE = name
    except Exception:
        pass

def _save_qr_config(name: str):
    try:
        _QR_CONFIG_FILE.write_text(json.dumps({"qr_style": name}), encoding="utf-8")
        return True
    except Exception:
        return False

# load persisted style at import/startup
_load_qr_config()

def _build_qr_url(style_name: str, text: str) -> str:
    """Build qrserver URL respecting the style params."""
    params = _QR_STYLES.get(style_name, {})
    size = params.get("size", "400x400")
    color = params.get("color")
    bgcolor = params.get("bgcolor")
    encoded = quote_plus(text)
    base = f"https://api.qrserver.com/v1/create-qr-code/?data={encoded}&size={size}"
    if color:
        base += f"&color={color}"
    if bgcolor:
        base += f"&bgcolor={bgcolor}"
    return base

# ---------- .qrstyle command (fixed global placement) ----------
@app.on_message(filters.command("qrstyle", prefixes=".") & filters.me)
async def qrstyle_cmd(client: Client, message: Message):
    global QR_STYLE  # MUST be declared before any use in function
    args = message.command[1:] if len(message.command) > 1 else []
    if not args:
        styles = ", ".join(f"`{k}`" for k in _QR_STYLES.keys())
        txt = (
            f"**✨ QR Style Manager ✨**\n\n"
            f"• Current default: `{QR_STYLE}`\n"
            f"• Available styles: {styles}\n\n"
            f"Set default with: `.qrstyle <style>`\n"
            f"Reset with: `.qrstyle reset`"
        )
        return await message.edit_text(txt)

    chosen = args[0].lower().strip()
    if chosen == "reset":
        default_name = _AVAILABLE_STYLES[0] if _AVAILABLE_STYLES else list(_QR_STYLES.keys())[0]
        success = _save_qr_config(default_name)
        if success:
            QR_STYLE = default_name
            return await message.edit_text(f"✅ QR style reset to `{QR_STYLE}`")
        else:
            return await message.edit_text("❌ Failed to save config (check FS permissions).")

    if chosen not in _QR_STYLES:
        styles = ", ".join(f"`{k}`" for k in _QR_STYLES.keys())
        return await message.edit_text(f"❌ Unknown style `{chosen}`.\nAvailable: {styles}")

    ok = _save_qr_config(chosen)
    if not ok:
        return await message.edit_text("❌ Failed to save config (check FS permissions).")

    QR_STYLE = chosen
    await message.edit_text(f"✨ Default QR style set to `{chosen}` — will be used by `.qr` from now on.")


# ---------- .qr handler (generate; supports reply & style override) ----------
@app.on_message(filters.command("qr", prefixes=".") & filters.me)
async def generate_qr(client: Client, message: Message):
    # --- Grab text for QR ---
    text = None

    # `.qr something`
    if len(message.command) > 1:
        text = " ".join(message.command[1:]).strip()

    # `.qr` but reply → use replied message
    elif message.reply_to_message:
        rep = message.reply_to_message
        if getattr(rep, "text", None):
            text = rep.text.strip()
        elif getattr(rep, "caption", None):
            text = rep.caption.strip()

    if not text:
        return await message.edit_text("**🌸❗Reply text or `.qr [text]` to generate a QR code.**")

    # --- Inline style override e.g. "style:kawaii text here"
    tokens = text.split()
    override_style = None
    if tokens and ":" in tokens[0] and tokens[0].lower().startswith("style"):
        parts = tokens[0].split(":", 1)
        if len(parts) == 2 and parts[1] in _QR_STYLES:
            override_style = parts[1]
            text = " ".join(tokens[1:]).strip()
            if not text:
                return await message.edit_text("Provide text after style specifier.")

    style_to_use = override_style or QR_STYLE
    template = _QR_STYLE_TEMPLATES.get(style_to_use, {})
    gen_status = template.get("gen_status", "Generating QR code...")
    caption_template = template.get("caption", "{txt}")
    error_msg = template.get("error", "Failed to generate QR.")

    # --- Send spinner first ---
    try:
        status_msg = await message.edit_text(gen_status)
    except:
        status_msg = message

    # kawaii spinner frames
    FRAMES = [
    "🌸⋆｡°✩", "✩°｡⋆🌸", "✧･ﾟ🌸", "･ﾟ✧🌸",
    "🌸✦", "✦🌸", "🌸✧", "✧🌸",
    "🌸✨", "✨🌸", "🌸💫", "💫🌸"
]
    spinner_running = True

    async def spinner():
        i = 0
        while spinner_running:
            try:
                await status_msg.edit_text(f"{gen_status}\n{FRAMES[i % len(FRAMES)]}")
            except:
                pass
            i += 1
            await asyncio.sleep(0.25)

    spin_task = asyncio.create_task(spinner())

    try:
        # Build QR URL
        qr_url = _build_qr_url(style_to_use, text)

        async with aiohttp.ClientSession() as session:
            async with session.get(qr_url) as resp:
                if resp.status != 200:
                    spinner_running = False
                    try: await spin_task
                    except: pass
                    return await status_msg.edit_text(error_msg)

                data_bytes = await resp.read()
                qr_file = BytesIO(data_bytes)
                qr_file.name = "qr.png"

    except Exception as e:
        spinner_running = False
        try: await spin_task
        except: pass
        return await status_msg.edit_text(f"{error_msg}\n\nError: {e}")

    # --- FINALLY: stop spinner & send only 1 clean QR message ---
    spinner_running = False
    try:
        await spin_task
    except:
        pass

    caption = caption_template.format(txt=text)

    try:
        # send final QR photo
        sent = await client.send_photo(
            chat_id=message.chat.id,
            photo=qr_file,
            caption=caption
        )

        # delete spinner message so output = ONLY photo
        try:
            await client.delete_messages(message.chat.id, status_msg.id)
        except:
            pass

    except Exception as e:
        return await status_msg.edit_text(f"{error_msg}\nError: {e}")
        
# -----------ip-------
@app.on_message(filters.command("ip", prefixes=".") & filters.me)
async def ip_info(client: Client, message: Message):
    """Get detailed IP address information"""
    if len(message.command) < 2:
        return await message.edit_text("**Usage:** `.ip [ip_address]`\n**Example:** `.ip 8.8.8.8`")
    
    try:
        ip = message.command[1]
        await message.edit_text(f"**🔄 Analyzing IP {ip}...**")
        

        async with aiohttp.ClientSession() as session:
            url = f"http://ip-api.com/json/{ip}?fields=status,message,continent,continentCode,country,countryCode,region,regionName,city,district,zip,lat,lon,timezone,offset,currency,isp,org,as,asname,reverse,mobile,proxy,hosting,query"
            
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    if data['status'] == 'success':
                        result = f"""**🌍 IP Address Information**

**IP:** {data['query']}
**ISP:** {data.get('isp', 'N/A')}
**Organization:** {data.get('org', 'N/A')}
**AS:** {data.get('as', 'N/A')}

**📍 Location**
**Country:** {data.get('country', 'N/A')} ({data.get('countryCode', 'N/A')})
**Region:** {data.get('regionName', 'N/A')} ({data.get('region', 'N/A')})
**City:** {data.get('city', 'N/A')}
**ZIP Code:** {data.get('zip', 'N/A')}
**Coordinates:** {data.get('lat', 'N/A')}, {data.get('lon', 'N/A')}

**🕐 Time Zone**
**Timezone:** {data.get('timezone', 'N/A')}
**UTC Offset:** {data.get('offset', 'N/A')}

**🔍 Additional Info**
**Reverse DNS:** {data.get('reverse', 'N/A')}
**Mobile:** {'Yes' if data.get('mobile') else 'No'}
**Proxy:** {'Yes' if data.get('proxy') else 'No'}
**Hosting:** {'Yes' if data.get('hosting') else 'No'}"""
                        
                        await message.edit_text(result)
                    else:
                        await message.edit_text(f"**❌ Failed to get IP info:** {data.get('message', 'Unknown error')}")
                else:
                    await message.edit_text("**❌ Failed to fetch IP information!**")
                    
    except Exception as e:
        await message.edit_text(f"**❌ Error:** `{str(e)}`")
        
#-----add------
@app.on_message(filters.command("tr", prefixes=".") & filters.me)
async def ggn_translate(client: Client, message: Message):
    args = message.command[1:] if len(message.command) > 1 else []
    target_lang = "en"
    mode = "single"
    batch_count = 3
    custom_text = ""
    auto_detect = False

    if not args:
        mode = "single"
    elif len(args) == 1:
        if args[0].lower() in ["batch", "b"]:
            mode = "batch"
        elif args[0].lower() in ["quick", "q"]:
            mode = "quick"
        elif args[0].lower() == "auto":
            auto_detect = True
        else:
            target_lang = args[0]
    elif len(args) >= 2:
        if args[0].lower() in ["batch", "b"]:
            mode = "batch"
            target_lang = args[1]
            if len(args) > 2:
                try:
                    batch_count = min(int(args[2]), 10)
                except:
                    batch_count = 3
        elif args[0].lower() in ["quick", "q"]:
            mode = "quick"
            target_lang = args[1]
        elif args[0].lower() == "auto":
            auto_detect = True
            target_lang = args[1]
        else:
            target_lang = args[0]
            custom_text = " ".join(args[1:])
            mode = "single"

    text_to_translate = ""
    if custom_text:
        text_to_translate = custom_text
    elif message.reply_to_message and message.reply_to_message.text:
        text_to_translate = message.reply_to_message.text
    elif mode == "single":
        return await message.edit_text(
            "**🔤 Universal Translator Usage:**\n\n"
            "**Single Translation:**\n"
            "• `.tr` - Translate replied message to English\n"
            "• `.tr es` - Translate replied message to Spanish\n"
            "• `.tr fr Hello` - Translate 'Hello' to French\n\n"
            "**Batch Translation:**\n"
            "• `.tr batch` - Translate last 3 messages to English\n"
            "• `.tr batch es 5` - Translate last 5 messages to Spanish\n\n"
            "**Quick Translation:**\n"
            "• `.tr quick` - Quick translate recent messages to English\n"
            "• `.tr quick fr` - Quick translate recent messages to French\n\n"
            "**Auto Detection:**\n"
            "• `.tr auto` - Auto-detect language and translate to English\n"
            "• `.tr auto es` - Auto-detect and translate to Spanish"
        )

    status_msg = {
        "batch": f"🔄 Collecting last {batch_count} messages for batch translation to {target_lang.upper()}...",
        "quick": f"🚀 Quick translating recent messages to {target_lang.upper()}...",
    }.get(mode, f"🔤 Translating to {target_lang.upper()}...")
    
    await message.edit_text(status_msg)

    try:
        translator_services = []
        try:
            from deep_translator import GoogleTranslator
            translator_services.append(("Google", GoogleTranslator))
        except: pass
        try:
            from deep_translator import MyMemoryTranslator
            translator_services.append(("MyMemory", MyMemoryTranslator))
        except: pass
        try:
            from deep_translator import LibreTranslator
            translator_services.append(("Libre", LibreTranslator))
        except: pass
        if not translator_services:
            return await message.edit_text("❌ No translation services available. Install: `pip install deep-translator`")
        if mode == "single":
            await handle_single_translation(message, text_to_translate, target_lang, auto_detect, translator_services)
        elif mode == "batch":
            await handle_batch_translation(client, message, target_lang, batch_count, translator_services)
        elif mode == "quick":
            await handle_quick_translation(client, message, target_lang, translator_services)
    except Exception as e:
        await message.edit_text(f"❌ Translation failed: {str(e)}")


async def handle_single_translation(message, text, target_lang, auto_detect, translator_services):
    if not text:
        return await message.edit_text("❌ No text to translate!")
    for service_name, TranslatorClass in translator_services:
        try:
            if auto_detect:
                detector = TranslatorClass(source='auto', target='en')
                detected_lang = detector.detect(text)
                if detected_lang == target_lang:
                    return await message.edit_text(
                        f"✅ **Text is already in {target_lang.upper()}**\n\n"
                        f"📝 **Text:** {text}\n"
                        f"🔍 **Detected Language:** {detected_lang}"
                    )
            translator = TranslatorClass(source='auto', target=target_lang, base_url='https://libretranslate.de') if service_name == "Libre" else TranslatorClass(source='auto', target=target_lang)
            translated_text = translator.translate(text)
            try:
                detected_lang = translator.detect(text) if hasattr(translator, 'detect') else TranslatorClass(source='auto', target='en').detect(text)
            except:
                detected_lang = "auto"
            result = (
                f"✅ **Translated to {target_lang.upper()}:**\n"
                f"{translated_text}\n\n"
                f"🔍 **Detected Language:** {detected_lang}\n"
                f"🔧 **Service:** {service_name}"
            )
            if len(text) > 100:
                result += f"\n\n📝 **Original:** {text[:200]}{'...' if len(text) > 200 else ''}"
            return await message.edit_text(result)
        except:
            continue
    await message.edit_text("❌ All translation services failed. Please try again later.")


async def handle_batch_translation(client, message, target_lang, count, translator_services):
    service_name, TranslatorClass = translator_services[0]
    try:
        translator = TranslatorClass(source='auto', target=target_lang, base_url='https://libretranslate.de') if service_name == "Libre" else TranslatorClass(source='auto', target=target_lang)
    except:
        return await message.edit_text("❌ Failed to initialize translator")
    messages_to_translate = []
    try:
        async for msg in client.get_chat_history(message.chat.id, limit=50):
            if msg.text and len(messages_to_translate) < count and msg.id != message.id:
                messages_to_translate.append({'text': msg.text, 'sender': msg.from_user.first_name if msg.from_user else "Unknown", 'id': msg.id})
            if len(messages_to_translate) >= count:
                break
    except Exception as e:
        return await message.edit_text(f"❌ Failed to get chat history: {str(e)}")
    if not messages_to_translate:
        return await message.edit_text("❌ No messages found to translate")
    messages_to_translate.reverse()
    await message.edit_text(f"🔄 Translating {len(messages_to_translate)} messages to {target_lang.upper()}...")
    translated_batch = []
    for i, msg_data in enumerate(messages_to_translate):
        try:
            translated = translator.translate(msg_data['text'])
            preview = msg_data['text'][:80] + "..." if len(msg_data['text']) > 80 else msg_data['text']
            translated_batch.append(f"**{i+1}. {msg_data['sender']}:**\n📝 {preview}\n🔄 **{translated}**\n")
        except:
            translated_batch.append(f"**{i+1}. {msg_data['sender']}:** ❌ Translation failed\n")
    result = f"📚 **Batch Translation to {target_lang.upper()}:**\n\n" + "\n".join(translated_batch) + f"\n🔧 **Service:** {service_name}"
    if len(result) > 4000:
        parts, part_num, current = [], 1, f"📚 **Batch Translation to {target_lang.upper()}:** (Part 1)\n\n"
        for t in translated_batch:
            if len(current + t) > 3500:
                parts.append(current)
                part_num += 1
                current = f"📚 **Part {part_num}:**\n\n" + t
            else:
                current += t
        if current:
            parts.append(current)
        await message.edit_text(parts[0])
        for part in parts[1:]:
            await message.reply_text(part)
    else:
        await message.edit_text(result)


async def handle_quick_translation(client, message, target_lang, translator_services):
    service_name, TranslatorClass = translator_services[0]
    try:
        translator = TranslatorClass(source='auto', target=target_lang, base_url='https://libretranslate.de') if service_name == "Libre" else TranslatorClass(source='auto', target=target_lang)
    except:
        return await message.edit_text("❌ Failed to initialize translator")
    recent_messages = []
    try:
        async for msg in client.get_chat_history(message.chat.id, limit=10):
            if msg.text and msg.id != message.id and len(recent_messages) < 3:
                recent_messages.append(msg.text)
    except Exception as e:
        return await message.edit_text(f"❌ Failed to get recent messages: {str(e)}")
    if not recent_messages:
        return await message.edit_text("❌ No recent messages to translate")
    recent_messages.reverse()
    translated_messages = []
    for i, text in enumerate(recent_messages):
        try:
            translated = translator.translate(text)
            translated_messages.append(f"**{i+1}.** {translated}")
        except:
            translated_messages.append(f"**{i+1}.** ❌ Translation failed")
    result = f"🚀 **Quick Translation to {target_lang.upper()}:**\n\n" + "\n\n".join(translated_messages) + f"\n\n🔧 **Service:** {service_name}"
    await message.edit_text(result)
    
@app.on_message(filters.command("settitle", prefixes=".") & filters.me)
async def set_chat_title(client: Client, message: Message):
    if not await check_admin(client, message.chat.id, client.me.id):
        return await message.edit_text("I'm not admin here!")
    
    if len(message.command) < 2:
        return await message.edit_text("Provide a title to set")
    
    title = " ".join(message.command[1:])
    try:

        if message.chat.id not in chat_titles:
            chat_titles[message.chat.id] = message.chat.title
        
        await client.set_chat_title(message.chat.id, title)
        await message.edit_text(f"**Chat title changed to:** {title}")
    except Exception as e:
        await message.edit_text(f"**Error:** {str(e)}")

@app.on_message(filters.command("restoretitle", prefixes=".") & filters.me)
async def restore_chat_title(client: Client, message: Message):
    if not await check_admin(client, message.chat.id, client.me.id):
        return await message.edit_text("I'm not admin here!")
    
    if message.chat.id not in chat_titles:
        return await message.edit_text("No original title stored for this chat")
    
    try:
        original_title = chat_titles[message.chat.id]
        await client.set_chat_title(message.chat.id, original_title)
        await message.edit_text(f"**Chat title restored to:** {original_title}")
        del chat_titles[message.chat.id]
    except Exception as e:
        await message.edit_text(f"**Error:** {str(e)}")
        
#-----readqr-----
@app.on_message(filters.command(["readqr", "rqr", "reqdqr"], prefixes=".") & filters.me)
async def read_qr(client: Client, message: Message):
    """
    Read QR from replied image/file.
    - Always respond (if not reply -> friendly prompt)
    - Show a few aesthetic progress edits
    - Use asyncio.to_thread for requests to avoid blocking
    """
    from pathlib import Path
    import asyncio

    # --- Jika tidak ada reply ---
    if not message.reply_to_message:
        return await message.edit_text("**🌸❗ Reply a QR code image or file first, senpai.**")

    rep = message.reply_to_message

    # --- Pastikan reply berupa photo atau file ---
    if not (getattr(rep, "photo", None) or getattr(rep, "document", None)):
        return await message.edit_text("**🌺 Reply must be a QR code image or a file (photo/document).**")

    # --- Aesthetic progress messages (will edit the same message) ---
    progress_frames = [
        "🔎 Reading QR… • scanning matrix",
        "🌸 Processing… • kawaii eyes enabled",
        "✨ Enhancing contrast…",
        "🔬 Analysing modules…",
        "💞 Decoding… almost there"
    ]

    try:
        status = await message.edit_text("**🔍 Preparing to read QR…**")
    except:
        status = await message.reply_text("**🔍 Preparing to read QR…**")
    try:
        for frame in progress_frames[:3]:
            await asyncio.sleep(0.5)
            try:
                await status.edit_text(f"{frame}")
            except:
                pass
    except asyncio.CancelledError:
        pass
    except Exception:
        pass
        
    try:
        file_path = await client.download_media(rep)
        if not file_path:
            return await status.edit_text("❌ Failed to download image/file.")
    except Exception as e:
        return await status.edit_text(f"❌ Download error: {e}")
    try:
        await status.edit_text("🌸 **Sending to decoder service…**")
    except:
        pass
    try:
        def _post_file(fp):
            import requests
            with open(fp, "rb") as f:
                files = {"file": f}
                r = requests.post("https://api.qrserver.com/v1/read-qr-code/", files=files, timeout=20)
            return r

        resp = await asyncio.to_thread(_post_file, file_path)
        
        try:
            Path(file_path).unlink(missing_ok=True)
        except Exception:
            pass

    except Exception as e:
        return await status.edit_text(f"❌ Network / decoder error: {e}")

    # --- Process response ---
    if resp is None or resp.status_code != 200:
        return await status.edit_text("🚫 QR server error — try again later.")

    try:
        data = resp.json()
    except Exception:
        return await status.edit_text("❌ Failed to parse decoder response.")

    decoded = None
    try:
        decoded = data[0]["symbol"][0].get("data")
    except Exception:
        decoded = None

    if not decoded:
        # optional: show one last pretty fail frame
        try:
            await status.edit_text("💔 Could not decode QR — maybe blurry or not a QR.")
        except:
            pass
        return

    # success — pretty output (no logic change)
    try:
        # pick cute prefix based on length
        prefix = "🌸" if len(decoded) < 200 else "✨"
        await status.edit_text(f"{prefix} Decoded QR:\n`{decoded}`")
    except Exception:
        # fallback simple message
        await status.edit_text(f"**Decoded QR:** {decoded}")

# Config: candidate download/upload endpoints (try in order)
DOWNLOAD_TEST_URLS = [
    # small endpoints for quick check (range ~10 MB)
    "https://speed.hetzner.de/10MB.bin",
    "https://speed.hetzner.de/100MB.bin",
    "https://speed.cloudflare.com/__down?bytes=10000000",
]

UPLOAD_TEST_ENDPOINTS = [
    # services that accept POST; try until one responds OK
    "https://speed.cloudflare.com/__up",
    "https://postman-echo.com/post",
    "https://eu.httpbin.org/post",
]

# Servers for multi-server ping checks
PING_SERVERS = {
    "Google": "https://www.google.com",
    "Cloudflare": "https://1.1.1.1",
    "GitHub": "https://github.com",
}

# DNS servers to test
DNS_SERVERS = {
    "Cloudflare": "1.1.1.1",
    "Google": "8.8.8.8",
    "OpenDNS": "208.67.222.222",
}

# small helper: TCP connect time (ms)
async def _tcp_connect_time(host: str, port: int = 53, timeout: float = 2.0) -> Optional[float]:
    start = time.perf_counter()
    try:
        fut = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(fut, timeout=timeout)
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        return round((time.perf_counter() - start) * 1000.0, 1)
    except Exception:
        return None

# helper: dns query latency (ms)
async def _dns_query_time(nameserver: str, qname: str = "google.com", timeout: float = 3.0) -> Optional[float]:
    # Try dnspython (TCP) first
    if DNSPYTHON_AVAILABLE:
        try:
            resolver = dns.resolver.Resolver()
            resolver.nameservers = [nameserver]
            resolver.lifetime = timeout
            resolver.timeout = timeout
            start = time.perf_counter()
            # use TCP to increase success on mobile networks
            resolver.resolve(qname, lifetime=timeout, tcp=True)
            return round((time.perf_counter() - start) * 1000.0, 1)
        except Exception:
            pass
    # fallback: measure TCP connect to port 53
    return await _tcp_connect_time(nameserver, port=53, timeout=timeout)

# helper: try best upload endpoint (returns tuple (upload_speed_mbps, used_endpoint) or (0, None))
async def _try_upload_speed(session: aiohttp.ClientSession, payload_size_bytes: int = 2 * 1024 * 1024, timeout: float = 20.0):
    data = b"0" * payload_size_bytes
    for url in UPLOAD_TEST_ENDPOINTS:
        try:
            start = time.perf_counter()
            async with session.post(url, data=data, timeout=timeout) as resp:
                status = resp.status
                # treat 200..299 as success
                if 200 <= status < 300:
                    dur = time.perf_counter() - start
                    speed_mbps = round((len(data) * 8) / (dur * 1024 * 1024), 2)
                    return speed_mbps, url
        except Exception:
            continue
    return 0.0, None

# ---------- Speedtest -----------
FRAMES = ["🌸✨", "🌸💖", "🌸🌈", "🌸💫", "🌸🌟", "💮🌸"]  # spinner frames (kawaii vibe)
SPEED_EMOJI = {
    "title": "⚡️🌸 SpeedLab",
    "ping":  "🏓",
    "download": "⬇️",
    "upload": "⬆️",
    "dns": "🔍",
    "ok": "✅",
    "warn": "⚠️",
    "bad": "❌",
}

SPINNER_INTERVAL = 0.6  # seconds between spinner frames


@app.on_message(filters.command("speedtest", prefixes=".") & filters.me)
async def speed_test(client, message):
    try:
        args = (message.text or "").split()[1:]
        mode = args[0].lower() if args else "quick"
        if mode in ("q", "quick"):
            await quick_speedtest(client, message)
        elif mode in ("adv", "advanced", "advanc"):
            await advanced_speedtest(client, message)
        else:
            await message.edit_text("Usage: `.speedtest [q/quick]` or `.speedtest [adv/advanced]`")
    except Exception as e:
        try:
            await message.edit_text(f"{SPEED_EMOJI['bad']} Quick failure: {e}")
        except:
            pass


# ---- quick speedtest (ui + core) ----
async def quick_speedtest(client, message):
    # spinner task that keeps editing a single message
    spinner_msg = await message.edit_text(f"{FRAMES[0]} {SPEED_EMOJI['title']} — Preparing quick test...")
    stop_spinner = False

    async def spinner_loop(msg_obj):
        i = 0
        while not stop_spinner:
            frame = FRAMES[i % len(FRAMES)]
            try:
                await msg_obj.edit_text(f"{frame} {SPEED_EMOJI['title']} — Ping → Down → Up")
            except Exception:
                pass
            i += 1
            await asyncio.sleep(SPINNER_INTERVAL)

    spinner_task = asyncio.create_task(spinner_loop(spinner_msg))

    try:
        # ping
        try:
            t0 = time.perf_counter()
            async with aiohttp.ClientSession() as s:
                async with s.get("https://www.google.com", timeout=5) as r:
                    pass
            ping_ms = round((time.perf_counter() - t0) * 1000, 2)
        except Exception:
            ping_ms = 999.0

        # download
        try:
            download_speed = 0.0
            urls = globals().get("DOWNLOAD_TEST_URLS", ["https://speed.cloudflare.com/__down?bytes=10000000"])
            async with aiohttp.ClientSession() as s:
                for url in urls:
                    try:
                        t0 = time.perf_counter()
                        total = 0
                        async with s.get(url, timeout=35) as r:
                            async for chunk in r.content.iter_chunked(8192):
                                total += len(chunk)
                                if total >= 5 * 1024 * 1024:
                                    break
                        dur = time.perf_counter() - t0
                        if dur > 0 and total > 0:
                            download_speed = round((total * 8) / (dur * 1024 * 1024), 2)
                            break
                    except Exception:
                        continue
        except Exception:
            download_speed = 0.0

        # upload (uses helper if present)
        try:
            session = aiohttp.ClientSession()
            try:
                up_speed, up_url = await _try_upload_speed(session, payload_size_bytes=1 * 1024 * 1024, timeout=18.0)
            except Exception:
                # fallback to httpbin small upload
                try:
                    data = b"0" * (512 * 1024)
                    t0 = time.perf_counter()
                    async with aiohttp.ClientSession() as s2:
                        async with s2.post("https://httpbin.org/post", data=data, timeout=20) as r:
                            pass
                    up_speed = round((len(data) * 8) / ((time.perf_counter() - t0) * 1024 * 1024), 2)
                    up_url = "httpbin.org"
                except Exception:
                    up_speed, up_url = 0.0, None
            finally:
                await session.close()
        except Exception:
            up_speed, up_url = 0.0, None

        # quality label
        if download_speed >= 100:
            quality = "🟢 Excellent"
        elif download_speed >= 50:
            quality = "🟡 Good"
        elif download_speed >= 25:
            quality = "🟠 Fair"
        else:
            quality = "🔴 Poor"

        # stop spinner, compose result and edit
        stop_spinner = True
        await spinner_task
        final = (
            f"{SPEED_EMOJI['ok']} {SPEED_EMOJI['title']} — Quick Results\n\n"
            f"{SPEED_EMOJI['ping']} Ping: {ping_ms} ms\n"
            f"{SPEED_EMOJI['download']} Download: {download_speed} Mbps\n"
            f"{SPEED_EMOJI['upload']} Upload: {up_speed} Mbps\n\n"
            f"📊 Quality: {quality}\n"
            f"🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"{'🔗 ' + (up_url or '') if up_url else ''}"
        )
        await spinner_msg.edit_text(final)
    except Exception as e:
        stop_spinner = True
        try:
            await spinner_task
        except:
            pass
        try:
            await spinner_msg.edit_text(f"{SPEED_EMOJI['bad']} Quick speedtest error: {e}")
        except:
            pass


# ---- advanced speedtest (ui + details) ----
async def advanced_speedtest(client, message):
    msg = await message.edit_text(f"{FRAMES[0]} {SPEED_EMOJI['title']} — Preparing advanced checks...")
    stop_spinner = False

    async def adv_spinner_loop(msg_obj):
        i = 0
        while not stop_spinner:
            frame = FRAMES[i % len(FRAMES)]
            try:
                await msg_obj.edit_text(f"{frame} {SPEED_EMOJI['title']} — Advanced • collecting data...")
            except Exception:
                pass
            i += 1
            await asyncio.sleep(SPINNER_INTERVAL)

    spinner_task = asyncio.create_task(adv_spinner_loop(msg))

    results = {}
    try:
        # system
        try:
            import psutil
            results['system'] = {
                'os': f"{platform.system()} {platform.release()}",
                'cpu': psutil.cpu_count(logical=True),
                'ram': f"{round(psutil.virtual_memory().available/1024**3,1)} GB available"
            }
        except Exception:
            results['system'] = {'os': f"{platform.system()} {platform.release()}", 'cpu': 'N/A', 'ram': 'N/A'}

        # network info
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get("https://api.ipify.org?format=json", timeout=6) as r:
                    ip_data = await r.json()
                async with s.get(f"https://ipapi.co/{ip_data.get('ip')}/json/", timeout=6) as r2:
                    geo = await r2.json()
            results['network'] = {'ip': ip_data.get('ip','N/A'), 'isp': geo.get('org','N/A'), 'location': f"{geo.get('city','N/A')}, {geo.get('country_name','N/A')}"}
        except Exception:
            results['network'] = {'ip': 'N/A', 'isp': 'N/A', 'location': 'N/A'}

        # ping multiple servers
        try:
            ping_results = {}
            servers = globals().get("PING_SERVERS", {"Google": "https://www.google.com", "Cloudflare": "https://1.1.1.1", "GitHub": "https://github.com"})
            async with aiohttp.ClientSession() as s:
                for name, url in servers.items():
                    times = []
                    for _ in range(3):
                        try:
                            t0 = time.perf_counter()
                            async with s.get(url, timeout=4) as r:
                                pass
                            times.append((time.perf_counter() - t0) * 1000)
                        except Exception:
                            times.append(999.0)
                        await asyncio.sleep(0.12)
                    try:
                        avg = round(statistics.mean(times), 1)
                        jitter = round(statistics.stdev(times), 1) if len(times) > 1 else 0.0
                    except Exception:
                        avg, jitter = 999.0, 999.0
                    ping_results[name] = {'avg': avg, 'jitter': jitter}
            results['ping'] = ping_results
        except Exception:
            results['ping'] = {}

        # stability
        try:
            samples = []
            async with aiohttp.ClientSession() as s:
                for _ in range(5):
                    try:
                        t0 = time.perf_counter()
                        async with s.get("https://www.google.com", timeout=4) as r:
                            pass
                        samples.append((time.perf_counter() - t0) * 1000)
                    except Exception:
                        samples.append(999.0)
                    await asyncio.sleep(0.15)
            stability_avg = round(statistics.mean(samples), 1)
            stability_jitter = round(statistics.stdev(samples), 1) if len(samples) > 1 else 0.0
            stability_quality = "Excellent" if stability_jitter < 5 else "Good" if stability_jitter < 15 else "Poor"
            results['stability'] = {'avg_ping': stability_avg, 'jitter': stability_jitter, 'quality': stability_quality}
        except Exception:
            results['stability'] = {'quality': 'N/A', 'avg_ping': 'N/A', 'jitter': 'N/A'}

        # download multi-size
        downloads = {}
        urls = globals().get("DOWNLOAD_TEST_URLS", ["https://speed.cloudflare.com/__down?bytes=10000000"])
        for size_mb in (5, 25, 50):
            val = 0.0
            try:
                async with aiohttp.ClientSession() as s:
                    for url in urls:
                        try:
                            t0 = time.perf_counter()
                            total = 0
                            async with s.get(f"{url}", timeout=60) as r:
                                async for chunk in r.content.iter_chunked(8192):
                                    total += len(chunk)
                                    if total >= size_mb * 1024 * 1024:
                                        break
                            dur = time.perf_counter() - t0
                            if dur > 0 and total > 0:
                                val = round((total * 8) / (dur * 1024 * 1024), 2)
                                break
                        except Exception:
                            continue
            except Exception:
                val = 0.0
            downloads[f"{size_mb}MB"] = val
        results['download'] = downloads

        # upload
        try:
            async with aiohttp.ClientSession() as s:
                up_speed, up_url = await _try_upload_speed(s, payload_size_bytes=2 * 1024 * 1024, timeout=30.0)
        except Exception:
            up_speed, up_url = 0.0, None
        results['upload'] = up_speed
        results['upload_endpoint'] = up_url

        # dns checks
        try:
            dns_out = {}
            servers = globals().get("DNS_SERVERS", {"Cloudflare": "1.1.1.1", "Google": "8.8.8.8", "OpenDNS": "208.67.222.222"})
            for name, ns in servers.items():
                try:
                    t = await _dns_query_time(ns, qname="google.com", timeout=3.0)
                    dns_out[name] = 999.0 if t is None else round(t, 1)
                except Exception:
                    dns_out[name] = 999.0
            results['dns'] = dns_out
        except Exception:
            results['dns'] = {}

        # stop spinner & compose final report
        stop_spinner = True
        await spinner_task

        # pretty report
        sys_info = results.get('system', {})
        net_info = results.get('network', {})
        ping_data = results.get('ping', {})
        stability = results.get('stability', {})
        downloads = results.get('download', {})
        dns_data = results.get('dns', {})

        avg_downloads = [v for v in downloads.values() if v]
        avg_download = round(sum(avg_downloads) / len(avg_downloads), 1) if avg_downloads else 0.0

        lines = [
            f"{SPEED_EMOJI['title']} — Advanced Results",
            "",
            f"💻 System: {sys_info.get('os','N/A')} • {sys_info.get('cpu','N/A')} cores • {sys_info.get('ram','N/A')}",
            f"🌐 Network: {net_info.get('isp','N/A')} • {net_info.get('location','N/A')}",
            "",
            "🏓 Multi-Server Ping:"
        ]
        for srv, d in ping_data.items():
            lines.append(f"• {srv}: {d.get('avg','N/A')} ms (±{d.get('jitter','N/A')} ms)")
        lines += [
            "",
            "📊 Connection Quality:",
            f"• Stability: {stability.get('quality','N/A')} (Jitter: {stability.get('jitter','N/A')} ms)",
            "",
            "⬇️ Download Speeds:"
        ]
        for k, v in downloads.items():
            lines.append(f"• {k}: {v} Mbps")
        lines += [
            "",
            f"⬆️ Upload: {results.get('upload','N/A')} Mbps (endpoint: {results.get('upload_endpoint','N/A')})",
            f"🔍 DNS (fastest): {min(list(dns_data.values())) if dns_data else 'N/A'} ms",
            "",
            f"📈 Overall Score: {avg_download} Mbps avg • {stability.get('quality','Unknown')} stability",
            f"🕒 Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        ]

        await msg.edit_text("\n".join(lines))
    except Exception as e:
        stop_spinner = True
        try:
            await spinner_task
        except:
            pass
        try:
            await msg.edit_text(f"{SPEED_EMOJI['bad']} Advanced speedtest failed: {e}")
        except:
            pass

# ---------- end speedtest UI block ----------

@app.on_message(filters.command("speedhistory", prefixes=".") & filters.me)
async def speed_history_cmd(client: Client, message: Message):
    if not speed_history:
        await message.edit_text("**📈 Speed History**\n\nNo tests recorded yet. Run `.speedtest` first!")
        return
    history_text = "**📈 Speed History (Last 10 tests)**\n\n"
    for i, record in enumerate(speed_history[-10:], 1):
        history_text += f"**{i}.** {record['time']} • ⬇️{record['download']}Mbps • 🏓{record['ping']}ms\n"
    await message.edit_text(history_text)

def save_speed_result(download, ping, upload=None):
    speed_history.append({
        'time': datetime.now().strftime('%m/%d %H:%M'),
        'download': download,
        'ping': ping,
        'upload': upload
    })
    if len(speed_history) > 50:
        speed_history.pop(0)

# ----- robust .kang handler (replace previous handler) -----
SHORTNAME_MAX_TRIES = 6
STICKER_EMOJI_DEFAULT = "✨"

def _safe_shortname_candidate(base: str) -> str:
    s = base.lower()
    s = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in s)
    while "__" in s:
        s = s.replace("__", "_")
    if not s or not s[0].isalpha():
        s = "a" + s
    return s[:48]

def _resize_image_to_webp(input_path: str, output_path: str):
    im = Image.open(input_path).convert("RGBA")
    max_side = max(im.width, im.height)
    if max_side != 512:
        scale = 512 / float(max_side)
        new_w = max(1, int(im.width * scale))
        new_h = max(1, int(im.height * scale))
        im = im.resize((new_w, new_h), Image.LANCZOS)
    # ensure saving as webp with alpha
    im.save(output_path, "WEBP", lossless=True, quality=100, method=6)

async def _upload_and_get_inputdocument(client, file_path: str):
    """
    Upload a document to self (or 'me') and return raw InputDocument (suitable for raw funcs).
    Also delete the temporary message.
    """
    msg = await client.send_document("me", file_path, force_document=True, disable_notification=True)
    # get file_id
    file_id = msg.document.file_id
    uploaded = utils.get_input_media_from_file_id(file_id, FileType.DOCUMENT)
    # delete helper message
    try:
        await msg.delete()
    except:
        pass
    return uploaded.id

async def _raw_create_sticker_set_fallback(client, title, short_name, sticker_input_doc, emoji, masks=None):
    r = await client.invoke(
        raw.functions.stickers.CreateStickerSet(
            user_id=await client.resolve_peer("me"),
            title=title,
            short_name=short_name,
            stickers=[raw.types.InputStickerSetItem(document=sticker_input_doc, emoji=emoji)],
            masks=masks if masks is not None else False
        )
    )
    # return parsed set if needed (not used here)
    return r

async def _raw_add_sticker_to_set_fallback(client, short_name, sticker_input_doc, emoji):
    r = await client.invoke(
        raw.functions.stickers.AddStickerToSet(
            stickerset=raw.types.InputStickerSetShortName(short_name=short_name),
            sticker=raw.types.InputStickerSetItem(document=sticker_input_doc, emoji=emoji)
        )
    )
    return r

@app.on_message((filters.me | filters.user(OWNER_ID)) & filters.command("kang", prefixes="."))
async def cmd_kang_auto(client, message: Message):
    caller = message.from_user
    if not caller:
        return

    try:
        me = await client.get_me()
        allowed_ids = {OWNER_ID, getattr(me, "id", None)} | set(sudo_users)
        if caller.id not in allowed_ids:
            return await message.edit_text("Kamu nggak punya izin pakai `.kang`")
    except Exception:
        pass

    if not message.reply_to_message:
        return await message.edit_text("Reply ke gambar / sticker / webp yang mau dijadiin sticker.")

    reply = message.reply_to_message
    args = (message.text or "").split()[1:]
    user_provided_short = None
    emoji = STICKER_EMOJI_DEFAULT
    if args:
        if len(args) == 1:
            # heuristic: shortnames normally ascii long, emoji usually short or non-ascii
            if len(args[0]) <= 3 or any(ord(c) > 128 for c in args[0]):
                emoji = args[0]
            else:
                user_provided_short = args[0]
        else:
            user_provided_short = args[0]
            emoji = args[1]

    status = await message.edit_text("✨ Processing...")

    tmpdir = tempfile.mkdtemp(prefix="kang_")
    try:
        # download media -> ensure we have a real file path
        try:
            media_path = await reply.download(file_name=tmpdir)
        except Exception as e:
            await status.edit_text(f"❌ Gagal download media: {e}")
            return

        # pyrogram sometimes returns a directory -> pick biggest file inside
        if os.path.isdir(media_path):
            files = [os.path.join(media_path, f) for f in os.listdir(media_path) if os.path.isfile(os.path.join(media_path, f))]
            if not files:
                await status.edit_text("❌ Gagal: file tidak ditemukan setelah download.")
                return
            files.sort(key=lambda p: os.path.getsize(p), reverse=True)
            media_path = files[0]

        # prepare webp destination
        webp_path = str(Path(tmpdir) / "sticker.webp")

        # If reply is already a sticker file (webp) we still normalize & re-save to ensure alpha+size
        try:
            _resize_image_to_webp(media_path, webp_path)
        except Exception as e:
            await status.edit_text(f"❌ Gagal convert image ke webp: {e}")
            return

        # build candidate shortname
        try:
            me = await client.get_me()
            uname = (getattr(me, "username", None) or getattr(me, "first_name", "user")).strip()
        except Exception:
            uname = "user"

        base_candidate = user_provided_short or f"{_safe_shortname_candidate(uname)}_pack"
        base_candidate = _safe_shortname_candidate(base_candidate)

        success = False
        last_err = None
        chosen_short = None

        for attempt in range(SHORTNAME_MAX_TRIES):
            candidate = base_candidate if attempt == 0 else f"{base_candidate}_{attempt}"
            try:
                # check exist
                exists = True
                try:
                    # prefer high-level API if present
                    _ = await client.get_sticker_set(candidate)
                    exists = True
                except Exception:
                    exists = False

                if exists:
                    # try high-level add first
                    try:
                        if hasattr(client, "add_sticker_to_set"):
                            await client.add_sticker_to_set(candidate, webp_path, emoji=emoji)
                            chosen_short = candidate
                            success = True
                            break
                        else:
                            # fallback: upload doc + raw.AddStickerToSet
                            input_doc = await _upload_and_get_inputdocument(client, webp_path)
                            await _raw_add_sticker_to_set_fallback(client, candidate, input_doc, emoji)
                            chosen_short = candidate
                            success = True
                            break
                    except Exception as e_add:
                        last_err = e_add
                        # if invalid file -> maybe conversion failed -> give up for this candidate
                        txt = str(e_add).lower()
                        if "invalid" in txt or "sticker_file_invalid" in txt:
                            # try next candidate (rare); but usually file issue -> break and report
                            break
                        # if some 'not found' / missing -> try next candidate name
                        if "not found" in txt or "invalid set" in txt or "shortname" in txt:
                            await asyncio.sleep(0.2)
                            continue
                        # other errors -> stop trying
                        break
                else:
                    # create new set (high-level if available)
                    try:
                        if hasattr(client, "create_sticker_set"):
                            await client.create_sticker_set(title=f"{uname}'s pack", short_name=candidate, sticker=webp_path, emoji=emoji)
                            chosen_short = candidate
                            success = True
                            break
                        else:
                            # fallback raw create: upload doc -> raw CreateStickerSet
                            input_doc = await _upload_and_get_inputdocument(client, webp_path)
                            await _raw_create_sticker_set_fallback(client, f"{uname}'s pack", candidate, input_doc, emoji)
                            chosen_short = candidate
                            success = True
                            break
                    except Exception as e_create:
                        last_err = e_create
                        txt = str(e_create).lower()
                        if "taken" in txt or "occupied" in txt or "already" in txt:
                            await asyncio.sleep(0.2)
                            continue
                        break

            except Exception as e_outer:
                last_err = e_outer
                await asyncio.sleep(0.3)
                continue

        if not success:
            txt = f"❌ Gagal bikin/menambah pack. Last error: {last_err}"
            await status.edit_text(txt)
            return

        await status.edit_text(f"✅ Sticker ditambahkan ke pack: https://t.me/addstickers/{chosen_short}")

    finally:
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass
            
#----helper add-----
async def check_admin(client: Client, chat_id: int, user_id: int):
    try:
        member = await client.get_chat_member(chat_id, user_id)
        status = member.status

        if status in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]:
            return True

        return False
    except:
        return False

def mock_text(text):
    """Convert text to alternating caps (mocking)"""
    return ''.join(c.upper() if i % 2 == 0 else c.lower() for i, c in enumerate(text))
    
def spoiler_text(text):
    """Create spoiler text"""
    return f"||{text}||"
    
def cowsay(text):
    """Generate cowsay ASCII art"""
    lines = text.split('\n')
    max_len = max(len(line) for line in lines)
    border = " " + "_" * (max_len + 2) + " "
    bottom_border = " " + "-" * (max_len + 2) + " "
    
    result = [border]
    for line in lines:
        padding = " " * (max_len - len(line))
        result.append(f"< {line}{padding} >")
    result.append(bottom_border)
    result.append("        \\   ^__^")
    result.append("         \\  (oo)\\_______")
    result.append("            (__)\\       )\\/\\")
    result.append("                ||----w |")
    result.append("                ||     ||")
    
    return "\n".join(result)
    
#----addddd-------
@app.on_message(filters.command("ascii", prefixes=".") & filters.me)
async def ascii_command(client, message):
    if len(message.command) < 2 and not message.reply_to_message:
        await message.edit("Usage: `.ascii <text>` or reply to a message.")
        return

    if len(message.command) >= 2:
        text = " ".join(message.command[1:])
    elif message.reply_to_message and message.reply_to_message.text:
        text = message.reply_to_message.text
    else:
        await message.edit("No valid text found to convert.")
        return

    try:
        ascii_art = figlet_format(text)
        if len(ascii_art) > 4096:
            ascii_art = ascii_art[:4093] + "..."
        await message.edit(f"```\n{ascii_art}\n```")
    except Exception as e:
        await message.edit(f"Error creating ASCII art: `{e}`")
        
@app.on_message(filters.command("purge", prefixes=".") & filters.me)
async def purge_messages(client: Client, message: Message):
    if not await check_admin(client, message.chat.id, client.me.id):
        return await message.edit_text("I'm not admin here!")
    
    if not message.reply_to_message:
        return await message.edit_text("Reply to start purging from")
    
    try:
        start_id = message.reply_to_message.id
        end_id = message.id
        deleted = 0
        
        for i in range(start_id, end_id + 1):
            try:
                await client.delete_messages(message.chat.id, i)
                deleted += 1
            except FloodWait as e:
                await asyncio.sleep(e.value)
            except:
                pass
        
        msg = await message.edit_text(f"**Purged {deleted} messages!**")
        await asyncio.sleep(3)
        await msg.delete()
    except Exception as e:
        await message.edit_text(f"Error: {str(e)}")

@app.on_message(filters.command("del", prefixes=".") & filters.me)
async def delete_message(client: Client, message: Message):
    if not message.reply_to_message:
        return await message.edit_text("Reply to a message to delete")
    
    try:
        await client.delete_messages(
            message.chat.id,
            [message.reply_to_message.id, message.id]
        )
    except Exception as e:
        await message.edit_text(f"Error: {str(e)}")
        
@app.on_message(filters.command("spoiler", prefixes=".") & filters.me)
async def spoiler_text_command(client: Client, message: Message):

    text = ""
    if len(message.command) > 1:
        text = " ".join(message.command[1:])
    elif message.reply_to_message:
        text = message.reply_to_message.text or ""
    else:
        return await message.edit_text("Provide text or reply to message")
    
    if not text:
        return await message.edit_text("No text found!")
    
    await message.edit_text(spoiler_text(text))
          
@app.on_message(filters.command("mock", prefixes=".") & filters.me)
async def mock_text_command(client: Client, message: Message):

    text = ""
    if len(message.command) > 1:
        text = " ".join(message.command[1:])
    elif message.reply_to_message:
        text = message.reply_to_message.text or ""
    else:
        return await message.edit_text("Provide text or reply to message")
    
    if not text:
        return await message.edit_text("No text found!")
    
    await message.edit_text(mock_text(text))
            
@app.on_message((filters.me | filters.user(OWNER_ID)) & filters.command("pin", prefixes="."))
async def pin_message(client: Client, message: Message):
    """
    .pin (reply) -> pins replied message.
    Owner, userbot account and sudo users are allowed to call (matches other handlers pattern).
    Bot must be admin in the chat with pin permission.
    """
    # require reply
    if not message.reply_to_message:
        return await message.edit_text("Reply ke pesan yang mau dipin.")

    # check bot (self) admin & has pin permission
    try:
        me = await client.get_me()
        bot_id = getattr(me, "id", None)
    except Exception:
        bot_id = None

    if not bot_id:
        return await message.edit_text("Gagal ambil info akun (get_me()).")

    # ensure this is a group/supergroup/channel where pin is meaningful
    chat = message.chat
    if not chat or getattr(chat, "type", None) in (None, "private", "direct"):
        return await message.edit_text("Perintah ini hanya berlaku di grup / channel.")

    # check bot admin
    ok = await check_admin(client, message.chat.id, bot_id)
    if not ok:
        return await message.edit_text("Aku bukan admin di sini atau gak punya izin untuk pin pesan.")

    # attempt to pin
    try:
        await client.pin_chat_message(
            message.chat.id,
            message.reply_to_message.id,
            disable_notification=False
        )
        await message.edit_text("✅ Pinned!")
    except Exception as e:
        # tampilkan pesan error singkat
        await message.edit_text(f"❌ Gagal pin: {e}")
 
@app.on_message(filters.command("unpin", prefixes=".") & filters.me)
async def unpin_message(client: Client, message: Message):
    if not await check_admin(client, message.chat.id, client.me.id):
        return await message.edit_text("I'm not admin here!")
    
    if not message.reply_to_message:
        return await message.edit_text("Reply to a pinned message to unpin")
    
    try:
        await client.unpin_chat_message(
            message.chat.id,
            message.reply_to_message.id
        )
        await message.edit_text("**Unpinned!**")
    except Exception as e:
        await message.edit_text(f"Error: {str(e)}")

@app.on_message(filters.command("admins", prefixes=".") & filters.me)
async def list_admins(client: Client, message: Message):
    try:
        admins = []
        async for admin in client.get_chat_members(
            message.chat.id, 
            filter=enums.ChatMembersFilter.ADMINISTRATORS
        ):
            if not admin.user.is_bot:
                name = admin.user.first_name or "No Name"
                admins.append(f"• 👑 [{name}](tg://user?id={admin.user.id})")

        if not admins:
            return await message.edit_text("❌ Tidak ada admin ditemukan.")

        admin_text = (
            "📜 **Daftar Admin Grup**\n"
            "———————————————\n"
            + "\n".join(admins)
            + "\n———————————————"
            "\n📝 Total admin: **{}**".format(len(admins))
        )

        await message.edit_text(admin_text)
    except Exception as e:
        await message.edit_text(f"⚠️ Error: `{e}`")
        

# Spinner frames — pilih satu dari list atau ubah sesuka lo
WEATHER_SPIN_FRAMES = ["🌸✨", "🌸💖", "🌸🌈", "🌸💫", "🍥✨", "🔮✨"]

@app.on_message(filters.command("weather", prefixes=".") & filters.me)
async def weather_info(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.edit_text("Provide city name. Example: `.weather jakarta`")

    city = " ".join(message.command[1:]).strip()
    if not city:
        return await message.edit_text("Provide city name. Example: `.weather jakarta`")

    url = f"https://wttr.in/{city}?format=j1"

    # start spinner task that edits message periodically
    spinner_running = True

    async def spinner_task(msg_obj):
        i = 0
        try:
            while spinner_running:
                frame = WEATHER_SPIN_FRAMES[i % len(WEATHER_SPIN_FRAMES)]
                try:
                    await msg_obj.edit_text(f"{frame}  Fetching weather for **{city.title()}**...\nPlease wait...")
                except Exception:
                    # if edit fails (flood/wait), ignore and continue
                    pass
                i += 1
                await asyncio.sleep(0.6)
        except asyncio.CancelledError:
            return

    status_msg = await message.edit_text(f"🌤 Fetching weather for **{city.title()}**...")

    spinner = asyncio.create_task(spinner_task(status_msg))

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=15) as resp:
                if resp.status != 200:
                    spinner_running = False
                    spinner.cancel()
                    return await status_msg.edit_text("Couldn't fetch weather data (remote). Try again later.")

                data = await resp.json()

        # parse safely
        try:
            current = data.get('current_condition', [])[0]
            weather_desc = current.get('weatherDesc', [{"value": "N/A"}])[0].get('value', "N/A")
            temp_c = current.get('temp_C', "N/A")
            feels = current.get('FeelsLikeC', "N/A")
            humidity = current.get('humidity', "N/A")
            wind = f"{current.get('windspeedKmph','N/A')} km/h ({current.get('winddir16Point','N/A')})"
            cloud = current.get('cloudcover', "N/A")
            astronomy = data.get('weather', [{}])[0].get('astronomy', [{}])[0]
            sunrise = astronomy.get('sunrise', "N/A")
            sunset = astronomy.get('sunset', "N/A")
        except Exception:
            spinner_running = False
            spinner.cancel()
            return await status_msg.edit_text("Error parsing weather data.")

        # stop spinner and show result (edit_text)
        spinner_running = False
        spinner.cancel()

        report = (
            f"🌤 **Weather — {city.title()}**\n\n"
            f"🔎 Condition : {weather_desc}\n"
            f"🌡 Temperature: {temp_c}°C (Feels like {feels}°C)\n"
            f"💧 Humidity: {humidity}%\n"
            f"💨 Wind: {wind}\n"
            f"☁️ Cloud Cover: {cloud}%\n\n"
            f"🌅 Sunrise: {sunrise}\n"
            f"🌇 Sunset : {sunset}\n\n"
            f"📅 Fetched: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}"
        )
        await status_msg.edit_text(report)

    except asyncio.TimeoutError:
        spinner_running = False
        spinner.cancel()
        await status_msg.edit_text("Request timed out. Try again later.")
    except Exception as e:
        spinner_running = False
        spinner.cancel()
        await status_msg.edit_text(f"Error: {e}")
        
# ---------- WHOIS (plain text, emoji'd) ----------
@app.on_message(filters.command("whois", prefixes=".") & filters.me)
async def advanced_user_info(client: Client, message: Message):
    # Resolve target
    try:
        if message.reply_to_message and message.reply_to_message.from_user:
            target_id = message.reply_to_message.from_user.id
        elif len(message.command) > 1:
            target_id = message.command[1]
        else:
            target_id = message.from_user.id

        user = await client.get_users(target_id)
    except Exception as e:
        return await message.edit_text(f"⚠️ Gagal ambil user: {e}")

    # Try to get chat member info (may fail in private/outside chats)
    chat_member = None
    try:
        chat_member = await client.get_chat_member(message.chat.id, user.id)
    except Exception:
        chat_member = None

    # Build plain-text output with emojis
    name = (user.first_name or "") + (f" {user.last_name}" if user.last_name else "")
    username = f"@{user.username}" if getattr(user, "username", None) else "—"
    mention_line = f"{name}"

    def t(v): return "✅" if v else "❌"

    bio = getattr(user, "bio", "") or ""
    status = getattr(chat_member, "status", None)
    if status is None:
        status_text = "—"
    else:
        # normalize enum/obj
        status_text = getattr(status, "value", str(status))

    joined = ""
    try:
        jd = getattr(chat_member, "joined_date", None)
        if jd:
            if isinstance(jd, (int, float)):
                joined = datetime.fromtimestamp(jd).strftime("%Y-%m-%d %H:%M UTC")
            else:
                joined = jd.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        joined = ""

    lines = [
        "🕵️‍♂️ WHOIS — Advanced User Info",
        f"🆔 ID       : {user.id}",
        f"👤 Name     : {mention_line}",
        f"🔖 Username : {username}",
    ]
    if bio:
        lines.append(f"💬 Bio      : {bio}")
    lines += [
        f"🤖 Bot      : {t(getattr(user, 'is_bot', False))}",
        f"✅ Verified : {t(getattr(user, 'is_verified', False))}",
        f"🔒 Restricted: {t(getattr(user, 'is_restricted', False))}",
        f"⚠️ Scam     : {t(getattr(user, 'is_scam', False))}",
        f"🌟 Premium  : {t(getattr(user, 'is_premium', False))}",
        f"📌 Status   : {status_text}",
    ]
    if joined:
        lines.append(f"📅 Joined   : {joined}")

    # Some extra optional flags
    try:
        lines.append(f"📇 Contact  : {t(getattr(user, 'is_contact', False))}")
    except Exception:
        pass

    out = "\n".join(lines)
    try:
        await message.edit_text(out)
    except Exception as e:
        await message.edit_text(f"⚠️ Error waktu kirim: {e}")


# ---------- STATS (plain text, emoji'd) ----------
@app.on_message((filters.me | ALLOW_FILTER) & filters.command("stats", prefixes="."))
async def chat_stats(client: Client, message: Message):
    try:
        chat = await client.get_chat(message.chat.id)
    except Exception as e:
        return await message.edit_text(f"⚠️ Gagal ambil info chat: {e}")

    # members count (best-effort)
    members_count = getattr(chat, "members_count", None)
    if members_count is None:
        try:
            members_count = await client.get_chat_members_count(message.chat.id)
        except Exception:
            members_count = "—"

    # admins count
    try:
        admins_count = 0
        async for _ in client.get_chat_members(message.chat.id, filter=enums.ChatMembersFilter.ADMINISTRATORS):
            admins_count += 1
    except Exception:
        admins_count = "—"

    chat_type = getattr(chat, "type", None)
    chat_type_text = getattr(chat_type, "value", str(chat_type)) if chat_type else "—"

    lines = [
        "📊 CHAT STATS",
        f"🏷️ Title   : {getattr(chat, 'title', '—')}",
        f"🆔 ID      : {getattr(chat, 'id', '—')}",
        f"👥 Members : {members_count}",
        f"👮 Admins  : {admins_count}",
        f"🧭 Type    : {chat_type_text}",
    ]

    out = "\n".join(lines)
    try:
        await message.edit_text(out)
    except Exception as e:
        await message.edit_text(f"⚠️ Error kirim stats: {e}")
        
# --- Konfigurasi API Hugging Face ---
HF_API_TOKEN = os.getenv("HF_API_TOKEN") # Ambil dari environment variable
# Ganti nama variabel biar lebih jelas
HF_MODEL_DEFAULT = os.getenv("HF_MODEL_DEFAULT", "openai/gpt-oss-120b:fastest") # Model default buat .ai_hf
HF_MODEL_DEEPSEEK = os.getenv("HF_MODEL_DEEPSEEK", "deepseek-ai/DeepSeek-R1:fastest") # Model default buat .ai_deepseek

if not HF_API_TOKEN:
    raise ValueError("HF_API_TOKEN environment variable is missing!")

# --- Fungsi Request ke Hugging Face Router (v1/chat/completions) ---
def get_hf_response_v1_chat(prompt: str, model_name: str) -> str:
    """
    Requests a chat completion response from the Hugging Face Router API.
    Uses the NEW v1/chat/completions endpoint format.
    """
    # -- INI URL YANG BENER SESUAI HASIL CURL --
    url = "https://router.huggingface.co/v1/chat/completions"
    # -------------------------------------------
    headers = {
        "Authorization": f"Bearer {HF_API_TOKEN}",
        "Content-Type": "application/json"
    }
    # Payload sesuai format dari curl
    payload = {
        "model": model_name, # Model name dipindah ke sini
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ],
        "stream": False, # Atau True kalo mau streaming (lebih kompleks)
        # Tambahkan parameter lain jika perlu
        # "max_tokens": 256,
        # "temperature": 0.7,
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status() # Akan raise exception jika status != 200
        result = response.json()
        # print(result) # Debug: Uncomment to see raw response if needed
        # Format respons untuk /chat/completions
        if isinstance(result, dict) and 'choices' in result and len(result['choices']) > 0:
            message_content = result['choices'][0].get('message', {}).get('content', '')
            if message_content:
                return message_content.strip() # Hilangkan whitespace di awal/akhir
            else:
                 return f"Respons dari Hugging Face (chat) tidak ditemukan konten: {result}"
        else:
             return f"Respons dari Hugging Face (chat) tidak sesuai format: {result}"
    except requests.exceptions.HTTPError as he:
        logger.error(f"HTTP Error request ke Hugging Face: {he}")
        if he.response is not None:
            error_detail = he.response.text
            logger.error(f"Response Body: {error_detail}")
            status_code = he.response.status_code
            if status_code == 404:
                 # Endpoint /chat/completions ini seharusnya ada, jadi 404 mungkin berarti model salah?
                 return f"Model '{model_name}' tidak ditemukan atau tidak dapat diakses melalui endpoint chat ini. Error: {error_detail}"
            elif status_code == 503:
                return "Model sedang overload atau maintenance. Coba lagi nanti."
            elif status_code == 422: # Unprocessable Entity (Payload invalid)
                return f"Permintaan ke Hugging Face tidak valid (mungkin model tidak support chat): {error_detail}"
            elif status_code == 429: # Rate Limit
                return "Kuota request Hugging Face habis atau terlalu cepat. Tunggu sebentar."
            else:
                return f"Error HTTP {status_code} dari Hugging Face: {error_detail}"
        return f"HTTP Error saat menghubungi Hugging Face: {he}"
    except requests.exceptions.RequestException as e:
        logger.error(f"Request Exception saat request ke Hugging Face: {e}")
        return f"Error request ke Hugging Face: {e}"
    except Exception as e:
        logger.error(f"Error tak terduga dari Hugging Face: {e}")
        return f"Error tak terduga: {e}"

# --- Handler Command Hugging Face - Default (OSS Model) ---
# ... (kode import dan fungsi get_hf_response_v1_chat sebelumnya tetap sama) ...

# --- Fungsi buat truncate atau split teks panjang ---
def split_message(text: str, max_length: int = 4000) -> list[str]:
    """
    Splits a long text into chunks not exceeding max_length.
    Tries to split by paragraphs or sentences if possible.
    Falls back to character split if necessary.
    """
    if len(text) <= max_length:
        return [text]

    chunks = []
    current_chunk = ""

    # Coba split dulu berdasarkan baris baru
    paragraphs = text.split('\n')
    for paragraph in paragraphs:
        # Tambahin baris baru ke paragraf kalo bukan paragraf terakhir
        if current_chunk and not current_chunk.endswith('\n'):
             current_chunk += "\n"
        if len(paragraph) + len(current_chunk) <= max_length:
            current_chunk += paragraph
        else:
            # Kalo paragraf aja udah kepanjangan, coba split lebih dalam
            if current_chunk: # Simpan chunk sebelumnya kalo ada
                chunks.append(current_chunk)
            current_chunk = paragraph # Mulai chunk baru dengan paragraf itu

            # Kalo chunk baru masih kepanjangan, split jadi potongan-potongan kecil
            if len(current_chunk) > max_length:
                temp_chunks = []
                temp_chunk = ""
                words = current_chunk.split(' ')
                for word in words:
                    # Tambahin spasi sebelum kata, kecuali kata pertama di chunk
                    word_to_add = f" {word}" if temp_chunk else word
                    if len(temp_chunk) + len(word_to_add) <= max_length:
                        temp_chunk += word_to_add
                    else:
                        if temp_chunk: # Simpan chunk sementara kalo ada isinya
                            temp_chunks.append(temp_chunk)
                        temp_chunk = word # Mulai chunk baru dengan kata itu
                if temp_chunk: # Tambahin sisa potongan terakhir
                    temp_chunks.append(temp_chunk)

                # Tambahin semua potongan kecil ke chunks utama
                chunks.extend(temp_chunks)
                current_chunk = "" # Reset current_chunk karena udah diproses

    # Tambahin chunk terakhir kalo ada
    if current_chunk:
        chunks.append(current_chunk)

    # Jaga-juga kalo ada potongan yang masih melebihi batas (misalnya dari split kata)
    final_chunks = []
    for chunk in chunks:
        if len(chunk) > max_length:
            # Jika potongan masih terlalu panjang, split karakter
            for i in range(0, len(chunk), max_length):
                final_chunks.append(chunk[i:i + max_length])
        else:
            final_chunks.append(chunk)

    return final_chunks

# --- Handler Command Hugging Face - Default (OSS Model) ---
# --- Handler Command Hugging Face - DeepSeek ---
@app.on_message(filters.command("openai", prefixes=".") & filters.me)
async def ai_hf_command(client: Client, message: Message):
    """
    Handles the '.ai_hf' command to query a default Hugging Face model (e.g., OSS).
    Splits response if it's too long.
    """
    if len(message.command) < 2:
        await message.edit("Mohon berikan pertanyaan setelah perintah `.OpenAI`. Contoh: `.OpenAI Apa itu machine learning?`")
        return

    prompt = " ".join(message.command[1:])
    initial_message = await message.edit("Sedang memproses permintaan ke OpenAI")

    response = get_hf_response_v1_chat(prompt, HF_MODEL_DEFAULT)

    # Potong atau split respons kalo terlalu panjang
    message_parts = split_message(response)

    # Kirim pesan pertama
    # Hapus pesan "Sedang memproses..."
    for i, part in enumerate(message_parts):
        # Kirim setiap part tanpa label part
        await message.reply(f"**[OpenAI]**\n{part}")

# --- Handler Command Hugging Face - DeepSeek ---
@app.on_message(filters.command("deepseek", prefixes=".") & filters.me)
async def ai_deepseek_command(client: Client, message: Message):
    """
    Handles the '.ai_deepseek' command to query a DeepSeek model via Hugging Face router.
    Splits response if it's too long.
    """
    if len(message.command) < 2:
        await message.edit("Mohon berikan pertanyaan setelah perintah `.deepseek`. Contoh: `.deepseek Jelaskan konsep AI.`")
        return

    prompt = " ".join(message.command[1:])
    initial_message = await message.edit("Sedang memproses permintaan ke DeepSeek")

    response = get_hf_response_v1_chat(prompt, HF_MODEL_DEEPSEEK)

    # Potong atau split respons kalo terlalu panjang
    message_parts = split_message(response)

    # Kirim pesan pertama
    # Hapus pesan "Sedang memproses..."
    for i, part in enumerate(message_parts):
        # Kirim setiap part tanpa label part
        await message.reply(f"**[DeepSeek]**\n{part}")

# --- Handler Command: Cek Model Hugging Face Default (OSS) Saat Ini ---
@app.on_message(filters.command("get_hf_model", prefixes=".") & filters.me)
async def get_hf_model_command(client: Client, message: Message):
    current_model = os.getenv("HF_MODEL_DEFAULT", "openai/gpt-oss-120b:fastest")
    await message.edit(f"Model OSS HF saat ini: `{current_model}`")

# --- Handler Command: Ganti Model Hugging Face DeepSeek ---
@app.on_message(filters.command("set_hf_model_deepseek", prefixes=".") & filters.me)
async def set_hf_model_deepseek_command(client: Client, message: Message):
    """
    Changes the default Hugging Face model used by the '/ai_deepseek' command.
    This change only persists for the current session.
    """
    if len(message.command) < 2:
        current_model = os.getenv("HF_MODEL_DEEPSEEK", "deepseek-ai/DeepSeek-R1:fastest")
        await message.edit(f"Model DeepSeek HF saat ini: `{current_model}`\nContoh penggunaan: `.set_hf_model_deepseek deepseek-ai/DeepSeek-V2:fastest`")
        return

    new_model = message.command[1]
    os.environ["HF_MODEL_DEEPSEEK"] = new_model # Update environment variable di runtime (hanya untuk sesi ini)
    await message.edit(f"Model DeepSeek HF berhasil diubah menjadi: `{new_model}`")

# --- Handler Command: Cek Model Hugging Face DeepSeek Saat Ini ---
@app.on_message(filters.command("get_hf_model_deepseek", prefixes=".") & filters.me)
async def get_hf_model_deepseek_command(client: Client, message: Message):
    current_model = os.getenv("HF_MODEL_DEEPSEEK", "deepseek-ai/DeepSeek-R1:fastest")
    await message.edit(f"Model DeepSeek HF saat ini: `{current_model}`")

        
# -------- Gemini AI config (GLOBAL) ----------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")  # set this if you want AI
GEMINI_MODELS = {
    "flash": "gemini-2.5-flash",
    "pro": "gemini-2.5-pro",
    "lite": "gemini-2.0-flash-lite-001",
}

# ---------- Google Custom Search helper ----------
def google_search(query: str, num: int = 3) -> list:
    """
    Simple Google Custom Search REST wrapper.
    Returns list of dicts: [{ 'title': ..., 'snippet': ..., 'link': ... }, ...]
    If API key or CSE id not configured, returns empty list.
    """
    if not GOOGLE_SEARCH_API_KEY or not GOOGLE_CSE_ID:
        return []

    try:
        params = {
            "key": GOOGLE_SEARCH_API_KEY,
            "cx": GOOGLE_CSE_ID,
            "q": query,
            "num": min(max(1, num), 10),
        }
        resp = requests.get("https://www.googleapis.com/customsearch/v1", params=params, timeout=10)
        if resp.status_code != 200:
            log.warning("Google search failed: %s %s", resp.status_code, resp.text[:200])
            return []
        data = resp.json()
        items = data.get("items") or []
        out = []
        for it in items[:num]:
            out.append({
                "title": it.get("title", "")[:200],
                "snippet": it.get("snippet", "")[:400],
                "link": it.get("link", "")
            })
        return out
    except Exception:
        log.exception("google_search error")
        return []

def _format_search_results_for_prompt(results: list) -> str:
    """
    Format results into a compact grounding block to prepend to the prompt.
    """
    if not results:
        return ""
    lines = ["Search results (from Google CSE):"]
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        snip = r.get("snippet", "")
        link = r.get("link", "")
        lines.append(f"[{i}] {title}\n{snip}\n{link}")
    lines.append("")  # blank line
    return "\n\n".join(lines)

def ask_ai_gemini(prompt: str, model: str = "gemini-2.5-flash") -> (bool, str):
    """
    Synchronous call to Gemini REST (simple). Returns (ok, text_or_error).
    If GOOGLE_SEARCH_API_KEY + GOOGLE_CSE_ID are set, the function will query Google CSE
    and prepend top results as grounding before sending to Gemini.
    """
    if not GEMINI_API_KEY:
        return False, "API key Gemini belum diset. Tambahkan GEMINI_API_KEY di .env"

    if not prompt:
        return False, "Tidak ada pertanyaan."

    final_prompt = prompt

    # If Google CSE configured -> fetch results and prepend as context
    try:
        if GOOGLE_SEARCH_API_KEY and GOOGLE_CSE_ID:
            # use the original prompt as search query (could be improved later)
            results = google_search(prompt, num=3)
            if results:
                grounding = _format_search_results_for_prompt(results)
                # prepend grounding instructions
                final_prompt = (
                    "Gunakan hasil pencarian berikut sebagai sumber (jika relevan) untuk menjawab.\n\n"
                    f"{grounding}\n\nPertanyaan: {prompt}\n\nJawab singkat dan sertakan sumber jika perlu."
                )
    except Exception:
        log.exception("grounding step failed (ignored)")

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={GEMINI_API_KEY}"
    )
    payload = {"contents": [{"parts": [{"text": final_prompt}]}]}

    try:
        r = requests.post(url, headers={"Content-Type": "application/json"}, json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()

        candidates = data.get("candidates") or []
        if not candidates:
            return True, "Model merespon tapi tanpa jawaban."

        parts = candidates[0].get("content", {}).get("parts", [])
        if parts:
            return True, parts[0].get("text", "").strip()

        return True, json.dumps(candidates[0], ensure_ascii=False)

    except requests.exceptions.HTTPError:
        try:
            return False, f"Error HTTP: {r.status_code}\n{r.text}"
        except Exception:
            return False, "Gagal memanggil Gemini: HTTP error"
    except Exception as e:
        return False, f"Gagal memanggil Gemini: {e}"

# ---------- helper: safe ChatPermissions builder ----------
def _safe_chat_permissions(kwargs: dict) -> Optional[ChatPermissions]:
    """
    Build ChatPermissions only with supported keyword args for this Pyrogram.
    Usage: perms = _safe_chat_permissions({ "can_send_messages": False, ... })
    """
    try:
        sig = None
        allowed = set()
        try:
            sig = inspect.signature(ChatPermissions.__init__)
            allowed = set(sig.parameters.keys())
            allowed.discard("self")
        except Exception:
            allowed = {
                "can_send_messages",
                "can_send_media_messages",
                "can_send_polls",
                "can_send_other_messages",
                "can_add_web_page_previews",
                "can_change_info",
                "can_invite_users",
                "can_pin_messages",
            }
        filt = {k: v for k, v in kwargs.items() if k in allowed}
        return ChatPermissions(**filt)
    except Exception:
        try:
            return ChatPermissions(can_send_messages=kwargs.get("can_send_messages", True))
        except Exception:
            try:
                return ChatPermissions()
            except Exception:
                return None

# --- duration parsing: return datetime (UTC) for temporary, or None for permanent ---
def parse_duration_to_datetime(duration: Optional[str]) -> Optional[datetime]:
    """
    Parse duration strings like:
      '5m', '3h', '2d', '1w' -> return datetime (UTC) in future
      numeric seconds '300' -> return datetime now + seconds
      None/empty/invalid -> return None (meaning permanent)
    """
    if not duration:
        return None

    s = str(duration).strip().lower()
    now = datetime.now(timezone.utc)
    try:
        # pure digits -> seconds
        if s.isdigit():
            secs = int(s)
            return now + timedelta(seconds=secs)

        unit = s[-1]
        val_str = s[:-1]
        val = float(val_str) if val_str else 0.0

        if unit == "s":
            secs = int(val)
        elif unit == "m":
            secs = int(val * 60)
        elif unit == "h":
            secs = int(val * 3600)
        elif unit == "d":
            secs = int(val * 86400)
        elif unit == "w":
            secs = int(val * 7 * 86400)
        else:
            # fallback try minutes
            try:
                secs = int(float(s) * 60)
            except Exception:
                return None

        return now + timedelta(seconds=secs)
    except Exception:
        return None

# -------- helpers ----------
async def is_allowed(client: Client, user_id: int) -> bool:
    """Owner, akun userbot, dan sudo boleh pakai command."""
    try:
        me = await client.get_me()
        if user_id == OWNER_ID:
            return True
        if user_id == me.id:
            return True
        if user_id in sudo_users:
            return True
        return False
    except:
        return user_id == OWNER_ID or user_id in sudo_users

def _is_private_chat(chat) -> bool:
    """Robust private chat check across Pyrogram versions."""
    if chat is None:
        return False
    t = getattr(chat, "type", None)
    if t is None:
        return False
    try:
        s = str(t).lower()
        return "private" in s
    except Exception:
        return False

async def fetch_profile_photo_bytes(client: Client, entity) -> Optional[BytesIO]:
    """
    Try to get profile photo bytes for a user/chat. Returns BytesIO or None.
    Works with numeric id, username, or chat object.
    """
    try:
        # try get_chat -> .photo
        try:
            chat = await client.get_chat(entity)
        except Exception:
            chat = None

        if chat and getattr(chat, "photo", None):
            try:
                bio = await client.download_media(chat.photo.big_file_id, in_memory=True)
                if bio:
                    bio.seek(0)
                    return bio
            except Exception:
                pass

        # fallback: try get_user_profile_photos (user)
        if hasattr(client, "get_user_profile_photos"):
            try:
                phs = await client.get_user_profile_photos(entity, limit=1)
                if phs and getattr(phs, "total_count", 0) > 0:
                    fid = phs.photos[0][-1].file_id
                    bio = await client.download_media(fid, in_memory=True)
                    if bio:
                        bio.seek(0)
                        return bio
            except Exception:
                pass

    except Exception:
        log.exception("fetch_profile_photo_bytes error")
    return None

# ============================
#  QUOTLY FULL BLOCK (listener + handler)
#  Paste langsung, lalu import/letakkan di userbot.py
# ============================

# --- adjust this if QuotLy username different (without @) ---
BOT_USERNAME = "QuotLyBot"

# tuning
POLL_TIMEOUT = 35.0     # max wait for responses (seconds)
POLL_INTERVAL = 0.7     # polling interval (seconds)
BOT_CACHE_SIZE = 60     # keep last N messages from QuotLyBot
MAX_QUOTE_COUNT = 20    # safety upper limit

# local cache (newest first)
_last_from_quotly: Deque[Message] = deque(maxlen=BOT_CACHE_SIZE)

# Listener: cache everything from the bot (works for DM and group if bot forwarded)
@app.on_message(filters.chat(BOT_USERNAME))
async def _quotly_cache_listener(client, message: Message):
    try:
        # newest at left
        _last_from_quotly.appendleft(message)
    except Exception:
        # must never crash the bot
        return

def _find_cached_after(ts: float, only_types: Optional[List[str]] = None) -> List[Message]:
    """
    Return list of cached messages from QuotLyBot whose timestamp >= ts.
    newest-first order; caller will reorder if needed.
    only_types is optional list like ["sticker","photo"] to filter.
    """
    out = []
    for msg in _last_from_quotly:
        try:
            m_ts = msg.date.timestamp()
            if m_ts >= ts:
                if only_types:
                    ok = False
                    if "sticker" in only_types and getattr(msg, "sticker", None):
                        ok = True
                    if "photo" in only_types and getattr(msg, "photo", None):
                        ok = True
                    if "document" in only_types and getattr(msg, "document", None):
                        ok = True
                    if not ok:
                        continue
                out.append(msg)
        except Exception:
            continue
    return out  # newest-first

# Build a dynamic allowed filter: owner + sudo_users (note: list is evaluated at import)
try:
    _sudo_list = list(sudo_users) if "sudo_users" in globals() else []
except Exception:
    _sudo_list = []
_ALLOWED_FILTER = filters.user([OWNER_ID] + _sudo_list)

# Main handler (owner, sudo, or self)
@app.on_message((filters.me | _ALLOWED_FILTER) & filters.command(["q","quotly"], prefixes="."))
async def quotly_handler(client, m: Message):
    reply = m.reply_to_message
    if not reply:
        return await m.reply_text("⚠️ Reply ke pesan yang mau di-quote dulu ya.")

    # parse args (robust)
    parts = (m.text or "").split()
    count = 1
    color = " "
    if len(parts) >= 2:
        # if first arg is digit => count
        if parts[1].isdigit():
            count = int(parts[1])
            if len(parts) >= 3:
                color = " ".join(parts[2:]).strip()
        else:
            # first arg is color or something
            color = " ".join(parts[1:]).strip()

    # clamp count
    count = max(1, min(count, MAX_QUOTE_COUNT))

    status = await m.edit_text(f"✨ Membuat quote ")

    # collect the messages to forward: try sequential ids from the replied message
    msgs_to_forward = []
    try:
        base_id = getattr(reply, "message_id", getattr(reply, "id", None))
        if base_id is None:
            base_id = reply.id if hasattr(reply, "id") else None
        # build ids; Telegram message ids in a chat are usually sequential integers
        ids = [base_id + i for i in range(count)]
        fetched = await client.get_messages(m.chat.id, ids)
        # get_messages may return Message or list
        if not isinstance(fetched, list):
            fetched = [fetched]
        msgs_to_forward = [x for x in fetched if x is not None]
    except Exception:
        msgs_to_forward = []

    # fallback: if we couldn't fetch sequence, use the reply and try to fetch next messages via iter_history
    if not msgs_to_forward or len(msgs_to_forward) < count:
        try:
            msgs_to_forward = []
            # include the replied message first
            msgs_to_forward.append(reply)
            async for hx in client.get_chat_history(m.chat.id, offset_id=reply.message_id, limit=count-1):
                # get_chat_history yields messages *before* offset_id, so we need messages with id > reply? 
                # to keep it simple: gather next messages using message_id+1 .. by trying get_messages per id
                break
        except Exception:
            # ignore, we'll try forward at least the reply
            if not msgs_to_forward:
                msgs_to_forward = [reply]

    # ensure at least the replied message exists
    if not msgs_to_forward:
        msgs_to_forward = [reply]

    # final trimming to requested count
    if len(msgs_to_forward) > count:
        msgs_to_forward = msgs_to_forward[:count]

    # prepare timestamp start for cache comparison
    ts_start = time.time()

    # send color command (best-effort) and forward messages
    try:
        # set color
        try:
            await client.send_message(BOT_USERNAME, f"/q {color}")
        except Exception:
            # ignore color errors
            pass

        # forward each message with small delay to avoid flooding
        for idx, fmsg in enumerate(msgs_to_forward):
            await fmsg.forward(BOT_USERNAME)
            # tiny spacing
            await asyncio.sleep(0.18)
    except Exception as e:
        return await status.edit_text(f"❌ Gagal kirim ke @{BOT_USERNAME}: `{e}`")

    # now wait for responses from QuotLyBot in cache
    deadline = time.time() + POLL_TIMEOUT
    collected: List[Message] = []
    seen_ids = set()

    while time.time() < deadline and len(collected) < len(msgs_to_forward):
        await asyncio.sleep(POLL_INTERVAL)
        try:
            # find cached messages after ts_start
            cand_list = _find_cached_after(ts_start, only_types=["sticker", "photo", "document", "text"])
            # cand_list newest-first; we want oldest-first relevant to preserve order of forwarded messages
            if cand_list:
                # iterate reversed to get chronological order
                for cand in reversed(cand_list):
                    if getattr(cand, "id", None) in seen_ids:
                        continue
                    # only accept media/text types likely produced by QuotLyBot
                    if getattr(cand, "sticker", None) or getattr(cand, "photo", None) or getattr(cand, "document", None) or getattr(cand, "text", None):
                        collected.append(cand)
                        seen_ids.add(getattr(cand, "id", None))
                        if len(collected) >= len(msgs_to_forward):
                            break
        except Exception:
            continue

    if not collected:
        return await status.edit_text("❌ Timeout — gak nemu output dari @QuotLyBot. Coba lagi nanti.")

    # if more collected than needed, keep last N that correspond to forwarded order
    if len(collected) > len(msgs_to_forward):
        collected = collected[-len(msgs_to_forward):]

    # send results to chat in same order (collected is chronological)
    try:
        for res in collected:
            # copy_message supports sticker/photo/document/text
            await client.copy_message(chat_id=m.chat.id, from_chat_id=BOT_USERNAME, message_id=res.id)
            # small gap so messages don't collapse
            await asyncio.sleep(0.12)
        try:
            await status.delete()
        except:
            pass
    except Exception as e:
        await status.edit_text(f"❌ Gagal kirim hasil ke chat: `{e}`")

# -----sudo cmd------
@app.on_message((filters.me | filters.user(OWNER_ID)) & filters.command("addsudo", prefixes="."))
async def cmd_addsudo(client, message: Message):
    caller = message.from_user
    if caller.id != OWNER_ID:
        return await message.edit_text("Hanya OWNER yang boleh nambah sudo.")

    if len(message.command) < 2 and not message.reply_to_message:
        return await message.edit_text("Pakai: `.addsudo id/@username` atau reply ke user.")

    # resolve target
    target = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user.id
    else:
        arg = message.command[1].strip()
        if arg.startswith("@"):
            arg = arg[1:]
        try:
            if arg.isdigit():
                target = int(arg)
            else:
                ent = await client.get_chat(arg)
                target = ent.id
        except:
            return await message.edit_text("Gagal resolve user.")

    if target == OWNER_ID:
        return await message.edit_text("Owner otomatis punya akses penuh.")

    sudo_users.add(target)
    _save_sudo(sudo_users)
    await message.edit_text(f"✔️ User `{target}` ditambahkan ke sudo.")
    
@app.on_message((filters.me | filters.user(OWNER_ID)) & filters.command("rmsudo", prefixes="."))
async def cmd_delsudo(client, message: Message):
    caller = message.from_user
    if caller.id != OWNER_ID:
        return await message.edit_text("Hanya OWNER yang boleh hapus sudo.")

    if len(message.command) < 2 and not message.reply_to_message:
        return await message.edit_text("Pakai: `.delsudo id/@username` atau reply.")

    target = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user.id
    else:
        arg = message.command[1].strip()
        if arg.startswith("@"):
            arg = arg[1:]
        try:
            if arg.isdigit():
                target = int(arg)
            else:
                ent = await client.get_chat(arg)
                target = ent.id
        except:
            return await message.edit_text("Gagal resolve user.")

    if target in sudo_users:
        sudo_users.discard(target)
        _save_sudo(sudo_users)
        return await message.edit_text(f"❌ User `{target}` dihapus dari sudo.")
    else:
        return await message.edit_text("User itu bukan sudo.")

@app.on_message((filters.me | filters.user(OWNER_ID)) & filters.command("sudolist", prefixes="."))
async def cmd_sudos(client, message: Message):
    if not await is_allowed(client, message.from_user.id):
        return

    if not sudo_users:
        return await message.edit_text("Belum ada sudo user.")
    
    lines = ["👑 Sudo Users:"]
    for u in sorted(sudo_users):
        lines.append(str(u))
    await message.edit_text("\n".join(lines))
            
# Robust helper v2: try high-level API; fallback raw with mapping to common raw.ChatAdminRights fields.
async def set_admin_custom_title(client: Client, chat_id: int, user_id: int, title: str):
    """
    Safe attempt to set admin custom title.
    Returns (ok: bool, error_msg: str).
    Behavior:
      - Try client.set_administrator_custom_title(...) if available.
      - Else read current member/admin rights, map them to raw.ChatAdminRights fields,
        and call channels.EditAdmin(...) with the same rights + rank=title.
      - If we cannot detect rights or cannot map any keys, we return error without doing risky fallback.
    """
    if not title:
        return True, ""

    # 1) try high-level API (if available)
    try:
        if hasattr(client, "set_administrator_custom_title"):
            await client.set_administrator_custom_title(chat_id=chat_id, user_id=user_id, custom_title=title)
            return True, ""
        # some pyrogram versions might expose a different name (rare) — attempt generic call if present
        alt_names = ["set_admin_custom_title", "set_chat_administrator_custom_title"]
        for n in alt_names:
            if hasattr(client, n):
                fn = getattr(client, n)
                await fn(chat_id, user_id, title)
                return True, ""
    except Exception as e_high:
        high_err = str(e_high)
    else:
        high_err = "no high-level method"

    # 2) fallback: fetch current chat member info to reuse rights
    try:
        cm = await client.get_chat_member(chat_id, user_id)
    except Exception as e:
        return False, f"high:{high_err} | can't fetch member: {e}"

    # collect boolean rights from common places on the chat member object
    extracted = {}
    # common attribute paths to probe
    probes = [
        cm,  # top-level attributes
        getattr(cm, "privileges", None),
        getattr(cm, "rights", None),
        getattr(cm, "admin_rights", None),
    ]
    for obj in probes:
        if not obj:
            continue
        # try typical __dict__ extraction
        try:
            for attr, val in getattr(obj, "__dict__", {}).items():
                if isinstance(val, bool):
                    extracted[attr] = val
        except Exception:
            pass
        # also try getattr for common names not in __dict__
        for cand in [
            "can_change_info","can_post_messages","can_edit_messages","can_delete_messages",
            "can_invite_users","can_restrict_members","can_pin_messages","can_promote_members",
            "is_anonymous","manage_topics","manage_video_chats","manage_calls","manage_voice_chats",
            "change_info","post_messages","edit_messages","delete_messages","invite_users",
            "restrict_members","pin_messages","promote_members","anonymous"
        ]:
            if cand in extracted:
                continue
            try:
                v = getattr(obj, cand)
                if isinstance(v, bool):
                    extracted[cand] = v
            except Exception:
                pass

    # mapping from extracted names to likely raw.ChatAdminRights field names
    name_map = {
        "can_change_info": "change_info",
        "change_info": "change_info",
        "can_post_messages": "post_messages",
        "post_messages": "post_messages",
        "can_edit_messages": "edit_messages",
        "edit_messages": "edit_messages",
        "can_delete_messages": "delete_messages",
        "delete_messages": "delete_messages",
        "can_invite_users": "invite_users",
        "invite_users": "invite_users",
        "can_restrict_members": "ban_users",       # raw uses ban_users commonly
        "restrict_members": "ban_users",
        "ban_users": "ban_users",
        "can_pin_messages": "pin_messages",
        "pin_messages": "pin_messages",
        "can_promote_members": "add_admins",       # sometimes 'add_admins' or 'promote_members'
        "promote_members": "add_admins",
        "add_admins": "add_admins",
        "is_anonymous": "anonymous",
        "anonymous": "anonymous",
        "manage_video_chats": "manage_video_chats",
        "manage_voice_chats": "manage_video_chats",
    }

    # build candidate rights dict (raw field name -> bool)
    candidate_rights = {}
    for k, v in extracted.items():
        mapped = name_map.get(k)
        if mapped:
            candidate_rights[mapped] = v

    # if nothing mapped, abort safe fallback
    if not candidate_rights:
        return False, f"high:{high_err} | detected rights keys not compatible with raw.ChatAdminRights (abort)"

    # now construct raw.ChatAdminRights with only allowed params
    try:
        from pyrogram.raw import functions as raw_f, types as raw_t
        import inspect as _inspect

        sig = _inspect.signature(raw_t.ChatAdminRights.__init__)
        allowed = set(sig.parameters.keys()) - {"self"}
        # filter candidate_rights to allowed keys
        filtered = {k: bool(v) for k, v in candidate_rights.items() if k in allowed}

        if not filtered:
            return False, f"high:{high_err} | detected rights keys not compatible with raw.ChatAdminRights (after filter) (abort)"

        rights = raw_t.ChatAdminRights(**filtered)

        # resolve peers (works for channels/groups)
        peer = await client.resolve_peer(chat_id)
        user_peer = await client.resolve_peer(user_id)

        # Perform EditAdmin with same admin rights + new title
        await client.invoke(
            raw_f.channels.EditAdmin(
                channel=peer,
                user_id=user_peer,
                admin_rights=rights,
                rank=title or ""
            )
        )
        return True, ""
    except Exception as e_raw:
        return False, f"high:{high_err} | raw:{e_raw}"

# -------------------------
# PROMOTE handler (kept as requested)
# -------------------------
@app.on_message((filters.me | ALLOW_FILTER) & filters.command("promote", prefixes="."))
async def cmd_promote(client: Client, message: Message):
    caller = message.from_user
    if not caller or not await is_allowed(client, caller.id):
        return await message.edit_text("Kamu tidak punya izin.")

    if not message.chat:
        return await message.edit_text("Gunakan di grup.")

    # resolve target + title
    target_id = None
    title = None

    # reply -> target
    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id

    parts = (message.text or "").split()
    args = parts[1:] if len(parts) > 1 else []

    if args:
        maybe = args[0]
        if maybe.startswith("@"):
            maybe = maybe[1:]

        if not target_id:
            try:
                if maybe.isdigit():
                    target_id = int(maybe)
                    args = args[1:]
                else:
                    ent = await client.get_chat(maybe)
                    target_id = getattr(ent, "id", None)
                    args = args[1:]
            except Exception:
                if target_id is None:
                    return await message.edit_text("Gagal resolve target. Reply dulu atau `.promote id/username [title]`")

        if args:
            title = " ".join(args).strip()

    if not target_id:
        return await message.edit_text("Reply ke user atau `.promote id/username [title]`")

    # display format
    try:
        ent = await client.get_users(target_id)
        uname = getattr(ent, "username", None)
    except Exception:
        uname = None

    display = f"{target_id}/{uname}" if uname else str(target_id)

    # check status
    try:
        cm = await client.get_chat_member(message.chat.id, target_id)
        status = getattr(cm, "status", "").lower()
    except Exception:
        status = ""

    # already admin
    if status in ("administrator", "creator"):
        if title:
            ok, err = await set_admin_custom_title(client, message.chat.id, target_id, title)
            if ok:
                return await message.edit_text(f"✅ User {display} sudah admin — title diupdate: {title}")
            else:
                short = str(err)[:400]
                return await message.edit_text(f"✅ User {display} sudah admin — tapi gagal update title.\n⚠️ {short}")
        return await message.edit_text(f"✅ User {display} sudah admin/creator.")

    # ==========================
    # FULL ADMIN RIGHTS ATTEMPT (as you wanted to keep)
    # ==========================
    full_rights = dict(
        can_change_info=True,
        can_delete_messages=True,
        can_invite_users=True,
        can_restrict_members=True,
        can_pin_messages=True,
        can_promote_members=True,
    )

    # promote full rights
    try:
        # try full promote first
        try:
            await client.promote_chat_member(
                chat_id=message.chat.id,
                user_id=target_id,
                **{k: v for k,v in full_rights.items() if v is not None}
            )
        except TypeError:
            # fallback minimal promote
            await client.promote_chat_member(chat_id=message.chat.id, user_id=target_id)
    except Exception as e:
        log.exception("promote failed")
        return await message.edit_text(f"❌ Gagal promote {display}: {e}")

    # set title jika ada
    if title:
        ok, err = await set_admin_custom_title(client, message.chat.id, target_id, title)
        if ok:
            return await message.edit_text(f"✔️ User {display} dipromote dan title: {title}")
        else:
            short = str(err)[:400]
            return await message.edit_text(f"✔️ User {display} dipromote. (Gagal set title)\n⚠️ {short}")
    else:
        return await message.edit_text(f"✔️ User {display} berhasil dipromote (title default).")

# ---------- DEMOTE (kept, but not modified heavily) ----------
@app.on_message((filters.me | ALLOW_FILTER) & filters.command("demote", prefixes="."))
async def cmd_demote(client: Client, message: Message):
    caller = message.from_user
    if not caller or not await is_allowed(client, caller.id):
        return await message.edit_text("Kamu tidak punya izin.")

    if not message.chat:
        return await message.edit_text("Gunakan di grup.")

    # resolve target id
    target_id = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
    else:
        parts = message.text.split()
        if len(parts) > 1:
            arg = parts[1].strip()
            if arg.startswith("@"):
                arg = arg[1:]
            try:
                if arg.isdigit():
                    target_id = int(arg)
                else:
                    ent = await client.get_chat(arg)
                    target_id = getattr(ent, "id", None)
            except Exception:
                target_id = None

    if not target_id:
        return await message.edit_text("Reply ke pesan user atau `.demote id/username`")

    # try to get chat member info (robust)
    try:
        cm = await client.get_chat_member(message.chat.id, target_id)
    except Exception as e:
        log.exception("get_chat_member failed")
        return await message.edit_text("Gagal ambil info member (cek izin bot).")

    # normalize status
    status = ""
    try:
        st = getattr(cm, "status", None)
        if st is None:
            status = str(cm).lower()
        else:
            status = str(st).lower()
    except Exception:
        status = ""

    if "administrator" not in status and "creator" not in status:
        return await message.edit_text(f"ℹ️ User `{target_id}` bukan admin — gak perlu di-demote.")

    # try simple promote_chat_member with no rights (demote by removing admin)
    try:
        await client.promote_chat_member(chat_id=message.chat.id, user_id=target_id,
                                        can_change_info=False,
                                        can_delete_messages=False,
                                        can_invite_users=False,
                                        can_restrict_members=False,
                                        can_pin_messages=False,
                                        can_promote_members=False)
        # try clear custom title quietly
        try:
            if hasattr(client, "set_administrator_custom_title"):
                await client.set_administrator_custom_title(chat_id=message.chat.id, user_id=target_id, custom_title="")
        except Exception:
            pass
        return await message.edit_text(f"✅ User `{target_id}` berhasil di-demote.")
    except Exception as e_prom:
        log.debug("promote_chat_member demote attempt failed: %s", e_prom)

    # fallback raw EditAdmin attempt (best-effort)
    try:
        sig = inspect.signature(raw_t.ChatAdminRights.__init__)
        allowed = set(sig.parameters.keys()) - {"self"}
    except Exception:
        allowed = {
            "change_info",
            "post_messages",
            "edit_messages",
            "delete_messages",
            "ban_users",
            "invite_users",
            "pin_messages",
            "add_admins",
            "manage_calls",
            "manage_video_chats",
        }

    rights_kwargs = {k: False for k in allowed}
    try:
        rights = raw_t.ChatAdminRights(**rights_kwargs)
    except Exception:
        # filter keys if needed
        filtered = {k: False for k in rights_kwargs.keys()}
        try:
            rights = raw_t.ChatAdminRights(**filtered)
        except Exception:
            rights = None

    if rights is not None:
        try:
            peer_chat = await client.resolve_peer(message.chat.id)
            peer_user = await client.resolve_peer(target_id)
            await client.invoke(raw_f.channels.EditAdmin(channel=peer_chat, user_id=peer_user, admin_rights=rights, rank=""))
            # clear custom title
            try:
                if hasattr(client, "set_administrator_custom_title"):
                    await client.set_administrator_custom_title(chat_id=message.chat.id, user_id=target_id, custom_title="")
            except Exception:
                pass
            return await message.edit_text(f"✅ User `{target_id}` berhasil di-demote.")
        except Exception as e_raw:
            log.exception("raw EditAdmin failed")
            return await message.edit_text(f"❌ Gagal demote `{target_id}` — alasan: {e_raw}")

    return await message.edit_text("❌ Gagal demote — alasan tidak diketahui.")

# ---------- DEBUG: cek hak dan status admin ----------
@app.on_message((filters.me | filters.user(OWNER_ID)) & filters.command("admincheck", prefixes="."))
async def cmd_admincheck(client: Client, message: Message):
    """
    Debug helper: tampilkan status bot di chat, status target (reply/id/username), dan beberapa flag penting.
    Usage:
      - reply ke user lalu: .admincheck
      - atau: .admincheck 123456789 / .admincheck username
    """
    caller = message.from_user
    if not caller or not await is_allowed(client, caller.id):
        return await message.edit_text("Kamu tidak punya izin.")

    if not message.chat:
        return await message.edit_text("Gunakan di grup.")

    # resolve target
    target_id = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
    else:
        parts = message.text.split()
        if len(parts) > 1:
            a = parts[1].strip()
            if a.startswith("@"):
                a = a[1:]
            try:
                if a.isdigit():
                    target_id = int(a)
                else:
                    ent = await client.get_chat(a)
                    target_id = getattr(ent, "id", None)
            except Exception as e:
                return await message.edit_text(f"Gagal resolve target: {e}")

    if not target_id:
        return await message.edit_text("Reply ke user atau `.admincheck id/username`")

    me = await client.get_me()
    out_lines = []
    out_lines.append(f"🔎 Bot id: {me.id}")

    # bot chat member
    try:
        my_cm = await client.get_chat_member(message.chat.id, me.id)
        out_lines.append(f"Bot status in chat: {getattr(my_cm, 'status', None)}")
        # print some known flags if exist
        for flag in ("is_member", "can_promote_members", "can_change_info", "can_delete_messages", "can_restrict_members", "can_pin_messages"):
            val = getattr(my_cm, flag, None)
            out_lines.append(f"my.{flag}: {val}")
    except Exception as e:
        out_lines.append(f"Failed get my chat member: {e}")

    # target chat member
    try:
        cm = await client.get_chat_member(message.chat.id, target_id)
        out_lines.append(f"Target id: {target_id}")
        out_lines.append(f"status: {getattr(cm, 'status', None)}")
        # try to show a few attributes
        user = getattr(cm, "user", None)
        if user:
            out_lines.append(f"user.id: {getattr(user, 'id', None)}")
            out_lines.append(f"user.username: {getattr(user, 'username', None)}")
            out_lines.append(f"user.is_contact: {getattr(user, 'is_contact', None)}")
        # promoted_by / joined_date / custom_title if present
        out_lines.append(f"promoted_by: {getattr(cm, 'promoted_by', None)}")
        out_lines.append(f"joined_date: {getattr(cm, 'joined_date', None)}")
        out_lines.append(f"custom_title: {getattr(cm, 'custom_title', None)}")
        for flag in ("can_promote_members", "can_change_info", "can_delete_messages", "can_restrict_members", "can_pin_messages"):
            out_lines.append(f"target.{flag}: {getattr(cm, flag, None)}")
    except Exception as e:
        out_lines.append(f"Failed get target chat member: {e}")

    await message.edit_text("```\n" + "\n".join(str(x) for x in out_lines) + "\n```")

# ------adduser-----
@app.on_message((filters.me | ALLOW_FILTER) & filters.command("add", prefixes="."))
async def cmd_add(client: Client, message: Message):
    """
    .add <username|id> — invite user into the group.
    Bisa juga reply pesan lalu ketik .add
    Behavior:
      - coba add_chat_members()
      - kalau gagal karena USER_NOT_MUTUAL_CONTACT atau sejenis, buat invite link
      - kirim invite link ke grup dan coba DM langsung ke user target
    """
    caller = message.from_user
    if not caller or not await is_allowed(client, caller.id):
        return await message.edit_text("Kamu tidak punya izin.")

    if not message.chat:
        return await message.edit_text("Gunakan di grup.")

    # resolve target
    target_id = None
    target_username = None

    # reply
    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
        target_username = getattr(message.reply_to_message.from_user, "username", None)

    # arg
    if not target_id and len(message.command) > 1:
        arg = message.command[1].strip()
        if arg.startswith("@"):
            arg = arg[1:]
        # try numeric id
        if arg.isdigit():
            target_id = int(arg)
        else:
            # try resolve username -> get_chat returns Chat/User object
            try:
                ent = await client.get_chat(arg)
                target_id = getattr(ent, "id", None)
                target_username = getattr(ent, "username", None)
            except Exception:
                target_id = None

    if not target_id:
        return await message.edit_text("Format: `.add @username` atau reply `.add`")

    # try to add directly
    try:
        await client.add_chat_members(message.chat.id, target_id)
        return await message.edit_text(f"👥 User `{target_id}` berhasil ditambahkan ke grup.")
    except Exception as e:
        err = str(e)
        log.debug("add_chat_members failed: %s", err)

        # create invite link (try modern API first, fallback to export)
        invite = None
        try:
            try:
                link_obj = await client.create_chat_invite_link(message.chat.id)
                invite = getattr(link_obj, "invite_link", None) or getattr(link_obj, "link", None)
            except Exception:
                try:
                    invite = await client.export_chat_invite_link(message.chat.id)
                except Exception:
                    invite = None
        except Exception:
            invite = None

        # If we couldn't create invite link -> inform group
        if not invite:
            log.exception("Failed to generate invite link")
            # helpful message depending on cause
            if "USER_NOT_MUTUAL_CONTACT" in err or "not a mutual contact" in err.lower():
                return await message.edit_text(
                    "⚠️ Gagal nambah langsung (bukan mutual contact) dan gagal bikin invite link.\n"
                    "Pastikan akun kamu punya izin admin untuk membuat invite link."
                )
            else:
                return await message.edit_text(f"❌ Gagal add user: {e}")

        # we have an invite link — try DM target user, else post link in chat
        dm_sent = False
        dm_error = None
        try:
            try:
                await client.send_message(target_id, (
                    f"👋 Hai! Kamu diundang ke grup: {message.chat.title or message.chat.id}\n\n"
                    f"Klik link ini untuk gabung:\n{invite}\n\n"
                ))
                dm_sent = True
            except Exception as de:
                dm_error = str(de)
                log.debug("send_message to user failed: %s", dm_error)
                dm_sent = False
        except Exception:
            dm_sent = False

        # Inform group about next steps
        if dm_sent:
            await message.edit_text(
                f"⚠️ Gagal nambah langsung (bukan mutual contact). Invite link sudah dikirim via DM ke `{target_id}`."
            )
        else:
            # fallback: post invite link publicly in the group and mention reason
            info = (
                f"⚠️ Gagal nambah langsung (bukan mutual contact).\n"
                f"Invite link: {invite}\n\n"
                "Aku juga coba DM user tapi gagal (kemungkinan privacy settings)."
            )
            await message.edit_text(info)

        return

# -------- commands: menu / ping / alive / restart / info / approve etc. --------

@app.on_message(filters.command("menu", prefixes="."))
async def cmd_menu(client, message: Message):
    """Userbot menu — owner + userbot + sudo only."""
    caller = message.from_user
    if not caller:
        return

    # gunakan helper is_allowed() biar OWNER, akun userbot, dan sudo_users di-acknowledge
    if not await is_allowed(client, caller.id):
        return

    menu = (
    "🌸 **Userbot Menu** 🌸\n\n"

    "💼 **General**\n"
    "• ✨ .ping — check latency\n"
    "• 💗 .alive — check userbot status\n"
    "• 🧾 .info — user / chat info\n"
    "• 📖 .menu — open this menu\n"
    "• 💤 .afk — enable AFK\n\n"

    "🛠️ **Utilities**\n"
    "• 🔤 .ascii — convert text to ASCII art\n"
    "• 🌀 .mock — mock text (aLtErNaTiNg cApS)\n"
    "• ▒  .spoiler — create spoiler text\n"
    "• 🕵️ .whois — advanced user info\n"
    "• ☁️ .weather — weather information\n\n"

        "🧠 **Artificial Intelligence**\n"
    "• 🌕 .ai — ask Gemini AI\n"
    "• 🦉 .openai — ask OpenAI\n"  # Ganti emote jadi 🦉 atau lainnya
    "• 🦈 .deepseek — ask DeepSeek AI\n"
    "• 🛠️ .set_hf_model [name] — set default OSS model\n"
    "• 🛠️ .set_hf_model_deepseek [name] — set DeepSeek model\n"
    "• 📋 .get_hf_model / .get_hf_model_deepseek — view current model\n"
    "• 🌐 .gsearch — Google search\n\n"

    "🛡️ **Moderation**\n"
    "• 🤫 .mute — mute user\n"
    "• 🔊 .unmute — unmute user\n"
    "• 🚫 .ban — ban user\n"
    "• ♻️ .unban — unban user\n"
    "• 👢 .kick — kick user\n\n"

    "👥 **User Management**\n"
    "• ➕ .add — add user to group\n"
    "• 📈 .promote — promote to admin\n"
    "• 📉 .demote — demote admin\n\n"

    "📫 **DM Control**\n"
    "• 💌 .approve — allow DM\n"
    "• ❌ .unapprove — revoke DM\n"
    "• 📃 .approved — list approved users\n"
    "• 🔒 .block — block user\n\n"

    "👑 **Sudo Commands**\n"
    "• 🧩 .addsudo — add sudo user\n"
    "• 🗑️ .rmsudo — remove sudo user\n"
    "• 📜 .sudolist — list sudo users\n\n"

    "📌 **Messages**\n"
    "• 📌 .pin — pin message\n"
    "• 📎 .unpin — unpin message\n"
    "• 🧹 .purge — delete messages\n"
    "• 🗑️ .del — delete replied message\n\n"

    "🎨 **Stickers**\n"
    "• 🖼️ .kang — create / add to sticker pack\n"
    "• ✨ .q / .quotly — make quote sticker\n\n"

    "🔍 **QR & Codes**\n"
    "• 🧾 .qr — generate QR code\n"
    "• 🔎 .readqr — read QR code from image\n"
    "• 🎀 .qrstyle — set default QR style\n\n"   # <── ADDED HERE

    "⚡ **Performance**\n"
    "• 🏁 .speedtest — run speedtest\n"
    "• 🚀 .speedtest adv — advanced speedtest\n\n"

    "📊 **Group / Stats**\n"
    "• 📜 .admins — list admins\n"
    "• 📈 .stats — group statistics\n\n"

    "⚙️ **System**\n"
    "• 🔁 .restart — restart userbot\n\n"

    "💡 **Note**\n"
    "- Auto-reply active in DMs unless approved.\n"
    "- Spam >3x will be auto-blocked.\n"
)
    await message.edit_text(menu)

# -------- DM protection & autoreply (updated message) ----------
@app.on_message(filters.private & ~filters.me)
async def _dm_protect(client: Client, message: Message):
    """
    Auto-reply DM protector:
     - ignored if sender in approved_users
     - increments dm_spam_counter otherwise
     - blocks user automatically when > MAX_SPAM
     - sends a polite kawaii auto-reply
    """
    user = message.from_user
    if not user:
        return
    uid = user.id

    # If approved, ignore and reset counter
    if uid in approved_users:
        dm_spam_counter.pop(uid, None)
        return

    # increment counter
    dm_spam_counter[uid] = dm_spam_counter.get(uid, 0) + 1

    # if exceed limit -> block (safe guarded)
    if dm_spam_counter.get(uid, 0) > MAX_SPAM:
        try:
            await client.block_user(uid)
        except Exception:
            log.exception("Failed to block user")
        # best-effort notify (ignore if can't send)
        try:
            await message.reply_text("⛔ You have been blocked for repeated spam.")
        except Exception:
            pass

        # cleanup state
        dm_spam_counter.pop(uid, None)
        try:
            approved_users.discard(uid)
            _save_approved(approved_users)
        except Exception:
            log.exception("Failed to update approved list after block")

        return

    # firm but polite auto-reply (safe)
    try:
        await message.reply_text(
            "🌺 **Auto-Reply** 🌺\n"
            "The owner is currently offline. Please wait until they are back online.\n"
            "⚠️ Do not send repeated messages — the system will automatically block spam.\n"
        )
    except Exception:
        log.exception("Auto-reply failed")

# -------- admin commands: approve / block / unapprove / approved list ----------
@app.on_message((filters.me | filters.user(OWNER_ID)) & filters.command("approve", prefixes="."))
async def cmd_approve(client: Client, message: Message):
    caller = message.from_user
    if not caller or not await is_allowed(client, caller.id):
        return await message.edit_text("Kamu tidak punya izin.")

    target = None
    # 1) reply
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user.id

    # 2) arg
    if not target and len(message.command) > 1:
        arg = message.command[1].strip()
        if arg.startswith("@"):
            arg = arg[1:]
        if arg.isdigit():
            target = int(arg)
        else:
            try:
                ent = await client.get_chat(arg)
                target = getattr(ent, "id", None)
            except Exception:
                target = arg

    # 3) no reply & no arg & in private chat -> approve the DM user
    if not target:
        if message.chat and _is_private_chat(message.chat):
            target = message.chat.id
        else:
            return await message.edit_text("Gunakan di DM atau reply ke pesan user atau pakai `.approve id/@username`")

    approved_users.add(target)
    _save_approved(approved_users)
    if isinstance(target, int):
        dm_spam_counter.pop(target, None)

    await message.edit_text(f"✔️ Approved: `{target}` — auto-reply dimatikan untuk user ini.")

@app.on_message((filters.me | filters.user(OWNER_ID)) & filters.command("block", prefixes="."))
async def cmd_block(client: Client, message: Message):
    caller = message.from_user
    if not caller or not await is_allowed(client, caller.id):
        return await message.edit_text("Kamu tidak punya izin.")

    target = None
    # reply
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user.id

    # arg
    if not target and len(message.command) > 1:
        arg = message.command[1].strip()
        if arg.startswith("@"):
            arg = arg[1:]
        if arg.isdigit():
            target = int(arg)
        else:
            try:
                ent = await client.get_chat(arg)
                target = getattr(ent, "id", None)
            except Exception:
                target = arg

    # no reply & no arg -> if in private chat, block that chat user
    if not target:
        if message.chat and _is_private_chat(message.chat):
            target = message.chat.id
        else:
            return await message.edit_text("Reply pesan user atau pakai `.block id/@username` atau jalankan di DM untuk blok otomatis.")

    # resolve numeric
    numeric_target = target
    if isinstance(target, str):
        try:
            ent = await client.get_chat(target)
            numeric_target = getattr(ent, "id", target)
        except Exception:
            numeric_target = target

    try:
        if isinstance(numeric_target, str):
            return await message.edit_text(f"Gagal blok: tidak bisa resolve username `{target}` ke id.")
        await client.block_user(numeric_target)
        approved_users.discard(numeric_target)
        dm_spam_counter.pop(numeric_target, None)
        _save_approved(approved_users)
        await message.edit_text(f"⛔ User `{numeric_target}` diblokir.")
    except Exception:
        log.exception("block failed")
        await message.edit_text("Gagal blokir: terjadi kesalahan.")

@app.on_message((filters.me | filters.user(OWNER_ID)) & filters.command("unapprove", prefixes="."))
async def cmd_unapprove(client: Client, message: Message):
    caller = message.from_user
    if not caller or not await is_allowed(client, caller.id):
        return await message.edit_text("Kamu tidak punya izin.")

    target = None
    # reply
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user.id

    # arg
    if not target and len(message.command) > 1:
        arg = message.command[1].strip()
        if arg.startswith("@"):
            arg = arg[1:]
        if arg.isdigit():
            target = int(arg)
        else:
            try:
                ent = await client.get_chat(arg)
                target = getattr(ent, "id", None) or arg
            except Exception:
                target = arg

    # no reply & no arg -> if in private chat, unapprove that chat user
    if not target:
        if message.chat and _is_private_chat(message.chat):
            target = message.chat.id
        else:
            return await message.edit_text("Reply pesan user atau pakai `.unapprove id/@username` atau jalankan di DM.")

    numeric_target = target
    if isinstance(target, str) and not target.isdigit():
        try:
            ent = await client.get_chat(target)
            numeric_target = getattr(ent, "id", target)
        except Exception:
            numeric_target = target

    approved_users.discard(numeric_target)
    if isinstance(numeric_target, int):
        dm_spam_counter.pop(numeric_target, None)
    _save_approved(approved_users)
    await message.edit_text(f"❌ User `{numeric_target}` di-unapprove.")

@app.on_message((filters.me | filters.user(OWNER_ID)) & filters.command("approved", prefixes="."))
async def cmd_approved_list(client: Client, message: Message):
    caller = message.from_user
    if not caller or not await is_allowed(client, caller.id):
        return await message.edit_text("Kamu tidak punya izin.")
    if not approved_users:
        return await message.edit_text("Belum ada user yang di-approve.")
    lines = ["✅ Approved users:"]
    for x in sorted(approved_users, key=lambda z: str(z)):
        lines.append(str(x))
    await message.edit_text("\n".join(lines))

# -------- moderation commands: mute/unmute/ban/unban/kick ----------
@app.on_message((filters.me | ALLOW_FILTER) & filters.command("mute", prefixes="."))
async def cmd_mute(client: Client, message: Message):
    """
    .mute [dur]  -- reply to user or .mute @user 5m
    dur examples: 5m, 1h, 3d (optional; empty => permanent)
    """
    caller = message.from_user
    if not caller or not await is_allowed(client, caller.id):
        return await message.edit_text("Kamu tidak punya izin.")

    # must be in group
    if not message.chat or not getattr(message.chat, "id", None):
        return await message.edit_text("Gunakan di grup (reply ke user atau sebut @username).")

    # resolve target id
    target_id = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
    elif len(message.command) > 1:
        arg = message.command[1].strip()
        # if first arg is duration when replying, we'll handle later
        if arg.startswith("@"):
            arg = arg[1:]
        try:
            if arg.isdigit():
                target_id = int(arg)
            else:
                ent = await client.get_chat(arg)
                target_id = getattr(ent, "id", None)
        except Exception:
            target_id = None

    if not target_id:
        # maybe user used `.mute 5m` while replying — handle that case
        if message.reply_to_message and len(message.command) == 2:
            maybe = message.command[1].strip()
            # this case handled later (target from reply, dur from maybe)
            pass
        else:
            return await message.edit_text("Reply ke pesan user atau `.mute id/@username`")

    # duration (if provided as second arg when using mention)
    dur = None
    if len(message.command) > 2:
        dur = message.command[2].strip()
    elif len(message.command) == 2 and message.reply_to_message:
        # if user used `.mute 5m` while replying, command[1] is duration
        maybe = message.command[1].strip()
        # treat it as duration if it looks like duration
        if not maybe.startswith("@") and not maybe.isdigit():
            dur = maybe

    until_dt = parse_duration_to_datetime(dur)  # None => permanent

    # build safe permissions
    perms = _safe_chat_permissions({
        "can_send_messages": False,
        "can_send_media_messages": False,
        "can_send_polls": False,
        "can_send_other_messages": False,
        "can_add_web_page_previews": False,
    })

    try:
        # if temporary -> pass until_date datetime, else omit until_date for permanent mute
        if until_dt:
            await client.restrict_chat_member(chat_id=message.chat.id, user_id=target_id, permissions=perms, until_date=until_dt)
            label = dur if dur else "sementara"
            await message.edit_text(f"🔇 User `{target_id}` dimute selama {label}.")
        else:
            await client.restrict_chat_member(chat_id=message.chat.id, user_id=target_id, permissions=perms)
            await message.edit_text(f"🔇 User `{target_id}` dimute permanen.")
    except Exception:
        log.exception("mute failed")
        await message.edit_text("mute failed: terjadi kesalahan.")

@app.on_message((filters.me | ALLOW_FILTER) & filters.command("unmute", prefixes="."))
async def cmd_unmute(client: Client, message: Message):
    """
    .unmute -- reply to user or .unmute @user
    """
    caller = message.from_user
    if not caller or not await is_allowed(client, caller.id):
        return await message.edit_text("Kamu tidak punya izin.")

    if not message.chat:
        return await message.edit_text("Gunakan di grup.")

    target_id = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
    elif len(message.command) > 1:
        arg = message.command[1].strip()
        if arg.startswith("@"):
            arg = arg[1:]
        try:
            if arg.isdigit():
                target_id = int(arg)
            else:
                ent = await client.get_chat(arg)
                target_id = getattr(ent, "id", None)
        except Exception:
            target_id = None

    if not target_id:
        return await message.edit_text("Reply ke pesan user atau `.unmute id/@username`")

    perms = _safe_chat_permissions({
        "can_send_messages": True,
        "can_send_media_messages": True,
        "can_send_polls": True,
        "can_send_other_messages": True,
        "can_add_web_page_previews": True,
    })

    try:
        # removing restrictions by setting allowed perms (no until_date needed)
        await client.restrict_chat_member(chat_id=message.chat.id, user_id=target_id, permissions=perms)
        await message.edit_text(f"🔊 User `{target_id}` di-unmute.")
    except Exception:
        log.exception("unmute failed")
        await message.edit_text("unmute failed: terjadi kesalahan.")

@app.on_message((filters.me | ALLOW_FILTER) & filters.command("ban", prefixes="."))
async def cmd_ban(client: Client, message: Message):
    """
    .ban [dur] — ban user (reply or @user). dur optional; format same as mute.
    """
    caller = message.from_user
    if not caller or not await is_allowed(client, caller.id):
        return await message.edit_text("Kamu tidak punya izin.")

    if not message.chat:
        return await message.edit_text("Gunakan di grup.")

    target_id = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
    elif len(message.command) > 1:
        arg = message.command[1].strip()
        if arg.startswith("@"):
            arg = arg[1:]
        try:
            if arg.isdigit():
                target_id = int(arg)
            else:
                ent = await client.get_chat(arg)
                target_id = getattr(ent, "id", None)
        except Exception:
            target_id = None

    if not target_id:
        return await message.edit_text("Reply ke pesan user atau `.ban id/@username`")

    dur = None
    if len(message.command) > 2:
        dur = message.command[2].strip()
    elif len(message.command) == 2 and message.reply_to_message:
        maybe = message.command[1].strip()
        if not maybe.startswith("@") and not maybe.isdigit():
            dur = maybe

    until_dt = parse_duration_to_datetime(dur)

    try:
        if until_dt:
            await client.ban_chat_member(chat_id=message.chat.id, user_id=target_id, until_date=until_dt)
            label = dur if dur else "sementara"
            await message.edit_text(f"⛔ User `{target_id}` diban selama {label}.")
        else:
            await client.ban_chat_member(chat_id=message.chat.id, user_id=target_id)
            await message.edit_text(f"⛔ User `{target_id}` diban permanen.")
    except Exception:
        log.exception("ban failed")
        await message.edit_text("ban failed: terjadi kesalahan.")

@app.on_message((filters.me | ALLOW_FILTER) & filters.command("unban", prefixes="."))
async def cmd_unban(client: Client, message: Message):
    """
    .unban — unban user (reply or @user)
    """
    caller = message.from_user
    if not caller or not await is_allowed(client, caller.id):
        return await message.edit_text("Kamu tidak punya izin.")

    if not message.chat:
        return await message.edit_text("Gunakan di grup.")

    target_id = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
    elif len(message.command) > 1:
        arg = message.command[1].strip()
        if arg.startswith("@"):
            arg = arg[1:]
        try:
            if arg.isdigit():
                target_id = int(arg)
            else:
                ent = await client.get_chat(arg)
                target_id = getattr(ent, "id", None)
        except Exception:
            target_id = None

    if not target_id:
        return await message.edit_text("Reply ke pesan user atau `.unban id/@username`")

    try:
        await client.unban_chat_member(chat_id=message.chat.id, user_id=target_id)
        await message.edit_text(f"✅ User `{target_id}` di-unban.")
    except Exception:
        log.exception("unban failed")
        await message.edit_text("unban failed: terjadi kesalahan.")

@app.on_message((filters.me | ALLOW_FILTER) & filters.command("kick", prefixes="."))
async def cmd_kick(client: Client, message: Message):
    """
    .kick — kick user (reply or @user). This bans then unbans to force remove.
    """
    caller = message.from_user
    if not caller or not await is_allowed(client, caller.id):
        return await message.edit_text("Kamu tidak punya izin.")

    if not message.chat:
        return await message.edit_text("Gunakan di grup.")

    target_id = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
    elif len(message.command) > 1:
        arg = message.command[1].strip()
        if arg.startswith("@"):
            arg = arg[1:]
        try:
            if arg.isdigit():
                target_id = int(arg)
            else:
                ent = await client.get_chat(arg)
                target_id = getattr(ent, "id", None)
        except Exception:
            target_id = None

    if not target_id:
        return await message.edit_text("Reply ke pesan user atau `.kick id/@username`")

    try:
        # ban briefly then unban to kick (use small window)
        until_dt = datetime.now(timezone.utc) + timedelta(seconds=8)
        await client.ban_chat_member(chat_id=message.chat.id, user_id=target_id, until_date=until_dt)
        await client.unban_chat_member(chat_id=message.chat.id, user_id=target_id)
        await message.edit_text(f"👢 User `{target_id}` telah dikick.")
    except Exception:
        log.exception("kick failed")
        await message.edit_text("kick failed: terjadi kesalahan.")

# -------- basic commands: ping / alive / restart / info ----------
@app.on_message((filters.me | ALLOW_FILTER) & filters.command("ping", prefixes="."))
async def cmd_ping(client: Client, message: Message):
    t0 = time.perf_counter()
    m = await message.edit_text("🏓 Ponging...")
    t1 = time.perf_counter()

    ms = int((t1 - t0) * 1000)
    emo = "⚡" if ms < 150 else "🐌" if ms > 600 else "🔥"
    await m.edit_text(f"{emo} Pong! {ms} ms")

@app.on_message((filters.me | ALLOW_FILTER) & filters.command("alive", prefixes="."))
async def cmd_alive(client: Client, message: Message):
    me = await client.get_me()

    # kawaii y2k emotes
    EMO = ["🌸", "💖", "⚡", "💫", "⭐", "🩷", "🌐"]
    e = random.choice(EMO)

    # system info
    try:
        import platform, psutil
        os_name = platform.system()
        cpu_count = psutil.cpu_count(logical=True)
        ram_gb = round(psutil.virtual_memory().total / (1024**3), 2)
    except Exception:
        os_name = "UnknownOS"
        cpu_count = "?"
        ram_gb = "?"

    uname = (
        f"@{me.username}"
        if getattr(me, "username", None)
        else getattr(me, "first_name", "—")
    )

    txt = (
        f"{e} **Userbot Status — ONLINE** {e}\n\n"
        f"👤 **User:** {uname}\n"
        f"🆔 **ID:** `{me.id}`\n\n"
        f"💻 **System:** `{os_name}` • `{cpu_count}` cores • `{ram_gb} GB RAM`\n"
        f"🔌 **Pyrogram:** v{pyrogram.__version__}\n"
        f"✨ Everything is running smoothly, senpai~\n"
    )

    await message.edit_text(txt)

# restart helper + handler (paste near other command handlers)
@app.on_message(filters.command("restart", prefixes=".") & filters.me)
async def restart_bot(client: Client, message: Message):

    # kawaii progress animation
    FRAMES = [
        "🌸 Rebooting… 0%",
        "🌸💞 Rebooting… 15%",
        "🌸🌈 Rebooting… 40%",
        "🌸✨ Rebooting… 60%",
        "🌸💫 Rebooting… 80%",
        "🌸🔥 Rebooting… 95%",
        "🌸💖 Rebooting… 100%\n\n🔁 **Restarting userbot…**"
    ]

    # tampilkan animasi
    for frame in FRAMES:
        try:
            await message.edit_text(frame)
        except:
            pass
        await asyncio.sleep(0.35)

    # jalankan restart
    try:
        await message.edit_text("🔁 **Userbot restarting... Please wait.**")
    except:
        pass

    # execute real restart (termux)
    os.execl(sys.executable, sys.executable, *sys.argv)

@app.on_message(filters.command("info", prefixes=".") & filters.me)
async def cmd_info(client: Client, message: Message):
    """
    .info — show ID / name / username + profile photo (no status), owner+userbot only.
    Works on reply, arg (id/username), or defaults to self.
    """
    caller = message.from_user
    if not caller or not await is_allowed(client, caller.id):
        return await message.edit_text("Kamu tidak punya izin.")

    # resolve target
    target = None
    if message.reply_to_message:
        src = message.reply_to_message
        if src.from_user:
            target = src.from_user.id
        elif src.sender_chat:
            target = src.sender_chat.id
        elif src.forward_from:
            target = src.forward_from.id
        elif src.forward_from_chat:
            target = src.forward_from_chat.id

    if not target and len(message.command) > 1:
        a = message.command[1].strip()
        if a.startswith("@"):
            a = a[1:]
        if a.isdigit():
            target = int(a)
        else:
            try:
                ent = await client.get_chat(a)
                target = getattr(ent, "id", None) or a
            except Exception:
                target = a

    if not target:
        me = await client.get_me()
        target = me.id

    try:
        entity = await client.get_chat(target)
    except Exception:
        return await message.edit_text("Gagal ambil info: terjadi kesalahan.")

    eid = getattr(entity, "id", "—")
    first = getattr(entity, "first_name", None) or getattr(entity, "title", None) or ""
    last = getattr(entity, "last_name", None) or ""
    fullname = (first + " " + last).strip() or "—"
    username = getattr(entity, "username", None) or None

    caption_lines = [
        "🧾 User Information",
        f"🆔 ID       : {eid}",
        f"👤 Name     : {fullname}",
        f"🔖 Username : @{username if username else '—'}",
    ]
    caption = "\n".join(caption_lines)

    bio = None
    try:
        bio = await fetch_profile_photo_bytes(client, target)
    except Exception:
        bio = None

    if bio:
        try:
            bio.seek(0)
            return await message.reply_photo(photo=bio, caption=caption)
        except Exception:
            log.exception("reply_photo failed, fallback to text")
    await message.edit_text(caption)

# -------- AI commands (GLOBAL mode) ----------
@app.on_message((filters.me | filters.user(OWNER_ID)) & filters.command("aimode", prefixes="."))
async def cmd_aimode(client: Client, message: Message):
    global AI_GLOBAL_MODE

    caller = message.from_user
    if not caller or not await is_allowed(client, caller.id):
        return await message.edit_text("Kamu tidak punya izin.")

    parts = message.text.split()

    # Jika cuma .aimode atau .aimode show
    if len(parts) < 2 or parts[1].lower() == "show":
        return await message.edit_text(
            f"🔧 Mode AI global saat ini: {AI_GLOBAL_MODE.upper()}\n"
            "Pilihan: flash / pro / lite\n"
            "Contoh: .aimode flash"
        )

    mode = parts[1].lower()

    if mode not in GEMINI_MODELS:
        return await message.edit_text("Mode harus salah satu: flash / pro / lite")

    # Set mode global
    AI_GLOBAL_MODE = mode
    _save_ai_global_mode(mode)

    await message.edit_text(
        f"✔️ Mode AI global diubah ke: {mode.upper()}"
    )

@app.on_message((filters.me | ALLOW_FILTER) & filters.command("ai", prefixes="."))
async def cmd_ai(client: Client, message: Message):
    """
    .ai [model] <prompt>  -> ask AI. If model arg present (flash|pro|lite) it overrides for this call.
    If no prompt and message is a reply, uses replied message text as prompt.
    Uses GLOBAL mode if model arg missing.
    """
    caller = message.from_user
    if not caller or not await is_allowed(client, caller.id):
        return await message.edit_text("Kamu tidak punya izin.")

    text = (message.text or "").strip()
    parts = text.split()
    args = parts[1:] if len(parts) > 1 else []

    model_key = AI_GLOBAL_MODE  # default
    prompt = ""

    if args:
        first = args[0].lower()
        if first in GEMINI_MODELS:
            model_key = first
            prompt = " ".join(args[1:]).strip()
        else:
            prompt = " ".join(args).strip()
    elif message.reply_to_message:
        prompt = message.reply_to_message.text or message.reply_to_message.caption or ""

    if not prompt:
        return await message.edit_text(
            f"AI global mode: **{AI_GLOBAL_MODE.upper()}**\n"
            "Cara pakai:\n"
            "• `.ai apa itu relativitas?`\n"
            "• `.ai pro jelasin teori string`\n"
            "• Balas pesan lalu ketik `.ai`"
        )

    # check API key
    if not GEMINI_API_KEY:
        return await message.edit_text("Gemini API key belum diset (GEMINI_API_KEY).")

    # start processing
    loading = await message.edit_text("⏳ Memproses...")

    model_name = GEMINI_MODELS.get(model_key, GEMINI_MODELS["flash"])
    ok, answer = ask_ai_gemini(prompt, model=model_name)
    if not ok:
        try:
            await loading.edit_text(f"❗ Error:\n{answer}")
        except Exception:
            await message.edit_text(f"❗ Error:\n{answer}")
        return

    final = f"💡 Jawaban ({model_key.upper()})\n\n{answer.strip()}"
    try:
        await loading.edit_text(final[:4000])
    except Exception:
        await message.edit_text(final[:4000])

# -------- Google search command (.gsearch) ----------
@app.on_message((filters.me | ALLOW_FILTER) & filters.command("gsearch", prefixes="."))
async def cmd_gsearch(client: Client, message: Message):
    """
    .gsearch <query>  OR reply to message with `.gsearch`
    Returns top search results from Google Custom Search.
    """
    caller = message.from_user
    if not caller or not await is_allowed(client, caller.id):
        return await message.edit_text("Kamu tidak punya izin.")

    # Resolve query: prefer args, else reply text
    query = None
    if len(message.command) > 1:
        query = " ".join(message.command[1:]).strip()
    elif message.reply_to_message:
        query = (message.reply_to_message.text or message.reply_to_message.caption or "").strip()

    if not query:
        return await message.edit_text("Gunakan: `.gsearch <query>` atau reply ke pesan lalu `.gsearch`.")

    # Quick check: if no API/CX configured, inform user
    if not GOOGLE_SEARCH_API_KEY or not GOOGLE_CSE_ID:
        return await message.edit_text("GOOGLE_SEARCH_API_KEY/GOOGLE_CSE_ID belum diset di env. `.gsearch` tidak bisa dijalankan.")

    loading = await message.edit_text("🔎 Mencari di Google...")
    try:
        results = google_search(query, num=3)
    except Exception as e:
        log.exception("gsearch error")
        return await loading.edit_text(f"❗ Gagal mencari: {e}")

    if not results:
        return await loading.edit_text("🔎 Pencarian sukses tapi tidak menemukan hasil atau terjadi error pada Google CSE.")

    # format reply
    parts = []
    for i, r in enumerate(results, start=1):
        t = r.get("title", "—")
        s = r.get("snippet", "")
        l = r.get("link", "")
        parts.append(f"{i}. {t}\n{s}\n{l}")

    footer = f"\n\n(Google CSE · results: {len(results)})"
    text = f"🔎 Hasil untuk: `{query}`\n\n" + "\n\n".join(parts) + footer

    try:
        await loading.edit_text(text[:4000])
    except Exception:
        await message.edit_text(text[:4000])

# ======================================
#            AFK SYSTEM SAFE
# ======================================

AFK_ACTIVE = False
AFK_REASON = ""
AFK_SINCE: Optional[datetime] = None

def _afk_human(d: Optional[datetime]):
    if not d:
        return "—"
    delta = datetime.now(timezone.utc) - d
    s = int(delta.total_seconds())
    if s < 60: return f"{s}s"
    m = s // 60
    if m < 60: return f"{m}m"
    h = m // 60
    if h < 24: return f"{h}h"
    return f"{h//24}d"

# ======================================
#            AFK SYSTEM KAWAII
# ======================================

AFK_ACTIVE = False
AFK_REASON = ""
AFK_SINCE: Optional[datetime] = None

def _afk_human(d: Optional[datetime]):
    """Return human-friendly duration (kawaii)."""
    if not d:
        return "—"
    delta = datetime.now(timezone.utc) - d
    s = int(delta.total_seconds())
    if s < 60:
        return f"{s}s"
    m = s // 60
    if m < 60:
        return f"{m}m"
    h = m // 60
    if h < 24:
        return f"{h}h"
    return f"{h//24}d"

# ========== SET AFK ==========
@app.on_message((filters.me | filters.user(OWNER_ID)) & filters.command("afk", prefixes="."))
async def afk_set(client, message):
    """
    .afk [reason]
    Aktivasi AFK. Works for owner & the userbot account.
    """
    global AFK_ACTIVE, AFK_REASON, AFK_SINCE
    parts = (message.text or "").split(maxsplit=1)
    AFK_REASON = parts[1].strip() if len(parts) > 1 else "lagi sibuk~"
    AFK_SINCE = datetime.now(timezone.utc)
    AFK_ACTIVE = True
    try:
        await message.edit_text(f"💤 _AFK aktif!_ — {AFK_REASON} ✨\n(aku balik nanti~)")
    except Exception:
        pass

@app.on_message((filters.me | ALLOW_FILTER) & filters.command("menu", prefixes="."))

# ========== MANUAL BACK ==========
@app.on_message((filters.me | filters.user(OWNER_ID)) & filters.command("back", prefixes="."))
async def afk_back(client, message):
    """
    Manual back: .back
    Show kawaii return message and duration.
    """
    global AFK_ACTIVE, AFK_REASON, AFK_SINCE

    if not AFK_ACTIVE:
        return await message.edit_text("✨ Kamu udah gak AFK kok~ welcome back! (≧◡≦)")

    # compute duration
    dur = _afk_human(AFK_SINCE)
    # reset
    AFK_ACTIVE = False
    AFK_REASON = ""
    AFK_SINCE = None

    # kawaii return message
    try:
        await message.edit_text(f"🌟 AFK dimatikan — kamu udah balik setelah **{dur}**! welcome~ 💫")
    except Exception:
        pass

# ========== AUTO DISABLE AFK (OUTGOING ONLY) ==========
@app.on_message(filters.me)
async def afk_auto_off(client, message):
    """
    Auto disable AFK when sending any outgoing non-command message.
    Commands (start with ., /, !) will NOT disable AFK.
    """
    global AFK_ACTIVE, AFK_REASON, AFK_SINCE

    if not AFK_ACTIVE:
        return

    # unify text/caption
    text = ""
    if getattr(message, "text", None):
        text = message.text
    elif getattr(message, "caption", None):
        text = message.caption
    text = (text or "").strip()

    # treat as command if starts with typical prefixes
    if text.startswith((".", "/", "!")):
        return  # keep AFK if user sent a command

    # any other outgoing -> disable AFK
    dur = _afk_human(AFK_SINCE)
    AFK_ACTIVE = False
    AFK_REASON = ""
    AFK_SINCE = None

    try:
        await message.reply_text(
            f"🌸 **Okaeri~!** 🌸\n"
            f"Kamu kembali setelah **{dur}** — welcome back, senpai! (≧ω≦)ﾉ"
        )
    except Exception:
        pass
        
# ========== AFK AUTOREPLY (KAWAII & LOUD) ==========
@app.on_message((filters.group | filters.private) & ~filters.me)
async def afk_reply(client, message):
    """
    If AFK active and message either replies to the userbot or mentions the userbot username,
    reply EVERY time (user requested repeated replies).
    Works for both owner account and the userbot account.
    """
    if not AFK_ACTIVE:
        return

    try:
        me = await client.get_me()
    except Exception:
        return

    # check reply-to the bot/owner
    replied = False
    if message.reply_to_message:
        r = message.reply_to_message.from_user
        if r and getattr(r, "id", None) == me.id:
            replied = True

    # check mention by username (match in text or caption)
    mentioned = False
    uname = None
    if getattr(me, "username", None):
        uname = f"@{me.username}".lower()

    text = ""
    if getattr(message, "text", None):
        text = message.text
    elif getattr(message, "caption", None):
        text = message.caption
    text = (text or "").lower()

    if uname and uname in text:
        mentioned = True

    if not (replied or mentioned):
        return

    dur = _afk_human(AFK_SINCE)
    reason = AFK_REASON or "lagi sibuk~"

    # kawaii AFK reply (repeat every time)
    reply_texts = [
        f"💤 Lagi AFK: {reason}\n⌛ {dur} yang lalu — maaf ya~",
        f"🌙 Aku AFK nih: {reason}\n⏰ Udah {dur}, balik nanti ya~",
        f"🍡 AFK Mode: {reason}\n⏳ {dur} yang lalu — bakal bales begitu balik~",
    ]

    # pick rotating reply to feel 'alive' (but still safe)
    try:
        idx = int(time.time()) % len(reply_texts)
        await message.reply_text(reply_texts[idx])
    except Exception:
        try:
            await message.reply_text(f"💤 Lagi AFK: {reason}\n⌛ {dur} yang lalu")
        except Exception:
            pass

# --- console logger / pretty formatter ---
LEVEL_EMOJI = {
    "CRITICAL": "🔥😭‼️",
    "ERROR":    "💢😾",
    "WARNING":  "⚠️😳",
    "INFO":     "🌸😊",
    "DEBUG":    "🔧🐱",
    "NOTSET":   "🎀✨",
}

# ANSI colors (optional). Aktifkan via env LOG_COLOR=1
LOG_COLOR = os.environ.get("LOG_COLOR", "0") in ("1", "true", "True", "yes")
ANSI = {
    "reset": "\x1b[0m",
    "grey": "\x1b[90m",
    "red": "\x1b[31m",
    "yellow": "\x1b[33m",
    "green": "\x1b[32m",
    "blue": "\x1b[34m",
    "magenta": "\x1b[35m",
}

def _level_color(levelname: str) -> str:
    if not LOG_COLOR:
        return ""
    if levelname == "ERROR" or levelname == "CRITICAL":
        return ANSI["red"]
    if levelname == "WARNING":
        return ANSI["yellow"]
    if levelname == "INFO":
        return ANSI["green"]
    if levelname == "DEBUG":
        return ANSI["blue"]
    return ANSI["magenta"]

class PrettyFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = time.strftime("%H:%M:%S", time.localtime(record.created))
        lvl = record.levelname
        emo = LEVEL_EMOJI.get(lvl, "📣")
        module = record.name.split(".")[-1]
        msg = record.getMessage()

        color = _level_color(lvl)
        reset = ANSI["reset"] if LOG_COLOR else ""

        # single-line summary + optional multi-line exc
        header = f"{color}{emo}  [{lvl}] {module} • {ts}{reset}"
        if record.exc_info:
            # include stacktrace (multi-line)
            formatted_exception = self.formatException(record.exc_info)
            body = f"{msg}\n{formatted_exception}"
        else:
            body = msg

        return f"{header}\n{body}\n"

# configure root logger to use the pretty formatter
root = logging.getLogger()
# remove all existing handlers (avoid duplicated prints)
for h in list(root.handlers):
    root.removeHandler(h)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
console_handler.setFormatter(PrettyFormatter())
root.addHandler(console_handler)
# default level (change if you want fewer logs)
root.setLevel(logging.INFO)

# =========================================================
# ANIME BOOT BANNER — FINAL PATCH
# =========================================================
BANNERS = [
        r"""
 ⠀⠀⠀  (≧◡≦) ♡  B O O T   S E Q U E N C E 
   ✦ Initializing system…  
   ✦ Loading cute dependencies…  
   ✦ Activating pastel power cores…  
   ✦ Deploying neko-protocol…  
   Userbot is starting! (๑˃ᴗ˂)ﻭ
        """,
        r"""
 ／l、
（ﾟ､ ｡ ７   < Nya~ Master! Userbot waking up…
  l  ~ヽ       • Loading neko engine  
  じしf_, )     • Warming up whiskers  
               • Injecting kawaii into memory…  
 💖 Ready to serve!
        """,
        r"""
(っ◔◡◔)っ ♥  U S E R B O T   B O O T I N G  ♥

  🍥 Loading chibi modules...
  🍥 Initializing moe-engine...
  🍥 Importing pastel-particle shaders...

  ✨ System Status:         OK
  ✨ Kawaii Protocols:      OK
  ✨ Async Magic:           OK

  ❤️  Userbot is now online — yoroshiku ne~! ❤️
        """,

    ]

def _print_banner():
    """Print a random banner block in clean formatting."""
    try:
        import textwrap
        banner = random.choice(BANNERS).strip("\n")
        wrapped = "\n".join(
            textwrap.fill(line, width=78, replace_whitespace=False)
            for line in banner.splitlines()
        )
        sep = "═" * 78
        print("\n" + sep)
        print(wrapped)
        print(sep + "\n")
    except Exception:
        print("Userbot starting... (banner failed)")

def main():
    # 1. Print banner (tidak boleh bikin crash)
    _print_banner()

    # 2. Log startup
    try:
        log.info("Starting userbot… (AI global mode: %s)", AI_GLOBAL_MODE.upper())
    except:
        print("Starting userbot…")

    # 3. Jalankan Pyrogram Userbot
    try:
        app.run()
    except KeyboardInterrupt:
        try:
            log.info("Stopped by user")
        except:
            print("Stopped by user")
    except Exception:
        try:
            log.exception("userbot crashed")
        except:
            import traceback
            traceback.print_exc()
    finally:
        try:
            log.info("Userbot stopped.")
        except:
            print("Userbot stopped.")


if __name__ == "__main__":
    main()
# =========================================================