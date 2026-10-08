"""Sync changed command payloads only; retain discord.py's native rate limiter."""
import asyncio
import hashlib
import json
import time
import discord
import db

_lock=asyncio.Lock()


def get(key):
    with db.cursor() as c:
        c.execute('SELECT value FROM game_config WHERE key=?',(key,));r=c.fetchone()
        return r['value'] if r else None


def put(key,value):
    with db.cursor() as c:c.execute('INSERT INTO game_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,str(value)))


async def sync(bot,tree,force=False):
    async with _lock:
        for guild in bot.guilds:
            tree.copy_global_to(guild=guild)
            payload=[await cmd.get_translated_payload(tree,tree.translator) for cmd in tree.get_commands(guild=guild)]
            digest=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            key=f'command-sync:{bot.application_id}:{guild.id}'
            if (not force and get(key)==digest) or float(get(key+':retry') or 0)>time.time():continue
            try:await tree.sync(guild=guild)
            except discord.HTTPException as exc:
                # Do not repeatedly send a forbidden, invalid or failed payload on reconnect.
                put(key+':retry',time.time()+900)
                print(f'[SYNC] Paused guild {guild.id}: HTTP {exc.status}',flush=True)
                continue
            put(key,digest);put(key+':retry',0)
        key=f'global-cleanup:{bot.application_id}'
        if bot.guilds and get(key)!='1' and float(get(key+':retry') or 0)<=time.time():
            try:await bot.http.bulk_upsert_global_commands(bot.application_id,payload=[])
            except discord.HTTPException as exc:
                put(key+':retry',time.time()+900)
                print(f'[SYNC] Global cleanup paused: HTTP {exc.status}',flush=True)
            else:put(key,'1')
