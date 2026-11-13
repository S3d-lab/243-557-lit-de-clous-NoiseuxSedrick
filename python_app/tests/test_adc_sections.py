"""Vérifications ciblées V2.1 : aucune valeur électrique ne vient d'un opérateur."""
from pathlib import Path
import sys
import math
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from src.uut_config import default_profile,validate_profile,CRITERIA
from src.hardware.stm32_hardware import Capture
from src.models.measurements import fundamental, flat_top_evidence, one_edge_per_period, Inconclusive, schmitt_estimates
from src.models.test_runner import TestRunner
from test_uut import FakeBench

class AdcBench(FakeBench):
    ground=.15
    def __init__(self):
        super().__init__();self.ac_mode='normal';self.sync_mode='normal'
    def adc_read(self):
        if self.channel==0:return round(self.ground/.003)
        return super().adc_read()
    @staticmethod
    def level(t):return int(not (325000 <= t%1000000 < 825000))
    def capture(self,mode,channel=0,channel2=0,pin=0,duration_ms=3000,cancel=None):
        if mode=='SYNC':
            self.commands.append(('CAP',mode,channel,channel2,pin,duration_ms))
            period=1000000/self.frequency if channel==channel2 else 1000000
            def level(t):return int(not (.325 <= (t%period)/period < .825))
            end=duration_ms*1000; rec=[(16+pin,1000,level(1000),1000)]
            for start in range(round(.325*period),end,round(period/2)):
                if self.sync_mode!='no_edges':rec.append((16+pin,start+500,level(start+500),1000))
            rec.append((16+pin,end,rec[-1][2],1000))
            for t in range(2500,end-5000,9000 if channel==channel2 else 10000):
                phase=t%period/period
                v=1.5+4*phase if phase<.5 else 5.5-4*phase
                if self.sync_mode=='no_crossing':v=2.5
                if self.sync_mode=='inverted_direction':v=5.0-v
                if self.sync_mode=='saturated':v=8.
                if channel==channel2:
                    rec.append((channel,t,round((v+self.ground)/.003),4000))
                    continue
                vref=2.8 if self.level(t+5000) else 2.2
                if self.sync_mode=='inverted_direction':vref=5.0-vref
                if self.sync_mode=='missing_reference':vref=8
                rec.extend([(channel,t,round((v+self.ground)/.003),4000),
                            (channel2,t+5000,round((vref+self.ground)/.003),4000)])
            return Capture(sorted(rec,key=lambda r:r[1]),12,end,1000)
        if mode=='ADC' and self.frequency==100 and self.ac_mode=='slow':
            return Capture([(channel,t,900+round(80*math.sin(2*math.pi*100*t/1e6)),7000)
                           for t in range(7000,duration_ms*1000,7000)],12,duration_ms*1000,0)
        base=super().capture(mode,channel,channel2,pin,duration_ms,cancel)
        if mode=='ADC' and self.frequency==100 and self.ac_mode=='saturated':
            base.records=[(ch,t,2047 if ch==3 else raw,span) for ch,t,raw,span in base.records]
        if mode=='ADC' and self.frequency==100 and self.ac_mode=='quiet_input' and channel==4:
            base.records=[(ch,t,900,span) for ch,t,raw,span in base.records]
        return base

def test_profile():
    p=default_profile();p['golden_ac_100hz']=True
    for a in p['adc'].values():a.update(gain=3.,confirmed=True)
    for k,bounds in {'a100':(.12,.18),'gain100':(4.5,5.5),'schmitt_vh':(2.60,3.30),
                     'schmitt_vl':(2.00,2.70),'schmitt_width':(0,1.3),
                     'schmitt_f':(.98,1.02)}.items():
        p['limits'][k]=dict(zip(('min','max'),bounds))
    return p

class AutomatedSections(unittest.TestCase):
    def setUp(self):
        self.bench=AdcBench();self.requests=[]
        def answer(request):
            self.requests.append(request)
            self.assertIn(request['kind'],('visual','action'))
            if request['kind']=='visual':self.assertIn('DS',request['text'])
            else:self.assertTrue('+5 V' in request['text'] or 'SW1' in request['text'])
            if 'MAINTENIR' in request['text']:self.bench.button=True
            if 'Relâcher SW1.' in request['text']:self.bench.button=False
            return True
        self.run_tests=TestRunner(self.bench,test_profile(),ask=answer)
        self.run_tests.wait=lambda seconds:self.run_tests.check_cancel()
    def row(self,name):return next(r for r in self.run_tests.rows if r['test']==name)
    def run_part(self,n):
        self.run_tests.section=n;getattr(self.run_tests,'section_'+str(n))()
    def test_all_sections_only_power_button_led_interventions(self):
        parts=[];self.run_tests.run_all(section_started=parts.append)
        self.assertEqual(parts,list(range(1,8)))
        self.assertEqual({r['text'] for r in self.requests if r['kind']=='visual' and r['section']==7},
            {'DS4 clignote-t-elle ?','Après coupure du +5 V externe, DS1, DS2, DS3 et DS4 sont-elles toutes éteintes ?'})
        self.assertEqual(self.bench.commands[-1],('STOP',))
        self.assertTrue(all('oscillo' not in r['source'].lower() for r in self.run_tests.rows))
    def test_gain_at_100hz_and_separate_resolved_shape_at_5hz(self):
        self.run_part(6);r=self.row('Gain AC à 100 Hz')
        self.assertEqual(r['status'],'PASS');self.assertAlmostEqual(r['value'],5,delta=.06)
        self.assertIn('TP29',r['source'])
        shape=self.row('Absence de plateau d’écrêtage sur TP31 à 5 Hz')
        self.assertEqual(shape['status'],'PASS');self.assertIn('ne conclut pas',shape['note'])
        self.assertEqual(self.requests,[])
    def test_outside_gain_limit_fails_without_operator(self):
        self.run_tests.profile['limits']['gain100']={'min':2.,'max':3.}
        self.run_part(6);self.assertEqual(self.row('Gain AC à 100 Hz')['status'],'FAIL')
    def test_missing_golden_keeps_valid_estimate_without_pass(self):
        del self.run_tests.profile['golden_ac_100hz']
        self.run_tests.profile=validate_profile(self.run_tests.profile)
        self.run_part(6);r=self.row('Gain AC à 100 Hz')
        self.assertIsNotNone(r['value']);self.assertEqual(r['status'],'À QUALIFIER')
    def test_slow_noisy_or_saturated_trace_cannot_pass_as_a_gain(self):
        for mode in ('slow','saturated','quiet_input'):
            with self.subTest(mode=mode):
                self.run_tests.rows.clear();self.bench.ac_mode=mode;self.run_part(6)
                r=self.row('Gain AC à 100 Hz');self.assertIsNone(r['value']);self.assertEqual(r['status'],'NON CONCLUANT')
                # Une capture mauvaise à 100 Hz n'invalide pas une autre
                # acquisition exploitable de la forme à 5 Hz.
                self.assertEqual(self.row('Absence de plateau d’écrêtage sur TP31 à 5 Hz')['status'],'PASS')
                self.assertEqual(self.requests,[])
    def test_filter_observed_amplitudes_and_missing_high_frequency(self):
        self.run_part(5);r=self.row('Fondamentale filtre à 100 Hz (crête)')
        self.assertEqual(r['status'],'PASS');self.assertAlmostEqual(r['value'],.15,delta=.004)
        self.assertEqual(self.row('Filtre : A1 < A10 et A100 < A10')['status'],'PASS')
        self.run_tests.rows.clear();self.bench.ac_mode='slow';self.run_part(5)
        self.assertEqual(self.row('Filtre : A1 < A10 et A100 < A10')['status'],'NON CONCLUANT')

    def test_filter_shows_observed_order_without_qualifying_the_profile(self):
        self.run_tests.profile['golden_ac_100hz']=False
        self.run_part(5)
        row=self.row('Filtre : A1 < A10 et A100 < A10')
        self.assertIs(row['value'],True)
        self.assertEqual(row['status'],'À QUALIFIER')

    def test_tp29_change_outside_1hz_does_not_mask_or_create_a_fundamental(self):
        # Harmonique 3 Hz dont l'amplitude change à chaque cycle; avec puis
        # sans fondamentale. L'amplitude/phase à 1 Hz est la grandeur testée.
        for a1 in (20,0):
            with self.subTest(a1=a1):
                def acquire(mode, channel, duration_ms, cancel):
                    rec=[]
                    for i in range(90):
                        p=2*math.pi*i/15
                        a3=90 if (i//15)%2 else 15
                        ac=round(a1*math.sin(p)+a3*math.sin(3*p))
                        rec.append((channel,33000+round(i*1e6/15),
                                    5000+(ac if self.bench.duty else 0),66667))
                    return Capture(rec,16,6000000,0)
                with patch.object(self.bench,'capture',side_effect=acquire):
                    result=self.run_tests.tp29_at_1hz()
                if a1:
                    self.assertIsNotNone(result)
                    self.assertAlmostEqual(result['value'],a1*.0001875,delta=.00016)
                else:
                    self.assertIsNone(result)
                    self.assertEqual(self.run_tests.rows[-1]['status'],'NON CONCLUANT')
    def test_tp29_repeatable_harmonics_do_not_hide_the_fundamental(self):
        # Petit signal à 1 Hz, harmonique nettement plus forte et offset continu.
        def acquire(mode, channel, duration_ms, cancel):
            self.assertEqual((mode,channel,duration_ms,self.bench.adc_resolution),('ADC',4,6000,16))
            rec=[]
            for t in range(33500,6000000,66700):
                p=2*math.pi*t/1e6
                raw=5000+(round(20*math.sin(p)+120*math.cos(3*p)) if self.bench.duty else 0)
                rec.append((channel,t,raw,66700))
            return Capture(rec,16,6000000,0)
        self.run_tests.profile['adc']['TP29']['confirmed']=False
        with patch.object(self.bench,'capture',side_effect=acquire):
            result=self.run_tests.tp29_at_1hz()
        self.assertAlmostEqual(result['value'],20*.0001875,delta=.00016)
        self.assertEqual(self.run_tests.rows[-1]['status'],'À QUALIFIER')
        self.assertEqual([w['frequency'] for w in self.run_tests.waves],[0,1])
        self.assertEqual((self.bench.duty,self.bench.adc_resolution),(0,12))

    def test_tp29_rejects_quiet_pickup_unstable_or_incomplete_captures(self):
        for fault in ('silence','signal_au_repos','phase_variable','trou','mauvaise_resolution'):
            with self.subTest(fault=fault):
                def acquire(mode, channel, duration_ms, cancel):
                    rec=[]
                    for t in range(33000,6000000,66667):
                        if fault=='trou' and 2e6<t<2.2e6:continue
                        drive=(self.bench.duty!=0 or fault=='signal_au_repos') and fault!='silence'
                        sign=-1 if fault=='phase_variable' and (t//1000000)%2 else 1
                        rec.append((channel,t,700+round(50*drive*sign*math.sin(2*math.pi*t/1e6)),66667))
                    return Capture(rec,12 if fault=='mauvaise_resolution' else 16,6000000,0)
                with patch.object(self.bench,'capture',side_effect=acquire):
                    self.assertIsNone(self.run_tests.tp29_at_1hz())
                self.assertEqual(self.run_tests.rows[-1]['status'],'NON CONCLUANT')
                self.assertEqual((self.bench.duty,self.bench.adc_resolution),(0,12))

    def test_schmitt_fixed_channel_measures_apparent_levels_at_5hz(self):
        self.run_part(7)
        r=self.row('Commutation inverse TP31 / TP30 à 5 Hz')
        self.assertIs(r['value'],True);self.assertEqual(r['status'],'PASS')
        self.assertEqual(self.row('Un front de chaque sens par période du stimulus')['status'],'PASS')
        for key in ('schmitt_vh','schmitt_vl','schmitt_width'):
            row=self.row(CRITERIA[key][0])
            self.assertIsNotNone(row['value']);self.assertEqual(row['status'],'PASS')
            self.assertIn('TP34 non sélectionné',row['note'])
        self.assertNotIn(('ADC',2),self.bench.commands)
        sync=[c for c in self.bench.commands if c[:2]==('CAP','SYNC')]
        self.assertEqual(sync,[('CAP','SYNC',3,3,6,6000)])
        self.assertEqual(self.bench.channel,0)
        self.assertEqual({w['tp'] for w in self.run_tests.waves},{'TP30','TP31'})
        self.assertEqual(self.row('État final Schmitt')['status'],'PASS')

    def test_schmitt_fixed_channel_requires_resolved_input_and_output_changes(self):
        for mode in ('no_crossing','no_edges','saturated'):
            with self.subTest(mode=mode):
                self.bench.sync_mode=mode;self.run_tests.rows.clear();self.run_part(7)
                r=self.row('Commutation inverse TP31 / TP30 à 5 Hz')
                self.assertIsNone(r['value']);self.assertEqual(r['status'],'NON CONCLUANT')
                for key in ('schmitt_vh','schmitt_vl','schmitt_width'):
                    self.assertEqual(self.row(CRITERIA[key][0])['status'],'NON CONCLUANT')

    def test_schmitt_wrong_observed_direction_is_reported_without_inventing_thresholds(self):
        self.bench.sync_mode='inverted_direction';self.run_part(7)
        r=self.row('Commutation inverse TP31 / TP30 à 5 Hz')
        self.assertIs(r['value'],False);self.assertEqual(r['status'],'FAIL')
        self.assertIsNone(self.row(CRITERIA['schmitt_vh'][0])['value'])

    def test_threshold_math_remains_available_for_a_suitable_acquisition(self):
        # Le calcul historique reste testé, mais ne commande plus TP34 sur ce banc.
        cap=self.bench.capture('SYNC',3,2,6,5000)
        def voltage(ch):
            return [(t,raw*.003-self.bench.ground,span) for t,raw,span in cap.channel(ch)]
        h=schmitt_estimates(voltage(3),cap.channel(22),voltage(2),.003,.003)
        for key,expected in [('vh',2.8),('vl',2.2),('width',.6)]:
            self.assertAlmostEqual(h[key],expected,delta=.035)
    def test_old_profile_stays_intact(self):
        old=test_profile();del old['golden_ac_100hz'];old['adc']['TP4']['offset']=.015
        new=validate_profile(old)
        self.assertFalse(new['golden_ac_100hz']);self.assertEqual(new['adc'],old['adc']);self.assertEqual(new['limits'],old['limits'])

class SamplingChecks(unittest.TestCase):
    def test_phase_coverage_and_nonstationary_amplitude(self):
        t=np.arange(0,5,.004);y=np.sin(2*np.pi*100*t)*(1+(t>2.5)*3)
        with self.assertRaises(Inconclusive):fundamental(t,y,100,.001)
        t=np.array([k*.01+offset for k in range(500) for offset in (0,.0001,.0002)])
        with self.assertRaises(Inconclusive):fundamental(t,np.sin(2*np.pi*100*t),100,.001)
    def test_resolved_plateau_and_real_sample_density(self):
        t=np.arange(0,.1,.0001);y=np.sin(2*np.pi*100*t)
        wave={'time':t,'voltage':y,'records':[(1,int(v*1e6),0,100) for v in t]}
        self.assertFalse(flat_top_evidence(wave,100,.001))
        wave['voltage']=np.clip(y,-.6,.6);self.assertTrue(flat_top_evidence(wave,100,.001))
        wave['records']=[(1,0,0,4000)]*len(t)
        with self.assertRaises(Inconclusive):flat_top_evidence(wave,100,.001)
    def test_short_or_extra_gpio_pulses_are_not_passed_as_one_per_cycle(self):
        gpio=[(0,0,1000)]+[(t,(i+1)%2,1000) for i,t in enumerate(range(200000,5000000,500000))]+[(5000000,0,1000)]
        self.assertTrue(one_edge_per_period(gpio));gpio.extend([(2000000,1,1000),(2040000,0,1000)]);gpio.sort()
        self.assertFalse(one_edge_per_period(gpio))
