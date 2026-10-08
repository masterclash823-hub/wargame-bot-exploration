import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

import db
import province_admin as provinces
from cogs.panel import PlayerPanel
from cogs.provinces import ProvincesCog
from nation_access import set_coop
from test_player_workflows import ui
from test_world_features import WorldFixture


class NamingFixture(WorldFixture):
    def setUp(self):
        super().setUp()
        for cell,owner in ((10,1),(11,1),(20,2),(30,None),(40,1)):
            self.province(cell,owner)
        with db.cursor() as c:
            c.execute("UPDATE provinces SET name='Old Town',buildings_json='[\"farm\"]' WHERE azgaar_cell_id=10")
            c.execute('UPDATE provinces SET active=0 WHERE azgaar_cell_id=40')
            c.execute('UPDATE nations SET treasury=0 WHERE id=1')

    def province_row(self,cell=10):
        return self.query('SELECT * FROM provinces WHERE azgaar_cell_id=?',(cell,))[0]


class NamingTests(NamingFixture,unittest.TestCase):
    def test_naming_and_renaming_are_free_and_preserve_every_other_province_field(self):
        before=self.province_row();balances=self.balances()
        result=provinces.rename(10,1,'  Nowy Kraków  ')
        self.assertEqual(result['name'],'Nowy Kraków')
        self.assertEqual(self.province_row(),dict(before,name='Nowy Kraków'))
        provinces.rename(10,1,"Port O'Brien")
        provinces.rename(10,1,"Port O'Brien",expected_name='Old Town')
        self.assertEqual(self.province_row(),dict(before,name="Port O'Brien"))
        self.assertEqual(self.balances(),balances)
        self.assertEqual(len(self.query('SELECT * FROM nation_history WHERE nation_id=1')),2)
        db.init_db()
        self.assertEqual(self.province_row()['name'],"Port O'Brien")

    def test_foreign_unclaimed_inactive_and_missing_provinces_are_rejected(self):
        before=self.query('SELECT * FROM provinces ORDER BY id')
        for cell,uid in ((20,1),(30,1),(40,1),(999,1),(10,2),(10,999)):
            with self.subTest(cell=cell,uid=uid),self.assertRaises(ValueError):
                provinces.rename(cell,uid,'New Town')
        self.assertEqual(self.query('SELECT * FROM provinces ORDER BY id'),before)
        self.assertFalse(self.query('SELECT * FROM nation_history'))

    def test_current_coop_can_name_but_revoked_access_and_changed_nation_cannot(self):
        set_coop(1,1,3)
        n,rows=provinces.naming_options(3)
        self.assertEqual({p['azgaar_cell_id'] for p in rows},{10,11})
        provinces.rename(10,3,'Co-op City',nation_id=n['id'])
        set_coop(1,1,3,remove=True)
        with self.assertRaises(ValueError):provinces.rename(10,3,'Too late',nation_id=1)
        with self.assertRaises(ValueError):provinces.naming_options(3)
        set_coop(2,2,3)
        with self.assertRaises(ValueError):provinces.rename(20,3,'Wrong panel',nation_id=1)
        self.assertEqual(self.province_row()['name'],'Co-op City')

    def test_invalid_names_do_not_change_land_and_polish_names_are_normalized(self):
        for name in ('','   ','x'*81,'City\nName','City\x00Name','City\tName'):
            with self.subTest(name=name),self.assertRaises(ValueError):provinces.rename(10,1,name)
        self.assertEqual(self.province_row()['name'],'Old Town')
        provinces.rename(10,1,'Ło\u0301dz\u0301')
        self.assertEqual(self.province_row()['name'],'Łódź')
        provinces.rename(10,1,'A'*80)
        self.assertEqual(len(self.province_row()['name']),80)

    def test_stale_form_cannot_overwrite_a_new_name(self):
        provinces.rename(10,1,'New Town',expected_name='Old Town')
        with self.assertRaisesRegex(ValueError,'name changed'):
            provinces.rename(10,1,'Stale Town',expected_name='Old Town')
        self.assertEqual(self.province_row()['name'],'New Town')


class NamingUITests(NamingFixture,unittest.IsolatedAsyncioTestCase):
    async def open_form(self,uid=1):
        i=ui(uid)
        await ProvincesCog.province_rename.callback(None,i)
        view=i.followup.send.call_args.kwargs['view']
        select=view.children[0];select._values=['10']
        selected=ui(uid);await select.callback(selected)
        return selected.response.send_modal.call_args.args[0]

    async def test_menu_defers_lists_only_owned_active_provinces_and_saves_city_name(self):
        i=ui(1);original=provinces.naming_options
        def load(*args):
            i.response.defer.assert_awaited_once_with(ephemeral=True)
            return original(*args)
        with patch.object(provinces,'naming_options',side_effect=load):
            await ProvincesCog.province_rename.callback(None,i)
        view=i.followup.send.call_args.kwargs['view']
        self.assertEqual({o.value for o in view.children[0].options},{'10','11'})
        self.assertTrue(i.followup.send.call_args.kwargs['ephemeral'])
        self.assertEqual(self.province_row()['name'],'Old Town')
        form=await self.open_form()
        self.assertEqual(form.inputs[0].default,'Old Town')
        outsider=ui(2);await form.submitter(outsider,'Not mine')
        self.assertEqual(self.province_row()['name'],'Old Town')
        submitted=ui(1);await form.submitter(submitted,'Nowy Kraków')
        submitted.response.defer.assert_awaited_once_with(ephemeral=True)
        self.assertIn('0 gold',submitted.followup.send.call_args.kwargs['content'])
        self.assertEqual(self.province_row()['name'],'Nowy Kraków')
        self.assertEqual(self.balances()[0]['treasury'],0)
        for command,args in ((ProvincesCog.info,(10,)),(ProvincesCog.province_list,('A',1))):
            shown=ui(1);await command.callback(None,shown,*args)
            self.assertIn('Nowy Kraków',str(shown.response.send_message.call_args.kwargs['embed'].to_dict()))

    async def test_open_form_rechecks_lost_province_and_revoked_coop(self):
        form=await self.open_form()
        with db.cursor() as c:c.execute('UPDATE provinces SET owner_nation_id=2 WHERE azgaar_cell_id=10')
        await form.submitter(ui(1),'Foreign Town')
        self.assertEqual(self.province_row()['name'],'Old Town')
        with db.cursor() as c:c.execute('UPDATE provinces SET owner_nation_id=1 WHERE azgaar_cell_id=10')
        set_coop(1,1,3);form=await self.open_form(3);set_coop(1,1,3,remove=True)
        await form.submitter(ui(3),'Revoked Town')
        self.assertEqual(self.province_row()['name'],'Old Town')

    async def test_direct_command_is_free_and_uses_the_same_ownership_checks(self):
        i=ui(1);await ProvincesCog.province_rename.callback(None,i,10,'Gdańsk')
        self.assertEqual(self.province_row()['name'],'Gdańsk')
        self.assertTrue(i.followup.send.call_args.kwargs['ephemeral'])
        await ProvincesCog.province_rename.callback(None,ui(2),10,'Enemy')
        self.assertEqual(self.province_row()['name'],'Gdańsk')
        self.assertEqual(self.balances()[0]['treasury'],0)

    async def test_territory_button_and_pagination_reach_all_owned_provinces(self):
        for cell in range(100,130):self.province(cell,1)
        bot=NS(get_cog=lambda name:ProvincesCog(None) if name=='ProvincesCog' else None)
        for lang,label in (('pl','Nazwij prowincję'),('en','Name a province')):
            panel=PlayerPanel(bot,1,lang,'territory')
            button=next(b for b in panel.children if getattr(b,'label',None)==label)
            i=ui(1);await button.callback(i)
            view=i.followup.send.call_args.kwargs['view']
            self.assertEqual(len(view.children[0].options),25)
            first={o.value for o in view.children[0].options}
            page=ui(1);await view.children[2].callback(page)
            last=page.response.edit_message.call_args.kwargs['view']
            ids=first|{o.value for o in last.children[0].options}
            self.assertEqual(ids,{'10','11'}|{str(c) for c in range(100,130)})
            self.assertTrue(last.children[2].disabled)


if __name__=='__main__':unittest.main()
