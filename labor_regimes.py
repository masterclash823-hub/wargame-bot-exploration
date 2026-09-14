"""Abstract labor institutions, with costs and a funded emancipation transition."""
import db
import i18n
from world_service import world_lock, owned, reward, tr

EXTRACTION = {'farm', 'pasture', 'plantation', 'lumber_camp', 'mine', 'copper_mine', 'clay_pit', 'tar_works'}
DEFAULT = {'mode': 'free', 'transition_months': 0, 'version': 0}


def state(c, nid):
    c.execute('SELECT * FROM labor_regimes WHERE nation_id=?', (nid,))
    r=c.fetchone() or dict(DEFAULT)
    c.execute('SELECT COALESCE(SUM(x.quantity),0) AS n FROM province_captives x JOIN provinces p ON p.id=x.province_id WHERE p.owner_nation_id=? AND p.active=1',(nid,))
    r['captives']=c.fetchone()['n'] if r['mode']=='slavery' else 0
    return r


def label(mode):
    return tr('Niewolnictwo', 'Slavery') if mode == 'slavery' else tr('Wolna praca', 'Free labor')


def costs(population, mode):
    return 50 if mode == 'slavery' else round(max(0, population) / 1000 * 20, 2)


def change(nid, uid, mode, version, expected_cost):
    if mode not in ('free', 'slavery'):
        raise ValueError(tr('Nieznana polityka pracy.', 'Unknown labor policy.'))
    with db.atomic() as c:
        world_lock(c); n = owned(c, nid, uid); old = state(c, nid)
        if old['version'] != version or old['mode'] == mode:
            raise ValueError(tr('Polityka już się zmieniła. Otwórz panel ponownie.', 'The policy changed. Open the panel again.'))
        c.execute('SELECT COALESCE(SUM(population),0) AS pop FROM provinces WHERE owner_nation_id=? AND active=1', (nid,))
        cost = costs(c.fetchone()['pop'], mode)
        if cost != expected_cost:
            raise ValueError(tr('Zmienił się koszt reformy. Sprawdź nową wycenę.', 'The reform cost changed. Review the new quote.'))
        from economy_services import spend
        spend(c, n, {'gold': cost})
        if mode == 'slavery':
            from captivity import emancipate
            emancipate(c,nid)  # Previously free residents do not regain an inherited captive marker.
            c.execute('UPDATE nations SET stability=CASE WHEN stability>=5 THEN stability-5 ELSE 0 END WHERE id=?', (nid,))
            reward(c, nid, reputation=-10)
        else:
            from captivity import emancipate
            emancipate(c,nid)
        c.execute('INSERT INTO labor_regimes(nation_id,mode,transition_months,version) VALUES(?,?,?,?) '
                  'ON CONFLICT(nation_id) DO UPDATE SET mode=excluded.mode,transition_months=excluded.transition_months,version=excluded.version',
                  (nid, mode, 3 if mode == 'free' else 0, old['version']+1))
        with i18n.using_language(i18n.get_user_language(n['owner_id'])):
            text = tr('Reforma pracy: ', 'Labor reform: ') + label(mode) + f' ({cost:g}g).'
        c.execute('INSERT INTO nation_history(nation_id,source,entry_text) VALUES(?,?,?)', (nid, 'system', text))
        from world_service import activity
        activity(c, 'labor_reform', nid, f'labor:{nid}:{old["version"]+1}', {'mode': mode})


def output_bonus(regime, building):
    if building not in EXTRACTION:
        return 0
    if regime.get('mode') == 'slavery':
        return .10
    return -.05 if regime.get('transition_months', 0) else 0


def settle(c, nid, report):
    if report['labor']['mode'] == 'slavery':
        reward(c, nid, reputation=-1)
    c.execute('UPDATE labor_regimes SET transition_months=transition_months-1 WHERE nation_id=? AND transition_months>0', (nid,))
