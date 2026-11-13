
import threading
import time
from dataclasses import dataclass
from src.uut_config import LSB
import serial
from src.config import STM32_BAUDRATE, STM32_TIMEOUT

class Cancelled(RuntimeError):
    pass

@dataclass
class Capture:
    # (source: 0..7 ADC / 16..23 GPIO, temps_us, brut, intervalle_us)
    records: list
    resolution: int
    elapsed_us: int
    max_gpio_gap_us: int

    def channel(self, source):
        return [(t, value, span) for ch,t,value,span in self.records if ch == source]

class STM32Hardware:
    """Gère la communication USB CDC avec le STM32."""

    def __init__(self, port: str):
        self.port = port
        self.serial = None
        self.adc_resolution = 12
        self.adc_channel = 0
        self._lock = threading.RLock()

    def connect(self):
        """Ouvre USB et vérifie la version. Le test initialise ensuite les cartes."""
        with self._lock:
            if self.serial is not None and self.serial.is_open:
                return

            self.serial = serial.Serial(
                port=self.port,
                baudrate=STM32_BAUDRATE,
                timeout=STM32_TIMEOUT,
                write_timeout=STM32_TIMEOUT
            )

            try:
                self.serial.reset_input_buffer()
                if self.ping() != 'PONG':
                    raise RuntimeError('Réponse PING incorrecte.')
                self.firmware = self._send_command('INFO')
                if self.firmware != 'UUT 2 TIM2_CH2 PA1 I2C1=20 I2C3=20,68':
                    raise RuntimeError('Charger le firmware UUT 2 fourni avec cette application. Réponse : ' + self.firmware)
            except Exception:
                self.serial.close()
                self.serial = None
                raise

    def disconnect(self):
        """Ferme la connexion USB CDC de façon sécurisée."""
        with self._lock:
            if self.serial is not None:
                self.serial.close()
                self.serial = None

    def _send_command(self, command: str, timeout: float | None = None) -> str:
        """Envoie une commande au STM32 et retourne sa réponse."""
        with self._lock:
            if self.serial is None or not self.serial.is_open:
                raise RuntimeError("STM32 non connecté")

            previous_timeout = self.serial.timeout

            try:
                if timeout is not None:
                    self.serial.timeout = timeout

                self.serial.write((command + "\n").encode("utf-8"))

                response = self.serial.readline().decode("utf-8").strip()

                if not response:
                    raise TimeoutError(
                        f"Aucune réponse du STM32 : {command}"
                    )

                return response

            finally:
                self.serial.timeout = previous_timeout


    def ping(self) -> str:
        return self._send_command("PING")


    def led_on(self) -> str:
        return self._send_command("LED ON")


    def led_off(self) -> str:
        return self._send_command("LED OFF")

    def pwm_select(self, channel: int) -> str:
        """Sélectionne une sortie du multiplexeur PWM."""
        if not 0 <= channel <= 7:
            raise ValueError("Le canal PWM doit être entre 0 et 7.")

        return self._ok(f"PWM SELECT {channel}")

    def pwm_set_duty(self, duty: int) -> str:
        """Règle le rapport cyclique du PWM entre 0 et 100 %."""
        if not 0 <= duty <= 100:
            raise ValueError("Le rapport cyclique doit être entre 0 et 100 %.")

        return self._ok(f"PWM DUTY {duty}")

    def pwm_set_frequency(self, frequency: int) -> str:
        """Configure la fréquence du PWM en hertz."""

        if not 1 <= frequency <= 10000:
            raise ValueError("La fréquence doit être entre 1 et 10000 Hz.")

        return self._ok(f"PWM FREQ {frequency}")
    

    def gpio_mode(self, board: str, pin: int, mode: str) -> str:
        """Configure une broche GPIO sur la carte ADC ou PWM."""
        board = board.upper()
        mode = mode.upper()

        if board not in ("ADC", "PWM"):
            raise ValueError("La carte doit être ADC ou PWM.")

        if not 0 <= pin <= 7:
            raise ValueError("La broche GPIO doit être entre 0 et 7.")

        if mode not in ("IN", "OUT"):
            raise ValueError("Le mode doit être IN ou OUT.")

        if board == "ADC":
            command = f"GPIO ADC MODE {pin} {mode}"
        else:
            command = f"GPIO MODE {pin} {mode}"

        return self._ok(command)

    def gpio_write(self, board: str, pin: int, value: int) -> str:
        """Écrit un niveau logique sur une sortie GPIO de la carte ADC ou PWM."""
        board = board.upper()

        if board not in ("ADC", "PWM"):
            raise ValueError("La carte doit être ADC ou PWM.")

        if not 0 <= pin <= 7:
            raise ValueError("La broche GPIO doit être entre 0 et 7.")

        if value not in (0, 1):
            raise ValueError("La valeur doit être 0 ou 1.")

        if board == "ADC":
            command = f"GPIO ADC WRITE {pin} {value}"
        else:
            command = f"GPIO WRITE {pin} {value}"

        return self._ok(command)

    def gpio_read(self, board: str, pin: int) -> int:
        """Lit une entrée GPIO sur la carte ADC ou PWM."""
        board = board.upper()

        if board not in ("ADC", "PWM"):
            raise ValueError("La carte doit être ADC ou PWM.")

        if not 0 <= pin <= 7:
            raise ValueError("La broche GPIO doit être entre 0 et 7.")

        if board == "ADC":
            command = f"GPIO ADC READ {pin}"
        else:
            command = f"GPIO READ {pin}"

        response = self._send_command(command)

        if response not in ("0", "1"):
            raise RuntimeError(f"Réponse GPIO invalide : {response}")

        return int(response)

    def adc_select(self, channel: int) -> str:
        """Sélectionne une entrée du multiplexeur ADC."""
        if not 0 <= channel <= 7:
            raise ValueError("Le canal ADC doit être entre 0 et 7.")

        response = self._ok(f"ADC SELECT {channel}")
        self.adc_channel = channel
        return response

    def adc_set_resolution(self, resolution: int) -> str:
        """Configure la résolution du MCP3423."""
        if resolution not in (12, 14, 16, 18):
            raise ValueError(
                "La résolution ADC doit être 12, 14, 16 ou 18 bits."
            )

        response = self._ok(f"ADC RESOLUTION {resolution}")

        if response != "OK":
            raise RuntimeError(
                f"Erreur de configuration ADC : {response}"
            )

        self.adc_resolution = resolution

        return response

    def adc_read(self) -> int:
        """Effectue une conversion ADC et retourne la valeur brute."""
        response = self._send_command("ADC READ")

        if response in ("ERROR", "TIMEOUT"):
            raise RuntimeError(f"Erreur de conversion ADC : {response}")

        try:
            return int(response)
        except ValueError:
            raise RuntimeError(
                f"Réponse ADC invalide : {response}"
            )

    def _ok(self, command):
        response = self._send_command(command)
        if response != 'OK':
            raise RuntimeError(f'{command} → {response}')
        return response

    def stop(self):
        response = self._send_command('STOP')
        if response != 'STOPPED':
            raise RuntimeError('Arrêt non confirmé : ' + response)
        return response

    def capture(self, mode, channel=0, channel2=0, pin=0, duration_ms=3000, cancel=None):
        if mode not in ('ADC', 'GPIO', 'SYNC'):
            raise ValueError('Mode de capture inconnu.')
        if not 100 <= duration_ms <= 6000 or any(not 0 <= c <= 7 for c in (channel, channel2, pin)):
            raise ValueError('Capture : durée 100..6000 ms et canaux 0..7.')
        args = {'ADC': f'{channel}', 'GPIO': f'{pin}',
                'SYNC': f'{channel} {channel2} {pin}'}[mode]
        def cancelled():
            if cancel is not None and cancel.is_set():
                raise Cancelled('Arrêt demandé.')
        cancelled()
        self._ok(f'CAP START {mode} {args} {duration_ms}')
        deadline = time.monotonic()+duration_ms/1000+3
        while True:
            cancelled()
            status = self._send_command('CAP STATUS').split()
            if len(status) == 6 and status[:2] == ['CAP','DONE']:
                count, resolution, elapsed, gap = map(int, status[2:])
                if not 0 < count <= 2048 or resolution not in LSB or elapsed < duration_ms*1000:
                    raise RuntimeError('Métadonnées de capture invalides.')
                break
            if status[:2] != ['CAP','BUSY']:
                raise RuntimeError('Acquisition : ' + ' '.join(status))
            if time.monotonic() > deadline:
                raise TimeoutError('Acquisition STM32 non terminée.')
            if cancel is not None:
                cancel.wait(.04)
            else:
                time.sleep(.04)
        records = []
        for index in range(0, count, 2):
            cancelled()
            response = self._send_command(f'CAP BLOCK {index}')
            try:
                block = [tuple(map(int, item.split(','))) for item in response.split(';') if item]
            except ValueError as error:
                raise RuntimeError(f'Bloc {index} invalide : {response}') from error
            if len(block) != min(2,count-index) or any(len(r)!=4 for r in block):
                raise RuntimeError('Bloc de capture incomplet.')
            for ch,t,raw,span in block:
                if ch not in range(8) and ch not in range(16,24):
                    raise RuntimeError('Source de capture invalide.')
                if not 0 <= t <= elapsed or not 0 <= span <= elapsed:
                    raise RuntimeError('Temps de capture invalide.')
            records.extend(block)
        return Capture(records, resolution, elapsed, gap)

    def adc_capture(self, duration_ms=3000):
        """Compatibilité scripts du cours: [(temps_ms, brut)]. Résolution conservée."""
        try:
            data = self.capture('ADC', channel=self.adc_channel, duration_ms=duration_ms)
            return [(t/1000, raw) for t,raw,_ in data.channel(self.adc_channel)]
        except Exception:
            self.stop()
            raise

    def adc_read_voltage(self):
        """Tension aux bornes du MCP3423, PAS encore corrigée du gain de carte."""
        return self.adc_read() * LSB[self.adc_resolution]
