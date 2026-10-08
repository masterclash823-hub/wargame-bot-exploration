"""Persistent server settings; callers check the GM role at the UI boundary."""
import db
from world_service import world_lock,tr


def setting(guild,key,default):
    with db.cursor() as c:
        c.execute('SELECT value FROM game_config WHERE key=?',(f'setup:{guild}:{key}',))
        row=c.fetchone()
        return row['value'] if row else default


def configure(guild,key,value):
    if not guild:raise ValueError(tr('Użyj na serwerze gry.','Use this in the game server.'))
    if key=='budget':
        if type(value)!=int or not 1<=value<=60:raise ValueError(tr('Budżet: od 1 do 60 punktów.','Budget: 1 to 60 points.'))
    elif key=='map':
        if type(value)!=bool:raise ValueError('Invalid map setting')
        value=int(value)
    else:raise ValueError('Invalid setting')
    with db.atomic() as c:
        world_lock(c)
        c.execute('INSERT INTO game_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                  (f'setup:{guild}:{key}',str(value)))


def budget(guild):return int(setting(guild,'budget','35'))
def map_available(guild):return bool(guild) and setting(guild,'map','0')=='1'
