"""Actualise uniquement les références dont la méthode de capture change en 2.3."""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from shutil import copy2
from src.golden_reference import METHOD, LEGACY_METHOD
from src.uut_config import PROFILE_PATH, load_profile, save_profile, validate_profile


def install(path=PROFILE_PATH):
    path=Path(path)
    if not path.is_file():
        raise ValueError('Profil absent; installer d’abord la comparaison Golden 2.2.')
    current=load_profile(path)
    golden=current.get('golden')
    if not golden:
        raise ValueError('Référence Golden 2.2 absente. Aucun profil modifié.')
    if golden['method']==METHOD:
        print('Méthode 2.3 déjà installée : toutes les références et tolérances sont conservées.')
        return None
    reference=json.loads(Path(__file__).with_name('golden_20261106.json').read_text(encoding='utf-8'))
    migration=reference['migration']
    if (golden['method']!=LEGACY_METHOD or golden['firmware']!=reference['firmware']
            or golden.get('source_report_sha256')!=migration['previous_report_sha256']):
        raise ValueError('Cette mise à jour concerne la Golden du 6 novembre 2026. Référence différente : aucun profil modifié.')
    for tp,settings in reference['adc'].items():
        if any(current['adc'][tp][key]!=value or golden['adc'][tp][key]!=value
               for key,value in settings.items()):
            raise ValueError(f'Échelle de {tp} différente du diagnostic. Aucun profil modifié.')
    old_gain=next(r for r in golden['checks'] if r['key']=='gain100')
    if old_gain['reference']!=migration['previous_gain_reference']:
        raise ValueError('Référence de gain différente de celle du diagnostic. Aucun profil modifié.')
    for key in ('schmitt_vh','schmitt_vl'):
        if any(v is not None for v in current['limits'][key].values()):
            raise ValueError(f'{key} contient déjà des limites. Vérifier leur pertinence pour des niveaux apparents à 5 Hz avant de migrer; aucun profil modifié.')

    updated=deepcopy(current)
    g=updated['golden']
    target=next(r for r in g['checks'] if r['key']=='gain100')
    measured=next(r for r in reference['checks'] if r['key']=='gain100')
    target['previous_reference']=target['reference']
    target['reference']=measured['reference']
    # La marge choisie précédemment n'est ni élargie ni recalculée.
    target['initial_rule']=measured['initial_rule']
    g['checks'].extend(deepcopy(r) for r in reference['checks'] if r['section']==7)
    for key in ('method','source','stable_diagnostic','migration'):
        g[key]=deepcopy(reference[key])
    if all(v is None for v in updated['limits']['schmitt_width'].values()):
        # Critère de signe du plan : VH > VL au-delà de la marge de mesure.
        updated['limits']['schmitt_width']={'min':0.0,'max':None}
    updated=validate_profile(updated)
    backup=path.with_name(path.name+'.avant_mesures_stables_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    copy2(path,backup)
    save_profile(updated,path)
    print(f'Méthode 2.3 installée. Sauvegarde : {backup}')
    print('Gain à 100 Hz : référence stabilisée, tolérance précédente conservée.')
    print('Forme et niveaux de commutation apparents : 5 Hz. TP34 non sélectionné.')
    print('VH/VL : références mesurées ajoutées; choisir les tolérances dans Comparaison Golden.')
    print('Critère d’hystérésis apparente : écart strictement positif après la marge de mesure.')
    return backup


if __name__=='__main__':
    try:
        install()
    except (ValueError,KeyError,OSError) as error:
        raise SystemExit(f'Installation arrêtée : {error}')
