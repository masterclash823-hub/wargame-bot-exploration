"""Remember closed DMs instead of repeating forbidden Discord requests."""
import time
import discord
from discord_sync import get,put


async def recipient(bot,uid,member=None):
    if float(get(f'dm-blocked:{uid}') or 0)>time.time():raise ValueError('DM temporarily unavailable; use the player panel.')
    cached=getattr(bot,'get_user',lambda _:None)(int(uid))
    return member or cached or await bot.fetch_user(int(uid))


async def send(uid,callback,*args,**kwargs):
    try:return await callback(*args,**kwargs)
    except discord.Forbidden:
        put(f'dm-blocked:{uid}',time.time()+3600)
        raise
