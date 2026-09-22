import asyncio
import io
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,Mock,patch

from PIL import Image
import event_images as images


def picture(color='red'):
    out=io.BytesIO();Image.new('RGB',(12,8),color).save(out,format='PNG');return out.getvalue()


def candidate(url='https://upload.wikimedia.org/example.png'):
    return dict(url=url,source='https://commons.wikimedia.org/wiki/File:Example.png',credit='Painter',license='Public domain')


def context(value):
    result=AsyncMock();result.__aenter__.return_value=value;return result


class SearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_broken_first_image_tries_next_candidate_and_validates_download(self):
        data=picture();first=candidate();second=candidate('https://upload.wikimedia.org/good.png')
        with patch.object(images,'_search',AsyncMock(return_value=[first,second])),patch.object(images,'_download',
                AsyncMock(side_effect=[ValueError('HTML instead of image'),(data,'png')])) as download:
            result=await images.find_event_image('Pożar miasta')
        self.assertEqual(result['data'],data);self.assertEqual(result['url'],second['url'])
        self.assertEqual(download.await_count,2)

    async def test_failed_commons_uses_museum_and_does_not_send_private_prose(self):
        seen=[]
        async def search(session,provider,query):
            seen.append((provider,query))
            if provider=='commons':raise asyncio.TimeoutError()
            return [candidate('https://www.artic.edu/iiif/2/abc/full/843,/0/default.jpg')]
        with patch.object(images,'_search',side_effect=search),patch.object(images,'_download',AsyncMock(return_value=(picture(),'png'))):
            self.assertIsNotNone(await images.find_event_image('Tajna narada gracza, pożar spichlerza.'))
        self.assertEqual(seen,[('commons','historic city fire'),('museum','fire')])

    async def test_provider_timeout_leaves_budget_for_backup_and_failure_is_not_cached(self):
        async def search(session,provider,query):
            if provider=='commons':await asyncio.Future()
            return [candidate()]
        with patch.object(images,'PROVIDER_TIMEOUT',.01),patch.object(images,'_search',side_effect=search),\
                patch.object(images,'_download',AsyncMock(return_value=(picture(),'png'))):
            self.assertIsNotNone(await images.find_event_image('Battle'))
        with patch.object(images,'_search',AsyncMock(return_value=[])):
            self.assertIsNone(await images.find_event_image('Battle','missing'))
        with patch.object(images,'_search',AsyncMock(return_value=[candidate()])),\
                patch.object(images,'_download',AsyncMock(return_value=(picture(),'png'))):
            self.assertIsNotNone(await images.find_event_image('Battle','new query'))

    async def test_real_request_shape_and_public_domain_museum_filter(self):
        rows=[dict(id=1,title='Fire',image_id='abc-123',artist_display='Artist',is_public_domain=True),
              dict(id=2,title='Other',image_id='abc-456',is_public_domain=False)]
        reply=NS(status=200,raise_for_status=Mock(),json=AsyncMock(return_value={
            'data':rows,'config':{'iiif_url':'https://www.artic.edu/iiif/2'}}))
        session=NS(get=Mock(return_value=context(reply)))
        found=await images._search(session,'museum','fire')
        self.assertEqual(len(found),1)
        self.assertEqual(found[0]['source'],'https://www.artic.edu/artworks/1')
        self.assertEqual(found[0]['url'],'https://www.artic.edu/iiif/2/abc-123/full/843,/0/default.jpg')
        args=session.get.call_args.kwargs
        self.assertEqual(args['params']['query[term][is_public_domain]'],'true')
        self.assertIn('is_public_domain',args['params']['fields'])
        rows[0]['title']='AI-generated fire'
        self.assertEqual(list(images.museum_images({'data':rows})),[])

    async def test_commons_uses_rendered_mime_and_handles_both_response_formats(self):
        page=dict(index=1,title='File:Fire.tif',imageinfo=[dict(mime='image/tiff',thumbmime='image/jpeg',
            thumburl='https://upload.wikimedia.org/thumb.jpg',descriptionurl=candidate()['source'],
            extmetadata={'LicenseShortName':{'value':'Public domain'}})])
        for pages in ([page],{'1':page}):
            self.assertIsNotNone(images.pick_image({'query':{'pages':pages}}))
        page['imageinfo'][0]['thumburl']='https://upload.wikimedia.org.attacker.test/image.jpg'
        self.assertIsNone(images.pick_image({'query':{'pages':[page]}}))
        self.assertNotIn('flood',images.search_topic('Powód konfliktu jest tajny'))

    async def test_download_rejects_html_oversize_and_redirect_to_private_host(self):
        async def chunks(data):yield data
        def response(data):return NS(status=200,content_length=len(data),raise_for_status=Mock(),
                                    content=NS(iter_chunked=lambda _:chunks(data)))
        session=NS(get=Mock(return_value=context(response(picture()))))
        data,extension=await images._download(session,candidate()['url'])
        self.assertEqual(data,picture());self.assertEqual(extension,'png')
        session.get.return_value=context(response(b'<html>Error</html>'))
        with self.assertRaises(ValueError):await images._download(session,candidate()['url'])
        session.get.return_value=context(response(b'x'*100))
        with patch.object(images,'MAX_BYTES',99),self.assertRaises(ValueError):await images._download(session,candidate()['url'])
        session.get.reset_mock()
        session.get.return_value=context(NS(status=302,headers={'Location':'http://127.0.0.1/private'}))
        with self.assertRaises(ValueError):await images._download(session,candidate()['url'])
        self.assertEqual(session.get.call_count,1)


if __name__=='__main__':unittest.main()
