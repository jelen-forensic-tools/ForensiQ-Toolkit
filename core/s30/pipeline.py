"""Complete analysis run shared by the GUI and the command line."""
import datetime as dt
import os

from .analyzer import DumpAnalyzer
from .export import export
from .i18n import t

DEFAULT_OPTIONS = dict(contacts=True, sms=True, calls=True, files=True, disk=True, pdf=True, thumbs=True)


def run_analysis(dump, out_root, options=None, case=None, log=print, progress=None, subfolder=True,
                 on_analyzed=None):
    """Analyze `dump` and write all selected outputs.

    Results go to a new sub-folder `<dump name>_<analysis>_<timestamp>` inside `out_root`
    (or directly into `out_root` when subfolder=False). `on_analyzed(analyzer)` is called after
    parsing, before export. Returns (analyzer, target_folder).
    """
    o = dict(DEFAULT_OPTIONS, **(options or {}))
    progress = progress or (lambda pct: None)
    name = os.path.splitext(os.path.basename(dump))[0]
    if subfolder:
        stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
        target = os.path.normpath(os.path.join(out_root, f"{name}_{t('fn.folder')}_{stamp}"))
        base, i = target, 1
        while os.path.exists(target):              # never mix outputs of two runs
            i += 1
            target = f'{base}_{i}'
    else:
        target = os.path.normpath(out_root)

    an = DumpAnalyzer(dump, log=log, progress=progress)
    an.run(contacts=o['contacts'], sms=o['sms'], calls=o['calls'], files=o['files'] or o['disk'])
    if on_analyzed:
        on_analyzed(an)
    export(an, target, contacts=o['contacts'], sms=o['sms'], calls=o['calls'], files=o['files'],
           disk_image=o['disk'], log=log)
    progress(85)
    if o['pdf']:
        from .report import build_pdf          # reportlab is only needed for the PDF
        log(t('log.pdf_gen'))
        build_pdf(an, os.path.join(target, f'report_{name}.pdf'), case=case,
                  sections=dict(contacts=o['contacts'], sms=o['sms'], calls=o['calls'], files=o['files']),
                  photo_thumbs=o['thumbs'], log=log)
    for w in an.warning_texts():
        log(t('log.warning', msg=w))
    progress(100)
    log(t('log.done', path=target))
    return an, target
