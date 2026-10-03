import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch

import db
import i18n
import terraforming as service
import terraforming_ui as ui
from cogs.provinces import ProvincesCog
from cogs.panel import PlayerPanel
from nation_access import set_coop
from test_regressions import interaction
from test_terraforming import TerraformFixture


def component(uid=1):
    result=interaction(uid)
    result.edit_original_response=AsyncMock()
    return result


class TerraformUITests(TerraformFixture,unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        # Exercise UI callbacks deterministically; transaction and concurrent
        # worker behaviour are covered by service tests with real connections.
        async def worker(fn,*args,**kwargs):return fn(*args,**kwargs)
        self.worker=patch('terraforming_ui.asyncio.to_thread',side_effect=worker)
        self.worker.start();self.addCleanup(self.worker.stop)

    def view(self,cell=None,uid=1):
        return ui.TerraformView(uid,service.preview(uid,cell))

    async def test_command_defers_before_query_and_opens_private_read_only_dropdown(self):
        before=self.balances();i=component()
        original=service.preview
        def load(*args,**kwargs):
            i.response.defer.assert_awaited_once_with(ephemeral=True)
            return original(*args,**kwargs)
        with patch.object(service,'preview',side_effect=load):
            await ProvincesCog.province_terraform.callback(None,i)
        view=i.followup.send.call_args.kwargs['view']
        self.assertTrue(i.followup.send.call_args.kwargs['ephemeral'])
        self.assertEqual([option.value for option in view.children[0].options],['10','11'])
        self.assertTrue(view.confirm_button.disabled)
        self.assertEqual(self.balances(),before)

    async def test_selection_shows_polish_quote_then_confirm_pays_exactly_once(self):
        with i18n.using_language('pl'):view=self.view()
        before=self.balances();select=view.children[0];select._values=['10']
        i=component();await select.callback(i)
        selected=i.edit_original_response.call_args.kwargs['view']
        details=str(selected.embed.to_dict())
        for text in ('Zalesianie','800','6 mies.','Koszt jednorazowy','Wymagania','12 mies.','bez zwrotu'):
            self.assertIn(text,details)
        self.assertEqual(before,self.balances())
        self.assertFalse(selected.confirm_button.disabled)
        confirm=component()
        await selected.confirm_button.callback(confirm)
        self.assertIn('Rozpoczęto terraformację #10',confirm.edit_original_response.call_args.kwargs['content'])
        await selected.confirm_button.callback(component())
        self.assertEqual(len(self.projects()),1)
        self.assertEqual(self.balances()[0]['treasury'],19200)
        self.assertEqual(self.province()['biome'],'Temperate Grassland')

    async def test_close_busy_and_other_user_cannot_charge(self):
        view=self.view(10);before=self.balances()
        await view.confirm(component(2))
        view.busy=True
        await view.confirm(component())
        view.busy=False
        await view.close(component())
        await view.confirm(component())
        self.assertEqual(self.projects(),[])
        self.assertEqual(self.balances(),before)

    async def test_revoked_coop_cannot_start_from_old_preview(self):
        set_coop(1,1,77)
        view=self.view(10,77)
        set_coop(1,1,77,remove=True)
        before=self.balances();i=component(77)
        await view.confirm(i)
        self.assertIn('access',i.followup.send.call_args.args[0])
        self.assertEqual(before,self.balances())

    async def test_pagination_and_progress_fit_component_and_embed_limits(self):
        with db.cursor() as c:
            c.executemany('INSERT INTO provinces(azgaar_cell_id,owner_nation_id,population,biome) VALUES(?,1,2000,?)',
                [(cell,'Temperate Grassland') for cell in range(20,49)])
        view=self.view()
        self.assertEqual(len(view.children[0].options),25)
        i=component();await view.browse(i,page=1)
        second=i.edit_original_response.call_args.kwargs['view']
        self.assertEqual(len(second.children[0].options),6)
        self.assertEqual(second.page,1)
        self.assertLessEqual(len(second.children),25)
        self.assertEqual(self.projects(),[])
        service.start(1,10,'afforest')
        with i18n.using_language('en'):active=self.view(10)
        self.assertIn('underway, remaining 6 months',str(active.embed.to_dict()))
        self.assertTrue(active.confirm_button.disabled)
        self.assertLessEqual(len(active.embed),6000)

    async def test_territory_button_invokes_the_real_terraform_command(self):
        cog=ProvincesCog(None)
        bot=NS(get_cog=lambda name:cog if name=='ProvincesCog' else None)
        for lang,label in (('pl','Terraformacja'),('en','Terraforming')):
            with self.subTest(lang=lang):
                panel=PlayerPanel(bot,1,lang,'territory')
                button=next(item for item in panel.children if getattr(item,'label',None)==label)
                request=component()
                await button.callback(request)
                self.assertIsInstance(request.followup.send.call_args.kwargs['view'],ui.TerraformView)
                self.assertEqual(self.projects(),[])


if __name__=='__main__':unittest.main()
