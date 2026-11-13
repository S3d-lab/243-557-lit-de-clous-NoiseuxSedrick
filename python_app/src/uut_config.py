"""Correspondance physique figée par Sedrick. Canaux logiciels numérotés 0..7."""
import json
import math
from copy import deepcopy
from pathlib import Path

TP = {
    'TP1': ('EXTERNE', None, 'Alimentation +5 V'),
    'TP2': ('GND', None, 'Masse commune : alimentation externe et banc'),
    'TP3': ('ADC', 5, 'Diviseur'), 'TP4': ('ADC', 6, 'VALIM'),
    'TP6': ('IN', 7, 'Sortie 555'), 'TP8': ('ADC', 7, 'VCC'),
    'TP11': ('OUT', 4, 'STAY_ON'), 'TP14': ('IN', 5, 'SW_IO'),
    'TP17': ('ADC', 0, 'Masse interne : mesure de la chute dans R6'),
    'TP18': ('PWM', 7, 'Vs'), 'TP23': ('OUT', 3, 'SA555_EN'),
    'TP29': ('ADC', 4, 'Sortie filtre'), 'TP30': ('IN', 6, 'Sortie Schmitt'),
    'TP31': ('ADC', 3, 'Sortie ampli / entrée Schmitt'),
    'TP34': ('ADC', 2, 'Seuil Schmitt'), 'TP35': ('ADC', 1, 'Vref'),
}
SECTIONS = ['Alimentation', 'Commande VALIM', 'Diviseur', 'Oscillateur 555',
            'Filtre', 'Amplificateur et Vref', 'Comparateur à hystérésis']
LSB = {12: .001, 14: .00025, 16: .0000625, 18: .000015625}
# Aucun seuil d'acceptation n'est déduit arbitrairement d'un nominal.
# Clé: (nom affiché, unité, repère nominal). Les limites se règlent dans l'UI.
CRITERIA = {
    'ground': ('TP17 − TP2', 'V', 0),
    'vcc': ('VCC : TP8 − TP17', 'V', 4.66),
    'valim_off': ('VALIM désactivée', 'V', 0),
    'valim_on_error': ('VALIM − VCC, activée', 'V', 0),
    'divider_ratio': ('TP3 / VALIM', '', .5),
    'divider_off': ('Diviseur désactivé', 'V', 0),
    'vcc_555': ('VCC pendant le 555', 'V', None),
    'f555': ('Fréquence 555', 'Hz', 10.08403),
    'd555': ('Rapport cyclique haut 555', '%', 52.38095),
    'vref_error': ('Vref − VCC/2', 'V', 0),
    'filter_dc': ('Filtre − Vref en continu', 'V', 0),
    'a1': ('Fondamentale filtre à 1 Hz (crête)', 'V', None),
    'a10': ('Fondamentale filtre à 10 Hz (crête)', 'V', None),
    'a100': ('Fondamentale filtre à 100 Hz (crête)', 'V', None),
    'amp_dc': ('Ampli − filtre en continu', 'V', 0),
    'gain100': ('Gain AC à 100 Hz', '', 5),
    'schmitt_vh': ('Seuil haut apparent TP31 à 5 Hz (VH)', 'V', None),
    'schmitt_vl': ('Seuil bas apparent TP31 à 5 Hz (VL)', 'V', None),
    'schmitt_width': ('Hystérésis apparente VH − VL à 5 Hz', 'V', None),
    'schmitt_f': ('Fréquence sortie Schmitt', 'Hz', 1),
}
PROFILE_PATH = Path(__file__).resolve().parents[1] / 'uut_profile.json'

def default_profile():
    return {'schema': 1, 'golden_ac_100hz': False, 'adc': {
        tp: {'gain': 1.0, 'offset': 0.0, 'confirmed': False}
        for tp, (kind, _, _) in TP.items() if kind == 'ADC'},
        'limits': {key: {'min': 4.5 if key == 'vcc_555' else None, 'max': None}
                   for key in CRITERIA}}

def validate_profile(profile):
    if profile.get('schema') != 1:
        raise ValueError('Version de profil inconnue.')
    if set(profile.get('adc', {})) != set(default_profile()['adc']):
        raise ValueError('Le profil doit décrire les huit TP ADC existants.')
    for tp, settings in profile['adc'].items():
        for name in ('gain', 'offset'):
            value = settings[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f'{tp} : {name} invalide.')
        if settings['gain'] <= 0 or type(settings['confirmed']) is not bool:
            raise ValueError(f'{tp} : gain positif et confirmation explicite requis.')
    if set(profile.get('limits', {})) != set(CRITERIA):
        raise ValueError('Liste des critères différente de celle du programme.')
    for name, bounds in profile['limits'].items():
        lo, hi = bounds['min'], bounds['max']
        for value in (lo, hi):
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value)):
                raise ValueError(f'Limite invalide : {name}.')
        if lo is not None and hi is not None and lo > hi:
            raise ValueError(f'Limites inversées : {name}.')
    result=deepcopy(profile)
    result.setdefault('golden_ac_100hz',False)  # Profils V2 existants, sans réinitialisation.
    if type(result['golden_ac_100hz']) is not bool:
        raise ValueError('Validation Golden à 100 Hz : valeur booléenne requise.')
    if 'golden' in result:
        from src.golden_reference import validate_golden
        result['golden'] = validate_golden(result['golden'])
    return result

def load_profile(path=PROFILE_PATH):
    return validate_profile(json.loads(path.read_text())) if path.exists() else default_profile()

def save_profile(profile, path=PROFILE_PATH):
    data = validate_profile(profile)
    temp = path.with_suffix('.json.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(path)
