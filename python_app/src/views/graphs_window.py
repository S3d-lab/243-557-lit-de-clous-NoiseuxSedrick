from PyQt6.QtWidgets import QWidget,QVBoxLayout,QScrollArea,QComboBox,QLabel
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

class GraphsWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Acquisitions du banc'); self.resize(1100,650)
        layout = QVBoxLayout(self)
        self.choice = QComboBox(); layout.addWidget(self.choice)
        self.note = QLabel(); self.note.setWordWrap(True); layout.addWidget(self.note)
        self.figure = Figure(figsize=(10,5)); self.canvas = FigureCanvasQTAgg(self.figure)
        layout.addWidget(self.canvas)
        self.waves = []; self.choice.currentIndexChanged.connect(self.draw)

    def add_wave(self,wave):
        self.waves.append(wave)
        self.choice.addItem(f"Section {wave['section']} — {wave['tp']} — {wave.get('frequency') or 'DC / logique'} Hz — acquisition {len(self.waves)}")
        self.choice.setCurrentIndex(len(self.waves)-1)

    def draw(self,index):
        if not 0 <= index < len(self.waves): return
        wave = self.waves[index]; self.figure.clear(); ax=self.figure.add_subplot(111)
        times,values=wave['time'],wave['voltage']
        if len(times)==len(values) and values:
            if wave.get('digital'):
                ax.step(times,values,where='post'); ax.set_ylabel('Niveau logique')
            else:
                ax.plot(times,values,'.-',markersize=3)
                ax.set_ylabel('Lecture comparée à la Golden (V)' if wave.get('golden_comparison') else
                              'Tension (V)' if wave['calibrated'] else 'Valeur provisoire (V), gain à confirmer')
            ax.set_xlim(min(times),max(times))
        else:
            ax.text(.5,.5,'Conversion non exploitable. Codes bruts conservés dans l’export.',ha='center',transform=ax.transAxes)
        ax.set_xlabel('Temps STM32 (s)'); ax.grid(True)
        self.note.setText('Les points ADC sont successifs. À 100 Hz, le contrôle d’écrêtage dépend de la cadence réellement mesurée. Les instants GPIO sont détectés par polling I2C.')
        self.figure.tight_layout(); self.canvas.draw_idle()

    def clear_graphs(self):
        self.choice.clear(); self.waves.clear(); self.figure.clear(); self.canvas.draw_idle()
