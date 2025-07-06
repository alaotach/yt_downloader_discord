import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp
import os
import asyncio
from flask import Flask, send_from_directory, url_for
import threading
import urllib.parse
from dotenv import load_dotenv
import time
import concurrent.futures
import json
from datetime import datetime, timedelta, timezone
import subprocess
import signal
import psutil
import sys

load_dotenv()

limits = {}
limit_json = 'limits.json'
history_json = 'history.json'
history = {}

LIMIT = 1.0 # gb
COOLDOWN = 30

last_download = {}

def load_limits():
    global limits
    try:
        if os.path.exists(limit_json):
            with open(limit_json, 'r') as f:
                limits = json.load(f)
    except Exception as e:
        limits = {}
        print(e)

def save_limits():
    try:
        with open(limit_json, 'w') as f:
            json.dump(limits, f, indent=2)
    except Exception as e:
        print(e)

def load_history():
    global history
    try:
        if os.path.exists(history_json):
            with open(history_json, 'r') as f:
                history = json.load(f)
    except Exception as e:
        print(e)
        history = {}

def save_history():
    try:
        with open(history_json, 'w') as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        print(e)

def add_history(user_id, title, url, file_size_mb, download_type, quality=None):
    user_id = str(user_id)
    if user_id not in history:
        history[user_id] = []
    
    history_entry = {
        'title': title,
        'url': url,
        'size_mb': file_size_mb,
        'type': download_type,
        'quality': quality,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'date': datetime.now(timezone.utc).date().isoformat()
    }
    
    history[user_id].insert(0, history_entry)
    history[user_id] = history[user_id]
    save_history()

def get_stats(user_id):
    user_id = str(user_id)
    day = datetime.now(timezone.utc).date().isoformat()
    
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
    day = datetime.now(timezone.utc).date()
    for date_str in list(usage.keys()):
        try:
            date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
            if (day - date_obj).days > 7:
                del usage[date_str]
        except:
            pass
    
    return limits[user_id]


def save(user_id, file_size_mb):
    user_id = str(user_id)
    stats = get_stats(user_id)
    day = datetime.now(timezone.utc).date().isoformat()
    
    stats['total'] += 1
    stats['size_total'] += file_size_mb / 1024
    
    if day not in stats['usedtoday']:
        stats['usedtoday'][day] = {'downloads': 0, 'size_mb': 0.0}
    
    stats['usedtoday'][day]['downloads'] += 1
    stats['usedtoday'][day]['size_mb'] += file_size_mb
    
    save_limits()


def formatted(size_mb):
    if size_mb >= 1024:
        return f"{size_mb/1024:.2f}GB"
    else:
        return f"{size_mb:.0f}MB"

load_limits()
load_history()

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
    def __init__(self, interaction, info, max_size_mb=None):
        self.interaction = interaction
        self.info = info
        self.max_size_mb = max_size_mb
        self.last_update = 0
        self.completed = False
        self.size_exceeded = False
        self.loop = asyncio.get_event_loop()
        self.update_queue = asyncio.Queue()
        self.download_process = None
        self.should_stop = False
    
    def set_process(self, process):
        self.download_process = process
    
    def stop_download(self):
        self.should_stop = True
        self.size_exceeded = True
        self.completed = True
        if self.download_process:
            try:
                parent = psutil.Process(self.download_process.pid)
                children = parent.children(recursive=True)
                for child in children:
                    child.kill()
                parent.kill()
                # print("killed")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        try:
            if os.path.exists('downloads'):
                for f in os.listdir('downloads'):
                    file_path = os.path.join('downloads', f)
                    if os.path.isfile(file_path):
                        if (f.endswith('.part') or 
                            f.endswith('.ytdl') or
                            f.endswith('.temp') or
                            f.endswith('.tmp') or
                            '.part-Frag' in f):
                            try:
                                os.remove(file_path)
                                # print(f)
                                if file_path in file_created:
                                    del file_created[file_path]
                            except Exception as e:
                                print(e)
        except Exception as e:
            print(e)
    
    def progress_hook(self, d):
        if self.completed or self.should_stop:
            return
        
        curr = time.time()
        if curr - self.last_update < 3:
            return
        self.last_update = curr
        
        if d['status'] == 'downloading':
            try:
                downloaded_mb = d.get('downloaded_bytes', 0) / (1024 * 1024)
                total_mb = d.get('total_bytes', d.get('total_bytes_estimate', 0)) / (1024 * 1024)
                if downloaded_mb > self.max_size_mb:
                    # print(f"stopping")
                    self.stop_download()
                    
                    error_emb = discord.Embed(
                        title="Download Stopped",
                        description=f"**{self.info['title']}**\n\nDownload stopped because size ({total_mb:.1f}MB) exceeds your quota limit ({self.max_size_mb:.1f}MB).",
                        color=discord.Color.random()
                    )
                    try:
                        self.loop.call_soon_threadsafe(lambda: asyncio.create_task(self.update(error_emb)))
                    except:
                        pass
                    return
                
                if 'total_bytes' in d:
                    progress = (d['downloaded_bytes'] / d['total_bytes']) * 100
                elif 'total_bytes_estimate' in d:
                    progress = (d['downloaded_bytes'] / d['total_bytes_estimate']) * 100
                else:
                    progress = 0
                    
                if progress >= 99:
                    return
                
                speed = d.get('speed', 0)
                speed = speed / (1024 * 1024) if speed else 0
                eta = d.get('eta', 0)
                
                emb = discord.Embed(
                    title="Downloading...",
                    description=f"**{self.info['title']}**",
                    color=discord.Color.random()
                )
                
                progress_bar = downloading_bar(progress)
                emb.add_field(name="Progress", value=f"```{progress_bar}```", inline=False)
                
                if total_mb > 0:
                    emb.add_field(name="Size", value=f"{downloaded_mb:.1f} MB / {total_mb:.1f} MB", inline=True)
                else:
                    emb.add_field(name="Downloaded", value=f"{downloaded_mb:.1f} MB", inline=True)
                
                if downloaded_mb > self.max_size_mb * 0.8:
                    emb.add_field(name="Warning", value=f"Close to quota limit ({self.max_size_mb:.1f}MB)", inline=True)
                
                if speed > 0:
                    emb.add_field(name="Speed", value=f"{speed:.1f} MB/s", inline=True)
                
                if eta and eta > 0:
                    eta_str = f"{eta//60}m {eta%60}s" if eta > 60 else f"{eta}s"
                    emb.add_field(name="ETA", value=eta_str, inline=True)
                    
                emb.set_image(url=self.info['thumbnail'])
                try:
                    self.loop.call_soon_threadsafe(lambda: asyncio.create_task(self.update(emb)))
                except:
                    pass
                
            except Exception as e:
                print(f"Progress hook error: {e}")
        
        elif d['status'] == 'finished':
            if not self.size_exceeded:
                self.completed = True
    
    async def update(self, embed):
        try:
            if not self.completed or embed.title == "❌ Download Stopped":
                await self.interaction.edit_original_response(embed=embed, view=None)
        except Exception as e:
            print(f"Update error: {e}")



def download_fs(url, type, format_id, progress_tracker, info, max_size_mb=None):
    try:
        if max_size_mb is None:
            max_size_mb = LIMIT*1024
        elif max_size_mb > 1024:
            max_size_mb = 1024
        
        if not os.path.exists('downloads'):
            os.makedirs('downloads')
        curr = time.time()
        if os.path.exists('downloads'):
            for filename in os.listdir('downloads'):
                file_path = os.path.join('downloads', filename)
                if os.path.isfile(file_path):
                    if curr - os.path.getctime(file_path) > 3600:
                        try:
                            os.remove(file_path)
                            if file_path in file_created:
                                del file_created[file_path]
                        except:
                            pass
        timestamp = int(curr)
        def killable_progress_hook(d):
            if progress_tracker.should_stop:
                raise Exception("Download stopped due to size limit")
            progress_tracker.progress_hook(d)
        
        base_opts = {
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'referer': 'https://www.youtube.com/',
            'headers': {
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Sec-Fetch-User': '?1',
                'Cache-Control': 'max-age=0',
            },
            'progress_hooks': [killable_progress_hook],
            'ignoreerrors': False,
            'no_warnings': True,
            'quiet': False,
        }
        
        if type == "audio":
            ydl_opts = {
                **base_opts,
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
                'outtmpl': f'downloads/{timestamp}_%(title)s_audio.%(ext)s',
                'extractaudio': True,
                'audioformat': 'mp3',
            }
            ext = '.mp3'
        else:
            ydl_opts = {
                **base_opts,
                'format': format_id,
                'outtmpl': f'downloads/{timestamp}_%(title)s_%(height)sp.%(ext)s'
            }
            ext = '.mp4'
        cmd = [
            sys.executable, '-m', 'yt_dlp',
            '--user-agent', base_opts['user_agent'],
            '--referer', base_opts['referer'],
            '--no-warnings',
            '--progress',
            '--newline',
        ]
        if type == "audio":
            cmd.extend([
                '--format', 'bestaudio/best',
                '--extract-audio',
                '--audio-format', 'mp3',
                '--audio-quality', '192',
                '--output', f'downloads/{timestamp}_%(title)s_audio.%(ext)s'
            ])
        else:
            cmd.extend([
                '--format', format_id,
                '--output', f'downloads/{timestamp}_%(title)s_%(height)sp.%(ext)s'
            ])
        
        cmd.append(url)
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            bufsize=1
        )
        progress_tracker.set_process(process)
        downloaded_mb = 0
        while True:
            if progress_tracker.should_stop:
                # print("stopped")
                break
                
            output = process.stdout.readline()
            if output == '' and process.poll() is not None:
                break
                
            if output:
                if '[download]' in output and '%' in output:
                    try:
                        if 'MiB' in output or 'MB' in output:
                            parts = output.split()
                            for i, part in enumerate(parts):
                                if 'MiB' in part or 'MB' in part:
                                    size_str = part.replace('MiB', '').replace('MB', '')
                                    downloaded_mb = float(size_str)
                                    if downloaded_mb > max_size_mb:
                                        # print(f"quota exceeded")
                                        progress_tracker.stop_download()
                                        break
                                    break
                    except:
                        pass
        process.wait()
        
        if progress_tracker.size_exceeded:
            try:
                if os.path.exists('downloads'):
                    for f in os.listdir('downloads'):
                        if (f.startswith(str(timestamp)) and 
                            (f.endswith('.part') or
                             f.endswith('.ytdl') or
                             f.endswith('.temp') or
                             '.part-Frag' in f)):
                            try:
                                os.remove(os.path.join('downloads', f))
                                print(f)
                            except:
                                pass
            except:
                pass
            
            return {
                'success': False,
                'error': f"Download stopped: File size exceeded quota limit ({max_size_mb}MB)"
            }
        if process.returncode != 0:
            stderr = process.stderr.read()
            if progress_tracker.should_stop:
                return {
                    'success': False,
                    'error': f"Download stopped: File size exceeded quota limit ({max_size_mb}MB)"
                }
            else:
                return {
                    'success': False,
                    'error': f"Download failed: {stderr}"
                }
        
        progress_tracker.completed = True
        file_path = None
        if os.path.exists('downloads'):
            files = [f for f in os.listdir('downloads') if f.startswith(str(timestamp)) and f.endswith(ext)]
            if files:
                files.sort(key=lambda x: os.path.getctime(os.path.join('downloads', x)), reverse=True)
                filename = files[0]
                file_path = os.path.join('downloads', filename)
        
        if not file_path or not os.path.exists(file_path):
            return {
                'success': False,
                'error': f"Download failed: File not found after download"
            }
        actual_size_mb = os.path.getsize(file_path) / (1024 * 1024)
        if max_size_mb and actual_size_mb > max_size_mb:
            try:
                os.remove(file_path)
            except:
                pass
            return {
                'success': False,
                'error': f"File too large! Downloaded file is {actual_size_mb:.1f}MB, which exceeds your remaining quota of {max_size_mb:.1f}MB."
            }
        
        file_created[file_path] = time.time()
        encoded = urllib.parse.quote(filename)
        download_url = f"http://localhost:5000/downloads/{encoded}"
        
        return {
            'success': True,
            'filename': filename,
            'file_path': file_path,
            'download_url': download_url,
            'size': actual_size_mb
        }
        
    except Exception as e:
        progress_tracker.completed = True
        return {
            'success': False,
            'error': str(e)
        }

def get_height_limit(max_size_mb):
    if max_size_mb < 50:
        return 480
    elif max_size_mb < 200:
        return 720
    elif max_size_mb < 500:
        return 1080
    else:
        return 2160

async def download_yt(url, type, format_id, interaction, info, send_dm=False, max_size_mb=None):
    try:
        progress_tracker = ProgressTracker(interaction, info, max_size_mb)
        emb = discord.Embed(
            title="Starting Download...",
            description=f"**{info['title']}**",
            color=discord.Color.random()
        )
        emb.add_field(name="Status", value="Preparing download... This may take a moment.", inline=False)
        emb.set_image(url=info['thumbnail'])
        await interaction.edit_original_response(embed=emb, view=None)
        
        loop = asyncio.get_event_loop()
        download_task = loop.run_in_executor(
            None, download_fs, url, type, format_id, progress_tracker, info, max_size_mb
        )
        async def progress_updater():
            last_status_update = time.time()
            while not download_task.done() and not progress_tracker.completed:
                await asyncio.sleep(5)
                if time.time() - last_status_update > 10:
                    try:
                        status_emb = discord.Embed(
                            title="Download in Progress...",
                            description=f"**{info['title']}**",
                            color=discord.Color.random()
                        )
                        status_emb.add_field(name="Status", value="Download is running... Please wait.", inline=False)
                        status_emb.set_image(url=info['thumbnail'])
                        await interaction.edit_original_response(embed=status_emb, view=None)
                        last_status_update = time.time()
                    except:
                        pass
        updater_task = asyncio.create_task(progress_updater())
        result = await download_task
        updater_task.cancel()
        await asyncio.sleep(1)
        
        if result['success']:
            save(interaction.user.id, result['size'])
            quality = format_id if type == "video" else "audio"
            add_history(interaction.user.id, info['title'], url, result['size'], type, quality)
            
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
            stats = get_stats(interaction.user.id)
            day = datetime.now(timezone.utc).date().isoformat()
            used = stats['usedtoday'].get(day, {'downloads': 0, 'size_mb': 0.0})
            used_gb = used['size_mb'] / 1024
            emb.add_field(name="Daily Usage", value=f"{used_gb:.2f}GB / {LIMIT}GB used", inline=False)
            emb.set_image(url=info['thumbnail'])
            if send_dm:
                try:
                    await interaction.user.send(embed=emb)
                    success_emb = discord.Embed(
                        title="Download Complete!",
                        description=f"**{info['title']}** has been downloaded and sent to your DMs!",
                        color=discord.Color.random()
                    )
                    await interaction.edit_original_response(embed=success_emb, view=None)
                except discord.Forbidden:
                    emb.add_field(name="Note", value="Couldn't send DM - please enable DMs from server members", inline=False)
                    await interaction.edit_original_response(embed=emb, view=None)
            else:
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
async def search(interaction: discord.Interaction, query: str, results: int = 5):
    await interaction.response.defer()

    if results > 10:
        results = 10
    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True,
            'playlist_items': f'1:{results}'
        }
        
        search_query = f"ytsearch{results}:{query}"
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            search_results = ydl.extract_info(search_query, download=False)
            
        if not search_results or 'entries' not in search_results:
            emb = discord.Embed(
                title="No Results",
                description=f"No videos found for: **{query}**",
                color=discord.Color.random()
            )
            await interaction.followup.send(embed=emb)
            return
        
        emb = discord.Embed(
            title="YouTube Search Results",
            description=f"Search: **{query}**",
            color=discord.Color.random()
        )
        
        view = discord.ui.View(timeout=300)
        
        for i, entry in enumerate(search_results['entries'][:results], 1):
            if entry:
                title = entry.get('title')
                duration = entry.get('duration')
                url = entry.get('url', f"https://youtube.com/watch?v={entry.get('id', '')}")
                
                # Fix the duration formatting to handle floats properly
                if duration:
                    duration = int(duration)  # Convert to int to avoid float formatting issues
                    duration_str = f"{duration//60}:{duration%60:02d}"
                else:
                    duration_str = "Unknown"
                
                emb.add_field(
                    name=f"{i}. {title[:60]}{'...' if len(title) > 60 else ''}",
                    value=f"Duration: {duration_str}\n[Watch on YouTube]({url})",
                    inline=False
                )
                download_btn = discord.ui.Button(
                    label=f"Download #{i}",
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"download_{i}"
                )
                
                def make_download_callback(video_url):
                    async def download_callback(button_interaction):
                        await button_interaction.response.defer()
                        await download_search(button_interaction, video_url)
                    return download_callback
                
                download_btn.callback = make_download_callback(url)
                view.add_item(download_btn)
        
        await interaction.followup.send(embed=emb, view=view)
        
    except Exception as e:
        emb = discord.Embed(
            title="Search Error",
            description= str(e),
            color=discord.Color.random()
        )
        await interaction.followup.send(embed=emb)

async def download_search(interaction, url):
    try:
        ydl_opts = {
            'quiet': True, 
            'no_warnings': True,
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'referer': 'https://www.youtube.com/',
            'headers': {
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Sec-Fetch-User': '?1',
                'Cache-Control': 'max-age=0',
            }
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            stats = get_stats(interaction.user.id)
            day = datetime.now(timezone.utc).date().isoformat()
            used = stats['usedtoday'].get(day, {'downloads': 0, 'size_mb': 0.0})
            mb_used = used['size_mb']
            gb_used = mb_used / 1024
            mb_limit = LIMIT * 1024
            mb_left = mb_limit - mb_used
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
                            'fps': fmt.get('fps', '')
                        })
            
            vid_fmt.sort(key=lambda x: x['height'], reverse=True)
            audio_can_download = mb_left > 5
            video_can_download = mb_left > 20
            duration = info.get('duration', 0)
            if duration:
                duration = int(duration)
                duration_formatted = f"{duration // 60}:{duration % 60:02d}"
            else:
                duration_formatted = "Unknown"
            
            emb = discord.Embed(
                title="Choose Download Type",
                description=f"**{info['title']}**\n Duration: {duration_formatted}",
                color=discord.Color.random()
            )
            remaining_gb = LIMIT - gb_used
            usage_text = f"Daily Usage: {gb_used:.2f}GB / {LIMIT}GB\nRemaining: {remaining_gb:.2f}GB"
            emb.add_field(name="Your quota", value=usage_text, inline=False)
            
            view = discord.ui.View(timeout=300)
            if audio_can_download:
                audio_button = discord.ui.Button(
                    label="Audio (MP3)", 
                    style=discord.ButtonStyle.primary
                )
                
                async def audio_callback(button):
                    await button.response.defer()
                    await download_yt(url, "audio", None, button, info, max_size_mb=mb_left)
                
                audio_button.callback = audio_callback
                view.add_item(audio_button)
                
                emb.add_field(name="Audio", value="MP3 format available", inline=False)
            else:
                emb.add_field(name="Audio", value="Not enough quota", inline=False)
            if video_can_download and vid_fmt:
                video_options = []
                
                for fmt in vid_fmt[:10]:
                    fps_text = f"@{fmt['fps']}fps" if fmt['fps'] else ""
                    video_options.append(f"{fmt['height']}p{fps_text}")

                video_text = "\n".join(video_options)
                if len(vid_fmt) > 10:
                    video_text += "\n... and more"
                emb.add_field(name="Available Videos", value=video_text, inline=False)
                video_button = discord.ui.Button(
                    label="Video", 
                    style=discord.ButtonStyle.primary
                )
                
                async def video_callback(button):
                    view = discord.ui.View(timeout=300)
                    
                    for fmt in vid_fmt[:10]:
                        fps_text = f" @{fmt['fps']}fps" if fmt['fps'] else ""
                        quality_btn = discord.ui.Button(
                            label=f"{fmt['height']}p{fps_text}",
                            style=discord.ButtonStyle.secondary
                        )
                        
                        def make_video_callback(format_id, height):
                            async def quality_callback(quality_interaction):
                                await quality_interaction.response.defer()
                                current_stats = get_stats(quality_interaction.user.id)
                                current_day = datetime.now(timezone.utc).date().isoformat()
                                current_used = current_stats['usedtoday'].get(current_day, {'downloads': 0, 'size_mb': 0.0})
                                current_remaining_mb = (LIMIT * 1024) - current_used['size_mb']
                                
                                emb = discord.Embed(
                                    title="Starting Download...",
                                    description=f"**{info['title']}**\nPreparing {height}p video download...",
                                    color=discord.Color.random()
                                )
                                await quality_interaction.edit_original_response(embed=emb, view=None)
                                await download_yt(url, "video", format_id, quality_interaction, info, max_size_mb=current_remaining_mb)
                            return quality_callback
                        
                        quality_btn.callback = make_video_callback(fmt['format_id'], fmt['height'])
                        view.add_item(quality_btn)
                    
                    back_btn = discord.ui.Button(label="← Back", style=discord.ButtonStyle.secondary)
                    async def back_callback(back_interaction):
                        await back_interaction.response.edit_message(embed=emb, view=view)
                    back_btn.callback = back_callback
                    view.add_item(back_btn)
                    
                    emb = discord.Embed(
                        title="Choose Video Quality",
                        description=f"**{info['title']}**\nSelect video quality:",
                        color=discord.Color.random()
                    )
                    await button.response.edit_message(embed=emb, view=view)
                
                video_button.callback = video_callback
                view.add_item(video_button)
            else:
                if not video_can_download:
                    emb.add_field(name="Video", value="Not enough quota", inline=False)
                else:
                    emb.add_field(name="Video", value="No video formats available", inline=False)
            
            emb.set_image(url=info['thumbnail'])
            await interaction.edit_original_response(embed=emb, view=view)
            
    except Exception as e:
        emb = discord.Embed(
            title="Error",
            description=str(e),
            color=discord.Color.random()
        )
        await interaction.edit_original_response(embed=emb, view=None)
@client.tree.command()
async def download(interaction: discord.Interaction, url: str):
    global last_download
    
    user_id = interaction.user.id
    now = time.time()
    
    if user_id in last_download:
        time_since_last = now - last_download[user_id]
        if time_since_last < COOLDOWN:
            remaining = COOLDOWN - time_since_last
            embed = discord.Embed(
                title="Rate Limited",
                description=f"Please wait {remaining:.1f} more seconds before downloading again.",
                color=0xFF6B35
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
    
    await interaction.response.defer()
    last_download[user_id] = now

    try:
        ydl_opts = {
            'quiet': True, 
            'no_warnings': True,
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'referer': 'https://www.youtube.com/',
            'headers': {
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Sec-Fetch-User': '?1',
                'Cache-Control': 'max-age=0',
            }
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            stats = get_stats(interaction.user.id)
            day = datetime.now(timezone.utc).date().isoformat()
            used = stats['usedtoday'].get(day, {'downloads': 0, 'size_mb': 0.0})
            mb_used = used['size_mb']
            gb_used = mb_used / 1024
            mb_limit = LIMIT * 1024
            mb_left = mb_limit - mb_used
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
                            'fps': fmt.get('fps', '')
                        })
            
            vid_fmt.sort(key=lambda x: x['height'], reverse=True)
            audio_can_download = mb_left > 5
            video_can_download = mb_left > 20

            emb = discord.Embed(
                title="Choose Download Type",
                description=f"**{info['title']}**\n Duration: {info.get('duration', 0) // 60}:{info.get('duration', 0) % 60:02d}",
                color=discord.Color.random()
            )
            remaining_gb = LIMIT - gb_used
            usage_text = f"Daily Usage: {gb_used:.2f}GB / {LIMIT}GB\nRemaining: {remaining_gb:.2f}GB"
            emb.add_field(name="Your quota", value=usage_text, inline=False)
            if audio_can_download:
                emb.add_field(name="Audio", value="MP3 format available", inline=False)
            else:
                emb.add_field(name="Audio", value="Not enough quota", inline=False)
            if video_can_download and vid_fmt:
                video_options = []
                
                for fmt in vid_fmt[:10]:
                    fps_text = f"@{fmt['fps']}fps" if fmt['fps'] else ""
                    video_options.append(f"{fmt['height']}p{fps_text}")

                video_text = "\n".join(video_options)
                if len(vid_fmt) > 10:
                    video_text += "\n... and more"
                emb.add_field(name="Available Videos", value=video_text, inline=False)
            else:
                if not video_can_download:
                    emb.add_field(name="Video", value="Not enough quota", inline=False)
                else:
                    emb.add_field(name="Video", value="No video formats available", inline=False)
            
            emb.set_image(url=info['thumbnail'])
            view = discord.ui.View(timeout=300)
            if audio_can_download:
                audio_button = discord.ui.Button(label="Audio (MP3)", style=discord.ButtonStyle.primary)
                
                async def audio_callback(button):
                    await button.response.defer()
                    await download_yt(url, "audio", None, button, info, max_size_mb=mb_left)
                
                audio_button.callback = audio_callback
                view.add_item(audio_button)
            if video_can_download and vid_fmt:
                video_button = discord.ui.Button(label="Video", style=discord.ButtonStyle.primary)
                
                async def video_callback(button):
                    view = discord.ui.View(timeout=300)
                    
                    for fmt in vid_fmt[:10]:
                        fps_text = f" @{fmt['fps']}fps" if fmt['fps'] else ""
                        quality_btn = discord.ui.Button(
                            label=f"{fmt['height']}p{fps_text}",
                            style=discord.ButtonStyle.secondary
                        )
                        
                        def make_video_callback(format_id, height):
                            async def quality_callback(quality_interaction):
                                await quality_interaction.response.defer()
                                current_stats = get_stats(quality_interaction.user.id)
                                current_day = datetime.now(timezone.utc).date().isoformat()
                                current_used = current_stats['usedtoday'].get(current_day, {'downloads': 0, 'size_mb': 0.0})
                                current_remaining_mb = (LIMIT * 1024) - current_used['size_mb']
                                
                                emb = discord.Embed(
                                    title="Starting Download...",
                                    description=f"**{info['title']}**\nPreparing {height}p video download...",
                                    color=discord.Color.random()
                                )
                                await quality_interaction.edit_original_response(embed=emb, view=None)
                                await download_yt(url, "video", format_id, quality_interaction, info, max_size_mb=current_remaining_mb)
                            return quality_callback
                        
                        quality_btn.callback = make_video_callback(fmt['format_id'], fmt['height'])
                        view.add_item(quality_btn)
                    
                    back_btn = discord.ui.Button(label="← Back", style=discord.ButtonStyle.secondary)
                    async def back_callback(back_interaction):
                        await back_interaction.response.edit_message(embed=emb, view=view)
                    back_btn.callback = back_callback
                    view.add_item(back_btn)
                    
                    emb = discord.Embed(
                        title="Choose Video Quality",
                        description=f"**{info['title']}**\nSelect video quality:",
                        color=discord.Color.random()
                    )
                    await button.response.edit_message(embed=emb, view=view)
                
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
async def stats(interaction: discord.Interaction):
    stats = get_stats(interaction.user.id)
    day = datetime.now(timezone.utc).date().isoformat()
    used = stats['usedtoday'].get(day, {'downloads': 0, 'size_mb': 0.0})
    
    used_gb = used['size_mb'] / 1024
    remaining_gb = LIMIT - used_gb
    percentage = (used_gb / LIMIT) * 100
    
    emb = discord.Embed(
        title="Your Daily Quota Usage",
        color=discord.Color.random()
    )
    
    filled = int(20 * percentage / 100)
    bar = '█' * filled + '░' * (20 - filled)
    progress_text = f"```[{bar}] {percentage:.1f}%```"
    
    emb.add_field(name="Daily Progress", value=progress_text, inline=False)
    emb.add_field(name="Used Today", value=f"{used_gb:.2f}GB", inline=True)
    emb.add_field(name="Remaining", value=f"{remaining_gb:.2f}GB", inline=True)
    emb.add_field(name="Downloads Today", value=str(used['downloads']), inline=True)
    emb.add_field(name="Total Downloads", value=str(stats['total']), inline=True)
    emb.add_field(name="Total Downloaded", value=f"{stats['size_total']:.2f}GB", inline=True)
    emb.add_field(
        name="Current Limits", 
        value=f"• {LIMIT}GB daily bandwidth\n• No single file size limit\n• All video qualities available", 
        inline=False
    )
    
    emb.add_field(name="Reset Time", value="Bandwidth resets daily at midnight UTC", inline=False)
    
    await interaction.response.send_message(embed=emb)

@client.tree.command()
async def show_limits(interaction: discord.Interaction):
    emb = discord.Embed(
        title="Download Limits & Information",
        description="Current system limits:",
        color=discord.Color.random()
    )
    
    limits_text = f"""
    **Daily Quota:** {LIMIT}GB per day
    **Single File Size:** No limit! Download files of any size
    **Video Quality:** All qualities available (8K, 4K, 1080p, 720p, etc.)
    **Audio Format:** MP3
    **Reset Time:** Daily at midnight UTC
    """
    
    emb.add_field(name="Limits", value=limits_text, inline=False)
    
    examples_text = f"""
    **Examples:**
    • 10 videos of 100MB each = 1GB (full daily limit)
    • 1 video of 800MB + smaller videos = 1GB total
    • 1 massive 1GB video = full daily limit
    • Mix any file sizes as long as total ≤ 1GB per day
    """
    
    emb.add_field(name="How it Works", value=examples_text, inline=False)
    emb.add_field(name="File Retention", value="Downloaded files are automatically deleted after 24 hours", inline=False)
    
    await interaction.response.send_message(embed=emb)

client.run(os.getenv('TOKEN'))