"""Gardes de mesure 2.3 : quantification, seuils apparents, migration du profil."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.models.measurements import (Inconclusive, fitted_quantization_bound,
    fundamental, apparent_switching_levels)
from src.models.test_runner import TestRunner
from src.hardware.stm32_hardware import Capture,Cancelled
from src.uut_config import CRITERIA,save_profile,load_profile
from src.golden_reference import METHOD,LEGACY_METHOD,comparison_problem
from installer_mesures_stables import install
from test_golden import reference_profile
from test_adc_sections import AdcBench,test_profile


class QuantizationTests(unittest.TestCase):
    def test_bound_covers_correlated_adversarial_quantization(self):
        phase=(np.arange(1423)*100/237.5)%1
        design=np.column_stack((np.ones(len(phase)),np.sin(2*np.pi*phase),np.cos(2*np.pi*phase)))
        projection=np.linalg.pinv(design)[1:3]
        step=.002; bound=fitted_quantization_bound(design,step)
        for theta in np.arange(0,2*np.pi,.071):
            direction=np.array((np.cos(theta),np.sin(theta)))
            error=step/2*np.sign(direction@projection)
            self.assertLessEqual(np.linalg.norm(projection@error),bound+1e-12)
        self.assertGreater(bound,.001)
        self.assertLess(bound,step)
        # Répéter les mêmes phases ne crée pas des bits supplémentaires.
        doubled=fitted_quantization_bound(np.vstack((design,design)),step)
        self.assertAlmostEqual(doubled,bound,places=12)

    def test_new_fit_keeps_sampling_signal_and_stationarity_guards(self):
        t=np.arange(0,6,1/237.5)
        for y in (np.ones(len(t)),.001*np.sin(2*np.pi*100*t),
                  np.sin(2*np.pi*100*t)*(1+3*(t>3))):
            with self.assertRaises(Inconclusive):
                fundamental(t,y,100,.002,fit_quantization=True)
        t=np.arange(0,6,.01)
        with self.assertRaises(Inconclusive):
            fundamental(t,np.sin(2*np.pi*100*t),100,.002,fit_quantization=True)


def synthetic_switching(hysteresis=True):
    # Triangle 1,5 à 3,5 V. Sans hystérésis, les deux commutations se font à 2,5 V.
    period=200000
    phases=(.325,.825) if hysteresis else (.25,.75)
    end=6000000; gpio=[(0,1,1000)]
    for start in range(0,end,period):
        gpio.extend((int(start+phase*period+500),level,1000) for phase,level in zip(phases,(0,1)))
    gpio.append((end,1,1000))
    analog=[]
    for t in range(2500,end-5000,9000):
        phase=(t%period)/period
        v=1.5+4*phase if phase<.5 else 5.5-4*phase
        analog.append((t,round(v/.002)*.002,4000))
    return analog,gpio


class ApparentThresholdTests(unittest.TestCase):
    def test_known_thresholds_are_inside_full_uncertainty_intervals(self):
        a,g=synthetic_switching()
        m=apparent_switching_levels(a,g,.002,1000)
        self.assertTrue(m['inverted'])
        for key,expected in (('vh',2.8),('vl',2.2),('width',.6)):
            self.assertLessEqual(abs(m[key]-expected),m[key+'_u'])
        self.assertGreater(m['width']-m['width_u'],0)
        self.assertGreaterEqual(min(m['falling'],m['rising']),29)

    def test_zero_hysteresis_does_not_gain_a_positive_certificate(self):
        a,g=synthetic_switching(False)
        m=apparent_switching_levels(a,g,.002,1000)
        self.assertLessEqual(m['width']-m['width_u'],0)
        self.assertGreaterEqual(m['width']+m['width_u'],0)
        bench=AdcBench();p=test_profile()
        r=TestRunner(bench,p);r.wait=lambda seconds:None;r.section=7
        data=Capture(sorted([(3,t,round(v/.003),span) for t,v,span in a]+
                            [(22,t,v,span) for t,v,span in g],key=lambda r:r[1]),12,6000000,1000)
        with patch.object(bench,'capture',return_value=data):r.schmitt_at_5hz()
        width=next(x for x in r.rows if x['test']==CRITERIA['schmitt_width'][0])
        self.assertEqual(width['status'],'NON CONCLUANT')

    def test_missing_flat_nonmonotone_and_slow_data_are_rejected(self):
        a,g=synthetic_switching()
        with self.assertRaises(Inconclusive):apparent_switching_levels([(t,2.5,s) for t,v,s in a],g,.002,1000)
        with self.assertRaises(Inconclusive):apparent_switching_levels(a[::4],g,.002,1000)
        with self.assertRaises(Inconclusive):apparent_switching_levels(a,[g[0],g[-1]],.002,1000)
        with self.assertRaises(Inconclusive):apparent_switching_levels(a,g,.002,50000)
        broken=list(a)
        index=next(i for i,(t,v,s) in enumerate(a) if 65000<t<70000)
        broken[index]=(broken[index][0],3.5,4000)
        with self.assertRaisesRegex(Inconclusive,'non monotone'):
            apparent_switching_levels(broken,g,.002,1000)

    def test_stabilisation_precedes_each_capture_and_does_not_select_tp34(self):
        bench=AdcBench();r=TestRunner(bench,test_profile());r.section=6
        r.wait=lambda seconds:bench.commands.append(('WAIT',seconds))
        r.section_6()
        for i,cmd in enumerate(bench.commands):
            if cmd[:2]==('CAP','ADC'):
                self.assertEqual(bench.commands[i-2:i],[('ADC',cmd[2]),('WAIT',2)])
                self.assertEqual(cmd[-1],6000)
        self.assertNotIn(('ADC',2),bench.commands)

    def test_clipped_output_fails_when_input_shape_is_resolved(self):
        bench=AdcBench();r=TestRunner(bench,test_profile());r.section=6;r.wait=lambda seconds:None
        def acquire(mode,channel,**kwargs):
            records=[]
            for t in range(4000,6000000,4000):
                v=np.sin(2*np.pi*5*t/1e6)
                if channel==3:v=float(np.clip(v,-.6,.6))
                records.append((channel,t,round((2.33+v)/.003),4000))
            return Capture(records,12,6000000,0)
        with patch.object(bench,'capture',side_effect=acquire):r.clipping_at_5hz()
        self.assertEqual(r.rows[-1]['status'],'FAIL')


class MigrationTests(unittest.TestCase):
    def old_profile(self):
        p=reference_profile();g=p['golden'];g['method']=LEGACY_METHOD
        g['checks']=[r for r in g['checks'] if r['section']!=7]
        gain=next(r for r in g['checks'] if r['key']=='gain100')
        gain['reference']=g['migration']['previous_gain_reference']
        gain['tolerance']=.987
        g['checks'][0]['tolerance']=.004
        p['limits']['f555']={'min':8.,'max':13.3}
        return p

    def test_migration_preserves_user_tuning_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'profile.json';old=self.old_profile();save_profile(old,path)
            before=path.read_bytes();backup=install(path);new=load_profile(path)
            self.assertEqual(backup.read_bytes(),before)
            self.assertEqual(new['adc'],old['adc'])
            self.assertEqual(new['golden']['checks'][0]['tolerance'],.004)
            self.assertEqual(next(x for x in new['golden']['checks'] if x['key']=='gain100')['tolerance'],.987)
            self.assertEqual(new['limits']['f555'],old['limits']['f555'])
            self.assertEqual(new['limits']['schmitt_width'],{'min':0.,'max':None})
            self.assertEqual(new['golden']['method'],METHOD)
            for row in new['golden']['checks'][-2:]:self.assertIsNone(row['tolerance'])
            current=path.read_bytes();self.assertIsNone(install(path));self.assertEqual(current,path.read_bytes())

    def test_incompatible_profiles_are_not_overwritten(self):
        for mutation in (lambda p:p['adc']['TP31'].update(gain=3),
                         lambda p:p['limits']['schmitt_vh'].update(min=2),
                         lambda p:p['golden'].update(source_report_sha256='another-board')):
            with tempfile.TemporaryDirectory() as folder:
                path=Path(folder)/'profile.json';p=self.old_profile();mutation(p);save_profile(p,path)
                before=path.read_bytes()
                with self.assertRaises(ValueError):install(path)
                self.assertEqual(path.read_bytes(),before)

    def test_legacy_profile_requires_migration_before_comparison(self):
        p=self.old_profile()
        self.assertIn('installer_mesures_stables.py',comparison_problem(p,p['golden']['firmware']))


if __name__=='__main__':unittest.main()
