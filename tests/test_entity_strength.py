import copy
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock,patch

import config
import db
import azgaar_format as fmt
import azgaar_service as service
from test_azgaar_exchange import world,native
from test_regressions import DatabaseFixture,interaction


class StrengthFixture(DatabaseFixture):
    def setUp(self):
        super().setUp()
        self.data=world()
        self.data['pack']['cultures'][1]['expansionism']=1.2
        self.data['pack']['religions'][1]['expansionism']=0.8
        service.import_map(json.dumps(self.data).encode(),native(self.data))

    def entities(self):
        with db.cursor() as c:
            c.execute('SELECT * FROM azgaar_entities ORDER BY kind,entity_id')
            return c.fetchall()


class StrengthTests(StrengthFixture,unittest.TestCase):
    def test_strength_preserves_metadata_and_survives_both_export_formats(self):
        for kind,value in (('cultures',2.5),('religions',0)):
            before=service.catalog(kind)[1]['entity']
            result=service.set_entity_strength(kind,1,value)
            self.assertEqual(result['previous'],before['expansionism'])
            self.assertEqual(service.catalog(kind)[1]['entity'],dict(before,expansionism=value))
        for native_format in (False,True):
            with self.subTest(native=native_format):
                output=service.export_map(native_format=native_format)
                data=(fmt.native_overlay(copy.deepcopy(self.data),fmt.decode(output)) if native_format else json.loads(output))
                self.assertEqual(data['pack']['cultures'][1]['expansionism'],2.5)
                self.assertEqual(data['pack']['religions'][1]['expansionism'],0)
                self.assertEqual(data['pack']['cultures'][1]['color'],'#123456')
                self.assertEqual(data['pack']['religions'][1]['deity'],'Moon')
        # A new import/restart also reads the values from persistent map data.
        exported=service.export_map(native_format=False)
        service.import_map(exported,resync=True)
        self.assertEqual(service.catalog('cultures')[1]['entity']['expansionism'],2.5)
        self.assertEqual(service.catalog('religions')[1]['entity']['expansionism'],0)

    def test_invalid_strengths_and_entities_leave_database_unchanged(self):
        before=self.entities()
        for value in (-1,float('nan'),float('inf'),-float('inf'),True,'2',None,10**1000):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(ValueError):service.set_entity_strength('cultures',1,value)
                self.assertEqual(self.entities(),before)
        for kind,entity in (('states',1),('cultures',0),('religions',-1),('cultures',999),('cultures',True)):
            with self.subTest(kind=kind,entity=entity):
                with self.assertRaises(ValueError):service.set_entity_strength(kind,entity,2)
                self.assertEqual(self.entities(),before)

    def test_removed_entity_cannot_be_edited(self):
        with db.cursor() as c:
            c.execute("UPDATE azgaar_entities SET data_json=? WHERE kind='cultures' AND entity_id=1",
                      (json.dumps(dict(i=1,name='Removed',removed=True)),))
        before=self.entities()
        with self.assertRaisesRegex(ValueError,'does not exist'):
            service.set_entity_strength('cultures',1,2)
        self.assertEqual(self.entities(),before)


class StrengthUITests(StrengthFixture,unittest.IsolatedAsyncioTestCase):
    async def test_command_requires_gm_and_acknowledges_before_saving(self):
        from cogs.provinces import ProvincesCog
        blocked=interaction(1)
        before=self.entities()
        with patch('cogs.provinces._gm',return_value=False):
            await ProvincesCog.map_strength.callback(None,blocked,'cultures',1,9)
        blocked.response.defer.assert_not_awaited()
        self.assertEqual(self.entities(),before)
        gm=interaction(999)
        async def worker(function,*args,**kwargs):
            gm.response.defer.assert_awaited_once_with(ephemeral=True)
            return function(*args,**kwargs)
        with patch('cogs.provinces._gm',return_value=True),patch('cogs.provinces.asyncio.to_thread',side_effect=worker):
            await ProvincesCog.map_strength.callback(None,gm,'religions',1,3.5)
        self.assertEqual(service.catalog('religions')[1]['entity']['expansionism'],3.5)
        self.assertIn('3.5',gm.followup.send.call_args.args[0])
        self.assertTrue(gm.followup.send.call_args.kwargs['ephemeral'])

    async def test_panel_picker_and_form_recheck_permissions_and_accept_decimal_comma(self):
        from admin_panel import AdminPanel
        import i18n
        roles=[NS(id=10,name=config.GM_ROLE_NAME)]
        for lang,button_label,key,kind in (('pl','Siła kultury','culture_strength','cultures'),
                                           ('en','Religion strength','religion_strength','religions')):
            with self.subTest(lang=lang),i18n.using_language(lang):
                panel=AdminPanel(None,999,section='map')
                self.assertTrue(any(getattr(child,'label',None)==button_label for child in panel.children))
                self.assertTrue(all(child.row<=4 for child in panel.children))
                gm=interaction(999,roles)
                await panel.action(gm,key)
                picker=gm.response.send_message.call_args.kwargs['view']
                select=picker.children[0]
                self.assertEqual([option.value for option in select.options],['1'])
                select._values=['1']
                chosen=interaction(999,roles)
                chosen.response.send_modal=AsyncMock()
                await select.callback(chosen)
                form=chosen.response.send_modal.call_args.args[0]
                self.assertEqual(form.inputs[0].default,'1.2' if kind=='cultures' else '0.8')
                with patch.object(panel,'call',new_callable=AsyncMock) as save:
                    await form.submitter(interaction(999),'2,5')
                    save.assert_not_awaited()
                    await form.submitter(interaction(1,roles),'2,5')
                    save.assert_not_awaited()
                    await form.submitter(gm,'2,5')
                    save.assert_awaited_once_with(gm,'ProvincesCog','map_strength',kind,1,2.5)

    async def test_catalog_shows_current_strength(self):
        from azgaar_ui import entities_command
        gm=interaction(999)
        with patch('azgaar_ui.gm_only',return_value=True):
            await entities_command(gm,'cultures',1)
        embed=gm.response.send_message.call_args.kwargs['embed']
        self.assertIn('strength: 1.2',embed.description)
        self.assertIn('/admin map_strength',embed.footer.text)
