import csv
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from PyQt6.QtWidgets import (QTableWidgetItem,QMessageBox,QFileDialog,QDialog,QVBoxLayout,
    QHBoxLayout,QLabel,QPushButton)
from PyQt6.QtGui import QColor
from src.controllers.test_worker import TestWorker,ConnectionWorker
from src.uut_config import load_profile,save_profile,TP
from src.views.profile_dialog import ProfileDialog

class MainController:
    def __init__(self,window):
        self.window=window;self.stm32=None;self.worker=None;self.prompt=None
        self.close_requested=False;self.rows=[];self.waves=[];self.run_profile=None
        self.run_started=None;self.current_section=0;self.last_outcome=''
        try:self.profile=load_profile()
        except Exception as error:
            from src.uut_config import default_profile
            self.profile=default_profile()
            QMessageBox.warning(window,'Profil invalide',f'{error}\nProfil provisoire chargé; corriger avant validation.')
        window.connect_button.clicked.connect(self.connect)
        window.run_button.clicked.connect(self.run)
        window.stop_button.clicked.connect(self.stop)
        window.reset_button.clicked.connect(self.reset)
        window.profile_button.clicked.connect(self.edit_profile)
        window.export_button.clicked.connect(self.export)
        window.closing.connect(self.close)

    def busy(self,active):
        w=self.window
        w.run_button.setEnabled(not active and self.stm32 is not None)
        w.stop_button.setEnabled(active and isinstance(self.worker,TestWorker))
        for control in (w.connect_button,w.reset_button,w.profile_button,w.port):control.setEnabled(not active)

    def connect(self):
        if self.worker is not None:return
        if self.stm32:
            self.disconnect_stm32();self.window.status.setText('Déconnecté.');self.busy(False);return
        self.worker=ConnectionWorker(self.window.port.text().strip())
        self.worker.connected.connect(self.connected)
        self.worker.failed.connect(lambda text:self.window.status.setText('Connexion : '+text))
        self.worker.finished.connect(self.worker_finished)
        self.busy(True);self.window.status.setText('Connexion et vérification des sorties…');self.worker.start()

    def connected(self,stm32):
        self.stm32=stm32
        self.window.connect_button.setText('Déconnecter')
        self.window.status.setText('Connecté — application 2.3 Golden, firmware UUT 2. Forme et seuils apparents à 5 Hz; gain à 100 Hz.')

    def run(self):
        if self.worker is not None or self.stm32 is None:return
        self.reset()
        self.run_profile=deepcopy(self.profile)
        self.run_started=datetime.now(timezone.utc).isoformat()
        self.worker=TestWorker(self.stm32,self.run_profile)
        self.worker.section_started.connect(self.started)
        self.worker.section_finished.connect(self.section_finished)
        self.worker.check_ready.connect(self.add_row)
        self.worker.waveform_ready.connect(self.add_wave)
        self.worker.prompt.connect(self.show_prompt)
        self.worker.outcome.connect(self.outcome)
        self.worker.finished.connect(self.worker_finished)
        self.busy(True);self.worker.start()

    def started(self,number):
        self.current_section=number
        self.section_finished(number,'En cours')
        self.window.status.setText(f'Section {number}/7 en cours…')

    def section_finished(self,number,status):
        if 1<=number<=7:self.window.sections.setItem(number-1,1,self.item(status,status))

    @staticmethod
    def item(text,status=''):
        item=QTableWidgetItem(str(text))
        color={'PASS':'#16713b','FAIL':'#b21b26','À QUALIFIER':'#936000','NON CONCLUANT':'#936000'}.get(status)
        if color:item.setForeground(QColor(color))
        item.setToolTip(str(text));return item

    def add_row(self,row):
        self.rows.append(row); table=self.window.details; i=table.rowCount();table.insertRow(i)
        value=row['value']
        text=('Oui' if value else 'Non') if isinstance(value,bool) else f'{value:.6g}' if isinstance(value,float) else '—' if value is None else str(value)
        bounds=f"{row['minimum'] if row['minimum'] is not None else '…'} à {row['maximum'] if row['maximum'] is not None else '…'}"
        for j,content in enumerate([row['section'],row['test'],f"{text} {row['unit']}",row['status'],bounds,row['note']]):
            table.setItem(i,j,self.item(content,row['status']))
        table.scrollToBottom()

    def add_wave(self,wave):
        self.waves.append(wave);self.window.graphs_window.add_wave(wave)

    def show_prompt(self,request):
        if not isinstance(self.worker,TestWorker) or self.worker.cancel.is_set():return
        dialog=QDialog(self.window);dialog.setWindowTitle(f"Section {request['section']} — intervention opérateur")
        dialog.setModal(False);dialog.resize(650,200)
        layout=QVBoxLayout(dialog);label=QLabel(request['text']);label.setWordWrap(True);layout.addWidget(label)
        actions=QHBoxLayout()
        kind=request['kind']
        def answer(value):
            if isinstance(self.worker,TestWorker):self.worker.reply(value)
            dialog.accept();self.prompt=None
        choices=[('Oui',True),('Non',False),('Non observé',None)] if kind=='visual' else [('Continuer',True)]
        for text,value in choices:
            button=QPushButton(text);button.clicked.connect(lambda checked=False,v=value:answer(v));actions.addWidget(button)
        stop=QPushButton('Arrêter');stop.clicked.connect(self.stop);actions.addWidget(stop)
        layout.addLayout(actions)
        dialog.rejected.connect(self.stop)
        self.prompt=dialog;dialog.show()

    def stop(self):
        if isinstance(self.worker,TestWorker):
            self.worker.stop()
            self.window.status.setText('Arrêt demandé. Couper le +5 V externe. Attente de l’accusé de réception des sorties…')
            self.window.stop_button.setEnabled(False)
        if self.prompt:
            dialog=self.prompt;self.prompt=None;dialog.accept()

    def outcome(self,state,message):
        self.last_outcome=state
        if state!='finished':self.section_finished(self.current_section,'Interrompu' if state=='cancelled' else 'Erreur')
        else:
            # Le dernier contrôle visuel a lieu après STOP et la coupure externe.
            statuses=[r['status'] for r in self.rows if r['section']==7]
            aggregate='FAIL' if 'FAIL' in statuses else 'NON CONCLUANT' if 'NON CONCLUANT' in statuses else 'À QUALIFIER' if 'À QUALIFIER' in statuses else 'PASS'
            self.section_finished(7,aggregate)
        if any(r['test']=='Arrêt des sorties non confirmé' for r in self.rows):
            message+=' ATTENTION : arrêt matériel non confirmé; couper le +5 V externe.'
        self.window.status.setText(message)

    def worker_finished(self):
        old=self.worker;self.worker=None
        if old:old.deleteLater()
        if self.prompt:self.prompt.accept();self.prompt=None
        self.busy(False)
        if self.close_requested:self.finish_close()

    def reset(self):
        if self.worker is not None:return
        self.rows=[];self.waves=[];self.run_profile=None;self.current_section=0;self.last_outcome='';self.run_started=None
        self.window.details.setRowCount(0);self.window.graphs_window.clear_graphs()
        for n in range(1,8):self.section_finished(n,'En attente')

    def edit_profile(self):
        if self.worker is not None:return
        dialog=ProfileDialog(self.profile,self.window)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            try:save_profile(dialog.profile);self.profile=dialog.profile
            except OSError as error:QMessageBox.warning(self.window,'Enregistrement impossible',str(error))

    def export(self):
        if not self.rows:
            QMessageBox.information(self.window,'Export','Aucun résultat à exporter.');return
        name,_=QFileDialog.getSaveFileName(self.window,'Exporter le rapport','resultats_uut.csv','CSV (*.csv)')
        if not name:return
        path=Path(name).with_suffix('.csv')
        try:
            with path.open('w',newline='',encoding='utf-8-sig') as stream:
                writer=csv.DictWriter(stream,fieldnames=list(self.rows[0]),delimiter=';');writer.writeheader();writer.writerows(self.rows)
            rawpath=path.with_name(path.stem+'_echantillons.csv')
            with rawpath.open('w',newline='',encoding='utf-8-sig') as stream:
                writer=csv.writer(stream,delimiter=';')
                writer.writerow(['acquisition','section','tp','frequency_hz','resolution','source','time_us','raw','interval_us'])
                for i,wave in enumerate(self.waves,1):
                    for record in wave['records']:writer.writerow([i,wave['section'],wave['tp'],wave.get('frequency'),wave['resolution'],*record])
            metadata=dict(application='UUT 2.3 Golden',started_utc=self.run_started,outcome=self.last_outcome or 'en_cours',
                          firmware=getattr(self.stm32,'firmware','UUT 2'),mapping=TP,profile=self.run_profile)
            metadata['captures']=[dict(acquisition=i,**{k:w.get(k) for k in
                ('section','tp','frequency','resolution','settle_s','elapsed_us','max_gpio_gap_us')})
                for i,w in enumerate(self.waves,1)]
            path.with_name(path.stem+'_profil.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            self.window.status.setText(f'Export : {path.name}, {rawpath.name} et profil associé.')
        except OSError as error:QMessageBox.warning(self.window,'Export impossible',str(error))

    def disconnect_stm32(self):
        if self.stm32:
            self.stm32.disconnect();self.stm32=None;self.window.connect_button.setText('Connecter')

    def close(self):
        if self.worker is not None:
            self.close_requested=True;self.stop()
            self.window.status.setText('Fermeture après arrêt du travail en cours. Couper le +5 V externe.')
        else:self.finish_close()

    def finish_close(self):
        self.disconnect_stm32();self.window.allow_close=True;self.window.close()
