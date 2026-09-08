"""English command names with Polish descriptions, parameters and choices."""
from discord import Locale, app_commands
import i18n

PARAMETERS = {
    'visibility':'widoczność', 'image_query':'wyszukiwanie_obrazka',
    'amount':'ilość','apply_casualties':'zastosuj_straty','atk_modifier_override':'mnożnik_ataku',
    'attacker_plan_id':'plan_atakującego','auth_key':'klucz','battle_id':'id_bitwy',
    'blueprint_id':'id_projektu','building':'budynek','category':'kategoria','cell_id':'id_komórki',
    'channel':'kanał','cost_json':'koszt_json','def_modifier_override':'mnożnik_obrony',
    'defender_plan_id':'plan_obrońcy','description':'opis','duration_months':'czas_w_miesiącach',
    'effect':'efekt','effect_json':'efekt_json','effects_json':'efekty_json','event_id':'id_wydarzenia',
    'field':'pole','file':'plik','final_effect':'efekt_końcowy','flag':'flaga','forces_note':'opis_sił',
    'from_cell':'komórka_początkowa','give_gold':'oddawane_złoto','give_resources':'oddawane_zasoby',
    'gm_note':'uwaga_gm','gm_notes':'uwagi_gm','gold_amount':'ilość_złota','gold_budget':'budżet_złota',
    'government':'ustrój','history':'historia','hours_per_month':'godziny_na_miesiąc','hull':'kadłub',
    'ids':'identyfikatory','key':'klucz','lang':'język','level':'poziom','location':'miejsce','final_location':'ostateczne_miejsce',
    'months':'miesiące','mp_id':'id_megaprojektu','name':'nazwa','nation':'naród','orders':'rozkazy',
    'page':'strona','private_note':'uwaga_prywatna','public_note':'uwaga_publiczna',
    'quantity':'liczba','reason':'powód','receive_gold':'otrzymywane_złoto',
    'receive_resources':'otrzymywane_zasoby','requires_terrain':'wymagany_teren','resource':'zasób',
    'route_id':'id_szlaku','route_name':'nazwa_szlaku','ship_id':'id_okrętu',
    'start_month':'miesiąc_początkowy','start_year':'rok_początkowy','steps':'kroki',
    'source_cell_id':'id_komórki_źródłowej','target_cell_id':'id_komórki_docelowej',
    'text':'tekst','tier':'poziom','to_cell':'komórka_docelowa','to_nation':'naród_docelowy',
    'trade_id':'id_wymiany','unit_id':'id_jednostki','unit_ids':'id_jednostek',
    'unit_type':'typ_jednostki','url':'adres','value':'wartość',
}


class PolishTranslator(app_commands.Translator):
    async def translate(self, string, locale, context):
        if locale != Locale.polish:
            return None
        source = string.message
        location = context.location.name
        if location in ('command_name', 'group_name'):
            # Explicitly replace previously synchronized Polish names as well.
            return source
        if location == 'parameter_name':
            return PARAMETERS.get(source)
        if location == 'parameter_description' and source == '…':
            return PARAMETERS.get(context.data.name, context.data.name).replace('_', ' ').capitalize()
        return i18n.text(source, lang='pl')
