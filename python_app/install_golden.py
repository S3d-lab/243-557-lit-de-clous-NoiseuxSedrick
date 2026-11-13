"""Installe la référence fournie, sans écraser les réglages ADC ni les limites existantes."""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from shutil import copy2
from src.uut_config import PROFILE_PATH, load_profile, save_profile, validate_profile


def install(path=PROFILE_PATH):
    path=Path(path)
    if not path.is_file():
        raise ValueError('Profil absent : enregistrer d’abord les huit gains ADC à 2 dans l’application.')
    current=load_profile(path)
    reference=json.loads(Path(__file__).with_name('golden_20261106.json').read_text(encoding='utf-8'))
    if current.get('golden'):
        print('Référence Golden déjà présente. Réglages conservés; ouvrir Étalonnage et critères.')
        return None
    for tp,settings in reference['adc'].items():
        if any(current['adc'][tp][name] != settings[name] for name in ('gain','offset')):
            raise ValueError(f'{tp} : régler Gain = 2 et Offset = 0 avant cette installation. Aucun réglage écrasé.')
    updated=deepcopy(current)
    updated['golden']=reference
    updated=validate_profile(updated)
    backup=path.with_name(path.name+'.avant_golden_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    copy2(path,backup)
    save_profile(updated,path)
    print(f'Comparaison Golden installée. Sauvegarde : {backup}')
    print('Gains ADC et limites existantes conservés. Tolérances modifiables dans Comparaison Golden.')
    return backup


if __name__=='__main__':
    try:
        install()
    except (ValueError,KeyError,OSError) as error:
        raise SystemExit(f'Installation arrêtée : {error}')
