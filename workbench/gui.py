from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QApplication,
        QComboBox,
        QDialog,
        QFileDialog,
        QFormLayout,
        QHBoxLayout,
        QHeaderView,
        QInputDialog,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QPlainTextEdit,
        QTableWidget,
        QTableWidgetItem,
        QTabWidget,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:  # pragma: no cover - startup guidance
    raise SystemExit("Install GUI dependencies with: python -m pip install -e .") from exc

from . import web3bb


APP_NAME = "Web3 Bug Bounty Workbench"


class WorkbenchWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1180, 760)
        self.current_run = QLineEdit()
        self.current_run.setPlaceholderText("Current run path")
        self.current_run.editingFinished.connect(self.refresh_run_tabs)

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addWidget(self.current_run)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.setCentralWidget(root)

        self.build_dashboard_tab()
        self.build_new_target_tab()
        self.build_doctor_tab()
        self.build_scope_tab()
        self.build_scan_tab()
        self.build_hypotheses_tab()
        self.build_packet_tab()
        self.refresh_dashboard()

    def run_path(self) -> Path:
        text = self.current_run.text().strip()
        if not text:
            raise ValueError("Select or create a run first.")
        return Path(text)

    def set_run(self, path: Path) -> None:
        self.current_run.setText(str(path))
        self.refresh_run_tabs()

    def build_dashboard_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        buttons = QHBoxLayout()
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_dashboard)
        open_folder = QPushButton("Open Run Folder")
        open_folder.clicked.connect(lambda: self.open_path(self.run_path()))
        export = QPushButton("Export Review Packet")
        export.clicked.connect(self.export_review_packet)
        buttons.addWidget(refresh)
        buttons.addWidget(open_folder)
        buttons.addWidget(export)
        buttons.addStretch()
        layout.addLayout(buttons)

        self.runs_table = QTableWidget(0, 6)
        self.runs_table.setHorizontalHeaderLabels(["Target", "Program URL", "Created", "Hypotheses", "Latest Status", "Run Path"])
        self.runs_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.runs_table.itemSelectionChanged.connect(self.pick_dashboard_run)
        layout.addWidget(self.runs_table)
        self.tabs.addTab(tab, "Dashboard")

    def build_new_target_tab(self) -> None:
        tab = QWidget()
        layout = QFormLayout(tab)
        self.target_name = QLineEdit()
        self.program_url = QLineEdit()
        self.scope_url = QLineEdit()
        self.resources_url = QLineEdit()
        self.source_path = QLineEdit()
        source_buttons = QHBoxLayout()
        source_buttons.addWidget(self.source_path)
        pick_file = QPushButton("Zip")
        pick_file.clicked.connect(self.pick_source_zip)
        pick_folder = QPushButton("Folder")
        pick_folder.clicked.connect(self.pick_source_folder)
        source_buttons.addWidget(pick_file)
        source_buttons.addWidget(pick_folder)
        actions = QHBoxLayout()
        for label, handler in [
            ("Create Run", self.create_run),
            ("Ingest", self.ingest_current_run),
            ("Scope", self.scope_current_run),
            ("Scan", self.scan_generic),
        ]:
            button = QPushButton(label)
            button.clicked.connect(handler)
            actions.addWidget(button)
        actions.addStretch()
        layout.addRow("Target name", self.target_name)
        layout.addRow("Program URL", self.program_url)
        layout.addRow("Scope URL", self.scope_url)
        layout.addRow("Resources URL", self.resources_url)
        layout.addRow("Foundry/Hardhat zip or folder", source_buttons)
        layout.addRow(actions)
        self.tabs.addTab(tab, "New Target")

    def build_doctor_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        buttons = QHBoxLayout()
        run = QPushButton("Run Doctor")
        run.clicked.connect(self.run_doctor)
        save = QPushButton("Save tool_versions.json")
        save.clicked.connect(self.run_doctor)
        buttons.addWidget(run)
        buttons.addWidget(save)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.doctor_table = QTableWidget(0, 5)
        self.doctor_table.setHorizontalHeaderLabels(["Tool", "Detected", "Version", "Path", "Install Hint"])
        self.doctor_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.doctor_table)
        self.tabs.addTab(tab, "Tool Doctor")

    def build_scope_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        buttons = QHBoxLayout()
        load = QPushButton("Load Scope")
        load.clicked.connect(self.load_scope)
        save = QPushButton("Save Scope")
        save.clicked.connect(self.save_scope)
        fetch = QPushButton("Fetch Page Text")
        fetch.clicked.connect(self.fetch_scope_page)
        buttons.addWidget(load)
        buttons.addWidget(save)
        buttons.addWidget(fetch)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.fetch_url = QLineEdit()
        self.fetch_url.setPlaceholderText("URL to fetch into the scope notes")
        layout.addWidget(self.fetch_url)
        self.scope_text = QPlainTextEdit()
        layout.addWidget(self.scope_text)
        self.tabs.addTab(tab, "Scope")

    def build_scan_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        buttons = QHBoxLayout()
        self.profile_combo = QComboBox()
        buttons.addWidget(QLabel("Foundry profile"))
        buttons.addWidget(self.profile_combo)
        for label, handler in [
            ("Run Generic Scan", self.scan_generic),
            ("Run Selected Profile Scan", self.scan_profile),
            ("Run All Profiles", self.scan_all_profiles),
            ("Refresh History", self.refresh_scan_history),
        ]:
            button = QPushButton(label)
            button.clicked.connect(handler)
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.executions_table = QTableWidget(0, 7)
        self.executions_table.setHorizontalHeaderLabels(["Tool", "Command", "Exit", "Summary", "Stdout", "Stderr", "Started"])
        self.executions_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.executions_table.cellDoubleClicked.connect(self.open_execution_file)
        layout.addWidget(self.executions_table)
        self.tabs.addTab(tab, "Scan")

    def build_hypotheses_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        buttons = QHBoxLayout()
        for label, handler in [
            ("Refresh", self.refresh_hypotheses),
            ("Add", self.add_hypothesis),
            ("Edit", self.edit_hypothesis),
            ("Import Leads", self.import_leads),
            ("Gate", self.gate_hypothesis),
            ("Close", self.close_hypothesis),
        ]:
            button = QPushButton(label)
            button.clicked.connect(handler)
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.hypotheses_table = QTableWidget(0, 8)
        self.hypotheses_table.setHorizontalHeaderLabels(
            ["ID", "Title", "Status", "PoC", "Validation", "Gate Decision", "Next Action", "Contract"]
        )
        self.hypotheses_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.hypotheses_table)
        self.tabs.addTab(tab, "Hypotheses")

    def build_packet_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        export = QPushButton("Export ChatGPT Review Packet")
        export.clicked.connect(self.export_review_packet)
        open_folder = QPushButton("Open Review Packet Folder")
        open_folder.clicked.connect(lambda: self.open_path(self.run_path() / "review_packet"))
        layout.addWidget(export)
        layout.addWidget(open_folder)
        self.packet_output = QPlainTextEdit()
        self.packet_output.setReadOnly(True)
        layout.addWidget(self.packet_output)
        self.tabs.addTab(tab, "Review Packet")

    def refresh_dashboard(self) -> None:
        runs = web3bb.list_runs()
        self.fill_table(
            self.runs_table,
            [
                [
                    item["target_name"],
                    item["program_url"],
                    item["created_at"],
                    str(item["hypothesis_count"]),
                    item["latest_status"],
                    item["run_path"],
                ]
                for item in runs
            ],
        )

    def pick_dashboard_run(self) -> None:
        rows = self.runs_table.selectionModel().selectedRows()
        if rows:
            self.set_run(Path(self.runs_table.item(rows[0].row(), 5).text()))

    def refresh_run_tabs(self) -> None:
        try:
            self.load_profiles()
            self.refresh_scan_history()
            self.refresh_hypotheses()
        except Exception:
            pass

    def pick_source_zip(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select source zip", "", "Zip files (*.zip);;All files (*)")
        if path:
            self.source_path.setText(path)

    def pick_source_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select source folder")
        if path:
            self.source_path.setText(path)

    def create_run(self) -> None:
        try:
            run = web3bb.init_run(self.target_name.text(), self.program_url.text(), Path(self.source_path.text()))
            self.set_run(run)
            self.refresh_dashboard()
            self.info(f"Created run:\n{run}")
        except Exception as exc:
            self.error(exc)

    def ingest_current_run(self) -> None:
        self.call_backend(lambda: web3bb.ingest_run(self.run_path()), "Ingest complete.")

    def scope_current_run(self) -> None:
        urls = [self.scope_url.text(), self.resources_url.text()]
        self.call_backend(lambda: web3bb.scope_run(self.run_path(), urls), "Scope brief ready.")
        self.load_scope()

    def run_doctor(self) -> None:
        try:
            output_dir = self.run_path() / "metadata" if self.current_run.text().strip() else Path.cwd()
            results = web3bb.doctor(output_dir)
            self.fill_table(
                self.doctor_table,
                [[tool, str(info["detected"]), info["version"], info["path"], info["install_hint"]] for tool, info in results.items()],
            )
        except Exception as exc:
            self.error(exc)

    def load_scope(self) -> None:
        try:
            path = self.run_path() / "scope" / "scope_brief.md"
            self.scope_text.setPlainText(path.read_text(encoding="utf-8") if path.exists() else "")
        except Exception as exc:
            self.error(exc)

    def save_scope(self) -> None:
        try:
            path = self.run_path() / "scope" / "scope_brief.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self.scope_text.toPlainText(), encoding="utf-8")
            self.info("Scope saved.")
        except Exception as exc:
            self.error(exc)

    def fetch_scope_page(self) -> None:
        try:
            text = web3bb.fetch_page_text(self.fetch_url.text().strip())
            current = self.scope_text.toPlainText().rstrip()
            self.scope_text.setPlainText(f"{current}\n\n## Fetched Page Text\n{text}\n" if current else text)
        except Exception as exc:
            self.error(exc)

    def load_profiles(self) -> None:
        self.profile_combo.clear()
        self.profile_combo.addItem("")
        path = self.run_path() / "metadata" / "profiles.json"
        profiles = web3bb.read_json(path) if path.exists() else {}
        self.profile_combo.addItems(sorted(profiles))

    def scan_generic(self) -> None:
        self.call_backend(lambda: web3bb.scan_run(self.run_path()), "Generic scan finished.")
        self.refresh_scan_history()

    def scan_profile(self) -> None:
        profile = self.profile_combo.currentText().strip()
        if not profile:
            self.info("Select a Foundry profile first.")
            return
        self.call_backend(lambda: web3bb.scan_run(self.run_path(), profile=profile), "Profile scan finished.")
        self.refresh_scan_history()

    def scan_all_profiles(self) -> None:
        self.call_backend(lambda: web3bb.scan_run(self.run_path(), all_profiles=True), "All-profile scan finished.")
        self.refresh_scan_history()

    def refresh_scan_history(self) -> None:
        rows = web3bb.tool_execution_history(self.run_path())
        self.fill_table(
            self.executions_table,
            [
                [
                    row["tool"],
                    row["command"],
                    str(row["exit_code"]),
                    row["parsed_summary"],
                    row["stdout_path"],
                    row["stderr_path"],
                    row["start_time"],
                ]
                for row in rows
            ],
        )

    def open_execution_file(self, row: int, column: int) -> None:
        if column in {4, 5}:
            self.open_path(Path(self.executions_table.item(row, column).text()))

    def refresh_hypotheses(self) -> None:
        rows = [dict(row) for row in web3bb.list_hypotheses(self.run_path())]
        self.fill_table(
            self.hypotheses_table,
            [
                [
                    row["id"],
                    row["title"],
                    row["status"],
                    row["poc_status"],
                    row["validation_status"],
                    row["gate_decision"],
                    row["next_action"],
                    row["contract"],
                ]
                for row in rows
            ],
        )

    def selected_hypothesis_id(self) -> str:
        rows = self.hypotheses_table.selectionModel().selectedRows()
        if not rows:
            raise ValueError("Select a hypothesis first.")
        return self.hypotheses_table.item(rows[0].row(), 0).text()

    def add_hypothesis(self) -> None:
        dialog = HypothesisDialog(self, {})
        if dialog.exec() == QDialog.Accepted:
            try:
                web3bb.add_hypothesis(self.run_path(), dialog.values())
                web3bb.export_run(self.run_path())
                self.refresh_hypotheses()
            except Exception as exc:
                self.error(exc)

    def edit_hypothesis(self) -> None:
        try:
            row = dict(web3bb.get_hypothesis(self.run_path(), self.selected_hypothesis_id()))
            dialog = HypothesisDialog(self, row)
            if dialog.exec() == QDialog.Accepted:
                web3bb.update_hypothesis(self.run_path(), row["id"], dialog.values())
                web3bb.export_run(self.run_path())
                self.refresh_hypotheses()
        except Exception as exc:
            self.error(exc)

    def import_leads(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import leads", "", "Lead files (*.csv *.md);;All files (*)")
        if not path:
            return
        self.call_backend(lambda: web3bb.import_leads(self.run_path(), Path(path)), "Leads imported.")
        self.refresh_hypotheses()

    def gate_hypothesis(self) -> None:
        try:
            decision, ok = QInputDialog.getText(self, "Gate hypothesis", "Decision")
            if not ok:
                return
            notes, ok = QInputDialog.getText(self, "Gate hypothesis", "Notes")
            if not ok:
                return
            web3bb.gate_hypothesis(self.run_path(), self.selected_hypothesis_id(), decision, notes)
            self.refresh_hypotheses()
        except Exception as exc:
            self.error(exc)

    def close_hypothesis(self) -> None:
        try:
            status, ok = QInputDialog.getItem(self, "Close hypothesis", "Status", web3bb.HYPOTHESIS_STATUSES, 0, False)
            if not ok:
                return
            reason, ok = QInputDialog.getText(self, "Close hypothesis", "Reason")
            if not ok:
                return
            web3bb.close_hypothesis(self.run_path(), self.selected_hypothesis_id(), status, reason)
            self.refresh_hypotheses()
        except Exception as exc:
            self.error(exc)

    def export_review_packet(self) -> None:
        try:
            result = web3bb.export_review_packet(self.run_path())
            self.packet_output.setPlainText("\n".join(f"{key}: {value}" for key, value in result.items()))
            self.info(f"Review packet exported:\n{result['review_packet']}")
        except Exception as exc:
            self.error(exc)

    def call_backend(self, func, message: str) -> None:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            result = func()
            self.info(f"{message}\n{result}")
        except Exception as exc:
            self.error(exc)
        finally:
            QApplication.restoreOverrideCursor()

    def fill_table(self, table: QTableWidget, rows: list[list[str]]) -> None:
        table.setRowCount(0)
        for values in rows:
            row = table.rowCount()
            table.insertRow(row)
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                table.setItem(row, column, item)

    def open_path(self, path: Path) -> None:
        if path.exists():
            os.startfile(path)  # type: ignore[attr-defined]

    def info(self, message: str) -> None:
        QMessageBox.information(self, APP_NAME, message)

    def error(self, exc: Exception) -> None:
        QMessageBox.critical(self, APP_NAME, str(exc))


class HypothesisDialog(QDialog):
    def __init__(self, parent: QWidget, row: dict) -> None:
        super().__init__(parent)
        self.setWindowTitle("Hypothesis")
        self.inputs: dict[str, QLineEdit | QPlainTextEdit | QComboBox] = {}
        layout = QFormLayout(self)
        for key in ["title", "contract", "function", "source", "poc_status", "validation_status", "gate_decision", "next_action"]:
            field = QLineEdit(str(row.get(key, "")))
            self.inputs[key] = field
            layout.addRow(key.replace("_", " ").title(), field)
        status = QComboBox()
        status.addItems(web3bb.HYPOTHESIS_STATUSES)
        status.setCurrentText(str(row.get("status", "New")))
        self.inputs["status"] = status
        layout.addRow("Status", status)
        hypothesis = QPlainTextEdit(str(row.get("hypothesis", "")))
        self.inputs["hypothesis"] = hypothesis
        layout.addRow("Hypothesis", hypothesis)
        notes = QPlainTextEdit(str(row.get("notes", "")))
        self.inputs["notes"] = notes
        layout.addRow("Notes", notes)
        buttons = QHBoxLayout()
        save = QPushButton("Save")
        save.clicked.connect(self.accept)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(save)
        buttons.addWidget(cancel)
        layout.addRow(buttons)

    def values(self) -> dict:
        data = {}
        for key, widget in self.inputs.items():
            if isinstance(widget, QPlainTextEdit):
                data[key] = widget.toPlainText()
            elif isinstance(widget, QComboBox):
                data[key] = widget.currentText()
            else:
                data[key] = widget.text()
        return data


def main() -> None:
    app = QApplication(sys.argv)
    window = WorkbenchWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
