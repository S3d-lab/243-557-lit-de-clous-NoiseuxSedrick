"""Thread de séquence; les fenêtres sont exclusivement créées par le thread Qt."""
import threading
from PyQt6.QtCore import QThread, pyqtSignal
from src.models.test_runner import TestRunner
from src.hardware.stm32_hardware import Cancelled

class TestWorker(QThread):
    section_started = pyqtSignal(int)
    section_finished = pyqtSignal(int, str)
    check_ready = pyqtSignal(object)
    waveform_ready = pyqtSignal(object)
    prompt = pyqtSignal(object)
    outcome = pyqtSignal(str, str)

    def __init__(self, stm32, profile):
        super().__init__()
        self.cancel = threading.Event()
        self.answer_ready = threading.Event()
        self.answer = None
        self.runner = TestRunner(stm32,profile,self.cancel,self.ask,
                                 self.check_ready.emit,self.waveform_ready.emit)

    def ask(self, request):
        self.answer_ready.clear()
        self.answer = None
        self.prompt.emit(request)
        while not self.answer_ready.wait(.05):
            self.runner.check_cancel()
        self.runner.check_cancel()
        return self.answer

    def reply(self, value):
        self.answer = value
        self.answer_ready.set()

    def stop(self):
        self.cancel.set()
        self.answer_ready.set()

    def run(self):
        try:
            self.runner.run_all(self.section_started.emit,self.section_finished.emit)
            self.outcome.emit('finished','Séquence terminée. Consulter les résultats et exporter le rapport.')
        except Cancelled as error:
            self.outcome.emit('cancelled',str(error))
        except Exception as error:
            self.outcome.emit('error',f'{type(error).__name__} : {error}. Couper le +5 V externe.')

class ConnectionWorker(QThread):
    connected = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, port):
        super().__init__()
        self.port = port

    def run(self):
        from src.hardware.stm32_hardware import STM32Hardware
        stm32 = STM32Hardware(self.port)
        try:
            stm32.connect()
            stm32.stop()
            self.connected.emit(stm32)
        except Exception as error:
            stm32.disconnect()
            self.failed.emit(str(error))
