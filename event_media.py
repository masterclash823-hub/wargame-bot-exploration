"""Durable event attachments, independent of flags, hosts and decision versions."""
import base64
import io
import json

import discord
import db
from event_images import validate_image


def save(c,event_id,image):
    metadata={k:v for k,v in image.items() if k not in ('data','extension','attachment')}
    data=image.get('data')
    filename=f'event-{event_id}.{validate_image(data)}' if data else ''
    c.execute('INSERT INTO event_media(event_id,image_json,data_base64,filename) VALUES(?,?,?,?) '
              'ON CONFLICT(event_id) DO UPDATE SET image_json=excluded.image_json,data_base64=excluded.data_base64,filename=excluded.filename',
              (event_id,json.dumps(metadata,ensure_ascii=False),base64.b64encode(data).decode('ascii') if data else '',filename))
    return metadata


def metadata(event_id):
    with db.cursor() as c:
        c.execute('SELECT image_json,filename FROM event_media WHERE event_id=?',(event_id,));row=c.fetchone()
    if not row:return None
    image=json.loads(row['image_json'])
    if row['filename']:image['attachment']=row['filename']
    return image or None


def attachment(event_id):
    with db.cursor() as c:
        c.execute('SELECT filename,data_base64 FROM event_media WHERE event_id=?',(event_id,));row=c.fetchone()
    if row and row['filename'] and row['data_base64']:
        return discord.File(io.BytesIO(base64.b64decode(row['data_base64'])),filename=row['filename'])


def remember_message(event_id,message_id):
    if type(message_id) is not int:return
    with db.cursor() as c:
        c.execute('INSERT INTO event_media(event_id,public_message_id) VALUES(?,?) '
                  'ON CONFLICT(event_id) DO UPDATE SET public_message_id=excluded.public_message_id',(event_id,str(message_id)))


def message_id(event_id):
    with db.cursor() as c:
        c.execute('SELECT public_message_id FROM event_media WHERE event_id=?',(event_id,));row=c.fetchone()
    return int(row['public_message_id']) if row and row['public_message_id'] else None
