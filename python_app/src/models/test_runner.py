"""Séquences du plan UUT, avec mapping physique et étapes opérateur explicites."""
import math
import threading
import time
from statistics import mean
from src.hardware.stm32_hardware import Cancelled
from src.uut_config import TP, SECTIONS, LSB, CRITERIA, default_profile, validate_profile
from src.models.measurements import Inconclusive, digital_metrics, fundamental, flat_top_evidence, one_edge_per_period
from src.models.measurements import sampled_1hz, apparent_switching_levels
from src.golden_reference import find_check, comparison_problem

PASS, FAIL, QUALIFY, UNKNOWN = 'PASS', 'FAIL', 'À QUALIFIER', 'NON CONCLUANT'

class TestRunner:
    def __init__(self, stm32, profile=None, cancel=None, ask=None, report=None, waveform=None):
        self.stm32 = stm32
        self.profile = validate_profile(profile or default_profile())
        self.cancel = cancel or threading.Event()
        self.ask_callback = ask
        self.report_callback = report or (lambda row: None)
        self.waveform_callback = waveform or (lambda wave: None)
        self.rows, self.waves = [], []
        self.section = 0

    def check_cancel(self):
        if self.cancel.is_set():
            raise Cancelled('Arrêt demandé : couper le +5 V externe.')

    def wait(self, seconds):
        if self.cancel.wait(seconds):
            self.check_cancel()

    def ask(self, text, kind='action'):
        if kind not in ('action','visual'):
            raise ValueError('Les mesures électriques doivent provenir du banc.')
        self.check_cancel()
        if self.ask_callback is None:
            raise RuntimeError('Cette séquence nécessite un opérateur.')
        result = self.ask_callback({'section': self.section, 'kind': kind, 'text': text})
        self.check_cancel()
        return result

    def row(self, name, value=None, unit='', status=QUALIFY, note='', minimum=None, maximum=None, source='Banc'):
        row = dict(section=self.section, test=name, value=value, unit=unit,
                   status=status, minimum=minimum, maximum=maximum, note=note, source=source)
        self.rows.append(row); self.report_callback(row)
        return row

    def criterion(self, key, value, qualified=True, note='', source='Banc', label=None, uncertainty=0):
        name, unit, nominal = CRITERIA[key]
        # Ne dépend pas de l'ordre des clés d'un JSON externe.
        lo, hi = self.profile['limits'][key]['min'], self.profile['limits'][key]['max']
        golden = find_check(self.profile, self.section, key, label)
        problem = self.golden_problem() if golden is not None else ''
        if golden is not None:
            # La référence est une lecture enregistrée du banc, pas une
            # tension vraie imposée ni un certificat d'étalonnage.
            nominal = None
            reference, tolerance = golden['reference'], golden['tolerance']
            lo = reference-tolerance if tolerance is not None else None
            hi = reference+tolerance if tolerance is not None else None
            qualified = not problem
            note += f' Référence Golden mesurée : {reference:.6g} {unit}.'
            if value is not None and math.isfinite(value):
                note += f' Écart à la Golden : {value-reference:+.6g} {unit}.'
            note += ' Comparaison sur le même banc, mêmes conditions et même échelle ADC.'
            source += ' / comparaison Golden'
            if problem: note += ' '+problem
        if value is None or not math.isfinite(value):
            status = UNKNOWN
        elif not qualified or (lo is None and hi is None):
            status = QUALIFY
        else:
            if (lo is None or value-uncertainty >= lo) and (hi is None or value+uncertainty <= hi):
                status = PASS
            elif (lo is not None and value+uncertainty < lo) or (hi is not None and value-uncertainty > hi):
                status = FAIL
            else:
                status = UNKNOWN
                note += ' L’intervalle de mesure chevauche une limite.'
        if nominal is not None:
            note += f' Repère nominal : {nominal:g} {unit}.'
        if not qualified and golden is None:
            note += ' Étalonnage ou méthode à confirmer.'
        if lo is None and hi is None:
            note += ' Limites du plan à renseigner.'
        return self.row(label or name, value, unit, status, note.strip(), lo, hi, source)

    def golden_problem(self):
        return comparison_problem(self.profile, getattr(self.stm32, 'firmware', None))

    def golden_check(self, key, label=None):
        return None if self.golden_problem() else find_check(self.profile, self.section, key, label)

    def golden_active(self):
        golden = self.profile.get('golden')
        return bool(golden and golden['enabled'] and not self.golden_problem())

    def visual(self, text):
        answer = self.ask(text, 'visual')
        self.row(text, answer, status=PASS if answer is True else FAIL if answer is False else UNKNOWN,
                 source='Observation opérateur')

    def calibrated(self, *points):
        return all(self.profile['adc'][tp]['confirmed'] for tp in points)

    def convert(self, tp, raw, resolution):
        limit = 1 << (resolution-1)
        if not -limit <= raw < limit:
            raise Inconclusive(f'{tp} : code ADC hors plage.')
        if raw <= -limit+1 or raw >= limit-2:
            raise Inconclusive(f'{tp} : ADC saturé, tension du TP non mesurable avec cette plage.')
        settings = self.profile['adc'][tp]
        return raw*LSB[resolution]*settings['gain']+settings['offset']

    def read(self, tp):
        self.check_cancel()
        self.stm32.adc_select(TP[tp][1])
        self.wait(.02)
        values = [self.convert(tp, self.stm32.adc_read(), self.stm32.adc_resolution) for _ in range(3)]
        return mean(values)

    def difference(self, tp, reference='TP17'):
        return self.read(tp)-self.read(reference)

    def analog_check(self, key, tp, reference='TP17', label=None):
        try:
            value = self.difference(tp, reference) if reference else self.read(tp)
            self.criterion(key, value, self.calibrated(tp, *([reference] if reference else [])), label=label)
            return value
        except Inconclusive as error:
            self.criterion(key, None, note=str(error), label=label)
            return None

    def gpio(self, tp, value=None):
        self.check_cancel()
        if value is None:
            return self.stm32.gpio_read('PWM', TP[tp][1])
        self.stm32.gpio_write('PWM', TP[tp][1], value)

    def logic(self, tp, expected, label):
        value = self.gpio(tp)
        self.row(label, value, status=PASS if value == expected else FAIL, note=f'{tp} attendu : {expected}.')

    def pwm(self, frequency=10, duty=0):
        self.check_cancel()
        self.stm32.pwm_set_duty(0)
        self.stm32.pwm_set_frequency(frequency)
        self.stm32.pwm_select(TP['TP18'][1])
        self.stm32.pwm_set_duty(duty)

    def initialise_outputs(self):
        for tp in ('TP11','TP23'):
            # Charger 0 dans le latch AVANT de passer la broche en sortie.
            self.stm32.gpio_write('PWM', TP[tp][1], 0)
            self.stm32.gpio_mode('PWM', TP[tp][1], 'OUT')
        self.pwm(duty=0)

    def quiet(self):
        self.gpio('TP11',0); self.gpio('TP23',0); self.pwm(duty=0)

    def capture(self, tp, frequency=None, duration_ms=5000, settle_s=0):
        if settle_s:
            # Attendre APRÈS la sélection : la charge du multiplexeur change
            # quand on change de TP, même si le PWM est déjà stabilisé.
            self.stm32.adc_select(TP[tp][1])
            self.wait(settle_s)
        data = self.stm32.capture('ADC', channel=TP[tp][1], duration_ms=duration_ms, cancel=self.cancel)
        records = data.channel(TP[tp][1])
        # Toujours conserver les codes bruts, y compris si la conversion sature.
        wave = {'section':self.section,'tp':tp,'frequency':frequency,
                'resolution':data.resolution,'records':data.records,'settle_s':settle_s,
                'elapsed_us':data.elapsed_us,'max_gpio_gap_us':data.max_gpio_gap_us,
                'calibrated':self.calibrated(tp),'golden_comparison':self.golden_active(),
                'time':[], 'voltage':[]}
        try:
            wave['time'] = [t/1e6 for t,raw,span in records]
            wave['voltage'] = [self.convert(tp,raw,data.resolution) for t,raw,span in records]
        finally:
            self.waves.append(wave); self.waveform_callback(wave)
        return wave

    def digital_capture(self, tp, duration_ms, frequency=None):
        data = self.stm32.capture('GPIO', pin=TP[tp][1], duration_ms=duration_ms, cancel=self.cancel)
        wave = {'section':self.section,'tp':tp,'frequency':frequency,'resolution':data.resolution,
                'elapsed_us':data.elapsed_us,'max_gpio_gap_us':data.max_gpio_gap_us,
                'records':data.records,'calibrated':True,
                'time':[t/1e6 for t,v,s in data.channel(16+TP[tp][1])],
                'voltage':[v for t,v,s in data.channel(16+TP[tp][1])], 'digital':True}
        self.waves.append(wave); self.waveform_callback(wave)
        return data

    def inactive_555(self, label):
        data = self.digital_capture('TP6', 1100)
        values = data.channel(16+TP['TP6'][1])
        status = PASS if len(values) >= 2 and all(v == 0 for t,v,s in values) else FAIL
        self.row(label, status=status, note=f'Observation 1,1 s; intervalle maximal de polling {data.max_gpio_gap_us/1000:.3f} ms. Des impulsions plus courtes peuvent échapper au banc.')
        self.visual('DS3 est-elle éteinte ?')

    def section_1(self):
        self.ask('Couper le +5 V externe. Relâcher SW1. Le câblage du banc reste en place. Confirmer quand fait.')
        self.visual('DS1 est-elle éteinte ?')
        self.ask('Appliquer le +5 V externe entre TP1 (+) et TP2 (−). Confirmer quand fait.')
        self.wait(.3)
        self.analog_check('ground','TP17',reference=None)
        self.analog_check('vcc','TP8')
        self.visual('DS1 est-elle allumée ?')
        self.initialise_outputs()

    def valim(self, enabled, label):
        self.wait(.15)
        try:
            value = self.difference('TP4')
            if enabled:
                value -= self.difference('TP8')
                key, points = 'valim_on_error', ('TP4','TP8','TP17')
            else:
                key, points = 'valim_off', ('TP4','TP17')
            self.criterion(key,value,self.calibrated(*points),label=label)
        except Inconclusive as error:
            self.row(label,status=UNKNOWN,note=str(error))
        self.visual('DS2 est-elle allumée ?' if enabled else 'DS2 est-elle éteinte ?')

    def section_2(self):
        self.quiet()
        self.valim(False,'SW1 relâché, STAY_ON=0 : VALIM')
        self.logic('TP14',1,'SW_IO au repos')
        self.gpio('TP11',1); self.valim(True,'STAY_ON=1 : VALIM − VCC')
        self.gpio('TP11',0)
        self.ask('Appuyer sur SW1 et le MAINTENIR enfoncé jusqu’à la prochaine consigne.')
        self.valim(True,'SW1 enfoncé : VALIM − VCC')
        self.logic('TP14',0,'SW_IO avec SW1 enfoncé')
        self.gpio('TP11',1)
        self.ask('Relâcher SW1. STAY_ON maintient maintenant VALIM.')
        self.valim(True,'Maintien après relâchement : VALIM − VCC')
        self.logic('TP14',1,'SW_IO après relâchement')
        self.gpio('TP11',0); self.valim(False,'Fin du maintien : VALIM')

    def section_3(self):
        self.quiet(); self.gpio('TP11',1); self.wait(.15)
        try:
            valim, divided = self.difference('TP4'), self.difference('TP3')
            if abs(valim) < .01:
                raise Inconclusive('VALIM trop faible pour calculer un rapport.')
            self.criterion('divider_ratio',divided/valim,self.calibrated('TP3','TP4','TP17'))
        except Inconclusive as error:
            self.criterion('divider_ratio',None,note=str(error))
        self.gpio('TP11',0); self.wait(.15)
        self.analog_check('divider_off','TP3')

    def section_4(self):
        self.quiet(); self.inactive_555('555 désactivé : TP6 reste bas')
        self.gpio('TP23',1); self.wait(.3)
        vcc = self.analog_check('vcc_555','TP8')
        if vcc is not None and self.calibrated('TP8','TP17') and not self.golden_check('vcc_555') and vcc < 4.5:
            self.gpio('TP23',0)
            self.row('Oscillation 555',status=UNKNOWN,note='VCC < 4,5 V : fonctionnement SA555 non garanti; test actif interrompu.')
        else:
            data = self.digital_capture('TP6',3000)
            try:
                m = digital_metrics(data.channel(23),data.max_gpio_gap_us)
                note = f"{m['cycles']} cycles; incertitude de détection ≤ {m['timing_uncertainty_us']/1000:.3f} ms."
                self.criterion('f555',m['frequency'],note=note, uncertainty=m['frequency']*2*m['timing_uncertainty_us']/m['period_us'])
                self.criterion('d555',m['duty'],note=note+f" Incertitude temporelle du rapport ≈ ±{m['duty_uncertainty_pct']:.2f} points.", uncertainty=m['duty_uncertainty_pct'])
            except Inconclusive as error:
                self.criterion('f555',None,note=str(error)); self.criterion('d555',None,note=str(error))
            self.visual('DS3 clignote-t-elle ?')
        self.gpio('TP23',0); self.inactive_555('555 désactivé en fin de test : TP6 reste bas')

    def vref(self):
        try:
            value = self.difference('TP35')-.5*self.difference('TP8')
            self.criterion('vref_error',value,self.calibrated('TP35','TP8','TP17'))
        except Inconclusive as error:
            self.criterion('vref_error',None,note=str(error))

    def adc_step(self, tp, bits):
        return LSB[bits]*self.profile['adc'][tp]['gain']

    def amplitude(self, tp, frequency, key=None, *, stable_gain=False):
        """Une seule source de mesure : la capture ADC du TP câblé."""
        source=f'ADC {tp}, fondamentale'
        try:
            # À 1 Hz, laisser de la marge aux premiers/derniers échantillons
            # pour disposer d'au moins cinq périodes entières.
            wave=self.capture(tp,frequency,
                              duration_ms=6000 if stable_gain else (5200 if frequency==1 else 5000),
                              settle_s=2 if stable_gain else 0)
            if self.golden_check(key or 'gain100') is not None and (frequency not in (10,100) or wave['resolution']!=12):
                raise Inconclusive('Cette référence Golden requiert la méthode et la résolution ADC 12 bits enregistrées à 10/100 Hz.')
            step=self.adc_step(tp,wave['resolution'])
            spans=[s/1e6 for c,t,v,s in wave['records'] if c==TP[tp][1]]
            m=fundamental(wave['time'],wave['voltage'],frequency,step,spans,
                          fit_quantization=stable_gain)
            if m['cycles']<5:
                raise Inconclusive('Moins de cinq périodes complètes utilisables.')
            if frequency<=10 and not m['qualified_bandwidth']:
                raise Inconclusive('Cadence trop faible pour le contrôle basse fréquence.')
        except Inconclusive as error:
            if key:self.criterion(key,None,note=str(error),source=source)
            else:self.row(f'{tp} à {frequency} Hz',status=UNKNOWN,note=str(error),source=source)
            return None
        # À 100 Hz, ne pas assimiler la tension filtrée par le MCP à
        # l'amplitude absolue de l'UUT. Comparaison sur la même chaîne Golden.
        qualified=self.calibrated(tp) and (m['qualified_bandwidth'] or
                  (frequency==100 and wave['resolution']==12 and self.profile['golden_ac_100hz']))
        if self.golden_check(key or 'gain100') is not None:
            qualified = m['qualified_bandwidth'] or (frequency==100 and wave['resolution']==12)
        note=(f"{m['sample_rate']:.1f} éch/s; {m['cycles']} périodes; "
              f"marge de répétabilité/résolution ±{m['uncertainty']:.4g} V.")
        if frequency==100:note+=' Amplitude indiquée par le banc à 100 Hz; comparaison sur la même chaîne que la Golden.'
        if stable_gain:
            note+=(f" Stabilisation 2 s après sélection; capture 6 s. Borne de quantification "
                   f"de l’ajustement : {m['quantization_bound']:.4g} V; gardes de dispersion et de répétabilité conservées.")
        if key:
            self.criterion(key,m['amplitude'],qualified,note,source,uncertainty=m['uncertainty'])
        else:
            self.row(f'Amplitude {tp} à {frequency} Hz',m['amplitude'],'V crête','INFO',note,source=source)
        return dict(value=m['amplitude'],u=m['uncertainty'],qualified=qualified,wave=wave,step=step)

    def tp29_at_1hz(self):
        """Deux acquisitions du même canal : témoin sans PWM et stimulus à 50 %."""
        source = 'ADC TP29 (16 bits), fondamentale à 1 Hz'
        previous = self.stm32.adc_resolution
        step = self.adc_step('TP29', 16)
        channel = TP['TP29'][1]
        estimates = []
        try:
            self.stm32.adc_set_resolution(16)
            self.stm32.adc_select(channel)
            for duty in (0, 50):
                self.pwm(1, duty)
                self.wait(5)
                wave = self.capture('TP29', frequency=1 if duty else 0, duration_ms=6000)
                if wave['resolution'] != 16:
                    raise Inconclusive('La capture reçue ne confirme pas le mode 16 bits.')
                spans = [s/1e6 for ch, t, raw, s in wave['records'] if ch == channel]
                estimates.append(sampled_1hz(wave['time'], wave['voltage'], step, spans))
            (rest, ur, _), (active, ua, cycles) = estimates
            if rest > max(4*step, 3*ur):
                raise Inconclusive('Composante à 1 Hz déjà détectable au repos.')
            if active <= 4*step or active - 3*ua <= rest + 3*ur:
                raise Inconclusive(f'À 1 Hz : PWM {active*1000:.3g} mV (marge {ua*1000:.3g}), '
                                   f'repos {rest*1000:.3g} mV (marge {ur*1000:.3g}). '
                                   'Réponse trop faible ou variable pour la distinguer du repos.')
        except Inconclusive as error:
            self.criterion('a1', None, note=str(error), source=source)
            return None
        finally:
            try:
                self.stm32.pwm_set_duty(0)
            finally:
                self.stm32.adc_set_resolution(previous)

        qualified = self.calibrated('TP29') or self.golden_check('a1') is not None
        note = (f'{cycles} périodes complètes; marge sur la fondamentale (répétabilité/résolution) ±{ua*1000:.3g} mV. '
                f'Au repos : composante à 1 Hz non résolue (marge {ur*1000:.3g} mV). '
                'Fondamentale de la trace du banc; réponse AC non compensée.')
        self.criterion('a1', active, qualified, note, source, uncertainty=ua)
        return dict(value=active, u=ua, qualified=qualified, wave=wave, step=step)

    def section_5(self):
        self.quiet(); self.wait(5); self.vref()
        self.analog_check('filter_dc','TP29','TP35',label='Filtre − Vref, Vs=0')
        self.pwm(duty=100); self.wait(5)
        self.analog_check('filter_dc','TP29','TP35',label='Filtre − Vref, Vs au niveau haut')
        readings=[self.tp29_at_1hz()]
        for frequency in (10,100):
            self.pwm(frequency,50); self.wait(5)
            readings.append(self.amplitude('TP29',frequency,f'a{frequency}'))
        status,value=UNKNOWN,None
        note='Comparer les intervalles de mesure des fondamentales à 1, 10 et 100 Hz.'
        if all(r is not None for r in readings):
            a,b,c=readings
            if all(r['value']+r['u'] < b['value']-b['u'] for r in (a,c)):
                value=True
            elif any(r['value']-r['u'] >= b['value']+b['u'] for r in (a,c)):
                value=False
            else:note+=' Intervalles superposés.'
            if value is not None:
                if all(r['qualified'] for r in readings):
                    status=PASS if value else FAIL
                else:
                    status=QUALIFY
                    note+=' Ordre observé au banc; étalonnage ou mesure à 100 Hz à confirmer.'
        else:note+=' Au moins une capture inexploitable.'
        self.row('Filtre : A1 < A10 et A100 < A10',value,status=status,note=note,source='ADC TP29')
        self.pwm(duty=0)

    def section_6(self):
        self.quiet(); self.wait(5); self.vref()
        self.analog_check('filter_dc','TP29','TP35')
        self.analog_check('amp_dc','TP31','TP29')
        self.pwm(100,50); self.wait(5)
        # Garder le stimulus inchangé entre les deux acquisitions de ce même ADC.
        a29,a31=(self.amplitude(tp,100,stable_gain=True) for tp in ('TP29','TP31'))
        source='ADC TP31 / ADC TP29'
        if a29 is None or a31 is None or a29['value']<=a29['u']:
            self.criterion('gain100',None,note='Amplitude entrée/sortie non résolue; pas de division.',source=source)
        else:
            gain=a31['value']/a29['value']
            lower=(a31['value']-a31['u'])/(a29['value']+a29['u'])
            upper=(a31['value']+a31['u'])/(a29['value']-a29['u'])
            qualified=a29['qualified'] and a31['qualified']
            self.criterion('gain100',gain,qualified,note=f'Captures successives sous le même stimulus. Intervalle estimé [{lower:.3g}; {upper:.3g}].',
                           source=source,uncertainty=max(gain-lower,upper-gain))
        self.clipping_at_5hz()
        self.pwm(duty=0)

    def clipping_at_5hz(self):
        """Contrôle de forme distinct du gain à 100 Hz, avec assez de points/cycle."""
        self.pwm(5,50)
        waves={}; problems={}
        for tp in ('TP29','TP31'):
            try:
                waves[tp]=self.capture(tp,5,duration_ms=6000,settle_s=2)
                if waves[tp]['resolution']!=12:
                    raise Inconclusive('Ce contrôle à 5 Hz requiert une capture ADC 12 bits.')
            except Inconclusive as error:
                problems[tp]=str(error)
        source='ADC TP31 à 5 Hz, contrôle TP29 si plateau de sortie'
        label='Absence de plateau d’écrêtage sur TP31 à 5 Hz'
        note=('PWM 5 Hz / 50 %, stabilisation 2 s par canal, capture 6 s. '
              'Plateaux d’au moins 15 % de période seulement. '
              'Ce contrôle ne conclut pas sur l’écrêtage à 100 Hz.')
        try:
            if 'TP31' in problems:raise Inconclusive(problems['TP31'])
            # Si aucun plateau n'est résolu en sortie, l'amplitude bien plus
            # faible de l'entrée ne doit pas invalider ce constat de forme.
            flat_out=flat_top_evidence(waves['TP31'],5,self.adc_step('TP31',12))
            if flat_out is None:
                raise Inconclusive('Plateau de sortie isolé; mesure à répéter.')
            if flat_out:
                if 'TP29' in problems:raise Inconclusive(problems['TP29'])
                flat_in=flat_top_evidence(waves['TP29'],5,self.adc_step('TP29',12))
                if flat_in is not False:
                    raise Inconclusive('Plateau aussi présent ou indéterminé à l’entrée; cause non attribuable à l’ampli.')
            qualified=self.calibrated('TP31') or self.golden_active()
            self.row(label,not flat_out,status=(FAIL if flat_out else PASS) if qualified else QUALIFY,
                     note=note,source=source)
        except Inconclusive as error:
            self.row(label,status=UNKNOWN,note=note+' '+str(error),source=source)

    def section_7(self):
        self.quiet()
        self.stm32.adc_select(TP['TP17'][1])
        self.pwm(1,50); self.wait(5)
        # Conserver le contrôle numérique seul, validé sur la Golden.
        sortie = self.digital_capture('TP30', 5000, frequency=1)
        points = sortie.channel(16 + TP['TP30'][1])
        source = f'GPIO6 / TP30 seul, acquisition {len(self.waves)}'
        note = (f'PWM TP18 commandé à 1 Hz, ADC fixe sur TP17. Intervalle maximal de lecture : '
                f'{sortie.max_gpio_gap_us/1000:.2f} ms. Impulsions plus courtes non exclues.')
        try:
            m = digital_metrics(points, sortie.max_gpio_gap_us)
        except Inconclusive as error:
            cycle = None
            note += f' {error}'
            self.criterion('schmitt_f', None, note=note, source=source)
        else:
            self.criterion('schmitt_f', m['frequency'], note=note, source=source,
                           uncertainty=2*m['frequency']*m['timing_uncertainty_us']/m['period_us'])
            cycle = one_edge_per_period(points)
        self.row('Un front de chaque sens par période du stimulus', cycle,
                 status=UNKNOWN if cycle is None else (PASS if cycle else FAIL),
                 note=note, source=source)

        self.schmitt_at_5hz()
        # Rétablir le clignotement lent pour l'observation visuelle de DS4.
        self.pwm(1,50); self.wait(1)
        self.visual('DS4 clignote-t-elle ?')
        self.pwm(duty=0); self.wait(5)
        final=self.digital_capture('TP30',1100)
        levels=[v for t,v,s in final.channel(16+TP['TP30'][1])]
        self.row('État final Schmitt',levels[-1] if levels else None,
                 status=PASS if len(levels)>=2 and len(set(levels))==1 else UNKNOWN,
                 note=f'Observation 1,1 s; polling maximal {final.max_gpio_gap_us/1000:.3f} ms. Haut ou bas possibles.')

    def schmitt_at_5hz(self):
        # Sur ce banc, sélectionner TP34 charge le seuil du comparateur.
        # Le mode SYNC permet de suivre TP31 et TP30 en gardant l'ADC sur TP31 :
        # les deux canaux demandés sont identiques, comme dans le diagnostic.
        self.pwm(5,50)
        self.stm32.adc_select(TP['TP31'][1])
        self.wait(2)
        data=self.stm32.capture('SYNC',channel=TP['TP31'][1],channel2=TP['TP31'][1],
                                pin=TP['TP30'][1],duration_ms=6000,cancel=self.cancel)
        self.stm32.adc_select(TP['TP17'][1])
        converted={}
        for tp in ('TP31','TP30'):
            digital=tp=='TP30'; source=TP[tp][1]+(16 if digital else 0)
            records=data.channel(source)
            try:
                values=[(t,raw if digital else self.convert(tp,raw,data.resolution),s) for t,raw,s in records]
                converted[tp]=values
            except Inconclusive as error:
                values=[];self.row(f'Acquisition {tp}',status=UNKNOWN,note=str(error),source=f'ADC {tp}')
            wave={'section':7,'tp':tp,'frequency':5,'resolution':data.resolution,'settle_s':2,
                  'elapsed_us':data.elapsed_us,'max_gpio_gap_us':data.max_gpio_gap_us,
                  'records':[r for r in data.records if r[0]==source],
                  'calibrated':digital or self.calibrated(tp),'golden_comparison':self.golden_active(),
                  'time':[t/1e6 for t,v,s in values],
                  'voltage':[v for t,v,s in values],'digital':digital}
            self.waves.append(wave);self.waveform_callback(wave)
        gpio=converted.get('TP30',[])
        src='Capture SYNC à 5 Hz : ADC fixe sur TP31, GPIO TP30'
        label='Commutation inverse TP31 / TP30 à 5 Hz'
        note=('PWM 5 Hz / 50 %. Niveaux apparents de la trace du banc, '
              'ADC fixe sur TP31; TP34 non sélectionné. '
              'Ce ne sont pas les tensions directement mesurées sur TP34 ni des seuils statiques. ')
        m=None
        try:
            if data.resolution!=12:
                raise Inconclusive('Ce contrôle à 5 Hz requiert une capture ADC 12 bits.')
            m=apparent_switching_levels(converted.get('TP31',[]),gpio,
                self.adc_step('TP31',12),data.max_gpio_gap_us)
            note+=(f"{m['falling']} fronts descendants / {m['rising']} montants; "
                   f"encadrement maximal {m['max_window_us']/1000:.2f} ms. ")
            self.row(label,m['inverted'],status=PASS if m['inverted'] else FAIL,note=note,source=src)
            if not m['inverted']:
                note+='Sens de commutation incorrect; seuils non publiés.'
        except Inconclusive as error:
            note+=str(error)
            self.row(label,status=UNKNOWN,note=note,source=src)
        qualified=self.calibrated('TP31') or self.golden_active()
        for key in ('vh','vl','width'):
            value=m[key] if m and m['inverted'] else None
            uncertainty=m[key+'_u'] if value is not None else 0
            detail=note
            if value is not None:
                detail+=f'Encadrement conservateur de la trace ±{uncertainty:.4g} V. '
            if key=='width':detail+='Critère de signe : VH − VL doit rester strictement positif après cette marge. '
            if key=='width' and value is not None and value-uncertainty<=0<value+uncertainty:
                self.row(CRITERIA['schmitt_width'][0],value,'V',UNKNOWN,
                         detail+'Les encadrements ne démontrent pas un écart positif.',source=src)
            else:
                self.criterion('schmitt_'+key,value,qualified,note=detail,source=src,uncertainty=uncertainty)

    def run_all(self, section_started=None, section_finished=None):
        """Toujours tenter STOP, y compris sur exception ou annulation opérateur."""
        started = section_started or (lambda n: None)
        finished = section_finished or (lambda n,s: None)
        primary_error = None
        try:
            self.stm32.stop()
            self.stm32.adc_set_resolution(12)
            for tp in ('TP6','TP14','TP30'):
                self.stm32.gpio_mode('PWM',TP[tp][1],'IN')
            for number in range(1,8):
                self.check_cancel(); self.section = number; started(number)
                begin = len(self.rows)
                getattr(self,f'section_{number}')()
                statuses = [r['status'] for r in self.rows[begin:]]
                result = FAIL if FAIL in statuses else UNKNOWN if UNKNOWN in statuses else QUALIFY if QUALIFY in statuses else PASS
                finished(number,result)
        except Exception as error:
            primary_error = error
            raise
        finally:
            try:
                self.stm32.stop()
            except Exception as stop_error:
                self.row('Arrêt des sorties non confirmé',status=UNKNOWN,
                         note=f'{stop_error}. Couper le +5 V externe.')
                if primary_error is None:
                    raise
        self.ask('Séquence terminée. Couper le +5 V externe, puis confirmer.')
        self.visual('Après coupure du +5 V externe, DS1, DS2, DS3 et DS4 sont-elles toutes éteintes ?')
        return self.rows

    # Méthodes du cours conservées, complétées et basées sur le mapping actuel.
    def test_voltage(self, adc_channel=TP['TP8'][1]):
        self.stm32.adc_select(adc_channel)
        return self.stm32.adc_read_voltage()

    def test_frequency(self,pwm_channel=7,adc_channel=7,frequency=10,duty=50):
        """Boucle PWM→ADC sur montage de diagnostic uniquement (pas l'UUT)."""
        try:
            self.stm32.pwm_set_duty(0); self.stm32.pwm_select(pwm_channel)
            self.stm32.pwm_set_frequency(frequency); self.stm32.pwm_set_duty(duty)
            self.stm32.adc_select(adc_channel)
            data = self.stm32.capture('ADC',channel=adc_channel,duration_ms=6000,cancel=self.cancel)
            raw = data.channel(adc_channel)
            values = [v*LSB[data.resolution] for t,v,s in raw]
            if not values or max(values)-min(values) < 8*LSB[data.resolution]:
                raise Inconclusive('Aucun signal exploitable sur l’ADC.')
            threshold = (max(values)+min(values))/2
            digital = [(t,int(v>threshold),s) for (t,r,s),v in zip(raw,values)]
            m = digital_metrics(digital,max((s for t,r,s in raw),default=0))
            return dict(frequency_requested=frequency,frequency_measured=m['frequency'],
                        duty_requested=duty,duty_measured=m['duty'],
                        voltage_min=min(values),voltage_max=max(values),
                        samples=[(t/1000,r) for t,r,s in raw])
        finally:
            self.stm32.stop()

    def test_frequency_response(self,frequency,pwm_channel=7,adc_channel=4):
        if not 1 <= frequency <= 100:
            raise ValueError('La fréquence doit être comprise entre 1 et 100 Hz.')
        try:
            self.stm32.pwm_set_duty(0); self.stm32.pwm_select(pwm_channel)
            self.stm32.pwm_set_frequency(frequency); self.stm32.pwm_set_duty(50)
            self.wait(5)
            data = self.stm32.capture('ADC',channel=adc_channel,duration_ms=5000,cancel=self.cancel)
            samples = data.channel(adc_channel)
            if not samples:
                raise Inconclusive('Aucun échantillon ADC reçu.')
            return dict(frequency=frequency,time=[t/1e6 for t,r,s in samples],
                        voltage=[r*LSB[data.resolution] for t,r,s in samples],
                        samples=[(t/1000,r) for t,r,s in samples])
        finally:
            self.stm32.stop()
