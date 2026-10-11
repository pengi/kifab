"""Table model for a list of :class:`Issue` objects."""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtGui import QBrush, QColor

from kifab.core.issues import Issue, Severity, sort_issues

COLUMNS = ("Severity", "Code", "Message", "Where", "Hint")
_COLORS = {
    Severity.ERROR: QColor(200, 30, 30),
    Severity.WARNING: QColor(190, 120, 0),
    Severity.INFO: QColor(90, 90, 90),
}
Index = QModelIndex | QPersistentModelIndex
SORT_ROLE = Qt.ItemDataRole.UserRole


def where(issue: Issue) -> str:
    parts = [p for p in (issue.path, issue.ref and f"[{issue.ref}]") if p]
    return " ".join(parts)


class IssueModel(QAbstractTableModel):
    def __init__(self) -> None:
        super().__init__()
        self._issues: list[Issue] = []

    @property
    def issues(self) -> list[Issue]:
        return list(self._issues)

    def set_issues(self, issues: list[Issue]) -> None:
        self.beginResetModel()
        self._issues = sort_issues(issues)
        self.endResetModel()

    def add(self, issue: Issue) -> None:
        row = len(self._issues)
        self.beginInsertRows(QModelIndex(), row, row)
        self._issues.append(issue)
        self.endInsertRows()

    def clear(self) -> None:
        self.set_issues([])

    def count(self, severity: Severity) -> int:
        return sum(1 for i in self._issues if i.severity is severity)

    def issue_at(self, row: int) -> Issue:
        return self._issues[row]

    # Qt model interface
    def rowCount(self, parent: Index = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._issues)

    def columnCount(self, parent: Index = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = 0
    ) -> object:
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
        ):
            return COLUMNS[section]
        return None

    def data(self, index: Index, role: int = 0) -> object:
        if not index.isValid():
            return None
        issue = self._issues[index.row()]
        texts = (
            issue.severity.value,
            issue.code.value,
            issue.message,
            where(issue),
            issue.hint or "",
        )
        if role == Qt.ItemDataRole.DisplayRole:
            return texts[index.column()]
        if role == Qt.ItemDataRole.ForegroundRole and index.column() == 0:
            return QBrush(_COLORS[issue.severity])
        if role == Qt.ItemDataRole.ToolTipRole:
            return issue.message + (f"\n-> {issue.hint}" if issue.hint else "")
        if role == SORT_ROLE:
            # severity sorts by rank (errors highest), the other columns by text
            return issue.severity.rank if index.column() == 0 else texts[index.column()]
        return None
