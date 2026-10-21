"""Read digital and raster-only synthetic PDFs through the packaged executable."""
import json,subprocess,sys,tempfile
from pathlib import Path
from reportlab.pdfgen import canvas
import pypdfium2 as pdfium

exe=Path(sys.argv[1])
with tempfile.TemporaryDirectory(prefix='ajeer-acceptance-') as folder:
    root=Path(folder);digital=root/'TEST WORKER.pdf';scanned=root/'SCANNED TEST.pdf'
    c=canvas.Canvas(str(digital));c.setFont('Helvetica',20)
    for y,line in [(720,'AJEER TEST PERMIT'),(650,'TQ1234567'),(580,'Iqama 2000000001'),(510,'Expiry date 2026-11-05')]:c.drawString(45,y,line)
    c.save()
    doc=pdfium.PdfDocument(str(digital));page=doc[0];bitmap=page.render(scale=2)
    bitmap.to_pil().convert('RGB').save(scanned,'PDF',resolution=144)
    bitmap.close();page.close();doc.close()
    output=root/'results.json'
    subprocess.run([str(exe),'--scan-test',str(root),str(output)],check=True,timeout=180)
    rows=json.loads(output.read_text())
    plain=next(r for r in rows if Path(r['file']).name==digital.name)
    scan=next(r for r in rows if Path(r['file']).name==scanned.name)
    assert plain['expiry']=='2026-11-05' and not plain['issue'],plain
    assert scan['method']=='Scan (OCR)' and scan['expiry']=='2026-11-05' and scan['issue'],scan
    print('PASS: packaged app reads a digital PDF and a scanned PDF; scan requires confirmation.')
