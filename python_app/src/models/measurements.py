"""Calculs indépendants du matériel, à partir d'horodatages STM32."""
import math
from statistics import mean, median
import numpy as np

class Inconclusive(RuntimeError):
    """La mesure ne permet pas de conclure sur le circuit."""

def digital_metrics(records, max_gap_us=0):
    """records = [(t_us, niveau, incertitude_us), ...], avec points initial/final."""
    if len(records) < 2:
        raise Inconclusive('Acquisition numérique trop courte.')
    if any(b[0] <= a[0] for a,b in zip(records, records[1:])):
        raise Inconclusive('Horodatages numériques non croissants.')
    if any(r[1] not in (0, 1) for r in records):
        raise Inconclusive('Niveau numérique invalide.')
    edges = [(b[0], b[1], b[2]) for a,b in zip(records, records[1:]) if a[1] != b[1]]
    rising = [t for t,v,_ in edges if v == 1]
    periods, highs = [], []
    for start, end in zip(rising, rising[1:]):
        falling = [t for t,v,_ in edges if v == 0 and start < t < end]
        if len(falling) != 1:
            raise Inconclusive('Cycle incomplet ou transitions supplémentaires.')
        periods.append(end-start); highs.append(falling[0]-start)
    if len(periods) < 2:
        raise Inconclusive('Moins de deux périodes complètes observées.')
    period = mean(periods)
    gap = max(max_gap_us, max((r[2] for r in records), default=0))
    # Si le polling peut masquer une impulsion, ne pas publier une mesure valide.
    if gap * 4 >= min(min(highs), min(p-h for p,h in zip(periods, highs))):
        raise Inconclusive('Polling I2C trop lent pour résoudre ces impulsions.')
    return {'frequency': 1e6/period, 'duty': 100*sum(highs)/sum(periods),
            'period_us': period, 'cycles': len(periods), 'edges': edges,
            'timing_uncertainty_us': gap,
            'duty_uncertainty_pct': 200*gap/period}

def fitted_quantization_bound(design, step_v):
    """Borne déterministe du module sin/cos pour une erreur de quantification ±LSB/2.

    Ce calcul ne suppose pas un bruit blanc et ne divise pas le LSB par sqrt(N).
    Pour P = pseudo-inverse(design)[1:3], max ||P e|| =
    LSB/2 * max_theta sum |u(theta) @ P[:, i]|. La marge angulaire ci-dessous
    majore aussi les directions situées entre les points de la grille.
    Il ne s'agit pas d'une borne d'erreur totale du banc analogique.
    """
    if not math.isfinite(step_v) or step_v < 0:
        raise Inconclusive('Pas ADC invalide.')
    projection = np.linalg.pinv(design)[1:3]
    n = 720
    angles = np.arange(n)*2*np.pi/n
    directions = np.column_stack((np.cos(angles), np.sin(angles)))
    sampled_max = np.abs(directions @ projection).sum(axis=1).max()
    between_grid = (np.pi/n)*np.linalg.norm(projection, axis=0).sum()
    return float(step_v/2*(sampled_max+between_grid))


def fundamental(time_s, voltage, frequency, step_v=0.0, spans_s=None, *, fit_quantization=False):
    """Amplitude crête dans la bande du banc, ajustée aux temps STM32.

    Un ajustement global et plusieurs fenêtres de périodes entières vérifient
    que le résultat est répétable. Ce contrôle ne corrige pas la réponse AC de
    l'électronique. Les marges ci-dessous sont des gardes de mesure, pas des
    tolérances de conformité de l'UUT.
    """
    t, y = np.asarray(time_s, float), np.asarray(voltage, float)
    if len(t) != len(y) or len(t) < 12 or not frequency > 0:
        raise Inconclusive('Nombre de points insuffisant pour la fondamentale.')
    dt = np.diff(t)
    if not np.isfinite(t).all() or not np.isfinite(y).all() or (dt <= 0).any():
        raise Inconclusive('Temps ou échantillons ADC invalides.')
    t = t-t[0]
    ncycles = int(t[-1]*frequency + 1e-10)
    if ncycles < 3:
        raise Inconclusive('Moins de trois périodes complètes dans la capture.')
    rate = (len(t)-1)/t[-1]
    if (rate <= 2.2*frequency or np.percentile(dt,95)*frequency > .75
            or dt.max()*frequency > 1.5):
        raise Inconclusive(f'Cadence réelle {rate:.1f} éch/s à {frequency} Hz; couverture temporelle insuffisante.')
    if spans_s is not None:
        spans = np.asarray(spans_s, float)
        if (len(spans)!=len(t) or not np.isfinite(spans).all() or (spans<=0).any()
                or np.percentile(spans,95)*frequency > .75):
            raise Inconclusive('Fenêtres de conversion trop longues ou absentes.')
    keep = t < ncycles/frequency
    t, y = t[keep], y[keep]
    phase = np.remainder(t*frequency,1.)
    phases = np.sort(phase)
    if np.diff(np.r_[phases,phases[0]+1]).max() > .26:
        raise Inconclusive('Phases du signal trop peu couvertes pour estimer une amplitude.')
    design = np.column_stack([np.ones(len(t)),np.sin(2*np.pi*phase),np.cos(2*np.pi*phase)])
    def fit(matrix, data):
        if len(data)<8 or np.linalg.cond(matrix)>10:
            raise Inconclusive('Phases mal réparties dans une fenêtre de mesure.')
        coefs = np.linalg.lstsq(matrix,data,rcond=None)[0]
        return float(np.hypot(coefs[1],coefs[2])), coefs
    amp, coefs = fit(design,y)
    residual = y-design@coefs
    rms = float(np.sqrt(np.mean(residual**2)))
    limits = np.linspace(0,ncycles,min(ncycles,5)+1,dtype=int)
    repeatability = 0.
    for begin,end in zip(limits,limits[1:]):
        inside = (t >= begin/frequency) & (t < end/frequency)
        local,_ = fit(design[inside],y[inside])
        repeatability = max(repeatability,abs(local-amp))
    # Estimation pratique de dispersion, pas une incertitude métrologique totale.
    variance = np.sum(residual**2)/max(1,len(y)-3)
    cov = np.linalg.inv(design.T@design)*variance
    fit_error = 3*math.sqrt(max(0.,float(np.linalg.eigvalsh(cov[1:,1:])[-1])))
    quantization = fitted_quantization_bound(design,step_v) if fit_quantization else float(step_v)
    uncertainty = max(quantization,fit_error,repeatability,1e-12)
    if amp <= max(4*step_v,3*uncertainty):
        raise Inconclusive('Fondamentale trop faible ou instable vis-à-vis du bruit et de la résolution ADC.')
    return dict(amplitude=amp,offset=float(coefs[0]),sample_rate=rate,cycles=ncycles,
                uncertainty=uncertainty,rms_residual=rms,quantization_bound=quantization,
                qualified_bandwidth=frequency<=10 and rate>=10*frequency,
                note='Amplitude de la fondamentale mesurée par le banc; réponse AC non compensée.')


def sampled_1hz(time_s, voltage, step_v, spans_s):
    """Fondamentale crête de la trace à 1 Hz, même si sa forme n'est pas sinusoïdale."""
    t, y, spans = (np.asarray(a, dtype=float) for a in (time_s, voltage, spans_s))
    if (t.ndim != 1 or y.shape != t.shape or spans.shape != t.shape or len(t) < 2
            or not all(np.isfinite(a).all() for a in (t, y, spans))
            or not np.isfinite(step_v) or step_v <= 0):
        raise Inconclusive('Capture TP29 invalide ou incomplète.')
    t = t - t[0]
    dt = np.diff(t)
    if (dt <= 0).any() or dt.max() > .100001 or (spans <= 0).any() or spans.max() > .100001:
        raise Inconclusive('Espacement ou durée de conversion incompatible avec 10 mesures par seconde.')
    cycles = int(t[-1])
    if cycles < 5 or any(np.count_nonzero((t >= k) & (t < k+1)) < 10 for k in range(cycles)):
        raise Inconclusive('Il faut cinq périodes complètes, avec au moins dix mesures réelles chacune.')

    # Grille de quadrature pour intégrer la trace interpolée aux mêmes phases.
    # Ces 64 points ne sont pas considérés comme des mesures indépendantes.
    phase = (np.arange(64) + .5) / 64
    waves = np.array([np.interp(k + phase, t, y) for k in range(cycles)])
    waves -= waves.mean(axis=1, keepdims=True)
    coef = (2 / len(phase)) * (waves @ np.exp(-2j * np.pi * phase))
    amplitude = float(abs(coef.mean()))
    # Répétabilité de la quantité mesurée, la composante à 1 Hz : retenir
    # le pire écart entre deux cycles, en conservant amplitude ET phase.
    # Les différences de forme hors de cette fréquence ne constituent pas
    # une erreur sur A1. Le plancher d'un pas ADC reste indépendant.
    spread_1hz = float(np.abs(coef[:, None] - coef[None, :]).max())
    margin = max(step_v, spread_1hz)
    return amplitude, margin, cycles


def flat_top_evidence(wave, frequency, step_v):
    """Détecte des plateaux aux extrêmes de la trace; None = événement isolé.
    Exige de vrais points successifs (ne replie pas des cycles sous-échantillonnés).
    Contrôle limité aux plateaux d'au moins 15 % d'une période.
    """
    t=np.asarray(wave['time'],float); y=np.asarray(wave['voltage'],float)
    spans=np.asarray([r[3]/1e6 for r in wave['records']],float)
    if len(t)<4 or len(t)!=len(y) or len(t)!=len(spans) or not frequency>0:
        raise Inconclusive('Trace insuffisante pour examiner un écrêtage.')
    dt=np.diff(t); period=1/frequency
    if (not np.isfinite(t).all() or not np.isfinite(y).all() or not np.isfinite(spans).all()
            or (dt<=0).any() or (spans<=0).any() or t[-1]-t[0]<3*period):
        raise Inconclusive('Durée ou horodatages insuffisants pour examiner un écrêtage.')
    if max(dt.max(),spans.max())>period/20:
        raise Inconclusive(f'{period/np.mean(dt):.1f} points/période en moyenne; '
                           'espacement et fenêtre de conversion doivent chacun être ≤ T/20 pour examiner les plateaux.')
    ylo,yhi=float(y.min()),float(y.max()); swing=yhi-ylo
    if swing<=80*step_v:
        raise Inconclusive('Excursion trop petite pour distinguer un plateau de la quantification ADC.')
    tol=max(2*step_v,.005*swing); found=[]
    for i,j in enumerate(np.searchsorted(t,t+.15*period)):
        if j>=len(t):break
        window=y[i:j+1]; v0,v1=window.min(),window.max()
        if v1-v0<=tol and (v1<=ylo+.05*swing or v0>=yhi-.05*swing):
            found.append(t[i])
    if not found:return False
    return True if found[-1]-found[0] >= .75*period else None


def opposite_edge_changes(analog, gpio, step_v, max_gap_us=0, period_us=1e6):
    """Sens des variations locales de TP31 autour des fronts de TP30.

    L'ADC reste sur TP31. Ce constat ne mesure aucun seuil ni hystérésis.
    Les tuples analogiques sont (milieu_us, volts, durée_conversion_us).
    """
    if (len(analog) < 4 or not math.isfinite(step_v) or step_v <= 0
            or any(not all(math.isfinite(x) for x in row) or row[2] <= 0 for row in analog)
            or any(b[0] <= a[0] for a, b in zip(analog, analog[1:]))):
        raise Inconclusive('Trace TP31 invalide ou absente.')
    digital_metrics(gpio, max_gap_us)
    if one_edge_per_period(gpio, period_us) is not True:
        raise Inconclusive('Les fronts TP30 de cette capture ne suivent pas le stimulus.')
    edges = [b for a, b in zip(gpio, gpio[1:]) if a[1] != b[1]]
    counts = {0: 0, 1: 0}
    checks, windows = [], []
    for i, (t, level, gap) in enumerate(edges):
        # Même fenêtre utile que le diagnostic de la Golden : ignorer la
        # première seconde, sans recaler ni inventer de mesures intermédiaires.
        if t < gpio[0][0] + period_us:
            continue
        left = edges[i-1][0] if i else gpio[0][0]
        right = edges[i+1][0]-edges[i+1][2] if i+1 < len(edges) else gpio[-1][0]
        before = [r for r in analog if left < r[0]-r[2]/2 and r[0]+r[2]/2 < t-gap]
        after = [r for r in analog if t < r[0]-r[2]/2 and r[0]+r[2]/2 < right]
        if not before or not after:
            raise Inconclusive('Un front TP30 ne possède pas de mesures TP31 avant et après.')
        a, b = before[-1], after[0]
        window = b[0]+b[2]/2-(a[0]-a[2]/2)
        if window > period_us/10:
            raise Inconclusive('Mesures TP31 trop éloignées du front TP30.')
        change = b[1]-a[1]
        if abs(change) <= 2*step_v:
            raise Inconclusive('Variation TP31 non résolue autour d’un front TP30.')
        checks.append(change > 0 if level == 0 else change < 0)
        counts[level] += 1
        windows.append(window)
    if min(counts.values()) < 2:
        raise Inconclusive('Moins de deux fronts exploitables dans chaque sens.')
    return dict(opposite=all(checks), falling=counts[0], rising=counts[1],
                max_window_us=max(windows))


def apparent_switching_levels(analog, gpio, step_v, max_gap_us=0, period_us=200000):
    """Niveaux apparents de TP31 encadrant les fronts TP30, ADC fixé sur TP31.

    Ce sont les niveaux de la trace chargée/filtrée par le banc à la fréquence
    testée, PAS une mesure directe de TP34 ni des seuils statiques de l'UUT.
    On conserve l'union des encadrements, le pas ADC et toute leur dispersion;
    aucune interpolation ne prétend retrouver la tension instantanée du front.
    """
    if (len(analog)<4 or not math.isfinite(step_v) or step_v<=0
            or not math.isfinite(period_us) or period_us<=0
            or any(not all(math.isfinite(x) for x in r) or r[2]<=0 for r in analog)
            or any(b[0]<=a[0] for a,b in zip(analog,analog[1:]))
            or any(not all(math.isfinite(x) for x in r) or r[2]<0 for r in gpio)):
        raise Inconclusive('Capture TP31/TP30 invalide.')
    digital_metrics(gpio,max_gap_us)
    if one_edge_per_period(gpio,period_us) is not True:
        raise Inconclusive('La capture TP30 ne présente pas un front de chaque sens par période.')
    if max(max(b[0]-a[0] for a,b in zip(analog,analog[1:])),
           max(r[2] for r in analog)) > period_us/10:
        raise Inconclusive('Cadence ADC insuffisante pour encadrer les commutations.')
    edges=[b for a,b in zip(gpio,gpio[1:]) if a[1]!=b[1]]
    brackets={0:[],1:[]}; directions=[]; windows=[]
    max_window=period_us/5
    for i,(t,level,gap) in enumerate(edges):
        left=edges[i-1][0] if i else gpio[0][0]
        right=edges[i+1][0]-edges[i+1][2] if i+1<len(edges) else gpio[-1][0]
        before=[r for r in analog if left<r[0]-r[2]/2 and r[0]+r[2]/2<t-gap]
        after=[r for r in analog if t<r[0]-r[2]/2 and r[0]+r[2]/2<right]
        if not before or not after:
            # Seul un front au bord de l'acquisition peut être incomplet.
            if ((not before and i==0 and t-gpio[0][0]<max_window)
                    or (not after and i==len(edges)-1 and gpio[-1][0]-t<max_window)):
                continue
            raise Inconclusive('Front intérieur dépourvu de mesures TP31 avant et après.')
        a,b=before[-1],after[0]
        window=b[0]+b[2]/2-(a[0]-a[2]/2)
        if window>max_window:
            raise Inconclusive('Encadrement ADC trop éloigné du front TP30.')
        change=b[1]-a[1]
        if abs(change)<=2*step_v:
            raise Inconclusive('Variation de TP31 non résolue autour du front TP30.')
        direction=1 if change>0 else -1
        near=[v for ts,v,span in analog if a[0]<=ts<=b[0]]
        if any(direction*(v1-v0)<-2*step_v-1e-12 for v0,v1 in zip(near,near[1:])):
            raise Inconclusive('TP31 non monotone dans un encadrement de commutation.')
        directions.append(direction==(1 if level==0 else -1))
        lo,hi=sorted((a[1],b[1]))
        brackets[level].append((lo-step_v,hi+step_v))
        windows.append(window)
    if min(len(v) for v in brackets.values())<3:
        raise Inconclusive('Moins de trois commutations encadrées dans chaque sens.')
    result=dict(inverted=all(directions),falling=len(brackets[0]),rising=len(brackets[1]),
                max_window_us=max(windows))
    if not result['inverted']:
        return result
    for level,key in ((0,'vh'),(1,'vl')):
        bounds=brackets[level]
        v=median((lo+hi)/2 for lo,hi in bounds)
        result[key]=v
        result[key+'_u']=max(v-min(lo for lo,hi in bounds),max(hi for lo,hi in bounds)-v)
    result['width']=result['vh']-result['vl']
    result['width_u']=result['vh_u']+result['vl_u']
    return result


def schmitt_estimates(analog, gpio, reference=None, step_v=.001, ref_step_v=.001,
                      ground_error=0., period_us=1e6):
    """TP31 et TP34 rapportés à TP17; (milieu_us, volts, durée_conversion_us).
    GPIO = (fin_lecture_us, niveau, intervalle_de_détection_us). Un front est
    encadré par deux conversions TP31. Le seuil TP34 est pris AVANT le front :
    la rétroaction le modifie juste après. Ne suppose pas de simultanéité.
    """
    if reference is None:raise Inconclusive('Trace du seuil TP34 absente.')
    for trace in (analog,reference):
        if (len(trace)<4 or any(b[0]<=a[0] for a,b in zip(trace,trace[1:]))
                or any(not math.isfinite(v) or span<=0 for t,v,span in trace)):
            raise Inconclusive('Trace analogique absente ou horodatages non exploitables.')
    if len(gpio)<2:raise Inconclusive('Aucun état TP30 enregistré.')
    transitions=[b for a,b in zip(gpio,gpio[1:]) if a[1]!=b[1]]
    if not transitions:raise Inconclusive('Aucun front sur TP30; franchissement des seuils non établi.')
    brackets={0:[],1:[]}; checks=[]; last=gpio[0][0]; max_window=period_us/10
    margins=(step_v+ref_step_v)
    for t,level,gap in transitions:
        around=lambda row: last < row[0]-row[2]/2 and row[0]+row[2]/2 < t-gap
        prior=[v for v in analog if around(v)]
        refs=[v for v in reference if around(v)]
        after=[v for v in analog if v[0]-v[2]/2 > t]
        last=t
        if not prior or len(refs)<2 or not after:
            if t-gpio[0][0] < max_window or gpio[-1][0]-t < max_window:continue
            raise Inconclusive('Un front intérieur est dépourvu de mesures ADC avant/après.')
        a,b=prior[-1],after[0]; r0,r1=refs[-2:]
        if b[0]-a[0] > max_window or t-r0[0] > max_window or gap>max_window/2:
            raise Inconclusive('Intervalle de mesure trop large autour d’un front de TP30.')
        # Ne pas utiliser des mesures traversant le front suivant (glitch, double transition).
        if any(t<t2<=b[0]+b[2]/2+g2 for t2,_,g2 in transitions):
            raise Inconclusive('Impulsions trop rapprochées pour séparer les conversions ADC.')
        vlo,vhi=sorted((r0[1],r1[1])); before,after_v=a[1],b[1]
        rising=before<vlo-margins and after_v>vhi+margins
        falling=before>vhi+margins and after_v<vlo-margins
        checks.append((rising if level==0 else falling) if rising or falling else None)
        low,high=sorted((before,after_v))
        brackets[level].append((low-step_v-ground_error,high+step_v+ground_error))
    if min(len(brackets[0]),len(brackets[1]))<2:
        raise Inconclusive('Moins de deux fronts exploitables de chaque sens.')
    if any(item is None for item in checks):
        raise Inconclusive('Le signal mesuré sur TP31 ne franchit pas clairement le seuil TP34 précédant chaque front.')
    if not all(checks):
        return {'inverted':False}
    result={'inverted':True,'counts':(len(brackets[0]),len(brackets[1]))}
    for level,key in ((0,'vh'),(1,'vl')):
        bounds=brackets[level]
        v=median((lo+hi)/2 for lo,hi in bounds)
        result[key]=v
        result[key+'_u']=max(v-min(lo for lo,hi in bounds),max(hi for lo,hi in bounds)-v)
    result['width']=result['vh']-result['vl']
    result['width_u']=result['vh_u']+result['vl_u']
    return result


def one_edge_per_period(gpio,period_us=1e6):
    """Un front de CHAQUE sens par période observée, phase PWM inconnue.
    Deux origines de phase (une par sens) évitent de pénaliser le rapport cyclique.
    Renvoie None si un intervalle de détection chevauche la frontière d'un cycle.
    """
    edges=[b for a,b in zip(gpio,gpio[1:]) if a[1]!=b[1]]
    for level in (0,1):
        times=[(t,g) for t,v,g in edges if v==level]
        if len(times)<3:return None
        center=times[0][0]-times[0][1]/2
        # Vérifier chaque fenêtre entière, sans imposer une phase à l'instant de départ.
        i=math.ceil((gpio[0][0]-center)/period_us + .5)
        n=0
        while center+(i+.5)*period_us <= gpio[-1][0]:
            low=center+(i-.5)*period_us; high=low+period_us
            if any(t-g < low <= t or t-g < high <= t for t,g in times):return None
            if sum(low<=t<high for t,g in times)!=1:return False
            n+=1;i+=1
        if n<2:return None
    return True
