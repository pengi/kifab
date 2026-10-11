"""Dialog for ``kifab init``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QWidget,
)

from kifab.core.config import SUPPORTED_LAYERS
from kifab.fabs.base import available_fabs


@dataclass(frozen=True)
class NewProjectRequest:
    parent_dir: Path
    name: str
    fab: str
    layers: int
    git: bool
    ci: bool

    @property
    def directory(self) -> Path:
        return self.parent_dir / self.name


class NewProjectDialog(QDialog):
    def __init__(
        self, parent: QWidget | None = None, start: Path | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("New kifab project")
        self.name = QLineEdit()
        self.name.setPlaceholderText("my-board")
        self.fab = QComboBox()
        for key, cls in available_fabs().items():
            self.fab.addItem(cls.title or key, key)
        self.layers = QSpinBox()
        self.layers.setRange(min(SUPPORTED_LAYERS), max(SUPPORTED_LAYERS))
        self.layers.setSingleStep(2)
        self.layers.setValue(2)
        self.location = QLineEdit(str(start or Path.home()))
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.location)
        row.addWidget(browse)
        self.git = QCheckBox("git init (unless already in a repository)")
        self.git.setChecked(True)
        self.ci = QCheckBox("GitHub release workflow (release on tag v*)")
        form = QFormLayout(self)
        form.addRow("Name", self.name)
        form.addRow("Fab", self.fab)
        form.addRow("Copper layers", self.layers)
        form.addRow("Create in", row)
        form.addRow("", self.git)
        form.addRow("", self.ci)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Create in", self.location.text()
        )
        if chosen:
            self.location.setText(chosen)

    def request(self) -> NewProjectRequest:
        return NewProjectRequest(
            parent_dir=Path(self.location.text()).expanduser(),
            name=self.name.text().strip(),
            fab=str(self.fab.currentData()),
            layers=self.layers.value(),
            git=self.git.isChecked(),
            ci=self.ci.isChecked(),
        )
