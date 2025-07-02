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
            for filename in os.listdir('downloads'):
                file_path = os.path.join('downloads', filename)
                if os.path.isfile(file_path):
                    file_created[file_path] = os.path.getctime(file_path)
            for file_path, created_time in list(file_created.items()):
                if curr - created_time > 86400:
                    os.remove(file_path)
                    del file_created[file_path]
        except Exception as e:
            print(e)
        await asyncio.sleep(3600)

@client.event
async def on_ready():
    print(f'Logged in as {client.user}')
    await client.tree.sync()
    client.loop.create_task(clean())

@client.tree.command()
async def download(interaction: discord.Interaction, url: str):
    await interaction.response.defer()
    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'outtmpl': 'downloads/%(title)s.%(ext)s',
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            filename = os.path.splitext(filename)[0] + '.mp3'
            filename = os.path.basename(filename)
            file_created[os.path.join('downloads', filename)] = time.time()
            encoded = urllib.parse.quote(filename)
            hosted_url = f"http://localhost:5000/downloads/{encoded}"

            emb = discord.Embed(
                title="Download Complete!",
                description=f"**{info['title']}**",
                color=discord.Color.random()
            )
            emb.add_field(name="Hosted URL", value=hosted_url, inline=False)
            emb.add_field(name="File", value=filename, inline=False)

            await interaction.followup.send(embed=emb)
    except Exception as e:
        await interaction.followup.send(e)

client.run(os.getenv('TOKEN'))