"""Presentation contracts for the mist-teal redesign. No user state or IO."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy
from growth_native import GrowthDialog, derive
from growth_theme import MENU_QSS, PALETTE, icon

APP = QApplication.instance() or QApplication([])


class GrowthDesignTests(unittest.TestCase):
    def setUp(self):
        self.dialog = GrowthDialog()
    def tearDown(self):
        self.dialog.close()
        self.dialog.deleteLater()
        APP.processEvents()
    def settle(self):
        for _ in range(4):
            APP.sendPostedEvents()
            APP.processEvents()
    def test_visual_tokens_and_all_control_states(self):
        sheet = self.dialog.styleSheet()
        for token in ('teal','ink','border','canvas'):
            self.assertIn(PALETTE[token], sheet)
        for state in (':focus', ':hover', ':pressed', ':disabled', ':checked', ':selected'):
            self.assertIn(state, sheet)
    def test_original_vector_icons_are_available(self):
        for name in ('growth','history','emotion','training','data','voice','shield','reset',
                     'chat','spin','heart','hide','close','spark','check','arrow'):
            self.assertFalse(icon(name).isNull(), name)
    def test_context_menu_has_readable_states_and_sections(self):
        for token in ('QMenu::item:selected', 'QMenu::item:disabled', 'QMenu::separator',
                      'QMenu::indicator:checked', 'QMenu::right-arrow'):
            self.assertIn(token, MENU_QSS)
        self.assertIn('font-size:13px', MENU_QSS)
        self.assertIn('#187E83', MENU_QSS)
    def test_four_traits_remain_exactly_the_original_numeric_values(self):
        self.dialog.update_state(derive({'xp':3265, 'counters':{'turns':24,'toolCalls':90}}))
        expected = derive({'xp':3265, 'counters':{'turns':24,'toolCalls':90}})['traits']
        self.assertEqual(set(self.dialog.trait_bars), set(expected))
        for key, number in expected.items():
            self.assertEqual(self.dialog.trait_bars[key].value(), int(number))
            self.assertTrue(self.dialog.trait_bars[key].parent().property('traitTile'))
    def test_all_pages_at_both_sizes_keep_footer_and_no_horizontal_scroll(self):
        self.dialog.show()
        for width, height in ((456,640),(420,480)):
            self.dialog.resize(width,height)
            for index in range(7):
                self.dialog.tabs.setCurrentIndex(index)
                self.settle()
                with self.subTest(size=(width,height),page=index):
                    close = self.dialog.close_button
                    point = close.mapTo(self.dialog, close.rect().bottomRight())
                    self.assertLessEqual(point.y(), self.dialog.height()-8)
                    self.assertLessEqual(point.x(), self.dialog.width()-8)
                    self.assertEqual(self.dialog.page_scrolls[index].horizontalScrollBar().maximum(), 0)
    def test_switching_pages_and_repainting_never_grants_permissions_or_training(self):
        spies = [QSignalSpy(signal) for signal in (self.dialog.voice_tools_requested,
            self.dialog.cloud_settings_requested, self.dialog.neural_reset_requested,
            self.dialog.neural_rollback_requested, self.dialog.emotion_learning_requested)]
        for index in range(7):
            self.dialog.tabs.setCurrentIndex(index)
            self.dialog.update_state(derive({'xp':3265}))
            self.dialog.update_emotion({'name':'平静','confidence':.6})
            self.dialog.update_training({})
            self.settle()
        self.assertTrue(all(spy.count()==0 for spy in spies))
    def test_ability_page_has_six_dimensions_and_hidden_compatibility_bars(self):
        self.dialog.update_state(derive({'xp': 3265, 'counters': {'turns': 20},
                                         'traits': {'affinity': 40, 'stamina': 30}}))
        self.dialog.update_behavior({'agility': 60, 'balance': 45, 'runs': 8, 'jumps': 3,
                                     'falls': 1, 'recoveries': 1, 'pets': 4, 'chats': 3})
        self.settle()
        self.assertEqual(len(self.dialog.ability_tiles), 6)
        self.assertEqual(len(self.dialog.ability_radar.values), 6)
        self.assertGreater(self.dialog.ability_tiles['action'][2].value(), 0)
        self.assertEqual(self.dialog.skill_bars['agility'].value(), 60)
        self.assertFalse(self.dialog.skill_bars['agility'].isVisible())

    def test_new_data_page_keeps_both_knowledge_and_voice_audit_panels(self):
        page = self.dialog.page_scrolls[6].widget().layout()
        self.assertLess(page.indexOf(self.dialog.data_panel), page.indexOf(self.dialog.voice_data_panel))
        self.assertEqual(self.dialog.tabs.count(), 7)
        self.assertEqual(self.dialog.emotion_sections.buttons()[0].text(), '此刻的我们')


if __name__ == '__main__':
    unittest.main()
