import discord
from discord.ext import commands
import yt_dlp
import os
import asyncio
from discord import app_commands
from flask import Flask, send_from_directory, url_for
import threading
import urllib.parse
from dotenv import load_dotenv
import time
import concurrent.futures
import json
from datetime import datetime, timedelta

load_dotenv()

limits = {}
limit_json = 'limits.json'

LIMIT = 1.0 # gb

def load_limits():
    global limits
    try:
        if os.path.exists(limit_json):
            with open(limit_json, 'r') as f:
                limits = json.load(f)
    except Exception as e:
        print(e)
        limits = {}

def save_limits():
    try:
        with open(limit_json, 'w') as f:
            json.dump(limits, f, indent=2)
    except Exception as e:
        print(e)

def get_user_stats(user_id):
    user_id = str(user_id)
    day = datetime.now(datetime.timezone.utc).date().isoformat()
    
    if user_id not in limits:
        limits[user_id] = {
            'total': 0,
            'size_total': 0.0,
            'usedtoday': {},
            'last_reset': day
        }
    
    if day not in limits[user_id]['usedtoday']:
        limits[user_id]['usedtoday'][day] = {
            'downloads': 0,
            'size_mb': 0.0
        }
    
    usage = limits[user_id]['usedtoday']
    day = datetime.now(datetime.timezone.utc).date()
    for date_str in list(usage.keys()):
        try:
            date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
            if (day - date_obj).days > 7:
                del usage[date_str]
        except:
            pass
    
    return limits[user_id]

def can_download(user_id, approx_mb_size=0):
    stats = get_user_stats(user_id)
    day = datetime.now(datetime.timezone.utc).date().isoformat()
    
    used = stats['usedtoday'].get(day, {'downloads': 0, 'size_mb': 0.0})
    mb_used = used['size_mb']
    gb_used = mb_used / 1024
    
    mb_limit = LIMIT * 1024
    mb_left = mb_limit - mb_used
    if approx_mb_size > mb_left:
        return {
            'can_download': False,
            'used_gb': gb_used,
            'limit_gb': LIMIT,
            'mb_left': mb_left,
            'approx_mb_size': approx_mb_size,
            'reason': f"Quota exceeded! Used: {gb_used:.2f}GB/{LIMIT}GB. Remaining: {mb_left:.0f}MB. Need: {approx_mb_size:.0f}MB. Try again tomorrow."
        }
    
    return {
        'can_download': True,
        'used_gb': gb_used,
        'limit_gb': LIMIT,
        'mb_left': mb_left,
        'downloads_today': used['downloads']
    }

def save(user_id, file_size_mb):
    user_id = str(user_id)
    stats = get_user_stats(user_id)
    day = datetime.now(datetime.timezone.utc).date().isoformat()
    
    stats['total'] += 1
    stats['size_total'] += file_size_mb / 1024
    
    if day not in stats['usedtoday']:
        stats['usedtoday'][day] = {'downloads': 0, 'size_mb': 0.0}
    
    stats['usedtoday'][day]['downloads'] += 1
    stats['usedtoday'][day]['size_mb'] += file_size_mb
    
    save_limits()

def approximate_file_size(info, format_id=None):
    max_size_bytes = 0
    
    if format_id:
        for fmt in info.get('formats', []):
            if fmt.get('format_id') == format_id:
                if fmt.get('filesize'):
                    return fmt['filesize'] / (1024 * 1024)
                elif fmt.get('filesize_approx'):
                    return fmt['filesize_approx'] / (1024 * 1024)
                break
    
    for fmt in info.get('formats', []):
        if fmt.get('filesize'):
            max_size_bytes = max(max_size_bytes, fmt['filesize'])
        elif fmt.get('filesize_approx'):
            max_size_bytes = max(max_size_bytes, fmt['filesize_approx'])
    
    if max_size_bytes == 0:
        duration = info.get('duration', 0)
        if duration > 0:
            estimated_mb = duration / 60 * 1.5
            return estimated_mb
        else:
            return 50
    
    return max_size_bytes / (1024 * 1024)

def formatted(size_mb):
    if size_mb >= 1024:
        return f"{size_mb/1024:.2f}GB"
    else:
        return f"{size_mb:.0f}MB"

load_limits()

app = Flask(__name__)
@app.route('/downloads/<filename>')
def download_file(filename):
    return send_from_directory('downloads', filename)

def run_flask():
    app.run(host='0.0.0.0', port=5000, debug=False)
thread = threading.Thread(target=run_flask, daemon=True)
thread.start()

intents = discord.Intents.default()
intents.message_content = True
client = commands.Bot(command_prefix='!', intents=intents)
file_created = {}

async def clean():
    while True:
        try:
            curr = time.time()
            if os.path.exists('downloads'):
                for filename in os.listdir('downloads'):
                    file_path = os.path.join('downloads', filename)
                    if os.path.isfile(file_path):
                        file_created[file_path] = os.path.getctime(file_path)
                for file_path, created_time in list(file_created.items()):
                    if curr - created_time > 86400:
                        os.remove(file_path)
                        del file_created[file_path]
                        print(f"Deleted old file: {os.path.basename(file_path)}")
        except Exception as e:
            print(e)
        await asyncio.sleep(3600)

@client.event
async def on_ready():
    print(f'Logged in as {client.user}')
    await client.tree.sync()
    client.loop.create_task(clean())

def downloading_bar(progress):
    filled = int(20 * progress // 100)
    bar = '█' * filled + '░' * (20 - filled)
    return f"[{bar}] {progress:.1f}%"

class ProgressTracker:
    def __init__(self, interaction, info):
        self.interaction = interaction
        self.info = info
        self.last_update = 0
        self.completed = False
        self.loop = asyncio.get_event_loop()
    def progress_hook(self, d):
        if self.completed:
            return
        curr = time.time()
        if curr - self.last_update < 2:
            return
        self.last_update = curr
        if d['status'] == 'downloading':
            try:
                if 'total_bytes' in d:
                    progress = (d['downloaded_bytes'] / d['total_bytes']) * 100
                elif 'total_bytes_estimate' in d:
                    progress = (d['downloaded_bytes'] / d['total_bytes_estimate']) * 100
                else:
                    progress = 0
                if progress >= 99:
                    return
                downloaded_mb = d.get('downloaded_bytes', 0) / (1024 * 1024)
                total_mb = d.get('total_bytes', d.get('total_bytes_estimate', 0)) / (1024 * 1024)
                speed = d.get('speed', 0)
                speed = speed / (1024 * 1024) if speed else 0
                eta = d.get('eta', 0)
                emb = discord.Embed(title="Downloading...",description=f"**{self.info['title']}**",color=discord.Color.random())
                
                progress_bar = downloading_bar(progress)
                emb.add_field(name="Progress", value=f"```{progress_bar}```", inline=False)
                
                if total_mb > 0:
                    emb.add_field(name="Size", value=f"{downloaded_mb:.1f} MB / {total_mb:.1f} MB", inline=True)
                else:
                    emb.add_field(name="Downloaded", value=f"{downloaded_mb:.1f} MB", inline=True)
                
                if speed > 0:
                    emb.add_field(name="Speed", value=f"{speed:.1f} MB/s", inline=True)
                
                if eta and eta > 0:
                    eta_str = f"{eta//60}m {eta%60}s" if eta > 60 else f"{eta}s"
                    emb.add_field(name="ETA", value=eta_str, inline=True)
                emb.set_image(url=self.info['thumbnail'])
                asyncio.run_coroutine_threadsafe(self.update(emb), self.loop)
                
            except Exception as e:
                print(e)
        
        elif d['status'] == 'finished':
            self.completed = True
    
    async def update(self, embed):
        try:
            if not self.completed:
                await self.interaction.edit_original_response(embed=embed, view=None)
        except Exception as e:
            print(e)

def download_fs(url, type, format_id, progress_tracker, info):
    try:
        if not os.path.exists('downloads'):
            os.makedirs('downloads')
        
        if type == "audio":
            ydl_opts = {
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                }],
                'outtmpl': 'downloads/%(title)s_audio.%(ext)s',
                'progress_hooks': [progress_tracker.progress_hook],
            }
            ext = '.mp3'
        else:
            formattt = None
            for fmt in info.get('formats', []):
                if fmt.get('format_id') == format_id:
                    formattt = fmt
                    break
            
            height = formattt.get('height', 'unknown') if formattt else 'unknown'
            
            ydl_opts = {
                'format': format_id,
                'outtmpl': f'downloads/%(title)s_{height}p.%(ext)s',
                'progress_hooks': [progress_tracker.progress_hook],
            }
            ext = '.mp4'
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        progress_tracker.completed = True
        if type == "audio":
            titlee = "".join(c for c in info['title'] if c.isalnum() or c in (' ', '-', '_')).rstrip()
            filename = f"{titlee}_audio{ext}"
        else:
            formattt = None
            for fmt in info.get('formats', []):
                if fmt.get('format_id') == format_id:
                    formattt = fmt
                    break
            
            height = formattt.get('height', 'unknown') if formattt else 'unknown'
            titlee = "".join(c for c in info['title'] if c.isalnum() or c in (' ', '-', '_')).rstrip()
            filename = f"{titlee}_{height}p{ext}"
        file_path = None
        if os.path.exists('downloads'):
            for file in os.listdir('downloads'):
                if type == "audio" and "_audio" in file and file.endswith('.mp3'):
                    if any(word in file.lower() for word in info['title'].lower().split()[:3]):
                        file_path = os.path.join('downloads', file)
                        filename = file
                        break
                elif type == "video" and f"_{height}p" in file and file.endswith('.mp4'):
                    if any(word in file.lower() for word in info['title'].lower().split()[:3]):
                        file_path = os.path.join('downloads', file)
                        filename = file
                        break
        
        if not file_path:
            files = [f for f in os.listdir('downloads') if f.endswith(ext)]
            if files:
                files.sort(key=lambda x: os.path.getctime(os.path.join('downloads', x)), reverse=True)
                filename = files[0]
                file_path = os.path.join('downloads', filename)
        
        if not file_path or not os.path.exists(file_path):
            raise Exception("Downloaded file not found")
        
        file_created[file_path] = time.time()
        encoded = urllib.parse.quote(filename)
        download_url = f"http://localhost:5000/downloads/{encoded}"
        size = os.path.getsize(file_path) / (1024 * 1024)
        
        return {
            'success': True,
            'filename': filename,
            'file_path': file_path,
            'download_url': download_url,
            'size': size
        }
        
    except Exception as e:
        progress_tracker.completed = True
        return {
            'success': False,
            'error': str(e)
        }

async def download_yt(url, type, format_id, interaction, info):
    try:
        progress_tracker = ProgressTracker(interaction, info)
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor() as executor:
            result = await loop.run_in_executor(executor, download_fs, url, type, format_id, progress_tracker, info)
        await asyncio.sleep(1)
        if result['success']:
            save(interaction.user.id, result['size'])
            
            emb = discord.Embed(
                title="Download Completed!",
                description=f"**{info['title']}**",
                color=discord.Color.random()
            )
            emb.add_field(name="Progress", value="```[████████████████████] 100.0%```", inline=False)
            emb.add_field(name="File Size", value=f"{result['size']:.1f} MB", inline=True)
            emb.add_field(name="Duration", value=f"{info.get('duration', 0) // 60}:{info.get('duration', 0) % 60:02d}", inline=True)
            emb.add_field(name="Status", value="Download Complete", inline=True)
            emb.add_field(name="URL", value=result['download_url'], inline=False)
            emb.add_field(name="Filename", value=result['filename'], inline=False)
            emb.add_field(name="Attention!", value="File will be deleted after 24 hours", inline=False)
            stats = get_user_stats(interaction.user.id)
            day = datetime.now(datetime.timezone.utc).date().isoformat()
            used = stats['usedtoday'].get(day, {'downloads': 0, 'size_mb': 0.0})
            used_gb = used['size_mb'] / 1024
            emb.add_field(name="Daily Usage", value=f"{used_gb:.2f}GB / {LIMIT}GB used", inline=False)
            
            emb.set_image(url=info['thumbnail'])
            await interaction.edit_original_response(embed=emb, view=None)
        else:
            emb = discord.Embed(
                title="Download Failed!!",
                description=result['error'],
                color=discord.Color.random()
            )
            await interaction.edit_original_response(embed=emb, view=None)
            
    except Exception as e:
        emb = discord.Embed(
            title="Download Failed",
            description=str(e),
            color=discord.Color.random()
        )
        await interaction.edit_original_response(embed=emb, view=None)

@client.tree.command()
async def download(interaction: discord.Interaction, url: str):
    await interaction.response.defer()
    
    try:
        ydl_opts = {'quiet': True, 'no_warnings': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = info.get('formats', [])
            vid_fmt = []
            for fmt in formats:
                if fmt.get('vcodec') and fmt.get('vcodec') != 'none' and fmt.get('height'):
                    height = fmt.get('height')
                    if height and not any(f['height'] == height for f in vid_fmt):
                        vid_fmt.append({
                            'height': height,
                            'format_id': fmt.get('format_id'),
                            'ext': fmt.get('ext', 'mp4'),
                            'fps': fmt.get('fps', ''),
                            'filesize': fmt.get('filesize', 0)
                        })
            
            vid_fmt.sort(key=lambda x: x['height'], reverse=True)
            audio_size = approximate_file_size(info)
            
            emb = discord.Embed(
                title="Choose Download Type",
                description=f"**{info['title']}**\n Duration: {info.get('duration', 0) // 60}:{info.get('duration', 0) % 60:02d}",
                color=discord.Color.random()
            )
            user_stats = get_user_stats(interaction.user.id)
            day = datetime.now(datetime.timezone.utc).date().isoformat()
            used = user_stats['usedtoday'].get(day, {'downloads': 0, 'size_mb': 0.0})
            used_gb = used['size_mb'] / 1024
            remaining_gb = LIMIT - used_gb
            
            usage_text = f"📊 Daily Usage: {used_gb:.2f}GB / {LIMIT}GB\n💾 Remaining: {remaining_gb:.2f}GB"
            emb.add_field(name="Your Bandwidth", value=usage_text, inline=False)
            audio_check = can_download(interaction.user.id, audio_size)
            if audio_check['can_download']:
                emb.add_field(name="Audio", value=f"Download MP3 (~{formatted(audio_size)})", inline=False)
            else:
                emb.add_field(name="Audio", value="❌ Not enough bandwidth", inline=False)
            if vid_fmt:
                video_options = []
                for fmt in vid_fmt[:10]:
                    video_size = approximate_file_size(info, fmt['format_id'])
                    video_check = can_download(interaction.user.id, video_size)
                    fps_text = f"@{fmt['fps']}fps" if fmt['fps'] else ""
                    size_text = f"~{formatted(video_size)}"
                    
                    if video_check['can_download']:
                        video_options.append(f"✅ {fmt['height']}p {fps_text} ({size_text})")
                    else:
                        video_options.append(f"❌ {fmt['height']}p {fps_text} ({size_text}) - Not enough bandwidth")
                
                video_text = "\n".join(video_options)
                if len(vid_fmt) > 10:
                    video_text += "\n... and more"
                emb.add_field(name="Available Videos", value=video_text, inline=False)
            
            emb.set_image(url=info['thumbnail'])
            view = discord.ui.View(timeout=300)
            if audio_check['can_download']:
                audio_button = discord.ui.Button(label=f"Audio (~{formatted(audio_size)})", style=discord.ButtonStyle.primary)
                
                async def audio_callback(button):
                    final_check = can_download(interaction.user.id, audio_size)
                    if not final_check['can_download']:
                        error_emb = discord.Embed(
                            title="❌ Cannot Download",
                            description=final_check['reason'],
                            color=discord.Color.random()
                        )
                        await button.response.edit_message(embed=error_emb, view=None)
                        return
                    
                    await button.response.defer()
                    emb = discord.Embed(
                        title="Starting Download...",
                        description=f"**{info['title']}**\nPreparing audio download...",
                        color=discord.Color.random()
                    )
                    await button.edit_original_response(embed=emb, view=None)
                    await download_yt(url, "audio", None, button, info)
                
                audio_button.callback = audio_callback
                view.add_item(audio_button)
            if vid_fmt and any(can_download(interaction.user.id, approximate_file_size(info, fmt['format_id']))['can_download'] for fmt in vid_fmt):
                video_button = discord.ui.Button(label="Video", style=discord.ButtonStyle.primary)
                
                async def video_callback(button):
                    quality_view = discord.ui.View(timeout=300)
                    
                    for fmt in vid_fmt[:10]:
                        video_size = approximate_file_size(info, fmt['format_id'])
                        video_check = can_download(interaction.user.id, video_size)
                        
                        if video_check['can_download']:
                            fps_text = f" @{fmt['fps']}fps" if fmt['fps'] else ""
                            size_text = f" (~{formatted(video_size)})"
                            quality_btn = discord.ui.Button(
                                label=f"{fmt['height']}p{fps_text}{size_text}",
                                style=discord.ButtonStyle.secondary
                            )
                            
                            def make_video_callback(format_id, height, size):
                                async def quality_callback(interaction):
                                    final_check = can_download(interaction.user.id, size)
                                    if not final_check['can_download']:
                                        error_emb = discord.Embed(
                                            title="❌ Cannot Download",
                                            description=final_check['reason'],
                                            color=discord.Color.random()
                                        )
                                        await interaction.response.edit_message(embed=error_emb, view=None)
                                        return
                                    
                                    await interaction.response.defer()
                                    emb = discord.Embed(
                                        title="Starting Download....",
                                        description=f"**{info['title']}**\nPreparing {height}p video download....",
                                        color=discord.Color.random()
                                    )
                                    await interaction.edit_original_response(embed=emb, view=None)
                                    await download_yt(url, "video", format_id, interaction, info)
                                return quality_callback
                            
                            quality_btn.callback = make_video_callback(fmt['format_id'], fmt['height'], video_size)
                            quality_view.add_item(quality_btn)
                    
                    back_btn = discord.ui.Button(label="← Back", style=discord.ButtonStyle.secondary)
                    async def back_callback(interaction):
                        await interaction.response.edit_message(embed=emb, view=view)
                    back_btn.callback = back_callback
                    quality_view.add_item(back_btn)
                    
                    emb = discord.Embed(
                        title="Choose Video Quality",
                        description=f"**{info['title']}**\nSelect video quality (with size estimates):",
                        color=discord.Color.random()
                    )
                    await button.response.edit_message(embed=emb, view=quality_view)
                
                video_button.callback = video_callback
                view.add_item(video_button)
            
            await interaction.followup.send(embed=emb, view=view)
            
    except Exception as e:
        emb = discord.Embed(
            title="Error",
            description=str(e),
            color=discord.Color.random()
        )
        await interaction.followup.send(embed=emb)

@client.tree.command()
async def usage(interaction: discord.Interaction):
    user_stats = get_user_stats(interaction.user.id)
    day = datetime.now(datetime.timezone.utc).date().isoformat()
    used = user_stats['usedtoday'].get(day, {'downloads': 0, 'size_mb': 0.0})
    
    used_gb = used['size_mb'] / 1024
    remaining_gb = LIMIT - used_gb
    percentage = (used_gb / LIMIT) * 100
    
    emb = discord.Embed(
        title="📊 Your Daily Bandwidth Usage",
        color=discord.Color.random()
    )
    
    filled = int(20 * percentage / 100)
    bar = '█' * filled + '░' * (20 - filled)
    progress_text = f"```[{bar}] {percentage:.1f}%```"
    
    emb.add_field(name="Daily Progress", value=progress_text, inline=False)
    emb.add_field(name="Used Today", value=f"{used_gb:.2f}GB", inline=True)
    emb.add_field(name="Remaining", value=f"{remaining_gb:.2f}GB", inline=True)
    emb.add_field(name="Downloads Today", value=str(used['downloads']), inline=True)
    emb.add_field(name="Total Downloads", value=str(user_stats['total']), inline=True)
    emb.add_field(name="Total Downloaded", value=f"{user_stats['size_total']:.2f}GB", inline=True)
    emb.add_field(
        name="Current Limits", 
        value=f"• {LIMIT}GB daily bandwidth\n• No single file size limit\n• All video qualities available", 
        inline=False
    )
    
    emb.add_field(name="Reset Time", value="Bandwidth resets daily at midnight UTC", inline=False)
    
    await interaction.response.send_message(embed=emb)

@client.tree.command()
async def limits(interaction: discord.Interaction):
    emb = discord.Embed(
        title="📋 Download Limits & Information",
        description="Current system limits:",
        color=discord.Color.random()
    )
    
    limits_text = f"""
    📊 **Daily Bandwidth:** {LIMIT}GB per day
    📦 **Single File Size:** No limit! Download files of any size
    🎥 **Video Quality:** All qualities available (8K, 4K, 1080p, 720p, etc.)
    🎵 **Audio Format:** MP3
    ⏰ **Reset Time:** Daily at midnight UTC
    """
    
    emb.add_field(name="Limits", value=limits_text, inline=False)
    
    examples_text = f"""
    📝 **Examples:**
    • 10 videos of 500MB each = 5GB (full daily limit)
    • 1 video of 3GB + smaller videos = 5GB total
    • 1 massive 5GB 8K video = full daily limit
    • Mix any file sizes as long as total ≤ 5GB per day
    """
    
    emb.add_field(name="How it Works", value=examples_text, inline=False)
    emb.add_field(name="File Retention", value="Downloaded files are automatically deleted after 24 hours", inline=False)
    
    await interaction.response.send_message(embed=emb)

client.run(os.getenv('TOKEN'))