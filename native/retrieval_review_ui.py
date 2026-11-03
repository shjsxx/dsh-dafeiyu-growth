"""Passive local retrieval-review dialog: returns labels, never persists them."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QLabel,
    QListWidget, QListWidgetItem, QPlainTextEdit, QVBoxLayout,
)

from retrieval_review import _clean_row, _identity


class RetrievalReviewDialog(QDialog):
    """Review one query and stable-ID hits; no file/network/training side effects.

    API: RetrievalReviewDialog(query, hits, group_id, parent=None). hits is a
    list of {id: stable_id, text: plaintext, ...}. After exec()==Accepted,
    payload() returns {query,candidates,relevant,no_answer,group}; otherwise
    None. The caller separately opts into store.add(...,consent=True).
    """
    def __init__(self, query, hits, group_id, parent=None):
        super().__init__(parent)
        self.setWindowTitle('标注本次记忆检索')
        self.resize(640, 560)
        self._payload = None
        self._updating = False
        self._valid_input = True
        self._query, self._group = query, group_id
        self._ids = []
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('仅标注这一个问题及其候选记忆，不保存整段对话；不会训练或上传。'))
        layout.addWidget(QLabel('问题（纯文本）'))
        self.query_view = QPlainTextEdit(self)
        self.query_view.setReadOnly(True)
        self.query_view.setPlainText(query if isinstance(query, str) else '')
        self.query_view.setMaximumHeight(100)
        layout.addWidget(self.query_view)
        layout.addWidget(QLabel('勾选能回答该问题的记忆（可多选），或选择“没有合适答案”。'))
        self.candidates_list = QListWidget(self)
        self.candidates_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.candidates_list.setWordWrap(True)
        layout.addWidget(self.candidates_list)
        if not isinstance(hits, (list, tuple)) or len(hits) > 40:
            self._valid_input = False
            hits = []
        for hit in hits:
            if (not isinstance(hit, dict) or not _identity(hit.get('id'))
                    or hit['id'] in self._ids or not isinstance(hit.get('text'), str)
                    or not hit['text'].strip() or len(hit['text']) > 10000):
                self._valid_input = False
                continue
            self._ids.append(hit['id'])
            item = QListWidgetItem(hit['text'], self.candidates_list)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, hit['id'])
        try:
            _clean_row({'query': query, 'group': group_id, 'candidates': self._ids,
                        'relevant': [], 'no_answer': True})
        except (ValueError, TypeError):
            self._valid_input = False
        self.no_answer_box = QCheckBox('没有合适答案', self)
        self.consent_box = QCheckBox('同意仅在本机保存该问题和命中标注（不保存整段对话，不上传）', self)
        self.no_answer_box.setChecked(False)
        self.consent_box.setChecked(False)
        layout.addWidget(self.no_answer_box)
        layout.addWidget(self.consent_box)
        self.validation_label = QLabel(self)
        self.validation_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.validation_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, self)
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.save_button.setText('同意并返回标注')
        self.cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        self.cancel_button.setText('取消')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.candidates_list.itemChanged.connect(self._candidate_changed)
        self.no_answer_box.toggled.connect(self._no_answer_changed)
        self.consent_box.toggled.connect(self._update_validity)
        self._update_validity()

    def _selected(self):
        return [self.candidates_list.item(index).data(Qt.ItemDataRole.UserRole)
                for index in range(self.candidates_list.count())
                if self.candidates_list.item(index).checkState() == Qt.CheckState.Checked]

    def _candidate_changed(self, item):
        if self._updating:
            return
        self._updating = True
        if item.checkState() == Qt.CheckState.Checked:
            self.no_answer_box.setChecked(False)
        self._updating = False
        self._update_validity()

    def _no_answer_changed(self, checked):
        if self._updating:
            return
        self._updating = True
        if checked:
            for index in range(self.candidates_list.count()):
                self.candidates_list.item(index).setCheckState(Qt.CheckState.Unchecked)
        self._updating = False
        self._update_validity()

    def _update_validity(self, *_):
        valid = (self._valid_input and self.consent_box.isChecked()
                 and (bool(self._selected()) != self.no_answer_box.isChecked()))
        self.save_button.setEnabled(valid)
        if not self._valid_input:
            message = '问题、会话组或候选稳定ID无效；不能保存标注。'
        elif not self.consent_box.isChecked():
            message = '未同意本机保存；不会保存任何标注。'
        elif not (self._selected() or self.no_answer_box.isChecked()):
            message = '请选择相关记忆或“没有合适答案”。'
        else:
            message = '返回后由调用方仅在本机保存；本窗口不写文件、不上传、不训练。'
        self.validation_label.setText(message)

    def accept(self):
        """Programmatic accept is consent/label gated too."""
        self._update_validity()
        if not self.save_button.isEnabled():
            return
        row = _clean_row({'query': self._query, 'group': self._group,
                          'candidates': self._ids, 'relevant': self._selected(),
                          'no_answer': self.no_answer_box.isChecked()})
        self._payload = {key: value for key, value in row.items() if key != 'at'}
        super().accept()

    def reject(self):
        self._payload = None
        super().reject()

    def payload(self):
        if self.result() != QDialog.DialogCode.Accepted or self._payload is None:
            return None
        return {key: list(value) if isinstance(value, list) else value
                for key, value in self._payload.items()}
