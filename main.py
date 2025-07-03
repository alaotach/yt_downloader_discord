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
import os
import time
import concurrent.futures

load_dotenv()

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
                        vid_fmt.append({'height': height,'format_id': fmt.get('format_id'),'ext': fmt.get('ext', 'mp4'),'fps': fmt.get('fps', ''),'filesize': fmt.get('filesize', 0)})
            
            vid_fmt.sort(key=lambda x: x['height'], reverse=True)
            emb = discord.Embed(
                title="Choose Download Type",
                description=f"**{info['title']}**\n Duration: {info.get('duration', 0) // 60}:{info.get('duration', 0) % 60:02d}",
                color=discord.Color.random()
            )
            emb.add_field(name="Audio", value="Downlod MP3", inline=False)
            if vid_fmt:
                quality = ", ".join([f"{fmt['height']}p" for fmt in vid_fmt[:5]])
                emb.add_field(name="Available Videos", value=quality + ("..." if len(vid_fmt) > 5 else ""), inline=False)
            
            
            emb.set_image(url=info['thumbnail'])
            view = discord.ui.View(timeout=300)
            audio_button = discord.ui.Button(label="Audio", style=discord.ButtonStyle.primary)
            
            async def audio_callback(button):
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
            if vid_fmt:
                video_button = discord.ui.Button(label="Video",style=discord.ButtonStyle.primary)
                async def video_callback(button):
                    quality_view = discord.ui.View(timeout=300)
                    for i, fmt in enumerate(vid_fmt[:5]):
                        fps_text = f" @{fmt['fps']}fps" if fmt['fps'] else ""
                        quality_btn = discord.ui.Button(
                            label=f"{fmt['height']}p{fps_text}",
                            style=discord.ButtonStyle.secondary
                        )
                        
                        def make_video_callback(format_id, height):
                            async def quality_callback(interaction):
                                await interaction.response.defer()
                                emb = discord.Embed(
                                    title="Starting Download....",
                                    description=f"**{info['title']}**\nPreparing {height}p video download....",
                                    color=discord.Color.random()
                                )
                                await interaction.edit_original_response(embed=emb, view=None)
                                await download_yt(url, "video", format_id, interaction, info)
                            return quality_callback
                        
                        quality_btn.callback = make_video_callback(fmt['format_id'], fmt['height'])
                        quality_view.add_item(quality_btn)
                    back_btn = discord.ui.Button(label="← Back", style=discord.ButtonStyle.secondary)
                    async def back_callback(interaction):
                        await interaction.response.edit_message(embed=emb, view=view)
                    back_btn.callback = back_callback
                    quality_view.add_item(back_btn)
                    
                    emb = discord.Embed(
                        title="Choose Video Quality",
                        description=f"**{info['title']}**\nSelect video quality:",
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

client.run(os.getenv('TOKEN'))