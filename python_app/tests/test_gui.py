"""Vérifie l'arrêt avec un dialogue en attente et la fermeture différée Qt."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import time
import unittest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from unittest.mock import patch
from PyQt6.QtWidgets import QApplication
from PyQt6.QtTest import QTest
from src.views.main_window import MainWindow
from src.controllers.main_controller import MainController
from src.models.test_runner import TestRunner
from test_uut import FakeBench

class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def until(self,predicate):
        deadline=time.monotonic()+5
        while not predicate() and time.monotonic()<deadline:QTest.qWait(10)
        self.assertTrue(predicate(),'Délai Qt dépassé')

    def test_close_during_operator_prompt_waits_for_worker_cleanup(self):
        window=MainWindow();controller=MainController(window)
        bench=FakeBench();bench.disconnect=lambda:None;controller.stm32=bench
        window.show();controller.run()
        self.until(lambda:controller.prompt is not None)
        self.assertFalse(controller.prompt.isModal())
        controller.close()
        self.until(lambda:controller.worker is None)
        self.assertEqual(controller.last_outcome,'cancelled')
        self.assertEqual(bench.commands[-1],('STOP',))
        self.assertTrue(window.allow_close)
        self.assertFalse(window.isVisible())

    def test_unconfirmed_stop_is_visible(self):
        window=MainWindow();controller=MainController(window)
        row={'section':1,'test':'Arrêt des sorties non confirmé','value':None,'unit':'',
             'status':'NON CONCLUANT','minimum':None,'maximum':None,'note':'ERROR STOP 2','source':'Banc'}
        controller.add_row(row)
        controller.outcome('error','Erreur USB.')
        self.assertIn('arrêt matériel non confirmé',window.status.text())
        window.allow_close=True;window.close()
