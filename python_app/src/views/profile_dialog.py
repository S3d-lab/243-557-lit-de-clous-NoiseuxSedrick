from copy import deepcopy
from PyQt6.QtWidgets import (QDialog,QVBoxLayout,QLabel,QTableWidget,QTableWidgetItem,
    QTabWidget,QCheckBox,QDialogButtonBox,QMessageBox,QHeaderView)
from PyQt6.QtCore import Qt, QSignalBlocker
from src.uut_config import TP, CRITERIA, validate_profile

class ProfileDialog(QDialog):
    def __init__(self, profile,parent=None):
        super().__init__(parent); self.setWindowTitle('Étalonnage et limites du plan'); self.resize(950,650)
        self.profile=deepcopy(profile)
        layout=QVBoxLayout(self)
        label=QLabel('ADC : lecture = code brut × LSB × gain + offset. Le gain 2 compense le diviseur du banc.\n« Confirmé » concerne l’étalonnage absolu. La comparaison Golden fonctionne séparément, avec les mêmes conditions de mesure.')
        label.setWordWrap(True);layout.addWidget(label)
        self.golden100=QCheckBox('Réponse et critères à 100 Hz vérifiés sur une carte Golden avec ces mêmes voies ADC à 12 bits')
        self.golden100.setChecked(profile.get('golden_ac_100hz',False))
        self.golden100.setToolTip('Qualification préalable du banc, pas de saisie de mesure pendant la séquence. Si la case est vide, les valeurs exploitables sont tout de même affichées.')
        layout.addWidget(self.golden100)
        tabs=QTabWidget();layout.addWidget(tabs)
        self.points=list(profile['adc'])
        self.adc=QTableWidget(8,5); self.adc.setHorizontalHeaderLabels(['TP','Canal','Gain','Offset (V)','Confirmé'])
        for i,tp in enumerate(self.points):
            for j,text in enumerate([tp,str(TP[tp][1]),str(profile['adc'][tp]['gain']),str(profile['adc'][tp]['offset'])]):
                item=QTableWidgetItem(text)
                if j<2: item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.adc.setItem(i,j,item)
            box=QCheckBox();box.setChecked(profile['adc'][tp]['confirmed']);self.adc.setCellWidget(i,4,box)
        self.keys=list(CRITERIA)
        self.limits=QTableWidget(len(self.keys),4);self.limits.setHorizontalHeaderLabels(['Critère','Repère nominal','Minimum inclus','Maximum inclus'])
        for i,key in enumerate(self.keys):
            name,unit,nominal=CRITERIA[key]; bounds=profile['limits'][key]
            for j,text in enumerate([name+f' ({unit})',str(nominal) if nominal is not None else 'À déterminer',
                                     '' if bounds['min'] is None else str(bounds['min']),
                                     '' if bounds['max'] is None else str(bounds['max'])]):
                item=QTableWidgetItem(text)
                if j<2:item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.limits.setItem(i,j,item)
        for table in (self.adc,self.limits):
            table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        tabs.addTab(self.adc,'Voies ADC');tabs.addTab(self.limits,'Critères d’acceptation')
        self.golden = None
        self.golden_enabled = None
        if profile.get('golden'):
            from PyQt6.QtWidgets import QWidget
            self.golden_rows = self.profile['golden']['checks']
            page = QWidget(); glayout = QVBoxLayout(page)
            self.golden_enabled = QCheckBox('Comparer les mesures analogiques à cette Golden')
            self.golden_enabled.setChecked(self.profile['golden']['enabled'])
            glayout.addWidget(self.golden_enabled)
            text = QLabel(self.profile['golden']['source']+'\n'
                          'Marges de départ modifiables. Tolérance vide = À QUALIFIER. '
                          'PASS = compatible avec cette Golden sur ce banc. '
                          'Les limites des fréquences restent dans « Critères d’acceptation ».')
            text.setWordWrap(True); glayout.addWidget(text)
            self.golden = QTableWidget(len(self.golden_rows),6)
            self.golden.setHorizontalHeaderLabels(['Section','Contrôle / unité','Référence','Tolérance ±','Minimum','Maximum'])
            for i,row in enumerate(self.golden_rows):
                unit = CRITERIA[row['key']][1]
                tolerance = row['tolerance']
                values = [str(row['section']), row['test']+(f' ({unit})' if unit else ''),
                          f"{row['reference']:.9g}", '' if tolerance is None else f'{tolerance:.9g}', '', '']
                for j,value in enumerate(values):
                    item=QTableWidgetItem(value)
                    if j!=3:item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self.golden.setItem(i,j,item)
                self.update_golden_bounds(i)
            self.golden.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            self.golden.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch)
            self.golden.itemChanged.connect(lambda item:self.update_golden_bounds(item.row()) if item.column()==3 else None)
            glayout.addWidget(self.golden);tabs.addTab(page,'Comparaison Golden')
            self.golden_enabled.toggled.connect(self.toggle_golden)
            self.toggle_golden(self.golden_enabled.isChecked())
            tabs.setCurrentWidget(page)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save);buttons.rejected.connect(self.reject);layout.addWidget(buttons)

    def update_golden_bounds(self,row_index):
        row = self.golden_rows[row_index]
        try:
            tolerance=float(self.golden.item(row_index,3).text().strip().replace(',','.'))
            if tolerance<0:raise ValueError()
            values=[f"{row['reference']-tolerance:.9g}",f"{row['reference']+tolerance:.9g}"]
        except ValueError:
            values=['—','—']
        blocker=QSignalBlocker(self.golden)
        for column,text in zip((4,5),values):self.golden.item(row_index,column).setText(text)

    def toggle_golden(self,active):
        self.golden.setEnabled(active)
        self.golden100.setEnabled(not active)
        covered={row['key'] for row in self.golden_rows}
        for i,key in enumerate(self.keys):
            for j in (2,3):
                item=self.limits.item(i,j)
                flags=item.flags() | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsEditable
                if active and key in covered:
                    flags &= ~(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsEditable)
                item.setFlags(flags)
                item.setToolTip('Limites de l’onglet Comparaison Golden utilisées.' if active and key in covered else '')

    def save(self):
        try:
            def number(text): return float(text.strip().replace(',','.'))
            for i,tp in enumerate(self.points):
                self.profile['adc'][tp]={'gain':number(self.adc.item(i,2).text()),
                     'offset':number(self.adc.item(i,3).text()),'confirmed':self.adc.cellWidget(i,4).isChecked()}
            for i,key in enumerate(self.keys):
                self.profile['limits'][key]={name:number(self.limits.item(i,j).text()) if self.limits.item(i,j).text().strip() else None
                                            for name,j in (('min',2),('max',3))}
            self.profile['golden_ac_100hz']=self.golden100.isChecked()
            if self.golden is not None:
                self.profile['golden']['enabled']=self.golden_enabled.isChecked()
                for i,row in enumerate(self.golden_rows):
                    text=self.golden.item(i,3).text().strip()
                    row['tolerance']=number(text) if text else None
            self.profile=validate_profile(self.profile)
            self.accept()
        except (ValueError,KeyError) as error:
            QMessageBox.warning(self,'Valeur invalide',str(error))
