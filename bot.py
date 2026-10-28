import os
import json
import re
import time
import datetime
import discord
import aiohttp
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

intents = discord.Intents.default()
intents.members = True          # Obrigatório para detectar saídas do servidor
intents.message_content = True  # Obrigatório para ler o conteúdo das mensagens e bloquear texto comum

bot = commands.Bot(
    command_prefix="!", 
    intents=intents,
    reconnect=True
)

# ================= CONFIGURAÇÕES =================
ID_CANAL_AVISOS_LIVE = 1533570756183592971  
CARGO_AUTORIZADO_ID = 1495476154130370741  # <--- ID do cargo autorizado
MEU_DISCORD_ID = 566803886359445504        # <--- O SEU ID do Discord

COR_ALERTA_LIVE = 0xD41C1C  

TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY") 

STREAMERS_REGISTRADOS = {}
ARQUIVO_DADOS = "streamers.json"

def carregar_streamers():
    global STREAMERS_REGISTRADOS
    if os.path.exists(ARQUIVO_DADOS):
        with open(ARQUIVO_DADOS, "r", encoding="utf-8") as f:
            try:
                dados = json.load(f)
                STREAMERS_REGISTRADOS = {}
                for chave, info in dados.items():
                    if not isinstance(info, dict):
                        continue
                    if "plataforma" not in info or "identifier" not in info:
                        url = info.get("url_original", f"https://twitch.tv/{chave}")
                        plat, ident = identificar_plataforma(url)
                        if not ident:
                            plat, ident = "twitch", chave.lower()
                        
                        STREAMERS_REGISTRADOS[ident] = {
                            "discord_id": info.get("discord_id", MEU_DISCORD_ID),
                            "plataforma": plat,
                            "identifier": ident,
                            "em_live": info.get("em_live", False),
                            "url_original": url
                        }
                    else:
                        STREAMERS_REGISTRADOS[chave] = info
            except json.JSONDecodeError:
                STREAMERS_REGISTRADOS = {}

def salvar_streamers():
    with open(ARQUIVO_DADOS, "w", encoding="utf-8") as f:
        json.dump(STREAMERS_REGISTRADOS, f, indent=4)

def identificar_plataforma(texto: str):
    texto = texto.strip()
    
    match_twitch = re.search(r'(?:https?://)?(?:www\.)?twitch\.(?:tv|com)/([a-zA-Z0-9_]{4,25})', texto, re.IGNORECASE)
    if match_twitch:
        return "twitch", match_twitch.group(1).lower()
        
    match_kick = re.search(r'(?:https?://)?(?:www\.)?kick\.com/([a-zA-Z0-9_]{3,25})', texto, re.IGNORECASE)
    if match_kick:
        return "kick", match_kick.group(1).lower()
        
    match_tiktok = re.search(r'(?:https?://)?(?:www\.)?tiktok\.com/@([a-zA-Z0-9_.]+)', texto, re.IGNORECASE)
    if match_tiktok:
        return "tiktok", match_tiktok.group(1).lower()
        
    match_yt = re.search(r'(?:https?://)?(?:www\.)?youtube\.com/(?:@([a-zA-Z0-9_.-]+)|channel/([a-zA-Z0-9_-]+)|c/([a-zA-Z0-9_.-]+))', texto, re.IGNORECASE)
    if match_yt:
        handle = match_yt.group(1) or match_yt.group(2) or match_yt.group(3)
        return "youtube", handle
        
    return None, None

async def pegar_token_twitch():
    if not TWITCH_CLIENT_ID or not TWITCH_CLIENT_SECRET:
        return None
    url = "https://id.twitch.tv/oauth2/token"
    params = {"client_id": TWITCH_CLIENT_ID, "client_secret": TWITCH_CLIENT_SECRET, "grant_type": "client_credentials"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("access_token")
    except Exception:
        pass
    return None

async def checar_twitch_lotes(session, token, usernames):
    headers = {"Client-ID": TWITCH_CLIENT_ID, "Authorization": f"Bearer {token}"}
    ativas = {}
    for i in range(0, len(usernames), 100):
        lote = usernames[i:i+100]
        params = [("user_login", user) for user in lote]
        try:
            async with session.get("https://api.twitch.tv/helix/streams", headers=headers, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for stream in data.get("data", []):
                        username = stream["user_login"].lower()
                        base_thumb = stream.get("thumbnail_url", "").format(width=1280, height=720)
                        thumb_atualizada = f"{base_thumb}?t={int(time.time())}" if base_thumb else ""
                        
                        ativas[username] = {
                            "title": stream.get("title", "Sem título"),
                            "game": stream.get("game_name", "Categoria não informada"),
                            "thumbnail": thumb_atualizada,
                            "url": f"https://twitch.tv/{username}"
                        }
        except Exception:
            pass
    return ativas

async def checar_kick(session, username):
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        async with session.get(f"https://kick.com/api/v2/channels/{username}", headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                livestream = data.get("livestream")
                if livestream:
                    base_thumb = livestream.get("thumbnail", {}).get("url", "")
                    thumb_atualizada = f"{base_thumb}?t={int(time.time())}" if base_thumb else ""
                    return {
                        "title": livestream.get("session_title", "Sem título"),
                        "game": livestream.get("categories", [{}])[0].get("name", "Categoria não informada") if livestream.get("categories") else "Categoria não informada",
                        "thumbnail": thumb_atualizada,
                        "url": f"https://kick.com/{username}"
                    }
    except Exception:
        pass
    return None

async def checar_youtube(session, handle):
    return None

@tasks.loop(minutes=5)
async def monitorar_lives():
    if not STREAMERS_REGISTRADOS:
        return

    try:
        token_twitch = await pegar_token_twitch()
        async with aiohttp.ClientSession() as session:
            
            twitch_users = [u for u, info in STREAMERS_REGISTRADOS.items() if isinstance(info, dict) and info.get("plataforma") == "twitch"]
            twitch_ativas = await checar_twitch_lotes(session, token_twitch, twitch_users) if (token_twitch and twitch_users) else {}

            canal_destino = bot.get_channel(ID_CANAL_AVISOS_LIVE)
            if not canal_destino:
                return

            for chave, info in STREAMERS_REGISTRADOS.items():
                if not isinstance(info, dict