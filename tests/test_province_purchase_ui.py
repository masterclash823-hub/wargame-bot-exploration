import unittest
from unittest.mock import AsyncMock, patch

from test_province_purchase import ProvincePurchaseFixture
from test_regressions import interaction
import db
import i18n
import province_admin as provinces
import province_purchase_ui as ui
from cogs.provinces import ProvincesCog
from nation_access import set_coop


def component(uid=1):
    i=interaction(uid)
    i.edit_original_response=AsyncMock()
    return i


class PurchaseUITests(ProvincePurchaseFixture,unittest.IsolatedAsyncioTestCase):
    def view(self,cell=None,uid=1):
        n,rows=provinces.purchase_options(uid)
        return ui.ProvincePurchaseView(uid,n,rows,cell)

    async def test_command_opens_read_only_dropdown_and_defers_before_query(self):
        before=self.balances()
        i=component()
        original=provinces.purchase_options
        def load(*args):
            i.response.defer.assert_awaited_once_with(ephemeral=True)
            return original(*args)
        with patch.object(provinces,'purchase_options',side_effect=load):
            await ProvincesCog.province_buy.callback(None,i)
        view=i.followup.send.call_args.kwargs['view']
        self.assertEqual([o.value for o in view.children[0].options],['20','22','23'])
        self.assertTrue(view.confirm_button.disabled)
        self.assertTrue(i.followup.send.call_args.kwargs['ephemeral'])
        self.assertEqual(before,self.balances())

    async def test_select_previews_details_and_confirm_charges_exactly_once(self):
        with db.cursor() as c:
            c.execute("UPDATE provinces SET name='Border forest',terrain='forest',base_resources_json=? WHERE azgaar_cell_id=20",('{"wood":8}',))
            c.execute("INSERT INTO azgaar_entities(kind,entity_id,data_json) VALUES('cultures',2,?)",('{"name":"Kultura A"}',))
            c.execute("INSERT INTO azgaar_entities(kind,entity_id,data_json) VALUES('religions',3,?)",('{"name":"Religia A"}',))
        with i18n.using_language('pl'):
            view=self.view()
        select=view.children[0];select._values=['20']
        i=component()
        before=self.balances()
        await select.callback(i)
        selected=i.edit_original_response.call_args.kwargs['view']
        details=str(selected.embed.to_dict())
        for text in ('Border forest','Kultura A','Religia A','Ludność','Zasoby bazowe','300','-100'):
            self.assertIn(text,details)
        self.assertFalse(selected.confirm_button.disabled)
        self.assertEqual(before,self.balances())
        confirmation=component()
        await selected.confirm_button.callback(confirmation)
        self.assertEqual(self.balances()[0]['treasury'],700)
        self.assertIn('Kupiono prowincję #20',confirmation.edit_original_response.call_args.kwargs['content'])
        await selected.confirm_button.callback(component())
        self.assertEqual(self.balances()[0]['treasury'],700)

    async def test_optional_cell_opens_preview_and_cancel_never_buys(self):
        before=self.balances();i=component()
        await ProvincesCog.province_buy.callback(None,i,20)
        view=i.followup.send.call_args.kwargs['view']
        self.assertEqual(view.selected['azgaar_cell_id'],20)
        self.assertEqual(self.balances(),before)
        await view.cancel(component())
        await view.confirm(component())
        self.assertEqual(self.balances(),before)

    async def test_pages_reach_all_neighbors_and_respect_discord_limits(self):
        with db.cursor() as c:
            for cell in range(100,132):
                c.execute('INSERT INTO provinces(azgaar_cell_id,name) VALUES(?,?)',(cell,'x'*200))
                c.execute('INSERT INTO province_neighbors(cell_id,neighbor_cell_id) VALUES(?,10)',(cell,))
        n,rows=provinces.purchase_options(1)
        for lang in ('pl','en'):
            with self.subTest(lang=lang),i18n.using_language(lang):
                view=ui.ProvincePurchaseView(1,n,rows)
                seen=[]
                for page in range(2):
                    seen.extend(o.value for o in view.children[0].options)
                    self.assertLessEqual(len(view.children[0].options),25)
                    self.assertTrue(all(len(o.label)<=100 and len(o.description)<=100 for o in view.children[0].options))
                    self.assertLessEqual(len(view.embed),6000)
                    if page==0:
                        i=component();await view.children[3].callback(i)
                        view=i.edit_original_response.call_args.kwargs['view']
                self.assertEqual(seen,[str(p['azgaar_cell_id']) for p in rows])
                self.assertTrue(view.children[3].disabled)

    async def test_price_or_ownership_change_after_preview_blocks_confirmation(self):
        view=self.view(20);before=self.balances()
        with db.cursor() as c:c.execute('UPDATE azgaar_cells SET culture_id=9 WHERE cell_id=10')
        i=component();await view.confirm(i)
        self.assertIn('price changed',i.followup.send.call_args.args[0])
        self.assertEqual(before,self.balances())
        refreshed=component();await view.children[4].callback(refreshed)
        view=refreshed.edit_original_response.call_args.kwargs['view']
        self.assertEqual(view.selected['cost'],400)
        with db.cursor() as c:c.execute('UPDATE provinces SET owner_nation_id=2 WHERE azgaar_cell_id=20')
        i=component();await view.confirm(i)
        self.assertIn('already has an owner',i.followup.send.call_args.args[0])
        self.assertEqual(before,self.balances())

    async def test_access_and_border_are_rechecked_after_preview(self):
        view=self.view(20);before=self.balances()
        await view.confirm(component(2))
        self.assertEqual(before,self.balances())
        set_coop(1,1,3)
        coop=self.view(20,3)
        set_coop(1,1,3,remove=True)
        i=component(3);await coop.confirm(i)
        self.assertIn('no nation',i.followup.send.call_args.args[0])
        with db.cursor() as c:c.execute('UPDATE provinces SET owner_nation_id=2 WHERE azgaar_cell_id=10')
        i=component();await view.confirm(i)
        self.assertIn('adjacent',i.followup.send.call_args.args[0])
        self.assertEqual(before,self.balances())

    async def test_empty_or_insufficient_gold_has_no_enabled_confirmation(self):
        with db.cursor() as c:c.execute('UPDATE nations SET treasury=0 WHERE id=1')
        view=self.view(20)
        self.assertTrue(view.confirm_button.disabled)
        self.assertIn('Missing gold',str(view.embed.to_dict()))
        with db.cursor() as c:c.execute('DELETE FROM province_neighbors')
        view=self.view()
        self.assertTrue(view.confirm_button.disabled)
        self.assertFalse(any(hasattr(child,'options') for child in view.children))
