from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QPushButton,
    QLabel,QLineEdit,QTableWidget,QHeaderView,QAbstractItemView,QSplitter)
from src.config import STM32_PORT
from src.uut_config import SECTIONS
from src.views.graphs_window import GraphsWindow

class MainWindow(QMainWindow):
    closing = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.allow_close = False
        self.setWindowTitle('Lit de clous — UUT / 7 sections')
        self.resize(1200,800)
        root = QWidget(); self.setCentralWidget(root); layout = QVBoxLayout(root)
        connection = QHBoxLayout()
        connection.addWidget(QLabel('Port USB :'))
        self.port = QLineEdit(STM32_PORT); connection.addWidget(self.port)
        self.connect_button = QPushButton('Connecter'); connection.addWidget(self.connect_button)
        self.profile_button = QPushButton('Étalonnage et critères'); connection.addWidget(self.profile_button)
        layout.addLayout(connection)
        self.status = QLabel('Déconnecté.'); self.status.setWordWrap(True); layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.run_button = QPushButton('Exécuter les 7 sections')
        self.stop_button = QPushButton('Arrêter les stimuli')
        self.reset_button = QPushButton('Effacer les résultats')
        for button in (self.run_button,self.stop_button,self.reset_button): actions.addWidget(button)
        self.run_button.setEnabled(False); self.stop_button.setEnabled(False)
        layout.addLayout(actions)
        layout.addWidget(QLabel('Alimentation +5 V externe : application et coupure manuelles. L’arrêt logiciel ne coupe pas le +5 V.'))
        self.sections = QTableWidget(7,2)
        self.sections.setHorizontalHeaderLabels(['Section','État'])
        self.sections.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)
        self.sections.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.ResizeToContents)
        from PyQt6.QtWidgets import QTableWidgetItem
        for i,name in enumerate(SECTIONS):
            self.sections.setItem(i,0,QTableWidgetItem(f'{i+1}. {name}'))
            self.sections.setItem(i,1,QTableWidgetItem('En attente'))
        self.sections.setFixedHeight(250)
        self.details = QTableWidget(0,6)
        self.details.setHorizontalHeaderLabels(['Section','Contrôle','Valeur','État','Limites','Remarque'])
        self.details.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column,width in enumerate((65,340,115,125,125,350)):
            self.details.setColumnWidth(column,width)
        self.details.horizontalHeader().setSectionResizeMode(5,QHeaderView.ResizeMode.Stretch)
        for table in (self.sections,self.details):
            table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            table.setAlternatingRowColors(True)
        layout.addWidget(self.sections); layout.addWidget(self.details,1)
        foot = QHBoxLayout()
        self.graphs_button = QPushButton('Graphiques')
        self.export_button = QPushButton('Exporter résultats et acquisitions')
        foot.addWidget(self.graphs_button); foot.addWidget(self.export_button); layout.addLayout(foot)
        self.graphs_window = GraphsWindow()
        self.graphs_button.clicked.connect(self.graphs_window.show)

    def closeEvent(self,event):
        if self.allow_close:
            self.graphs_window.close(); event.accept()
        else:
            event.ignore(); self.closing.emit()
