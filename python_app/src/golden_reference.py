"""Comparaison à des lectures Golden, distincte de l'étalonnage absolu."""
import math
from copy import deepcopy
from src.uut_config import CRITERIA, TP

LEGACY_METHOD = 'UUT2.2-Golden-TP31-fixe'
METHOD = 'UUT2.3-Golden-stable-5Hz'
# Une référence par condition : les trois contrôles du filtre continu et
# les deux mesures de Vref ne doivent pas partager une seule cible.
LEGACY_CHECKS = [
    (1, 'ground', CRITERIA['ground'][0]),
    (1, 'vcc', CRITERIA['vcc'][0]),
    (2, 'valim_off', 'SW1 relâché, STAY_ON=0 : VALIM'),
    (2, 'valim_on_error', 'STAY_ON=1 : VALIM − VCC'),
    (2, 'valim_on_error', 'SW1 enfoncé : VALIM − VCC'),
    (2, 'valim_on_error', 'Maintien après relâchement : VALIM − VCC'),
    (2, 'valim_off', 'Fin du maintien : VALIM'),
    (3, 'divider_ratio', CRITERIA['divider_ratio'][0]),
    (3, 'divider_off', CRITERIA['divider_off'][0]),
    (4, 'vcc_555', CRITERIA['vcc_555'][0]),
    (5, 'vref_error', CRITERIA['vref_error'][0]),
    (5, 'filter_dc', 'Filtre − Vref, Vs=0'),
    (5, 'filter_dc', 'Filtre − Vref, Vs au niveau haut'),
    (5, 'a1', CRITERIA['a1'][0]),
    (5, 'a10', CRITERIA['a10'][0]),
    (5, 'a100', CRITERIA['a100'][0]),
    (6, 'vref_error', CRITERIA['vref_error'][0]),
    (6, 'filter_dc', CRITERIA['filter_dc'][0]),
    (6, 'amp_dc', CRITERIA['amp_dc'][0]),
    (6, 'gain100', CRITERIA['gain100'][0]),
]
CHECKS = LEGACY_CHECKS + [
    (7, 'schmitt_vh', CRITERIA['schmitt_vh'][0]),
    (7, 'schmitt_vl', CRITERIA['schmitt_vl'][0]),
]


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_golden(golden):
    if golden is None:
        return None
    g = deepcopy(golden)
    if not isinstance(g, dict) or g.get('method') not in (METHOD,LEGACY_METHOD) or type(g.get('enabled')) is not bool:
        raise ValueError('Référence Golden : méthode ou activation invalide.')
    checks=CHECKS if g['method']==METHOD else LEGACY_CHECKS
    if not isinstance(g.get('source'), str) or not g['source'] or not isinstance(g.get('firmware'), str):
        raise ValueError('Référence Golden : origine ou firmware manquant.')
    expected = {tp for tp, (kind, _, _) in TP.items() if kind == 'ADC'}
    if not isinstance(g.get('adc'), dict) or set(g['adc']) != expected:
        raise ValueError('Référence Golden : échelle ADC incomplète.')
    for settings in g['adc'].values():
        if not isinstance(settings, dict) or not finite(settings.get('gain')) or settings['gain'] <= 0 or not finite(settings.get('offset')):
            raise ValueError('Référence Golden : gain ou offset invalide.')
    if not isinstance(g.get('checks'), list):
        raise ValueError('Référence Golden : contrôles manquants.')
    identities = []
    for row in g['checks']:
        if not isinstance(row, dict) or type(row.get('section')) is not int:
            raise ValueError('Référence Golden : contrôle invalide.')
        identity = (row['section'], row.get('key'), row.get('test'))
        if identity not in checks or not finite(row.get('reference')):
            raise ValueError('Référence Golden : contrôle ou valeur de référence invalide.')
        tolerance = row.get('tolerance')
        if tolerance is not None and (not finite(tolerance) or tolerance < 0):
            raise ValueError('Référence Golden : tolérance négative ou non numérique.')
        identities.append(identity)
    if len(identities) != len(checks) or set(identities) != set(checks):
        raise ValueError('Référence Golden : contrôles absents ou dupliqués.')
    return g


def comparison_problem(profile, firmware):
    g = profile.get('golden')
    if not g or not g['enabled']:
        return ''
    if g['method']!=METHOD:
        return 'Profil Golden 2.2 : exécuter installer_mesures_stables.py avant de comparer avec la méthode 2.3.'
    for tp, expected in g['adc'].items():
        if any(not math.isclose(profile['adc'][tp][key], expected[key], rel_tol=0, abs_tol=1e-12)
               for key in ('gain', 'offset')):
            return f'Échelle ADC de {tp} différente de la référence Golden. Comparaison à requalifier.'
    if firmware != g['firmware']:
        return 'Firmware différent de la référence Golden. Comparaison à requalifier.'
    return ''


def find_check(profile, section, key, label=None):
    g = profile.get('golden')
    if not g or not g['enabled']:
        return None
    name = label or CRITERIA[key][0]
    return next((row for row in g['checks']
                 if (row['section'], row['key'], row['test']) == (section, key, name)), None)
