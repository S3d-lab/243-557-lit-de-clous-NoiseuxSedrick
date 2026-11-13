"""Tests hors matériel. Exécuter depuis la racine: python -m unittest discover -s python_app/tests -v"""
import math
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.uut_config import TP,default_profile,validate_profile
from src.models.measurements import digital_metrics,fundamental,Inconclusive,schmitt_estimates
from src.hardware.stm32_hardware import STM32Hardware,Capture,Cancelled
from src.models.test_runner import TestRunner

class FakeBench:
    def __init__(self):
        self.commands=[];self.outputs={3:0,4:0};self.adc_resolution=12;self.channel=0
        self.button=False;self.duty=0;self.frequency=10;self.stop_count=0
    def stop(self):
        self.commands.append(('STOP',));self.stop_count+=1;self.duty=0;self.outputs={3:0,4:0};return 'OK'
    def adc_set_resolution(self,res):self.adc_resolution=res
    def adc_select(self,ch):self.channel=ch;self.commands.append(('ADC',ch));return 'OK'
    def gpio_mode(self,board,pin,mode):self.commands.append(('MODE',pin,mode));return 'OK'
    def gpio_write(self,board,pin,value):self.outputs[pin]=value;self.commands.append(('WRITE',pin,value));return 'OK'
    def gpio_read(self,board,pin):return int(not self.button) if pin==5 else 0
    def pwm_select(self,ch):self.commands.append(('PWM',ch));return 'OK'
    def pwm_set_frequency(self,f):self.frequency=f;return 'OK'
    def pwm_set_duty(self,d):self.duty=d;return 'OK'
    def adc_read(self):
        valim=4.66 if self.button or self.outputs[4] else 0
        # gain simulé 3 : les tensions UUT restent dans la plage du MCP.
        return round({0:0,1:2.33,2:2.33,3:2.33,4:2.33,5:valim/2,6:valim,7:4.66}[self.channel]/.003)
    def capture(self,mode,channel=0,channel2=0,pin=0,duration_ms=3000,cancel=None):
        self.commands.append(('CAP',mode,channel,channel2,pin,duration_ms))
        if cancel and cancel.is_set():raise Cancelled()
        records=[];end=duration_ms*1000
        if mode=='ADC':
            amp=.1 if self.frequency==1 else .4 if self.frequency==10 else .15
            if channel==3:amp*=5
            if self.adc_resolution==16:
                amp=amp if self.duty==50 else 0.
                rec=[(channel,t,round((2.33+amp*math.sin(2*math.pi*self.frequency*t/1e6))/.0001875),66667)
                     for t in range(33334,end,66667)]
                return Capture(rec,16,end,0)
            records=[(channel,t,round((2.33+amp*math.sin(2*math.pi*self.frequency*t/1e6))/.003),4000)
                     for t in range(4000,end,4000)]
        else:
            period=100000 if pin==7 else 1000000
            active=self.outputs[3] if pin==7 else bool(self.duty)
            values=[(16+pin,1,0,1000)]
            if active:
                for start in range(25000,end,period):
                    values.append((16+pin,start,1,1000))
                    if start+period//2<end:values.append((16+pin,start+period//2,0,1000))
            values.append((16+pin,end,values[-1][2],1000));records=values
            if mode=='SYNC':
                for t in range(10000,end,10000):
                    records.extend([(channel,t,round((2.33+math.sin(2*math.pi*t/1e6))/.003),5000),
                                    (channel2,t+3000,round(2.33/.003),5000)])
                records.sort(key=lambda r:r[1])
        return Capture(records,12,end,1000)

class MathTests(unittest.TestCase):
    def test_frequency_duty_complete_cycles(self):
        data=[(0,0,800)]
        for t in range(20000,500000,100000):data.extend([(t,1,800),(t+30000,0,800)])
        data.append((550000,0,800));m=digital_metrics(data,800)
        self.assertAlmostEqual(m['frequency'],10);self.assertAlmostEqual(m['duty'],30)
    def test_short_or_unresolved_signal(self):
        with self.assertRaises(Inconclusive):digital_metrics([(0,0,1000),(1000,1,1000)])
        data=[(t,(t//1000)%2,1000) for t in range(0,10000,1000)]
        with self.assertRaises(Inconclusive):digital_metrics(data)
    def test_fundamental_offset_and_jitter(self):
        t=[i*.004+0.00005*math.sin(i) for i in range(1250)]
        y=[2.33+.4*math.sin(2*math.pi*10*x+.4) for x in t]
        m=fundamental(t,y,10);self.assertAlmostEqual(m['amplitude'],.4,places=8)
        self.assertTrue(m['qualified_bandwidth'])
    def test_aliasing_rejected(self):
        t=[i*.01 for i in range(500)]
        with self.assertRaises(Inconclusive):fundamental(t,[math.sin(2*math.pi*100*x) for x in t],100)
    def test_high_frequency_never_qualified_by_adc_only(self):
        t=[i*.004 for i in range(1250)]
        self.assertFalse(fundamental(t,[math.sin(2*math.pi*100*x) for x in t],100)['qualified_bandwidth'])

class RunnerTests(unittest.TestCase):
    def test_mapping_is_frozen(self):
        self.assertEqual(len(TP),16)
        self.assertEqual(TP['TP18'][:2],('PWM',7))
        self.assertEqual(TP['TP17'][:2],('ADC',0))
        self.assertEqual(TP['TP1'][0],'EXTERNE')
        self.assertEqual(sorted(v[1] for v in TP.values() if v[0]=='ADC'),list(range(8)))
    def test_missing_limits_or_calibration_cannot_pass(self):
        runner=TestRunner(FakeBench())
        self.assertEqual(runner.criterion('vcc',4.66)['status'],'À QUALIFIER')
        runner.profile['limits']['vcc']={'min':4.5,'max':5.0}
        self.assertEqual(runner.criterion('vcc',4.66,False)['status'],'À QUALIFIER')
        self.assertEqual(runner.criterion('vcc',4.66,True)['status'],'PASS')
        self.assertEqual(runner.criterion('vcc',3.0,True)['status'],'FAIL')
    def test_uncertainty_can_prevent_false_pass(self):
        runner=TestRunner(FakeBench());runner.profile['limits']['f555']={'min':9.9,'max':10.1}
        self.assertEqual(runner.criterion('f555',10,uncertainty=.2)['status'],'NON CONCLUANT')
    def test_saturation_never_becomes_a_valid_voltage(self):
        runner=TestRunner(FakeBench())
        for raw in (2047,-2048,4095):
            with self.assertRaises(Inconclusive):runner.convert('TP8',raw,12)
    def test_all_seven_sequences_and_output_order(self):
        bench=FakeBench();profile=default_profile()
        for p in profile['adc'].values():p.update(gain=3,confirmed=True)
        def ask(request):
            if 'MAINTENIR' in request['text']:bench.button=True
            if 'Relâcher SW1.' in request['text']:bench.button=False
            if request['kind']=='number':return None
            return True
        runner=TestRunner(bench,profile,ask=ask)
        runner.wait=lambda seconds:runner.check_cancel()
        sections=[];runner.run_all(section_started=sections.append)
        self.assertEqual(sections,list(range(1,8)))
        self.assertGreater(len(runner.rows),40)
        self.assertEqual(bench.commands[-1],('STOP',))
        self.assertEqual(bench.stop_count,2)
        self.assertEqual({cmd[1] for cmd in bench.commands if cmd[0]=='PWM'},{7})
        self.assertEqual({cmd[1] for cmd in bench.commands if cmd[0]=='WRITE'},{3,4})
        for pin in (3,4):self.assertLess(bench.commands.index(('WRITE',pin,0)),bench.commands.index(('MODE',pin,'OUT')))
        self.assertTrue(any(r['status']=='À QUALIFIER' for r in runner.rows))
    def test_stop_after_communication_error(self):
        bench=FakeBench();runner=TestRunner(bench,ask=lambda req:True)
        runner.wait=lambda seconds:None
        bench.adc_read=lambda:(_ for _ in ()).throw(TimeoutError('USB'))
        with self.assertRaises(TimeoutError):runner.run_all()
        self.assertEqual(bench.commands[-1],('STOP',));self.assertEqual(bench.stop_count,2)
    def test_stop_after_operator_cancel(self):
        bench=FakeBench();event=threading.Event()
        def ask(request):event.set();return True
        runner=TestRunner(bench,cancel=event,ask=ask)
        with self.assertRaises(Cancelled):runner.run_all()
        self.assertEqual(bench.commands[-1],('STOP',))
    def test_stop_error_is_reported_even_with_primary_error(self):
        bench=FakeBench();n=[0]
        def stop():
            n[0]+=1
            if n[0]==2:raise RuntimeError('ERROR STOP 2')
        bench.stop=stop;runner=TestRunner(bench,ask=lambda request:(_ for _ in ()).throw(ValueError('primary')))
        with self.assertRaisesRegex(ValueError,'primary'):runner.run_all()
        self.assertEqual(runner.rows[-1]['test'],'Arrêt des sorties non confirmé')
    def test_invalid_profile(self):
        profile=default_profile();profile['adc']['TP8']['gain']=float('nan')
        with self.assertRaises(ValueError):validate_profile(profile)

class FakeSerial:
    is_open=True;timeout=2
    def __init__(self,responses):self.responses=list(responses);self.sent=[]
    def write(self,data):self.sent.append(data.decode().strip());return len(data)
    def readline(self):return (self.responses.pop(0)+'\n').encode()

class ProtocolTests(unittest.TestCase):
    def test_capture_metadata_and_us_preserved(self):
        hw=STM32Hardware('fake');hw.adc_resolution=16
        hw.serial=FakeSerial(['OK','CAP DONE 2 16 1000000 0','4,1000,1234,100;4,2000,1235,100;'])
        data=hw.capture('ADC',channel=4,duration_ms=1000)
        self.assertEqual(data.resolution,16);self.assertEqual(data.channel(4)[0],(1000,1234,100))
        self.assertEqual(hw.adc_resolution,16)
    def test_incomplete_block_rejected(self):
        hw=STM32Hardware('fake');hw.serial=FakeSerial(['OK','CAP DONE 2 12 1000000 0','4,1000,1,100;'])
        with self.assertRaises(RuntimeError):hw.capture('ADC',channel=4,duration_ms=1000)
    def test_cancel_checked_between_status_requests(self):
        event=threading.Event();hw=STM32Hardware('fake')
        def command(text):
            if text=='CAP STATUS':event.set();return 'CAP BUSY 1 12 1000 0'
            return 'OK'
        hw._send_command=command
        with self.assertRaises(Cancelled):hw.capture('GPIO',pin=7,cancel=event)
    def test_error_response_is_not_accepted(self):
        hw=STM32Hardware('fake');hw.serial=FakeSerial(['ERROR'])
        with self.assertRaises(RuntimeError):hw.pwm_select(7)

if __name__=='__main__':unittest.main()
