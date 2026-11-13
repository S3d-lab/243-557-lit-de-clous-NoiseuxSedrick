# Banc UUT — application 2.1 ADC, firmware UUT 2

Le filtre, le gain et le Schmitt sont mesurés avec les entrées du banc. Aucune valeur électrique n’est saisie pendant un test. Seules l'alimentation externe, la manipulation de SW1 et l'observation des LED restent manuelles.

## Installation du correctif sur une V2 fonctionnelle

Fermer l'application. Extraire `lit_de_clous_v2_1_adc.zip`. Dans le terminal ouvert dans le dossier extrait qui contient `installer.py`, coller :

```bash
projet="$HOME/Projects/code_lit_clou/243-557-lit-de-clous-etudiants"
python3 installer.py "$projet" && "$projet/.venv/bin/python" "$projet/python_app/main.py"
```

Le port USB, le firmware, la configuration des points de test, les dépendances et le fichier du profil personnel sont conservés. Aucun flash ni passage dans CubeIDE pour ce correctif Python. Après connexion, la fenêtre indique « application 2.1 ADC, firmware UUT 2 ».

Le correctif s'applique aux sources V2 de `lit_de_clous_uut_v2.zip`. L'installateur vérifie les fichiers avant toute modification, sauvegarde ceux qu'il remplace dans `sauvegardes_uut/` et affiche la commande de restauration. Un conflit local est signalé sans écrasement. Conserver le dossier extrait si une restauration est nécessaire.

## Mapping figé

Les canaux logiciels vont de 0 à 7. Les numéros de broche physiques se rapportent au connecteur concerné, pas à un numéro GPIO du STM32.

| TP | Connexion | Broche physique | Fonction |
|---|---|---:|---|
| TP1 | Alimentation externe +5 V | — | Alimentation UUT |
| TP2 | GND commun, externe − et carte PWM | PWM 10 | Référence du banc |
| TP3 | ADC5 | ADC 6 | Diviseur |
| TP4 | ADC6 | ADC 7 | VALIM |
| TP6 | GPIO7 PWM, entrée | IO 8 | Sortie 555 |
| TP8 | ADC7 | ADC 8 | VCC |
| TP11 | GPIO4 PWM, sortie | IO 5 | STAY_ON |
| TP14 | GPIO5 PWM, entrée | IO 6 | SW_IO |
| TP17 | ADC0 | ADC 1 | Masse interne, mesure de la chute dans R6 |
| TP18 | PWM7 | MUX 8 | Stimulus Vs |
| TP23 | GPIO3 PWM, sortie | IO 4 | SA555_EN |
| TP29 | ADC4 | ADC 5 | Sortie filtre |
| TP30 | GPIO6 PWM, entrée | IO 7 | Sortie comparateur |
| TP31 | ADC3 | ADC 4 | Sortie ampli / entrée inverseuse du comparateur |
| TP34 | ADC2 | ADC 3 | Seuil du comparateur |
| TP35 | ADC1 | ADC 2 | Vref |

Carte PWM : MikroBUS 2, I2C1, expander 0x20, PWM PA1/TIM2_CH2.
Carte ADC : MikroBUS 1, I2C3, expander 0x20 et MCP3423 0x68.
Les constantes d'adresse I2C restent décalées d'un bit dans les appels HAL. Le driver nommé `TCA6416A` utilise les registres GPIO de base compatibles avec le PCAL6416A de la carte PWM. L'acquisition ne dépend pas des interruptions PCAL.

## Utilisation et critères

1. Ouvrir **Étalonnage et critères**. Pour chaque ADC : `V(TP−TP2) = brut × LSB × gain + offset`. Le gain 1 et l'offset 0 initiaux sont **provisoires**. Confirmer la voie seulement après vérification du circuit d'entrée ou étalonnage. Les limites vides du plan restent vides.
2. Lancer **Exécuter les 7 sections**. Suivre les consignes d'alimentation, de pression/relâchement de SW1 et les observations de LED. L'interface reste disponible pendant les acquisitions et les dialogues.
3. Filtre et ampli : la fondamentale est ajustée aux temps des échantillons (`offset + a·sin + b·cos`). L'amplitude crête est `√(a²+b²)`. Le gain est le rapport des amplitudes sur TP31 et TP29, avec le même stimulus de 100 Hz entre les deux captures. Une capture exploitable affiche une valeur même sans limites de conformité.
4. Comparateur : les acquisitions TP31, TP34 et GPIO TP30 ont une horloge commune. TP31/TP34 sont alternés. Le banc mesure TP17 avant et après la capture pour rapporter les seuils à la masse interne. Il compare les conversions TP31 qui entourent chaque front GPIO aux deux dernières conversions de TP34 entièrement **avant** le front. Il estime VH lors d'un front descendant de TP30, VL lors d'un front montant et l'hystérésis VH − VL. Il n’impose pas de valeurs de seuil si le franchissement n'est pas résolu.
5. Exporter les résultats : CSV des contrôles, CSV des codes bruts/horodatages, JSON du profil et du mapping utilisés. Les résultats déjà acquis restent exportables après une erreur ou un arrêt.

**PASS** : critère satisfait avec une méthode qualifiée. **FAIL** : critère non satisfait. **À QUALIFIER** : étalonnage, méthode ou limites à compléter. **NON CONCLUANT** : mesure indisponible, saturée, trop peu résolue ou observation non réalisée. **INFO** : donnée descriptive, sans verdict d'acceptation.

Une section ne reçoit PASS que si ses contrôles obligatoires sont conclusifs et satisfaits. Une diode Schottky de chute indicative 0,34 V donne le repère VCC ≈ 4,66 V avec 5 V d'entrée; ce repère ne constitue pas une tolérance garantie. Les valeurs Golden et les tolérances du plan ne sont pas inventées.

## Séquences

1. Alimentation : DS1 avant/après alimentation, TP17−TP2 et VCC=TP8−TP17.
2. VALIM : repos, STAY_ON, SW1 maintenu, relâchement avec maintien, arrêt du maintien; lecture SW_IO et observations DS2.
3. Diviseur : rapport TP3/VALIM puis retour au niveau bas.
4. 555 : entrée EN inactive, oscillation active avec contrôle de VCC, fréquence/rapport cyclique, retour inactif et DS3.
5. Filtre : Vref et rejet du continu pour Vs bas/haut; acquisitions à 1, 10 et 100 Hz; comparaison des amplitudes de fondamentale.
6. Ampli : point de repos et gain continu, gain AC à 100 Hz, recherche de plateaux dans les échantillons, selon la cadence effectivement obtenue.
7. Comparateur : traces TP31, TP34 et TP30, fréquence de sortie, estimation des seuils et encadrement de l’hystérésis, séquence de fronts et observation de DS4, état final et extinction des LED après coupure externe.

## Portée des mesures de la V2.1

- **100 Hz** : afficher la cadence réelle et la dispersion. Le logiciel travaille sur des périodes entières et contrôle aussi la répartition des phases, la répétabilité sur plusieurs fenêtres, le bruit et la saturation. La marge chiffrée est une estimation de répétabilité et de résolution; elle ne couvre pas toutes les erreurs de calibration ou de bande passante.
- L'ADC et son étage d'entrée ont leur propre réponse AC. Les amplitudes affichées sont celles **indiquées par le banc** en volts crête, sans correction de sa réponse en fréquence. La case **Réponse et critères à 100 Hz vérifiés sur une carte Golden** s’utilise une fois dans le profil, après caractérisation sur un UUT connu bon avec les mêmes voies, gains, stimulus, résolution 12 bits et méthode. Elle n'ajoute aucune valeur lors d'un test, ne remplit pas les limites et ne contourne pas les contrôles d'acquisition. Avec un profil existant elle est décochée par défaut; en l'absence de qualification, une mesure exploitable reste « À QUALIFIER ».
- **Écrêtage** : le gain et la forme sont deux contrôles distincts. Pour chercher un plateau d'au moins 15 % de la période, cette méthode exige que l'espacement réel **et** la fenêtre de conversion restent ≤ T/20 et que l'excursion dépasse 80 pas ADC. Avec le MCP3423 à 100 Hz, une amplitude peut être exploitable alors que la forme reste **NON CONCLUANTE**. Cela ne prouve ni une panne de l'ampli ni l'absence de tout écrêtage. Le programme ne remplace pas cette mesure par un résultat saisi.
- **Schmitt** : l'intervalle estimé autour de chaque seuil tient compte des conversions avant/après le front, de la quantification et de la dérive mesurée de TP17. Le résultat suppose une variation monotone de TP31 dans cette fenêtre, TP34 stable avant le front et TP17 stable pendant la capture. Il n'exclut pas des rebonds plus brefs que le polling. Un seuil mal encadré, une voie saturée, une excursion insuffisante ou une incertitude qui chevauche la limite donnent **NON CONCLUANT**. Un sens de commutation résolu et contraire au comportement attendu peut donner **FAIL** sur des voies étalonnées.
- Les contrôles de cadence, de dispersion et de densité servent à juger **la qualité de la mesure**, pas à inventer des tolérances UUT. Les valeurs d'acceptation restent celles de ton plan. Les CSV conservent les codes bruts et les horodatages; le JSON associe le profil et la version de l'application à l'essai.

## Architecture et changements

- `python_app/src/uut_config.py` : mapping, repères et profil.
- `python_app/src/models/test_runner.py` : déroulement des sept sections; anciennes méthodes du cours complétées.
- `python_app/src/models/measurements.py` : fronts, période, rapport cyclique, fondamentale et encadrement descriptif des seuils.
- `python_app/src/hardware/stm32_hardware.py` : transport série, accusés de réception, acquisition et arrêt.
- `python_app/src/controllers/` : séquence en thread, demandes opérateur, affichage/export et fermeture différée.
- `Core/Src/main.c`, `stm32f4xx_hal_msp.c`, `.ioc` : PWM déplacé de PB1/TIM3_CH4 vers **PA1/TIM2_CH2**. TIM2 permet la période 99999 nécessaire à 1 Hz avec le tick choisi de 100 kHz. Plus de stimulus 50 % ni de GPIO1 forcée à 1 au démarrage.
- `Core/Src/capture.c` : acquisition coopérative, jusqu'à 6 secondes, tampon 2048 enregistrements, horloge DWT commune, restauration de résolution ADC et détection de dépassement.
- `Core/Src/command_handler.c` : protocole UUT 2, STOP, CAP et restauration du scan I2C.
- `mcp3423.c` : attente maximale 18 bits portée de 350 à 500 ms, au-delà de la durée de conversion à la cadence minimale spécifiée.

L'alimentation, les expandeurs et le convertisseur doivent répondre pour lancer les tests. Les anciens scripts USB/PWM présents dans le projet sont conservés; certains utilisent encore les anciens canaux par défaut ou le protocole brut précédent. Pour le câblage UUT, utiliser l'application actualisée, pas ces démonstrations indépendantes.

## Protocole ajouté

Toutes les commandes sont ASCII terminées par `\n`. Attendre une réponse avant la commande suivante; le récepteur USB conserve une seule commande à la fois.

| Commande | Réponse / effet |
|---|---|
| `INFO` | Version et routage compilés |
| `STOP` | `STOPPED`, ou `ERROR STOP <masque>` si arrêt partiel |
| `PWM DISABLE` | Désactive le mux PWM |
| `CAP START ADC c durée_ms` | Acquisition analogique, résolution courante |
| `CAP START GPIO p durée_ms` | Niveau initial, changements et niveau final d'une entrée PWM |
| `CAP START SYNC c1 c2 p durée_ms` | Deux ADC alternés et une entrée GPIO, même horloge |
| `CAP STATUS` | `CAP BUSY/DONE/ABORTED count resolution elapsed_us max_gpio_gap_us`, ou `CAP ERROR raison` |
| `CAP BLOCK index` | Deux enregistrements `source,t_us,brut,intervalle_us;` au maximum |
| `I2C PROBE bus adresse_décimale` | `ACK`, `NACK`, ou erreur de bus |

Sources 0..7 = ADC, 16..23 = GPIO PWM. Pour l'ADC, le temps est placé au milieu de l'intervalle de conversion estimé; ce n'est pas un instant d'échantillonnage ponctuel exact. Pour les GPIO, l'intervalle donne la fenêtre de détection du changement. La durée autorisée est 100..6000 ms. Le mode et la résolution ADC sont restaurés après acquisition. L'ancien `ADC CAPTURE 3000` bloquant est remplacé par CAP; l'API Python `adc_capture()` adapte ses appels automatiquement.

STOP met le rapport PWM à zéro, désactive les mux, charge les sorties TP11/TP23 à zéro puis les remet en entrée. Toutes les opérations sont tentées même si l'une échoue. Le masque d'erreur indique : bit0 restauration ADC; bit1 mux PWM; bits2/3 écriture GPIO3/4; bits4/5 passage en entrée; bit6 mux ADC. **Il ne coupe pas le +5 V externe.** L'arrêt intervient entre les transactions I2C/USB et n'est pas un arrêt d'urgence matériel. Une rupture USB/plantage du PC ne peut pas garantir la commande des sorties; couper alors l'alimentation externe.

## Vérifications

V2.1 : tests Python hors matériel pour les sept sections, la communication simulée, les erreurs, l’arrêt et le comportement Qt. Les cas dédiés aux sections 5–7 couvrent la polarité des fronts, le décalage de TP17, les tolérances chevauchées, les signaux insuffisants, le rapport d'amplitudes, les données saturées et la conservation du profil V2. Le firmware n’est pas modifié. La validation sur une UUT physique reste à faire.

Pour reproduire : depuis la racine du projet, `.venv/bin/python -m unittest discover -s python_app/tests -v`.

## Sources

- Sources Python/STM32 : archive fournie `lit_de_clous_sources_20261104_164042.zip`.
- Mapping réel des TP et décision d'alimentation externe : messages de Sedrick dans cette conversation.
- [Énoncé du mini-projet, § 3.2](https://chatgpt.com/library?file_id=libfile_18185432fef88191850726813b46ce01&account_id=personal) : traitement des échantillons ADC par le programme.
- [Plan de test, sections 5 à 7](https://chatgpt.com/library?file_id=libfile_555e2c27e1908191b61be7dbb0273285&account_id=personal) : fondamentale, gain et seuils.
- UUT : `SCH_UUT-1.5.pdf`.
- Carte support : `Schematic_Prints_-_243-436_Carte_Support_STM32_RevA-4.PDF` (routage MikroBUS 1/2).
- Carte PWM de Sedrick : `20261004183330_SCH-1(1).pdf` (PCAL et mux). Son étage ADC optionnel ne sert pas à supposer l'étalonnage de la carte ADC réellement utilisée.
- [ST — STM32F405/407, fonctions alternatives PA1/TIM2_CH2](https://www.st.com/resource/en/datasheet/stm32f407vg.pdf).
- [Microchip — MCP3422/3/4, DS22088C](https://ww1.microchip.com/downloads/aemDocuments/documents/OTH/ProductDocuments/DataSheets/22088c.pdf) : plage, LSB, modes, cadence et bit RDY.
- [NXP — PCAL6416A](https://www.nxp.com/docs/en/data-sheet/PCAL6416A.pdf) : registres GPIO et comportement des interruptions.
