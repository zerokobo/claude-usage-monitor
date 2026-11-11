import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    import app
except ImportError:
    app = None


@unittest.skipIf(app is None, 'Qt runtime required; install requirements.txt first')
class UITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = app.QApplication.instance() or app.QApplication([])

    def setUp(self):
        self.panel = app.Panel(demo=True)

    def tearDown(self):
        self.panel.tray.hide()
        self.panel.deleteLater()
        self.qt.processEvents()

    def test_demo_shows_both_accounts(self):
        self.assertEqual(list(self.panel.cards), ['個人', '仕事'])
        big, bars, status, captions = self.panel.cards['個人']
        self.assertIn('68%', big.text())
        self.assertEqual(bars[1][1].value(), 460)

    def test_rate_limit_keeps_values_and_error_clears_them(self):
        big, bars, status, captions = self.panel.cards['仕事']
        self.panel.receive('仕事', None, 0, '取得間隔を空けています。6分後に再試行')
        self.assertIn('22%', big.text())
        self.assertIn('再試行', status.toolTip())
        self.panel.receive('仕事', [], 0, 'ログイン期限切れ')
        self.assertNotIn('22%', big.text())
        self.assertTrue(all(bar.value() == 0 for _, bar in bars))

    def test_accounts_can_be_removed_and_rebuilt(self):
        self.panel.set_accounts([{'name': '個人', 'config_dir': ''}])
        self.qt.processEvents()
        self.assertEqual(list(self.panel.cards), ['個人'])
        self.assertFalse(self.panel.legend.isVisibleTo(self.panel))
        self.panel.receive('仕事', [], 0, 'x')  # removed account: ignored


if __name__ == '__main__':
    unittest.main()
