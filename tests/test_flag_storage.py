import hashlib
import io
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
from PIL import Image
from test_regressions import DatabaseFixture, interaction
import config
import db
from flags import flag_url, flag_text
from flag_storage import save_flag, image_png, MAX_BYTES
from cogs.nations import NationCog
from keep_alive import app


def png(color='red', size=(30, 20)):
    out=io.BytesIO()
    Image.new('RGBA',size,color).save(out,'PNG')
    return out.getvalue()


class SourceTests(unittest.TestCase):
    def test_file_pages_and_wrapped_urls(self):
        source='https://github.com/u/repo/blob/main/Flag name.png?raw=true'
        expected='https://raw.githubusercontent.com/u/repo/main/Flag%20name.png'
        for value in (source,'<'+source+'>','[flag]('+source+')'):
            self.assertEqual(flag_url(value),expected)
            self.assertEqual(flag_text(value),'')
        wiki='https://commons.wikimedia.org/wiki/Special:Redirect/file/Flag.svg?width=960'
        for source in ('https://commons.wikimedia.org/wiki/File:Flag.svg','https://upload.wikimedia.org/wikipedia/commons/a/ab/Flag.svg'):
            self.assertEqual(flag_url(source),wiki)
        signed='https://cdn.discordapp.com/attachments/123/456/flag.png?ex=abc&is=def&hm=123'
        self.assertEqual(flag_url(signed),signed)
        self.assertEqual(flag_url('🇵🇱'),'https://flagcdn.com/w320/pl.png')
        self.assertIn('.gif?',flag_url('<a:flag:123456789012345678>'))

    def test_invalid_sources_never_become_image_urls(self):
        for source in ('','javascript:alert(1)','file:///tmp/x.png','https://user:pass@example.com/f.png','https://[broken'):
            self.assertIsNone(flag_url(source))
        with patch.dict('os.environ',{'RENDER_EXTERNAL_URL':'','FLAG_PUBLIC_BASE_URL':''}):
            self.assertIsNone(flag_url('flag:'+'a'*64))
            self.assertEqual(flag_text('flag:'+'a'*64),'')

    def test_valid_pixels_white_and_alpha_are_preserved(self):
        for color in ('white','red',(20,30,40,128)):
            with Image.open(io.BytesIO(image_png(png(color)))) as image:
                self.assertEqual(image.getpixel((0,0)),Image.new('RGBA',(1,1),color).getpixel((0,0)))
        with Image.open(io.BytesIO(image_png(png(size=(1600,800))))) as image:
            self.assertEqual(image.size,(1024,512))
        for data in (b'<html>page</html>',b'<svg></svg>',b'',b'x'*(MAX_BYTES+1),png((0,0,0,0)),png(size=(2001,2000))):
            with self.assertRaises(ValueError):image_png(data)


class StorageTests(DatabaseFixture,unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        env=patch.dict('os.environ',{'RENDER_EXTERNAL_URL':'https://game.onrender.com','FLAG_PUBLIC_BASE_URL':''})
        env.start();self.addCleanup(env.stop)

    def saved(self):
        with db.cursor() as c:
            c.execute('SELECT flag FROM nations WHERE id=1');return c.fetchone()['flag']

    async def test_database_upload_public_png_and_cache_refresh(self):
        marker=save_flag(1,1,data=png())
        self.assertEqual(marker,self.saved())
        self.assertEqual(flag_url(marker),'https://game.onrender.com/flags/'+marker[5:]+'.png')
        client=app.test_client()
        response=client.get('/flags/'+marker[5:]+'.png')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.mimetype,'image/png')
        self.assertEqual(hashlib.sha256(response.data).hexdigest(),marker[5:])
        self.assertEqual(client.get('/flags/'+marker[5:]+'.png',headers={'If-None-Match':response.headers['ETag']}).status_code,304)
        self.assertEqual(client.get('/flags/not-a-hash.png').status_code,404)
        self.assertEqual(client.get('/flags/'+'0'*64+'.png').status_code,404)
        self.assertEqual(save_flag(2,2,data=png()),marker)
        self.assertNotEqual(save_flag(1,1,data=png('blue')),marker)

    async def test_authorization_bad_data_and_missing_host_preserve_flag(self):
        save_flag(1,1,source='🇵🇱')
        for kwargs in ({'data':b'bad'},{'source':'plain text'},{'source':'https://example.com/f.svg'},{'source':'flag:'+'a'*64}):
            with self.assertRaises(ValueError):save_flag(1,1,**kwargs)
            self.assertEqual(self.saved(),'🇵🇱')
        with self.assertRaises(ValueError):save_flag(1,2,data=png())
        with patch.dict('os.environ',{'RENDER_EXTERNAL_URL':'','FLAG_PUBLIC_BASE_URL':''}):
            with self.assertRaises(ValueError):save_flag(1,1,data=png())
        self.assertEqual(self.saved(),'🇵🇱')
        save_flag(1,999,gm=True,source='🇬🇧')
        self.assertEqual(self.saved(),'🇬🇧')

    async def test_command_upload_rejects_other_owner_and_changed_owner(self):
        file=NS(size=100,read=AsyncMock(return_value=png()))
        request=interaction(1)
        await NationCog.flag.callback(None,request,file=file)
        self.assertEqual(request.followup.send.call_args.kwargs['embed'].thumbnail.url,flag_url(self.saved()))
        file.read.reset_mock()
        await NationCog.flag.callback(None,interaction(2),nation='A',file=file)
        file.read.assert_not_awaited()
        async def download():
            with db.cursor() as c:c.execute("UPDATE nations SET owner_id='3' WHERE id=1")
            return png('blue')
        file.read=AsyncMock(side_effect=download)
        before=self.saved()
        await NationCog.flag.callback(None,interaction(1),file=file)
        self.assertEqual(self.saved(),before)
        gm=interaction(999,[NS(id=42,name=config.GM_ROLE_NAME)])
        await NationCog.flag.callback(None,gm,nation='A',flag='🇬🇧')
        self.assertEqual(self.saved(),'🇬🇧')
