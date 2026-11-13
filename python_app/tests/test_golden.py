"""Contrat de comparaison Golden et conservation des résultats non mesurables."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.uut_config import default_profile, validate_profile, save_profile, load_profile, CRITERIA
from src.models.test_runner import TestRunner
from src.models.measurements import Inconclusive
from test_adc_sections import AdcBench
from install_golden import install


def reference_profile():
    profile=default_profile()
    for settings in profile['adc'].values():settings['gain']=2.
    profile['golden']=json.loads((ROOT/'golden_20261106.json').read_text(encoding='utf-8'))
    return validate_profile(profile)


class GoldenTests(unittest.TestCase):
    def setUp(self):
        self.profile=reference_profile()
        self.bench=AdcBench()
        self.bench.firmware=self.profile['golden']['firmware']
        self.runner=TestRunner(self.bench,self.profile,ask=lambda request:True)
        self.runner.wait=lambda seconds:self.runner.check_cancel()

    def test_known_board_target_and_outside_values_without_absolute_calibration(self):
        self.runner.section=1
        self.assertFalse(self.runner.calibrated('TP8'))
        row=self.runner.criterion('vcc',3.536,qualified=False)
        self.assertEqual(row['status'],'PASS')
        self.assertIn('comparaison Golden',row['source'])
        self.assertNotIn('Repère nominal : 4.66',row['note'])
        self.assertAlmostEqual(row['minimum'],3.1824)
        self.assertEqual(self.runner.criterion('vcc',2.5,False)['status'],'FAIL')
        self.assertEqual(self.runner.criterion('vcc',None,False)['status'],'NON CONCLUANT')
        self.assertEqual(self.runner.criterion('vcc',float('nan'),False)['status'],'NON CONCLUANT')

    def test_offset_calibration_or_firmware_change_does_not_reuse_reference(self):
        self.runner.section=1
        for change in ('gain','offset','firmware'):
            with self.subTest(change=change):
                original=self.bench.firmware
                if change=='firmware':self.bench.firmware='autre firmware'
                else:self.runner.profile['adc']['TP8'][change]+=1
                self.assertEqual(self.runner.criterion('vcc',3.536,True)['status'],'À QUALIFIER')
                if change=='firmware':self.bench.firmware=original
                else:self.runner.profile['adc']['TP8'][change]-=1
        self.assertTrue(self.runner.golden_active())

    def test_zero_and_missing_tolerances_and_boundary_uncertainty(self):
        self.runner.section=1
        check=self.runner.golden_check('vcc')
        check['tolerance']=None
        self.assertEqual(self.runner.criterion('vcc',3.536,False)['status'],'À QUALIFIER')
        check['tolerance']=0.
        self.assertEqual(self.runner.criterion('vcc',3.536,False,uncertainty=.002)['status'],'NON CONCLUANT')
        check['tolerance']=.1
        self.assertEqual(self.runner.criterion('vcc',3.63,False,uncertainty=.01)['status'],'NON CONCLUANT')
        self.assertEqual(self.runner.criterion('vcc',3.7,False,uncertainty=.01)['status'],'FAIL')

    def test_large_gain_uncertainty_remains_inconclusive_at_the_golden_value(self):
        self.runner.section=6
        target=self.runner.golden_check('gain100')['reference']
        self.assertEqual(self.runner.criterion('gain100',target,False,uncertainty=3.43)['status'],'NON CONCLUANT')

    def test_reference_depends_on_section_and_stimulus(self):
        refs={}
        for section,label in [(5,'Filtre − Vref, Vs=0'),(5,'Filtre − Vref, Vs au niveau haut'),
                              (6,'Filtre − Vref en continu')]:
            self.runner.section=section
            refs[(section,label)]=self.runner.golden_check('filter_dc',label)['reference']
        self.assertEqual(len(set(refs.values())),3)
        self.runner.section=5
        val=self.runner.golden_check('vref_error')['reference']
        self.runner.section=6
        self.assertEqual(self.runner.criterion('vref_error',val,False)['status'],'FAIL')

    def test_disable_restores_original_mode_and_digital_limits_are_unchanged(self):
        self.runner.section=1
        self.runner.profile['golden']['enabled']=False
        self.assertEqual(self.runner.criterion('vcc',3.536,False)['status'],'À QUALIFIER')
        self.runner.profile['limits']['f555']={'min':8.,'max':13.3}
        self.runner.profile['golden']['enabled']=True
        self.runner.section=4
        self.assertEqual(self.runner.criterion('f555',11.9)['status'],'PASS')
        self.assertEqual(self.runner.criterion('f555',20.)['status'],'FAIL')

    def test_555_digital_capture_is_not_stopped_by_a_golden_front_end_reading(self):
        self.runner.section=4
        # Ce test protège aussi contre la réactivation ultérieure des cases
        # d'étalonnage : le mode Golden reste une comparaison de lectures.
        for p in self.runner.profile['adc'].values():p['confirmed']=True
        self.runner.section_4()
        self.assertTrue(any(c[:5]==('CAP','GPIO',0,0,7) and c[-1]==3000 for c in self.bench.commands))

    def test_unresolved_analog_still_fails_and_tp34_is_never_selected(self):
        for raw in (2047,-2048,4095):
            with self.assertRaises(Inconclusive):self.runner.convert('TP8',raw,12)
        self.runner.section=6;self.bench.ac_mode='saturated'
        self.runner.section_6()
        gain=next(r for r in self.runner.rows if r['test']=='Gain AC à 100 Hz')
        self.assertEqual(gain['status'],'NON CONCLUANT')
        self.runner.section=7;self.bench.sync_mode='no_crossing';self.runner.section_7()
        self.assertNotIn(('ADC',2),self.bench.commands)
        for name in ('Commutation inverse TP31 / TP30 à 5 Hz',*(CRITERIA[k][0] for k in ('schmitt_vh','schmitt_vl','schmitt_width'))):
            self.assertEqual(next(r for r in self.runner.rows if r['test']==name)['status'],'NON CONCLUANT')

    def test_golden_does_not_accept_another_capture_resolution(self):
        self.runner.section=5
        wave={'time':[0.,1.], 'voltage':[0.,.1], 'resolution':16, 'records':[]}
        with patch.object(self.runner,'capture',return_value=wave):
            self.assertIsNone(self.runner.amplitude('TP29',100,'a100'))
        self.assertEqual(self.runner.rows[-1]['status'],'NON CONCLUANT')

    def test_invalid_reference_does_not_silently_pass(self):
        for mutate in (lambda p:p['golden']['checks'].pop(),
                       lambda p:p['golden']['checks'][0].update(tolerance=-.1),
                       lambda p:p['golden']['checks'][0].update(reference=float('inf'))):
            p=reference_profile();mutate(p)
            with self.assertRaises(ValueError):validate_profile(p)

    def test_install_preserves_limits_and_calibration_and_creates_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'uut_profile.json'
            old=reference_profile();del old['golden']
            old['limits']['f555']={'min':8.,'max':13.3}
            save_profile(old,path)
            before=path.read_bytes();backup=install(path)
            self.assertEqual(backup.read_bytes(),before)
            new=load_profile(path)
            self.assertEqual(new['adc'],old['adc'])
            self.assertEqual(new['limits'],old['limits'])
            new['golden']['checks'][0]['tolerance']=.005;save_profile(new,path)
            self.assertIsNone(install(path))
            self.assertEqual(load_profile(path)['golden']['checks'][0]['tolerance'],.005)
            wrong=default_profile();save_profile(wrong,path);before=path.read_bytes()
            with self.assertRaises(ValueError):install(path)
            self.assertEqual(path.read_bytes(),before)


class GoldenGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def test_editing_golden_tolerance_updates_bounds_without_changing_adc(self):
        from src.views.profile_dialog import ProfileDialog
        p=reference_profile();dialog=ProfileDialog(p)
        i=next(i for i,r in enumerate(dialog.golden_rows) if r['key']=='vcc')
        dialog.golden.item(i,3).setText('0,1')
        self.assertAlmostEqual(float(dialog.golden.item(i,4).text()),3.436)
        dialog.save()
        self.assertEqual(dialog.profile['adc'],p['adc'])
        self.assertEqual(dialog.profile['golden']['checks'][i]['tolerance'],.1)
        self.assertTrue(dialog.profile['golden']['enabled'])
        self.assertEqual(dialog.profile['limits'],p['limits'])
        self.assertAlmostEqual(p['golden']['checks'][i]['tolerance'],.3536)

    def test_legacy_dialog_and_disabled_golden_keep_original_profile(self):
        from src.views.profile_dialog import ProfileDialog
        old=default_profile();dialog=ProfileDialog(old);dialog.save()
        self.assertEqual(dialog.profile,old)
        dialog=ProfileDialog(reference_profile());dialog.golden_enabled.setChecked(False);dialog.save()
        self.assertFalse(dialog.profile['golden']['enabled'])


if __name__=='__main__':unittest.main()
