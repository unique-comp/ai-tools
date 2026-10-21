import unittest
from unittest.mock import patch
from datetime import date, timedelta
from pathlib import Path
import tempfile
import ajeer_tracker as app

class TrackerTests(unittest.TestCase):
    def record(self, expiry='2026-11-05'):
        return app.extract('TQ1234567 Iqama 2000000001 Expiry date '+expiry, 'TEST WORKER.pdf')
    def test_expiry_and_identity(self):
        r=self.record()
        self.assertEqual((r['expiry'],r['permit'],r['iqama'],r['issue']),('2026-11-05','TQ1234567','2000000001',''))
    def test_all_date_boundaries(self):
        today=date(2026,10,21)
        for days,status in [(-1,'Expired'),(0,'Within 15 days'),(15,'Within 15 days'),(16,'16–30 days'),(30,'16–30 days'),(31,'Over 30 days')]:
            with self.subTest(days=days):self.assertEqual(app.bucket({'expiry':(today+timedelta(days=days)).isoformat()},today),status)
    def test_invalid_dates_are_reviewed(self):
        self.assertTrue(self.record('2026-02-31')['issue'])
    def test_duplicate_excluded(self):
        rows=app.deduplicate([self.record(),self.record()])
        self.assertEqual(sum(bool(r.get('duplicate')) for r in rows),1)
    def test_conflict_reviewed(self):
        rows=app.deduplicate([self.record(),self.record('2026-11-06')])
        self.assertTrue(all(r['issue'] for r in rows))
    def test_mac_desktop(self):
        with patch.object(app.sys,'platform','darwin'):
            self.assertEqual(app.desktop(),Path.home()/'Desktop')
    def test_mac_open_uses_argument_list(self):
        with patch.object(app.sys,'platform','darwin'),patch.object(app.subprocess,'Popen') as p:
            app.open_item('/tmp/A folder/test.pdf')
            p.assert_called_once_with(['/usr/bin/open','/tmp/A folder/test.pdf'])
    def test_pdf_extraction(self):
        from reportlab.pdfgen import canvas
        with tempfile.TemporaryDirectory() as folder:
            pdf=Path(folder)/'synthetic.pdf'
            c=canvas.Canvas(str(pdf));c.drawString(40,700,'TQ1234567 Iqama 2000000001 Expiry date 2026-11-05');c.save()
            rows=app.read_pdf(pdf,'synthetic')
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['expiry'],'2026-11-05')
            self.assertEqual(rows[0]['issue'],'')

if __name__=='__main__':unittest.main()
